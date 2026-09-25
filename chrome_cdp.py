from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urlsplit, urlunsplit, parse_qsl, urlencode, quote

import websocket

from config import load_settings
from database import (
    get_by_source_key,
    backlog_completed,
    get_state,
    set_state,
    remember_seen_candidate,
    is_seen_candidate,
    seen_candidate,
    mark_seen_processed,
    pending_initial_new_count,
    touch_visible_applications,
)

BASE_DIR = Path(__file__).resolve().parent
RESUME_DIR = BASE_DIR / "data" / "resumes"
RESUME_DIR.mkdir(parents=True, exist_ok=True)

EMAILISH_INDEED = re.compile(r"indeed\.", re.I)


def new_candidates_queue_url(url):
    value = (url or "").strip()
    if not value or "indeed." not in value.lower():
        return value

    try:
        parts = urlsplit(value)
        low_path = (parts.path or "").lower()
        if not any(x in low_path for x in ["candidate", "applicant", "application"]):
            return value

        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["statusName"] = "New"
        query["tab"] = "manage"

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )
    except Exception:
        return value


WAIT_CANDIDATES_READY_JS = r"""
(async () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const started = Date.now();

  while (Date.now() - started < 9000) {
    const body = document.body?.innerText || '';
    const ready = (
      /manage\s+candidates/i.test(body)
      || /all\s+applications/i.test(body)
      || (
        /\bnew\b/i.test(body)
        && /search\s+candidates/i.test(body)
      )
      || document.querySelector(
        '[data-testid*="candidate" i],'
        + '[data-testid*="applicant" i],'
        + '[data-testid*="application" i]'
      )
    );

    if (ready) {
      window.scrollTo(0, 0);

      for (const el of document.querySelectorAll(
        '[role="main"] [style*="overflow"],'
        + '[data-testid*="candidate" i] [style*="overflow"],'
        + '[class*="candidate" i] [style*="overflow"]'
      )) {
        try {
          if (el.scrollHeight > el.clientHeight + 120) {
            el.scrollTop = 0;
          }
        } catch (_) {}
      }

      return {ready: true, bodyPreview: body.slice(0, 5000)};
    }

    await sleep(250);
  }

  return {
    ready: false,
    bodyPreview: (document.body?.innerText || '').slice(0, 5000),
  };
})()
"""


class ChromeConnectionError(RuntimeError):
    pass


def chrome_user_data_dir() -> Path:
    """
    Chrome Stable default user-data root on Windows.
    Remote Debugging enabled via chrome://inspect/#remote-debugging writes
    DevToolsActivePort here for the running browser.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ChromeConnectionError("LOCALAPPDATA is not available on this Windows account.")
    return Path(local) / "Google" / "Chrome" / "User Data"


def devtools_active_port_path() -> Path:
    return chrome_user_data_dir() / "DevToolsActivePort"


def read_devtools_endpoint():
    path = devtools_active_port_path()

    if not path.exists():
        raise ChromeConnectionError(
            "Chrome Remote Debugging is not enabled for the running Chrome. "
            "Open chrome://inspect/#remote-debugging in your normal Chrome, "
            "enable Remote Debugging, and click Allow when Chrome asks."
        )

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ChromeConnectionError(f"Could not read {path}: {e}")

    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    if len(lines) < 2:
        raise ChromeConnectionError(
            f"Chrome DevToolsActivePort is incomplete: {path}"
        )

    try:
        port = int(lines[0])
    except Exception:
        raise ChromeConnectionError(
            f"Chrome DevToolsActivePort contains invalid port: {lines[0]!r}"
        )

    if not (1 <= port <= 65535):
        raise ChromeConnectionError(f"Chrome debugging port is invalid: {port}")

    browser_path = lines[1]
    if not browser_path.startswith("/"):
        browser_path = "/" + browser_path

    ws_url = f"ws://127.0.0.1:{port}{browser_path}"
    return {
        "port": port,
        "ws_url": ws_url,
        "path": str(path),
        "user_data_dir": str(chrome_user_data_dir()),
    }


class CDPClient:
    """
    Minimal Chrome DevTools Protocol client attached directly to the user's
    already-running Chrome browser WebSocket.

    No Playwright. No test Chrome. No extra browser profile.
    """
    def __init__(self, timeout=15):
        ep = read_devtools_endpoint()
        self.endpoint = ep
        try:
            # suppress_origin avoids Chrome rejecting a synthetic WebSocket Origin.
            self.ws = websocket.create_connection(
                ep["ws_url"],
                timeout=timeout,
                suppress_origin=True,
            )
        except TypeError:
            # Compatibility fallback for older websocket-client.
            self.ws = websocket.create_connection(
                ep["ws_url"],
                timeout=timeout,
            )
        except Exception as e:
            raise ChromeConnectionError(
                "Chrome debugging WebSocket could not connect. "
                "Make sure Remote Debugging is enabled and click Allow in Chrome. "
                f"Details: {e}"
            )

        self._id = 0
        self._lock = threading.RLock()

        # Page-level CDP sessions can be invalidated by Indeed navigation or
        # Chrome target replacement while the browser-level DevTools WebSocket
        # remains healthy. Keep target/session aliases so we can re-attach
        # transparently instead of failing every candidate.
        self._session_targets = {}
        self._session_aliases = {}
        self._session_recovery_count = 0

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

        self._session_targets.clear()
        self._session_aliases.clear()

    @staticmethod
    def _is_missing_session_error(error):
        text = str(error or "").lower()
        return (
            "-32001" in text
            or "session with given id not found" in text
            or "session with given id" in text and "not found" in text
            or "no session with given id" in text
            or "invalid session id" in text
        )

    def _resolve_session_id(self, session_id):
        if not session_id:
            return session_id

        current = session_id
        seen = set()

        while current in self._session_aliases and current not in seen:
            seen.add(current)
            current = self._session_aliases[current]

        return current

    def _remember_session(self, session_id, target_id):
        if session_id and target_id:
            self._session_targets[session_id] = target_id

    def _forget_session(self, session_id):
        if not session_id:
            return

        resolved = self._resolve_session_id(session_id)

        for key, value in list(self._session_aliases.items()):
            if key in {session_id, resolved} or value in {session_id, resolved}:
                self._session_aliases.pop(key, None)

        self._session_targets.pop(session_id, None)
        self._session_targets.pop(resolved, None)

    def _recover_page_session(self, original_session_id):
        """
        Re-attach a dead flattened CDP page session without opening a new
        browser-level DevTools WebSocket.

        This specifically repairs Chrome error -32001:
          "Session with given id not found"
        """
        resolved = self._resolve_session_id(original_session_id)
        target_id = (
            self._session_targets.get(resolved)
            or self._session_targets.get(original_session_id)
        )

        # Confirm the old target still exists. If Indeed replaced it, pick the
        # best currently-open Indeed page and continue in that target.
        try:
            targets = self.targets()
        except Exception:
            targets = []

        live_ids = {
            t.get("targetId")
            for t in targets
            if t.get("targetId")
        }

        if target_id not in live_ids:
            indeed_targets = [
                t
                for t in targets
                if "indeed." in (t.get("url") or "").lower()
            ]

            if not indeed_targets:
                return None

            saved = load_settings().get("indeed_candidates_url", "")
            indeed_targets.sort(
                key=lambda t: _target_score(t, saved),
                reverse=True,
            )
            target_id = indeed_targets[0].get("targetId")

        if not target_id:
            return None

        # Attach directly at browser level. command() has no page session here,
        # so recovery cannot recurse into itself.
        r = self.command(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
            timeout=15,
            _allow_session_recovery=False,
        )

        new_sid = r.get("sessionId")
        if not new_sid:
            return None

        self._remember_session(new_sid, target_id)
        self._session_aliases[original_session_id] = new_sid

        if resolved and resolved != original_session_id:
            self._session_aliases[resolved] = new_sid

        # Enable useful page domains on the replacement session.
        for domain in ("Runtime.enable", "Page.enable"):
            try:
                self.command(
                    domain,
                    session_id=new_sid,
                    timeout=10,
                    _allow_session_recovery=False,
                )
            except Exception:
                pass

        self._session_recovery_count += 1

        try:
            set_state(
                "cdp_session_recoveries",
                str(self._session_recovery_count),
            )
            set_state(
                "cdp_last_session_recovery_at",
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

        return new_sid

    def command(
        self,
        method,
        params=None,
        session_id=None,
        timeout=30,
        _allow_session_recovery=True,
    ):
        with self._lock:
            original_session_id = session_id
            effective_session_id = self._resolve_session_id(session_id)

            self._id += 1
            msg_id = self._id

            payload = {
                "id": msg_id,
                "method": method,
                "params": params or {},
            }

            if effective_session_id:
                payload["sessionId"] = effective_session_id

            try:
                self.ws.settimeout(timeout)
                self.ws.send(json.dumps(payload))
            except Exception as e:
                raise ChromeConnectionError(
                    f"Chrome command send failed: {e}"
                )

            deadline = time.time() + timeout

            while time.time() < deadline:
                try:
                    raw = self.ws.recv()
                except Exception as e:
                    raise ChromeConnectionError(
                        f"Chrome command receive failed: {e}"
                    )

                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                # Ignore asynchronous CDP events and replies for other commands.
                if data.get("id") != msg_id:
                    continue

                if "error" in data:
                    err = data["error"]

                    if (
                        original_session_id
                        and _allow_session_recovery
                        and self._is_missing_session_error(err)
                        and method not in {
                            "Target.attachToTarget",
                            "Target.detachFromTarget",
                        }
                    ):
                        recovered = self._recover_page_session(
                            original_session_id
                        )

                        if recovered:
                            remaining = max(
                                5,
                                int(deadline - time.time()),
                            )

                            return self.command(
                                method,
                                params,
                                session_id=original_session_id,
                                timeout=remaining,
                                _allow_session_recovery=False,
                            )

                    raise ChromeConnectionError(
                        f"Chrome CDP {method} failed: {err}"
                    )

                return data.get("result") or {}

            raise ChromeConnectionError(
                f"Chrome CDP command timed out: {method}"
            )

    def targets(self):
        result = self.command("Target.getTargets", timeout=15)
        targets = []
        for t in result.get("targetInfos", []):
            if t.get("type") != "page":
                continue
            targets.append({
                "targetId": t.get("targetId"),
                "url": t.get("url") or "",
                "title": t.get("title") or "",
                "attached": bool(t.get("attached")),
            })
        return targets

    def attach(self, target_id):
        r = self.command(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
            timeout=15,
        )
        sid = r.get("sessionId")
        if not sid:
            raise ChromeConnectionError("Chrome did not return a page session.")

        self._remember_session(sid, target_id)

        # Enable useful domains.
        try:
            self.command("Runtime.enable", session_id=sid, timeout=10)
        except Exception:
            pass
        try:
            self.command("Page.enable", session_id=sid, timeout=10)
        except Exception:
            pass
        return sid

    def detach(self, session_id):
        effective = self._resolve_session_id(session_id)

        try:
            self.command(
                "Target.detachFromTarget",
                {"sessionId": effective},
                timeout=10,
                _allow_session_recovery=False,
            )
        except Exception:
            pass
        finally:
            self._forget_session(session_id)

    def evaluate(
        self,
        session_id,
        expression,
        await_promise=True,
        timeout=60,
        user_gesture=False,
    ):
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": bool(await_promise),
                "returnByValue": True,
                "userGesture": bool(user_gesture),
            },
            session_id=session_id,
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            desc = (
                result.get("exceptionDetails", {})
                .get("exception", {})
                .get("description")
            ) or str(result.get("exceptionDetails"))
            raise ChromeConnectionError(f"JavaScript error in Indeed page: {desc}")
        return (result.get("result") or {}).get("value")

    def navigate(self, session_id, url, timeout=45):
        if "indeed." not in (url or "").lower():
            raise ChromeConnectionError("V6 blocks automatic navigation outside Indeed.")
        self.command(
            "Page.navigate",
            {"url": url},
            session_id=session_id,
            timeout=15,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self.evaluate(
                    session_id,
                    "document.readyState",
                    await_promise=False,
                    timeout=8,
                )
                if state in ("interactive", "complete"):
                    time.sleep(0.8)
                    return
            except Exception:
                pass
            time.sleep(0.25)

        raise ChromeConnectionError("Indeed page navigation timed out.")



# ---------------------------------------------------------------------
# V10.1: ONE PERSISTENT CHROME DEVTOOLS CONNECTION
# ---------------------------------------------------------------------
#
# Chrome's "Allow remote debugging?" permission is associated with a new
# external DevTools connection. V10 previously created a new WebSocket for
# every dashboard poll/status check, which caused the permission popup to
# appear repeatedly.
#
# V10.1 creates one browser-level CDP WebSocket after the user explicitly
# clicks "Connect Chrome" and reuses it for all subsequent work.
# Dashboard polling NEVER opens a new Chrome connection.
#
_shared_client = None
_shared_client_lock = threading.RLock()
_shared_connected_at = None
_shared_last_error = None


def _close_client_quietly(client):
    try:
        if client:
            client.close()
    except Exception:
        pass


def reset_shared_chrome(reason=None):
    global _shared_client, _shared_connected_at, _shared_last_error

    with _shared_client_lock:
        old = _shared_client
        _shared_client = None
        _shared_connected_at = None
        if reason:
            _shared_last_error = str(reason)

    _close_client_quietly(old)


def _ping_client(client):
    # Browser.getVersion is a cheap browser-level command and does not
    # attach to/open any page.
    client.command("Browser.getVersion", timeout=8)
    return True


def connect_shared_chrome(timeout=60):
    """
    Explicit user-approved connection.

    This is the ONLY normal path that creates a new Chrome WebSocket.
    It may trigger Chrome's one-time "Allow remote debugging?" prompt.
    """
    global _shared_client, _shared_connected_at, _shared_last_error

    with _shared_client_lock:
        if _shared_client is not None:
            try:
                _ping_client(_shared_client)
                return _shared_client
            except Exception as e:
                old = _shared_client
                _shared_client = None
                _shared_connected_at = None
                _shared_last_error = str(e)
                _close_client_quietly(old)

        client = CDPClient(timeout=timeout)

        try:
            _ping_client(client)
        except Exception:
            _close_client_quietly(client)
            raise

        _shared_client = client
        _shared_connected_at = time.time()
        _shared_last_error = None
        return client


def get_shared_chrome():
    """
    Return the already-approved connection.

    IMPORTANT: this function NEVER creates a connection and therefore never
    triggers the Chrome permission popup.
    """
    with _shared_client_lock:
        client = _shared_client

    if client is None:
        raise ChromeConnectionError(
            "Chrome is not connected to this console session yet. "
            "Automatic reconnect is running in the background."
        )

    try:
        _ping_client(client)
        return client
    except Exception as e:
        reset_shared_chrome(e)
        raise ChromeConnectionError(
            "The existing Chrome connection was closed. "
            "Automatic reconnect will restore it."
        )


def shared_connection_snapshot():
    with _shared_client_lock:
        return {
            "connected": _shared_client is not None,
            "connected_at": _shared_connected_at,
            "last_error": _shared_last_error,
        }


def cdp_session_recovery_snapshot():
    with _shared_client_lock:
        client = _shared_client

    return {
        "recoveries": int(
            getattr(client, "_session_recovery_count", 0)
            if client is not None
            else (get_state("cdp_session_recoveries", "0") or 0)
        ),
        "last_recovery_at": get_state("cdp_last_session_recovery_at"),
    }


def _target_score(t, saved_url=""):
    url = (t.get("url") or "")
    low = url.lower()
    title = (t.get("title") or "").lower()
    if "indeed." not in low:
        return -1000

    score = 1
    saved = urldefrag(saved_url or "")[0]
    if saved and urldefrag(url)[0] == saved:
        score += 100
    if any(k in low for k in ["candidate", "applicant", "application"]):
        score += 20
    if any(k in title for k in ["candidate", "applicant", "jobs - indeed for employers"]):
        score += 10
    if "employer" in low or "employers" in low:
        score += 5
    return score


def choose_indeed_target(client: CDPClient, saved_url=""):
    targets = client.targets()
    scored = sorted(
        ((_target_score(t, saved_url), t) for t in targets),
        key=lambda x: x[0],
        reverse=True,
    )
    if not scored or scored[0][0] < 0:
        raise ChromeConnectionError(
            "Chrome is connected, but no Indeed tab is open yet."
        )
    return scored[0][1], targets


def find_indeed_target(client: CDPClient, saved_url=""):
    """Return (target_or_none, all_targets) without treating missing Indeed as a Chrome failure."""
    targets = client.targets()
    scored = sorted(
        ((_target_score(t, saved_url), t) for t in targets),
        key=lambda x: x[0],
        reverse=True,
    )
    if not scored or scored[0][0] < 0:
        return None, targets
    return scored[0][1], targets


def open_indeed_in_existing_chrome(url=None):
    """
    Create an Indeed tab inside the SAME already-approved Chrome connection.
    No new browser-level DevTools connection is created.
    """
    s = load_settings()
    target_url = (
        url
        or s.get("indeed_start_url")
        or "https://www.indeed.com/employers/cs/login"
    ).strip()

    if "indeed." not in target_url.lower():
        target_url = "https://www.indeed.com/employers/cs/login"

    c = get_shared_chrome()

    result = c.command(
        "Target.createTarget",
        {"url": target_url},
        timeout=20,
    )

    target_id = result.get("targetId")
    if not target_id:
        raise ChromeConnectionError("Chrome did not create the Indeed tab.")

    time.sleep(1.0)

    for t in c.targets():
        if t.get("targetId") == target_id:
            return {
                "ok": True,
                "target_id": target_id,
                "url": t.get("url") or target_url,
                "title": t.get("title") or "Indeed",
            }

    return {
        "ok": True,
        "target_id": target_id,
        "url": target_url,
        "title": "Indeed",
    }



EMPLOYER_JOB_DISCOVERY_JS = r"""
(async () => {
  const MAX_JOBS = __MAX_JOBS__;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();

  const started = Date.now();
  while (Date.now() - started < 7000) {
    const body = document.body?.innerText || '';
    if (/\bjobs\b/i.test(body) && document.querySelector('a[href]')) break;
    await sleep(250);
  }

  const candidates = [];
  for (const a of [...document.querySelectorAll('a[href]')]) {
    const href = a.href || '';
    const text = normalize(a.innerText || a.textContent || '');
    if (!href || !text || text.length < 3 || text.length > 180) continue;
    if (!/indeed\./i.test(href)) continue;

    const lowHref = href.toLowerCase();
    const context = normalize(a.closest('tr,article,li,[role="row"],div')?.innerText || '');
    const looksLikeJobLink =
      /(viewjob|jobdetail|jobkey|\/job\/|\/jobs\/|jobid=|jobkey=)/i.test(lowHref)
      || /\b(candidates|job status|date posted|sponsorship|premium|paused|open)\b/i.test(context);

    if (!looksLikeJobLink) continue;
    if (/^(all|new|matches|jobs|tags|post a job|candidates)$/i.test(text)) continue;

    let jobStatus = '';
    const statusMatch = context.match(/\b(Open|Paused|Closed|Expired|Filled)\b/i);
    if (statusMatch) jobStatus = normalize(statusMatch[1]);

    candidates.push({href, title: text, job_status: jobStatus});
  }

  const unique = [];
  const seen = new Set();
  for (const item of candidates) {
    const key = item.href.split('#')[0];
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(item);
    if (unique.length >= MAX_JOBS) break;
  }

  const htmlToText = raw => {
    try {
      const doc = new DOMParser().parseFromString(raw || '', 'text/html');
      return normalize(doc.body?.innerText || doc.body?.textContent || '');
    } catch (_) {
      return normalize(raw || '');
    }
  };

  const cleanDescription = value =>
    (value || '')
      .replace(/\r/g, '\n')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();

  const parseJobPage = (raw, url, fallbackTitle, fallbackStatus) => {
    const doc = new DOMParser().parseFromString(raw || '', 'text/html');
    let title = fallbackTitle || '';
    let description = '';

    try {
      for (const script of [...doc.querySelectorAll('script[type="application/ld+json"]')]) {
        try {
          const value = JSON.parse(script.textContent || 'null');
          const queue = Array.isArray(value) ? [...value] : [value];
          while (queue.length) {
            const item = queue.shift();
            if (!item || typeof item !== 'object') continue;
            if (Array.isArray(item['@graph'])) queue.push(...item['@graph']);
            const types = Array.isArray(item['@type']) ? item['@type'] : [item['@type']];
            if (types.some(x => String(x || '').toLowerCase() === 'jobposting')) {
              title = normalize(item.title || item.name || title);
              description = cleanDescription(htmlToText(String(item.description || '')));
              if (description.length >= 100) {
                return {job_title: title, job_description: description, job_url: url, job_status: fallbackStatus || '', source: 'indeed_jobs_jsonld'};
              }
            }
          }
        } catch (_) {}
      }
    } catch (_) {}

    const selectors = [
      '#jobDescriptionText',
      '[data-testid*="job-description" i]',
      '[data-testid*="jobDescription" i]',
      '[class*="jobDescription" i]',
      '[class*="job-description" i]',
      '[id*="jobDescription" i]',
      '[aria-label*="job description" i]'
    ];

    for (const selector of selectors) {
      try {
        for (const el of [...doc.querySelectorAll(selector)]) {
          const text = cleanDescription(el.innerText || el.textContent || '');
          if (text.length > description.length && text.length <= 40000) {
            description = text;
          }
        }
      } catch (_) {}
    }

    if (!title) {
      const node = doc.querySelector('[data-testid*="job-title" i],h1,[class*="jobTitle" i]');
      title = normalize(node?.innerText || node?.textContent || fallbackTitle || '');
    }

    return {
      job_title: title,
      job_description: description,
      job_url: url,
      job_status: fallbackStatus || '',
      source: 'indeed_jobs_page'
    };
  };

  const results = await Promise.all(
    unique.map(async item => {
      try {
        const r = await fetch(item.href, {
          credentials: 'include',
          redirect: 'follow',
          cache: 'no-store'
        });
        if (!r.ok) return null;
        const raw = await r.text();
        const parsed = parseJobPage(raw, r.url || item.href, item.title, item.job_status);
        if (!parsed.job_title || !parsed.job_description || parsed.job_description.length < 100) {
          return null;
        }
        return parsed;
      } catch (_) {
        return null;
      }
    })
  );

  return results.filter(Boolean);
})()
"""


def discover_employer_job_descriptions(max_jobs=12):
    """
    Fully automatic role-description discovery from the user's already-signed-in
    Indeed Employer session. Uses a temporary Indeed Jobs tab and closes it.
    Candidate monitoring remains the priority; callers should hold/try the scan
    lock before invoking this discovery pass.
    """
    c = get_shared_chrome()
    target_id = None
    sid = None

    try:
        result = c.command(
            "Target.createTarget",
            {"url": "https://employers.indeed.com/jobs"},
            timeout=20,
        )
        target_id = result.get("targetId")
        if not target_id:
            return []

        sid = c.attach(target_id)
        c.navigate(
            sid,
            "https://employers.indeed.com/jobs",
            timeout=45,
        )

        expression = EMPLOYER_JOB_DISCOVERY_JS.replace(
            "__MAX_JOBS__",
            str(max(1, min(int(max_jobs), 25))),
        )
        rows = c.evaluate(
            sid,
            expression,
            await_promise=True,
            timeout=35,
        ) or []

        return [x for x in rows if isinstance(x, dict)]

    finally:
        if sid:
            c.detach(sid)
        if target_id:
            try:
                c.command(
                    "Target.closeTarget",
                    {"targetId": target_id},
                    timeout=10,
                )
            except Exception:
                pass



def normalize_whatsapp_phone(phone, default_country_code="91"):
    digits = re.sub(r"\D+", "", str(phone or ""))
    if not digits:
        return ""
    digits = digits.lstrip("0")
    cc = re.sub(r"\D+", "", str(default_country_code or "91"))
    if len(digits) == 10 and cc:
        digits = cc + digits
    return digits[:18]


def open_whatsapp_login_tab():
    """Open WhatsApp Web in the existing approved Chrome profile."""
    c = get_shared_chrome()
    result = c.command(
        "Target.createTarget",
        {"url": "https://web.whatsapp.com/"},
        timeout=20,
    )
    target_id = result.get("targetId")
    if not target_id:
        raise ChromeConnectionError("Chrome did not create the WhatsApp Web tab.")
    set_state("whatsapp_web_status", "LOGIN_TAB_OPEN")
    return {"ok": True, "target_id": target_id, "message": "WhatsApp Web opened. Scan QR once if requested."}


WHATSAPP_STATUS_JS = r"""
(() => {
  const body = (document.body?.innerText || '').toLowerCase();
  const loginRequired = (
    /scan\s+(the\s+)?qr|link\s+with\s+phone|use\s+whatsapp\s+on\s+your\s+phone|log\s+in\s+to\s+whatsapp/.test(body)
  );
  if (loginRequired) return {status:'LOGIN_REQUIRED'};

  const ready = Boolean(
    document.querySelector('#pane-side') ||
    document.querySelector('[data-testid="chat-list"]') ||
    document.querySelector('[aria-label*="chat list" i]') ||
    document.querySelector('[data-testid="chat-list-search"]')
  );
  if (ready) return {status:'CONNECTED'};
  return {status:'LOADING'};
})()
"""


def whatsapp_web_session_status():
    """Read WhatsApp Web state from the same approved Chrome profile."""
    c = get_shared_chrome()
    targets = [
        t for t in c.targets()
        if "web.whatsapp.com" in str(t.get("url") or "").lower()
    ]
    if not targets:
        set_state("whatsapp_web_status", "NOT_OPEN")
        return {"ok": False, "status": "NOT_OPEN", "target_id": None}

    target = targets[-1]
    sid = None
    try:
        sid = c.attach(target["targetId"])
        result = c.evaluate(
            sid,
            WHATSAPP_STATUS_JS,
            await_promise=False,
            timeout=12,
        ) or {}
        status = str(result.get("status") or "LOADING").upper()
        set_state("whatsapp_web_status", status)
        return {
            "ok": status == "CONNECTED",
            "status": status,
            "target_id": target.get("targetId"),
        }
    finally:
        if sid:
            c.detach(sid)


def ensure_whatsapp_web_ready():
    """Open WhatsApp Web automatically once and report whether login is ready."""
    try:
        status = whatsapp_web_session_status()
    except Exception:
        status = {"status": "NOT_OPEN", "target_id": None}

    if status.get("status") == "NOT_OPEN":
        opened = open_whatsapp_login_tab()
        return {
            "ok": False,
            "status": "LOGIN_TAB_OPEN",
            "target_id": opened.get("target_id"),
            "message": opened.get("message"),
        }

    return status


WHATSAPP_SEND_JS = r"""
(async () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const started = Date.now();

  while (Date.now() - started < 30000) {
    const body = (document.body?.innerText || '').toLowerCase();

    if (
      /scan\s+(the\s+)?qr|link\s+with\s+phone|use\s+whatsapp\s+on\s+your\s+phone|log\s+in\s+to\s+whatsapp/.test(body)
    ) {
      return {ok:false, code:'LOGIN_REQUIRED', message:'WhatsApp Web login is required. The console opens WhatsApp automatically; scan the QR once in the existing Chrome profile.'};
    }

    if (/phone number shared via url is invalid|invalid phone number|couldn't find this phone number/.test(body)) {
      return {ok:false, code:'INVALID_PHONE', message:'WhatsApp rejected the candidate phone number.'};
    }

    let button = document.querySelector(
      'button[data-testid="compose-btn-send"],button[aria-label="Send"],button[aria-label*="send" i]'
    );

    if (!button) {
      const icon = document.querySelector('span[data-icon="send"],span[data-testid="send"]');
      button = icon?.closest('button,[role="button"]') || null;
    }

    if (button && !button.disabled) {
      button.click();
      await sleep(1000);
      return {ok:true, code:'SENT'};
    }

    await sleep(350);
  }

  return {ok:false, code:'SEND_BUTTON_TIMEOUT', message:'WhatsApp Web did not become ready in time.'};
})()
"""


def send_whatsapp_web_message(phone, message, default_country_code="91"):
    """Send one message through an already logged-in WhatsApp Web session."""
    digits = normalize_whatsapp_phone(phone, default_country_code)
    if not digits:
        return {"ok": False, "code": "NO_PHONE", "message": "Candidate phone number is missing."}

    c = get_shared_chrome()
    target_id = None
    sid = None
    keep_open = False

    try:
        url = (
            "https://web.whatsapp.com/send?phone="
            + digits
            + "&text="
            + quote(str(message or ""), safe="")
            + "&type=phone_number&app_absent=0"
        )
        result = c.command("Target.createTarget", {"url": url}, timeout=20)
        target_id = result.get("targetId")
        if not target_id:
            return {"ok": False, "code": "TARGET_FAILED", "message": "Chrome did not open WhatsApp Web."}

        sid = c.attach(target_id)
        result = c.evaluate(
            sid,
            WHATSAPP_SEND_JS,
            await_promise=True,
            timeout=40,
        ) or {}

        if result.get("code") == "LOGIN_REQUIRED":
            keep_open = True
            set_state("whatsapp_web_status", "LOGIN_REQUIRED")
        elif result.get("ok"):
            set_state("whatsapp_web_status", "CONNECTED")
        return result
    finally:
        if sid:
            c.detach(sid)
        if target_id and not keep_open:
            try:
                c.command("Target.closeTarget", {"targetId": target_id}, timeout=10)
            except Exception:
                pass


def chrome_status(connect_if_needed=False):
    """
    Status calls from the dashboard are read-only and never create a Chrome
    connection. The background connection manager owns automatic reconnect.
    """
    snapshot = shared_connection_snapshot()

    if not snapshot["connected"] and not connect_if_needed:
        return {
            "ok": False,
            "chrome_connected": False,
            "indeed_found": False,
            "error": (
                "Automatic Chrome connection is starting."
            ),
            "message": "CHROME NOT CONNECTED",
            "permission_prompt_expected": False,
        }

    try:
        c = (
            connect_shared_chrome(timeout=60)
            if connect_if_needed
            else get_shared_chrome()
        )

        saved = load_settings().get("indeed_candidates_url", "")
        target, targets = find_indeed_target(c, saved)

        if target:
            return {
                "ok": True,
                "chrome_connected": True,
                "indeed_found": True,
                "url": target.get("url") or "",
                "title": target.get("title") or "",
                "page_count": len(targets),
                "target_id": target.get("targetId"),
                "devtools_port": c.endpoint["port"],
                "message": "CHROME CONNECTED • INDEED FOUND",
                "persistent_session": True,
            }

        return {
            "ok": True,
            "chrome_connected": True,
            "indeed_found": False,
            "url": "",
            "title": "",
            "page_count": len(targets),
            "target_id": None,
            "devtools_port": c.endpoint["port"],
            "message": "CHROME CONNECTED • OPEN INDEED",
            "warning": "Chrome is connected, but no Indeed tab is open yet.",
            "persistent_session": True,
        }

    except Exception as e:
        return {
            "ok": False,
            "chrome_connected": False,
            "indeed_found": False,
            "error": str(e),
            "message": "CHROME NOT CONNECTED",
        }


def current_indeed_page():
    c = get_shared_chrome()
    target, _ = choose_indeed_target(
        c,
        load_settings().get("indeed_candidates_url", ""),
    )
    return {
        "url": target.get("url") or "",
        "title": target.get("title") or "",
        "target_id": target.get("targetId"),
    }


def stable_candidate_key(href):
    from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

    url = urldefrag(href or "")[0]
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    id_keys = [
        "candidateId",
        "candidateid",
        "applicantId",
        "applicantid",
        "applicationId",
        "applicationid",
        "id",
    ]

    for key in id_keys:
        value = query.get(key)
        if value and len(value) >= 5:
            return hashlib.sha1(
                f"{key.lower()}:{value}".encode("utf-8", "ignore")
            ).hexdigest()

    m = re.search(
        r"/(?:candidate|applicant|application)/([^/?#]{5,})",
        parsed.path,
        flags=re.I,
    )
    if m:
        return hashlib.sha1(
            f"path:{m.group(1)}".encode("utf-8", "ignore")
        ).hexdigest()

    volatile = {
        "status", "stage", "sort", "page", "from", "source", "view",
        "tab", "filter", "jobId", "jobid",
    }
    stable_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in volatile
    ]
    canonical = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            urlencode(stable_query),
            "",
        )
    )

    return hashlib.sha1(
        canonical.encode("utf-8", "ignore")
    ).hexdigest()



def stable_row_candidate_key(
    candidate_name,
    job_title,
    location_text="",
    identity_hint="",
):
    identity = re.sub(
        r"\s+",
        " ",
        (identity_hint or "").strip().lower(),
    )

    if identity:
        return hashlib.sha1(
            ("row-id:" + identity).encode("utf-8", "ignore")
        ).hexdigest()

    canonical = "|".join([
        re.sub(r"\s+", " ", (candidate_name or "").strip().lower()),
        re.sub(r"\s+", " ", (location_text or "").strip().lower()),
    ])

    return hashlib.sha1(
        ("row:" + canonical).encode("utf-8", "ignore")
    ).hexdigest()


def build_row_click_js(entry):
    """
    Build JS that finds the smallest live Indeed candidate block matching
    candidate name + Applied to job (+ location when available), then clicks
    the candidate-name control/row with a user gesture.
    """
    name = json.dumps(entry.get("label") or "")
    job = json.dumps(entry.get("job_title") or "")
    location = json.dumps(entry.get("location_text") or "")

    return f"""
(async () => {{
  const targetName = {name};
  const targetJob = {job};
  const targetLocation = {location};

  const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const eq = (a, b) => normalize(a).toLowerCase() === normalize(b).toLowerCase();

  const candidateBlocks = [];

  for (const el of document.querySelectorAll(
    '[data-testid*="candidate" i],'
    + '[data-testid*="applicant" i],'
    + '[data-testid*="application" i],'
    + '[role="row"],tr,li,article,div'
  )) {{
    const raw = el.innerText || '';
    if (!raw || raw.length > 2600) continue;

    const hasName =
      raw.toLowerCase().includes(targetName.toLowerCase());

    const hasJob =
      !targetJob
      || raw.toLowerCase().includes(
        ('applied to: ' + targetJob).toLowerCase()
      )
      || raw.toLowerCase().includes(targetJob.toLowerCase());

    const hasLocation =
      !targetLocation
      || raw.toLowerCase().includes(targetLocation.toLowerCase());

    if (hasName && hasJob && hasLocation) {{
      candidateBlocks.push(el);
    }}
  }}

  candidateBlocks.sort(
    (a, b) => (a.innerText || '').length - (b.innerText || '').length
  );

  const row = candidateBlocks[0];

  if (!row) {{
    return {{
      clicked: false,
      reason: 'matching candidate row was not found',
      beforeUrl: location.href,
    }};
  }}

  row.scrollIntoView({{
    block: 'center',
    inline: 'nearest',
    behavior: 'instant',
  }});

  await new Promise(resolve => setTimeout(resolve, 180));

  let target = null;

  // Prefer a candidate-name anchor/button/control.
  for (const el of row.querySelectorAll(
    'a,button,[role="button"],'
    + '[data-testid*="name" i],'
    + '[data-testid*="candidate" i]'
  )) {{
    const t = normalize(el.innerText || el.textContent || '');
    if (eq(t, targetName)) {{
      target = el;
      break;
    }}
  }}

  if (!target) {{
    // Find the smallest element whose text is exactly the candidate name.
    const descendants = [...row.querySelectorAll('*')]
      .filter(el => eq(el.innerText || el.textContent || '', targetName))
      .sort(
        (a, b) =>
          (a.innerText || '').length - (b.innerText || '').length
      );

    target = descendants[0] || row;
  }}

  const fire = (type) => {{
    try {{
      target.dispatchEvent(
        new MouseEvent(type, {{
          bubbles: true,
          cancelable: true,
          view: window,
          button: 0,
        }})
      );
    }} catch (_) {{}}
  }};

  fire('pointerdown');
  fire('mousedown');
  fire('pointerup');
  fire('mouseup');
  fire('click');

  try {{
    target.click();
  }} catch (_) {{}}

  await new Promise(resolve => setTimeout(resolve, 1300));

  return {{
    clicked: true,
    beforeUrl: location.href,
    candidateName: targetName,
    jobTitle: targetJob,
    rowText: (row.innerText || '').slice(0, 1800),
  }};
}})()
"""


GENERIC_CANDIDATE_LABELS = {
    "candidate",
    "candidates",
    "view candidate",
    "view candidates",
    "applicant",
    "applicants",
    "view applicant",
    "view applicants",
    "application",
    "applications",
    "view applications",
    "manage candidates",
}


def candidate_link_score(href, text, context_text="", data_test_id=""):
    h = (href or "").lower().strip()
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    ctx = re.sub(r"\s+", " ", (context_text or "").lower()).strip()
    dt = (data_test_id or "").lower()

    if "indeed." not in h:
        return -100

    if any(
        x in h
        for x in [
            "/help/",
            "/hire/resources",
            "/company/",
            "/employers/cs/login",
        ]
    ):
        return -100

    if t in GENERIC_CANDIDATE_LABELS:
        return -100

    path_no_query = h.split("?", 1)[0].rstrip("/")
    has_unique_query_id = bool(
        re.search(
            r"(?:candidate|applicant|application)(?:id|uid|key)=",
            h,
        )
    )

    if (
        re.search(
            r"/(?:candidate|candidates|applicant|applicants|applications)$",
            path_no_query,
        )
        and not has_unique_query_id
    ):
        return -100

    score = 0

    if has_unique_query_id:
        score += 24

    if re.search(
        r"/(?:candidate|applicant|application)/[^/?#]{5,}",
        h,
    ):
        score += 20

    if any(k in dt for k in ["candidate", "applicant", "application"]):
        score += 8

    if "candidate" in h:
        score += 5
    if "applicant" in h:
        score += 5
    if "application" in h:
        score += 3

    if (
        2 <= len(t) <= 100
        and t not in GENERIC_CANDIDATE_LABELS
        and not t.startswith("view ")
    ):
        score += 3

    if any(
        x in ctx
        for x in [
            "awaiting review",
            "new",
            "applied",
            "application",
            "resume",
            "screening",
            "contacting",
            "reviewed",
        ]
    ):
        score += 4

    return score


INDEED_PIPELINE_STATUS_PATTERNS = [
    ("Not Selected", re.compile(r"(?i)(?:^|[\n•·|—-])\s*not\s+selected\s*(?:$|[\n•·|—-])")),
    ("Selected", re.compile(r"(?i)(?:^|[\n•·|—-])\s*selected\s*(?:$|[\n•·|—-])")),
    ("Hired", re.compile(r"(?i)(?:^|[\n•·|—-])\s*hired\s*(?:$|[\n•·|—-])")),
    ("Rejected", re.compile(r"(?i)(?:^|[\n•·|—-])\s*rejected\s*(?:$|[\n•·|—-])")),
    ("Withdrawn", re.compile(r"(?i)(?:^|[\n•·|—-])\s*withdrawn\s*(?:$|[\n•·|—-])")),
    ("Archived", re.compile(r"(?i)(?:^|[\n•·|—-])\s*archived\s*(?:$|[\n•·|—-])")),
    ("Interviewing", re.compile(r"(?i)(?:^|[\n•·|—-])\s*interviewing\s*(?:$|[\n•·|—-])")),
    ("Contacting", re.compile(r"(?i)(?:^|[\n•·|—-])\s*contacting\s*(?:$|[\n•·|—-])")),
    ("Reviewing", re.compile(r"(?i)(?:^|[\n•·|—-])\s*reviewing\s*(?:$|[\n•·|—-])")),
    ("New", re.compile(r"(?i)(?:^|[\n•·|—-])\s*new\s*(?:$|[\n•·|—-])")),
]


def candidate_pipeline_status(text, explicit=None):
    explicit_value = re.sub(r"\s+", " ", str(explicit or "")).strip()
    canonical = {
        "new": "New",
        "reviewing": "Reviewing",
        "contacting": "Contacting",
        "interviewing": "Interviewing",
        "hired": "Hired",
        "selected": "Selected",
        "not selected": "Not Selected",
        "rejected": "Rejected",
        "withdrawn": "Withdrawn",
        "archived": "Archived",
    }
    if explicit_value.lower() in canonical:
        return canonical[explicit_value.lower()]
    raw = str(text or "")
    for label, pattern in INDEED_PIPELINE_STATUS_PATTERNS:
        if pattern.search(raw):
            return label
    return explicit_value[:40]


def is_current_new_candidate(text):
    """
    Indeed candidate row status used for the one-time catch-up.

    Examples from the live queue:
      New • Applied Today
      New · Applied Today
      New
    """
    s = re.sub(r"\s+", " ", (text or "").strip().lower())

    if not s:
        return False

    # Require "new" as its own word/status, not a substring.
    has_new = bool(re.search(r"(^|[\s•·|—-])new([\s•·|—-]|$)", s))
    if not has_new:
        return False

    # Strong status/application context avoids navigation labels.
    has_application_context = any(
        token in s
        for token in [
            "applied",
            "application",
            "awaiting review",
            "resume",
            "matches to job post",
        ]
    )

    return has_new and has_application_context


def is_new_status_hint(text):
    # Kept as compatibility alias for older internal calls.
    return is_current_new_candidate(text)


COLLECT_LINKS_JS = r"""
(async () => {
  const originalY = window.scrollY;
  const records = new Map();

  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const normalize = (s) => (s || '').replace(/\s+/g, ' ').trim();

  const pipelineStatusFromText = (raw) => {
    const text = String(raw || '');
    const patterns = [
      ['Not Selected', /(?:^|[\n•·|—-])\s*not\s+selected\s*(?:$|[\n•·|—-])/i],
      ['Selected', /(?:^|[\n•·|—-])\s*selected\s*(?:$|[\n•·|—-])/i],
      ['Hired', /(?:^|[\n•·|—-])\s*hired\s*(?:$|[\n•·|—-])/i],
      ['Rejected', /(?:^|[\n•·|—-])\s*rejected\s*(?:$|[\n•·|—-])/i],
      ['Withdrawn', /(?:^|[\n•·|—-])\s*withdrawn\s*(?:$|[\n•·|—-])/i],
      ['Archived', /(?:^|[\n•·|—-])\s*archived\s*(?:$|[\n•·|—-])/i],
      ['Interviewing', /(?:^|[\n•·|—-])\s*interviewing\s*(?:$|[\n•·|—-])/i],
      ['Contacting', /(?:^|[\n•·|—-])\s*contacting\s*(?:$|[\n•·|—-])/i],
      ['Reviewing', /(?:^|[\n•·|—-])\s*reviewing\s*(?:$|[\n•·|—-])/i],
      ['New', /(?:^|[\n•·|—-])\s*new\s*(?:$|[\n•·|—-])/i],
    ];
    for (const [label, pattern] of patterns) {
      if (pattern.test(text)) return label;
    }
    return '';
  };

  const parseCandidateBlock = (row) => {
    if (!row) return null;

    const raw = (row.innerText || '').trim();
    if (!raw || raw.length < 10 || raw.length > 3000) return null;

    const lines = raw
      .split(/\n+/)
      .map(normalize)
      .filter(Boolean);

    let jobTitle = '';

    // Same-line form: "Applied to: Purchase Executive"
    const sameLineJob = raw.match(
      /(?:^|\n)\s*Applied\s+to\s*:?\s*([^\n]{2,180})/i
    );

    if (sameLineJob) {
      const possible = normalize(sameLineJob[1]);
      if (
        possible
        && !/^new\b/i.test(possible)
        && !/^applied\b/i.test(possible)
      ) {
        jobTitle = possible;
      }
    }

    // Split-line DOM form:
    // Applied to:
    // Purchase Executive
    if (!jobTitle) {
      for (let i=0; i<lines.length; i++) {
        const m = lines[i].match(/^Applied\s+to\s*:?\s*(.*)$/i);
        if (!m) continue;

        const inline = normalize(m[1] || '');
        if (inline && inline.length >= 2) {
          jobTitle = inline;
          break;
        }

        if (lines[i+1]) {
          jobTitle = normalize(lines[i+1]);
          break;
        }
      }
    }

    // DOM anchor fallback. Indeed often renders the role as a separate link.
    if (!jobTitle) {
      for (const a of [...row.querySelectorAll('a[href]')]) {
        const href = (a.href || '').toLowerCase();
        const text = normalize(a.innerText || a.textContent || '');

        if (
          text
          && text.length >= 2
          && text.length <= 180
          && /(viewjob|jobdetail|jobkey|\/job\/|\/jobs\/)/i.test(href)
        ) {
          jobTitle = text;
          break;
        }
      }
    }

    // Keep the candidate even if role is still unavailable. The detail page
    // gets one more chance to recover the exact role; sending remains blocked
    // until a real role is found.
    jobTitle = normalize(jobTitle);

    const rejectName = (line) => {
      const l = (line || '').toLowerCase();
      return (
        !line ||
        line.length > 120 ||
        l === 'candidates' ||
        l === 'candidate' ||
        l === 'activity' ||
        l === 'interest' ||
        l === 'matches to job post' ||
        l === 'all open and paused jobs' ||
        l === 'all jobs' ||
        l === 'education' ||
        l === 'yes' ||
        l === 'no' ||
        l === 'manage candidates' ||
        l === 'find candidates' ||
        l === 'download cv' ||
        l === 'download resume' ||
        l === 'core skills' ||
        l === 'resume' ||
        l === 'contact information' ||
        l.startsWith('applied to:') ||
        /^new(?:\s|$)/i.test(line) ||
        /^reviewing(?:\s|$)/i.test(line) ||
        /^contacting(?:\s|$)/i.test(line) ||
        /^interviewing(?:\s|$)/i.test(line) ||
        /^rejected(?:\s|$)/i.test(line) ||
        /^hired(?:\s|$)/i.test(line)
      );
    };

    let candidateName = '';

    const explicitNames = [
      ...row.querySelectorAll(
        '[data-testid*="candidate-name" i],'
        + '[data-testid*="applicant-name" i],'
        + '[class*="candidateName" i],'
        + '[class*="candidate-name" i],'
        + 'strong'
      )
    ];

    for (const el of explicitNames) {
      const t = normalize(el.innerText || el.textContent || '');
      if (t && !rejectName(t)) {
        candidateName = t;
        break;
      }
    }

    if (!candidateName) {
      for (const line of lines.slice(0, 10)) {
        if (!rejectName(line)) {
          candidateName = line;
          break;
        }
      }
    }

    if (!candidateName) return null;

    let locationText = '';
    for (const line of lines.slice(1, 8)) {
      const l = line.toLowerCase();
      if (
        line === candidateName ||
        l.startsWith('applied to:') ||
        /^new(?:\s|$)/.test(l) ||
        /^reviewing(?:\s|$)/.test(l) ||
        /^contacting(?:\s|$)/.test(l) ||
        /^interviewing(?:\s|$)/.test(l) ||
        /^rejected(?:\s|$)/.test(l) ||
        /^hired(?:\s|$)/.test(l)
      ) {
        continue;
      }
      if (line.length <= 100) {
        locationText = line;
        break;
      }
    }

    const currentNew =
      /(?:^|[\s•·|—-])new(?:[\s•·|—-]|$)/i.test(raw)
      && /\bapplied\b/i.test(raw);

    // Candidate-name anchor may exist even when the URL itself does not
    // contain "candidate" or "application".
    let candidateHref = '';

    for (const a of [...row.querySelectorAll('a[href]')]) {
      const href = a.href || '';
      const text = normalize(a.innerText || a.textContent || '');
      if (!href || !/indeed\./i.test(href) || /\/login/i.test(href)) {
        continue;
      }

      const lowHref = href.toLowerCase();
      const nameMatches =
        text.toLowerCase() === candidateName.toLowerCase();

      const explicitCandidateUrl =
        /(candidate|applicant|application)/i.test(lowHref)
        && !/(viewjob|jobdetail|jobkey|\/jobs?\/)/i.test(lowHref);

      if (nameMatches || explicitCandidateUrl) {
        candidateHref = href;
        break;
      }
    }

    // Pull stable React / Indeed identifiers from attributes when possible.
    const identityBits = [];
    const nodes = [row, ...row.querySelectorAll('*')].slice(0, 300);

    for (const el of nodes) {
      for (const attr of [...(el.attributes || [])]) {
        const n = (attr.name || '').toLowerCase();
        const v = (attr.value || '').trim();

        if (!v || v.length > 220) continue;

        if (
          /(candidate|applicant|application).*(id|uid|key)/i.test(n)
          || /^(data-(id|key|uid)|candidateid|applicantid|applicationid)$/i.test(n)
        ) {
          identityBits.push(`${n}=${v}`);
        }

        if (
          /(candidate|applicant|application)/i.test(v)
          && /[a-z0-9_-]{6,}/i.test(v)
          && identityBits.length < 16
        ) {
          identityBits.push(`${n}=${v}`);
        }
      }

      if (identityBits.length >= 16) break;
    }

    return {
      recordType: 'row',
      href: candidateHref,
      text: candidateName,
      candidateName,
      jobTitle,
      locationText,
      contextText: raw.slice(0, 2000),
      dataTestId: (
        row.getAttribute?.('data-testid')
        || ''
      ).slice(0, 240),
      identityHint: identityBits.join('|').slice(0, 2000),
      currentNew,
      pipelineStatus: pipelineStatusFromText(raw),
    };
  };

  const smallestCandidateRows = () => {
    const result = new Set();

    // First, semantic candidates.
    for (const el of document.querySelectorAll(
      '[data-testid*="candidate" i],'
      + '[data-testid*="applicant" i],'
      + '[data-testid*="application" i],'
      + '[role="row"],tr,li,article'
    )) {
      const t = el.innerText || '';
      if (
        /\bApplied\s+to\s*:?\s*/i.test(t)
        && t.length >= 10
        && t.length <= 3000
      ) {
        result.add(el);
      }
    }

    // Indeed Manage Candidates can also use plain nested DIVs.
    for (const el of [...document.querySelectorAll('div')]) {
      const t = el.innerText || '';
      if (
        !/\bApplied\s+to\s*:?\s*/i.test(t)
        || t.length < 10
        || t.length > 2300
      ) {
        continue;
      }

      let smallerChild = false;
      for (const child of [...el.children]) {
        const ct = child.innerText || '';
        if (
          /\bApplied\s+to\s*:?\s*/i.test(ct)
          && ct.length >= 10
          && ct.length < t.length
          && ct.length <= 2000
        ) {
          smallerChild = true;
          break;
        }
      }

      if (!smallerChild) {
        result.add(el);
      }
    }

    return [...result];
  };

  const collect = () => {
    for (const row of smallestCandidateRows()) {
      const rec = parseCandidateBlock(row);
      if (!rec) continue;

      const key = [
        rec.identityHint || '',
        rec.candidateName.toLowerCase(),
        rec.jobTitle.toLowerCase(),
        rec.locationText.toLowerCase(),
      ].join('|||');

      const old = records.get(key);
      if (
        !old
        || (rec.contextText || '').length > (old.contextText || '').length
      ) {
        records.set(key, rec);
      }
    }

    // Compatibility fallback for pages that still expose proper candidate URLs.
    for (const a of [...document.querySelectorAll('a[href]')]) {
      const href = a.href || '';
      const low = href.toLowerCase();
      if (
        !/indeed\./i.test(href)
        || /\/login/i.test(href)
        || !/(candidate|applicant|application)/i.test(low)
      ) {
        continue;
      }

      const text = normalize(a.innerText || a.textContent || '');
      const container =
        a.closest(
          '[data-testid*="candidate" i],'
          + '[data-testid*="applicant" i],'
          + '[role="row"],tr,li,article'
        )
        || a.parentElement;

      const contextText = (container?.innerText || '').trim().slice(0, 2000);

      const key = `href|||${href}`;
      if (!records.has(key)) {
        records.set(key, {
          recordType: 'link',
          href,
          text,
          candidateName: text,
          jobTitle: '',
          locationText: '',
          contextText,
          dataTestId: (
            container?.getAttribute?.('data-testid')
            || a.getAttribute('data-testid')
            || ''
          ).slice(0, 240),
          identityHint: '',
          currentNew:
            /(?:^|[\s•·|—-])new(?:[\s•·|—-]|$)/i.test(contextText)
            && /\bapplied\b/i.test(contextText),
          pipelineStatus: pipelineStatusFromText(contextText),
        });
      }
    }
  };

  collect();

  let lastCount = records.size;
  let stableRounds = 0;
  let reachedBottom = false;

  for (let i = 0; i < 100; i++) {
    const maxY = Math.max(
      0,
      document.documentElement.scrollHeight - window.innerHeight
    );

    if (window.scrollY >= maxY - 8) {
      reachedBottom = true;
      collect();
      stableRounds += 1;
      if (stableRounds >= 4) break;
    } else {
      window.scrollTo(
        0,
        Math.min(
          maxY,
          window.scrollY
            + Math.max(420, Math.floor(window.innerHeight * 0.72))
        )
      );
      await sleep(180);
      collect();

      if (records.size === lastCount) {
        stableRounds += 1;
      } else {
        stableRounds = 0;
        lastCount = records.size;
      }
    }
  }

  window.scrollTo(0, originalY);
  await sleep(140);

  return {
    url: location.href,
    title: document.title,
    bodyPreview: (
      document.body?.innerText
      || ''
    ).slice(0, 12000),
    links: [...records.values()],
    collectionComplete: reachedBottom,
    collectedLinkCount: records.size,
  };
})()
"""


COLLECT_VISIBLE_LINKS_JS = r"""
(async () => {
  window.scrollTo(0, 0);
  const records = new Map();
  const normalize = (s) => (s || '').replace(/\s+/g, ' ').trim();

  const pipelineStatusFromText = (raw) => {
    const text = String(raw || '');
    const patterns = [
      ['Not Selected', /(?:^|[\n•·|—-])\s*not\s+selected\s*(?:$|[\n•·|—-])/i],
      ['Selected', /(?:^|[\n•·|—-])\s*selected\s*(?:$|[\n•·|—-])/i],
      ['Hired', /(?:^|[\n•·|—-])\s*hired\s*(?:$|[\n•·|—-])/i],
      ['Rejected', /(?:^|[\n•·|—-])\s*rejected\s*(?:$|[\n•·|—-])/i],
      ['Withdrawn', /(?:^|[\n•·|—-])\s*withdrawn\s*(?:$|[\n•·|—-])/i],
      ['Archived', /(?:^|[\n•·|—-])\s*archived\s*(?:$|[\n•·|—-])/i],
      ['Interviewing', /(?:^|[\n•·|—-])\s*interviewing\s*(?:$|[\n•·|—-])/i],
      ['Contacting', /(?:^|[\n•·|—-])\s*contacting\s*(?:$|[\n•·|—-])/i],
      ['Reviewing', /(?:^|[\n•·|—-])\s*reviewing\s*(?:$|[\n•·|—-])/i],
      ['New', /(?:^|[\n•·|—-])\s*new\s*(?:$|[\n•·|—-])/i],
    ];
    for (const [label, pattern] of patterns) {
      if (pattern.test(text)) return label;
    }
    return '';
  };

  const parseCandidateBlock = (row) => {
    if (!row) return null;

    const raw = (row.innerText || '').trim();
    if (!raw || raw.length < 10 || raw.length > 3000) return null;

    const lines = raw
      .split(/\n+/)
      .map(normalize)
      .filter(Boolean);

    let jobTitle = '';

    const sameLineJob = raw.match(
      /(?:^|\n)\s*Applied\s+to\s*:?\s*([^\n]{2,180})/i
    );

    if (sameLineJob) {
      const possible = normalize(sameLineJob[1]);
      if (possible && !/^new\b/i.test(possible) && !/^applied\b/i.test(possible)) {
        jobTitle = possible;
      }
    }

    if (!jobTitle) {
      for (let i=0; i<lines.length; i++) {
        const m = lines[i].match(/^Applied\s+to\s*:?\s*(.*)$/i);
        if (!m) continue;
        const inline = normalize(m[1] || '');
        if (inline && inline.length >= 2) {
          jobTitle = inline;
          break;
        }
        if (lines[i+1]) {
          jobTitle = normalize(lines[i+1]);
          break;
        }
      }
    }

    if (!jobTitle) {
      for (const a of [...row.querySelectorAll('a[href]')]) {
        const href = (a.href || '').toLowerCase();
        const text = normalize(a.innerText || a.textContent || '');
        if (
          text
          && text.length >= 2
          && text.length <= 180
          && /(viewjob|jobdetail|jobkey|\/job\/|\/jobs\/)/i.test(href)
        ) {
          jobTitle = text;
          break;
        }
      }
    }

    const rejectName = (line) => {
      const l = (line || '').toLowerCase();
      return (
        !line ||
        line.length > 120 ||
        [
          'candidates','candidate','activity','interest',
          'matches to job post','all open and paused jobs','all jobs',
          'education','yes','no','manage candidates','find candidates',
          'download cv','download resume','core skills','resume',
          'contact information'
        ].includes(l) ||
        l.startsWith('applied to:') ||
        /^new(?:\s|$)/i.test(line) ||
        /^reviewing(?:\s|$)/i.test(line) ||
        /^contacting(?:\s|$)/i.test(line) ||
        /^interviewing(?:\s|$)/i.test(line) ||
        /^rejected(?:\s|$)/i.test(line) ||
        /^hired(?:\s|$)/i.test(line)
      );
    };

    let candidateName = '';

    for (const el of row.querySelectorAll(
      '[data-testid*="candidate-name" i],'
      + '[data-testid*="applicant-name" i],'
      + '[class*="candidateName" i],'
      + '[class*="candidate-name" i],strong'
    )) {
      const t = normalize(el.innerText || el.textContent || '');
      if (t && !rejectName(t)) {
        candidateName = t;
        break;
      }
    }

    if (!candidateName) {
      for (const line of lines.slice(0, 10)) {
        if (!rejectName(line)) {
          candidateName = line;
          break;
        }
      }
    }

    if (!candidateName) return null;

    let locationText = '';
    for (const line of lines.slice(1, 8)) {
      const l = line.toLowerCase();
      if (
        line === candidateName ||
        l.startsWith('applied to:') ||
        /^new(?:\s|$)/.test(l) ||
        /^reviewing(?:\s|$)/.test(l)
      ) continue;
      if (line.length <= 100) {
        locationText = line;
        break;
      }
    }

    const currentNew =
      /(?:^|[\s•·|—-])new(?:[\s•·|—-]|$)/i.test(raw)
      && /\bapplied\b/i.test(raw);

    let candidateHref = '';

    for (const a of [...row.querySelectorAll('a[href]')]) {
      const href = a.href || '';
      const text = normalize(a.innerText || a.textContent || '');
      if (!href || !/indeed\./i.test(href) || /\/login/i.test(href)) continue;

      const lowHref = href.toLowerCase();
      const nameMatches = text.toLowerCase() === candidateName.toLowerCase();
      const explicitCandidateUrl =
        /(candidate|applicant|application)/i.test(lowHref)
        && !/(viewjob|jobdetail|jobkey|\/jobs?\/)/i.test(lowHref);

      if (nameMatches || explicitCandidateUrl) {
        candidateHref = href;
        break;
      }
    }

    const identityBits = [];

    for (const el of [row, ...row.querySelectorAll('*')].slice(0, 250)) {
      for (const attr of [...(el.attributes || [])]) {
        const n = (attr.name || '').toLowerCase();
        const v = (attr.value || '').trim();

        if (!v || v.length > 220) continue;

        if (
          /(candidate|applicant|application).*(id|uid|key)/i.test(n)
          || /^(data-(id|key|uid)|candidateid|applicantid|applicationid)$/i.test(n)
        ) {
          identityBits.push(`${n}=${v}`);
        }
      }
      if (identityBits.length >= 12) break;
    }

    return {
      recordType: 'row',
      href: candidateHref,
      text: candidateName,
      candidateName,
      jobTitle: normalize(jobTitle),
      locationText,
      contextText: raw.slice(0, 2000),
      dataTestId: (row.getAttribute?.('data-testid') || '').slice(0, 240),
      identityHint: identityBits.join('|').slice(0, 1600),
      currentNew,
      pipelineStatus: pipelineStatusFromText(raw),
    };
  };

  const candidates = [];

  for (const el of document.querySelectorAll(
    '[data-testid*="candidate" i],'
    + '[data-testid*="applicant" i],'
    + '[data-testid*="application" i],'
    + '[role="row"],tr,li,article,div'
  )) {
    const raw = el.innerText || '';
    if (!/\bApplied\s+to\s*:?\s*/i.test(raw)) continue;
    if (raw.length < 10 || raw.length > 2400) continue;

    let smallerChild = false;
    for (const child of [...el.children]) {
      const childText = child.innerText || '';
      if (
        /\bApplied\s+to\s*:?\s*/i.test(childText)
        && childText.length >= 10
        && childText.length < raw.length
      ) {
        smallerChild = true;
        break;
      }
    }

    if (!smallerChild) candidates.push(el);
  }

  for (const row of candidates) {
    const rec = parseCandidateBlock(row);
    if (!rec) continue;

    const key = [
      rec.identityHint || '',
      rec.candidateName.toLowerCase(),
      rec.locationText.toLowerCase(),
    ].join('|||');

    if (!records.has(key)) records.set(key, rec);
  }

  return {
    url: location.href,
    title: document.title,
    bodyPreview: (document.body?.innerText || '').slice(0, 12000),
    links: [...records.values()],
    collectionComplete: false,
    collectedLinkCount: records.size,
    fastVisible: true,
  };
})()
"""

CANDIDATE_PAYLOAD_JS = r"""
(async () => {
  const TARGET_CANDIDATE_NAME = __TARGET_CANDIDATE_NAME__;
  const TARGET_ROW_JOB = __TARGET_ROW_JOB__;
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

  // Let the Indeed SPA finish rendering the selected candidate.
  await sleep(900);

  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return (
      r.width > 0 &&
      r.height > 0 &&
      style.display !== 'none' &&
      style.visibility !== 'hidden'
    );
  };

  // Expand the candidate sections that can contain contact/resume details.
  // We only click button-like elements so we do not navigate away from the
  // candidate page through an arbitrary anchor.
  const buttonLike = [
    ...document.querySelectorAll(
      'button,[role="button"],'
      + '[data-testid*="resume" i],'
      + '[data-testid*="contact" i],'
      + '[aria-label*="resume" i],'
      + '[aria-label*="contact" i]'
    )
  ];

  const clicked = new Set();

  const shouldExpand = (el) => {
    const text = (
      el.innerText
      || el.textContent
      || el.getAttribute?.('aria-label')
      || ''
    ).trim().toLowerCase();

    if (!text || text.length > 160) return false;

    return (
      /^resume$/.test(text) ||
      /view\s+resume/.test(text) ||
      /show\s+resume/.test(text) ||
      /candidate\s+resume/.test(text) ||
      /^cv$/.test(text) ||
      /contact\s+info/.test(text) ||
      /contact\s+information/.test(text) ||
      /contact\s+details/.test(text) ||
      /show\s+contact/.test(text) ||
      /view\s+contact/.test(text) ||
      /download\s+(?:cv|resume)/.test(text) ||
      /open\s+(?:cv|resume)/.test(text)
    );
  };

  for (const el of buttonLike) {
    if (clicked.size >= 14) break;
    if (!isVisible(el) || !shouldExpand(el)) continue;

    const key = (
      (el.innerText || el.textContent || '')
      + '|'
      + (el.getAttribute?.('aria-label') || '')
    ).trim().toLowerCase();

    if (clicked.has(key)) continue;
    clicked.add(key);

    try {
      el.click();
      await sleep(650);
    } catch (_) {}
  }

  const normalize = (s) => (s || '').replace(/\s+/g, ' ').trim();

  // Find the selected candidate detail/drawer. Email extraction must stay
  // inside this scope to avoid reading another applicant from the list.
  const rootCandidates = [
    ...document.querySelectorAll(
      '[role="dialog"],aside,'
      + '[data-testid*="candidate-detail" i],'
      + '[data-testid*="candidate-panel" i],'
      + '[data-testid*="application-detail" i],'
      + '[data-testid*="candidate-drawer" i],'
      + '[class*="candidateDetail" i],'
      + '[class*="candidate-detail" i],'
      + '[class*="drawer" i]'
    )
  ].filter(isVisible);

  const targetNameLow = normalize(TARGET_CANDIDATE_NAME).toLowerCase();

  let detailRoot = null;

  for (const root of rootCandidates) {
    const text = normalize(root.innerText || '');
    if (
      text
      && (
        !targetNameLow
        || text.toLowerCase().includes(targetNameLow)
      )
    ) {
      if (
        !detailRoot
        || text.length < normalize(detailRoot.innerText || '').length
      ) {
        detailRoot = root;
      }
    }
  }

  if (!detailRoot && targetNameLow) {
    const matching = [...document.querySelectorAll('section,article,div')]
      .filter(isVisible)
      .map(el => ({
        el,
        text: normalize(el.innerText || ''),
      }))
      .filter(x =>
        x.text
        && x.text.length >= targetNameLow.length
        && x.text.length <= 30000
        && x.text.toLowerCase().includes(targetNameLow)
        && /(resume|contact|application|applied)/i.test(x.text)
      )
      .sort((a,b) => a.text.length - b.text.length);

    detailRoot = matching[0]?.el || null;
  }

  if (!detailRoot && targetNameLow) {
    const pageText = normalize(document.body?.innerText || '');
    const currentUrl = (location.href || '').toLowerCase();

    if (
      /(candidate|applicant|application)/i.test(currentUrl)
      && pageText.toLowerCase().includes(targetNameLow)
      && /(resume|contact|applied\s+to|application)/i.test(pageText)
    ) {
      detailRoot = document.body;
    }
  }

  const scopeRoot = detailRoot || document.body;
  const scopeIsCandidateSpecific = Boolean(detailRoot);

  const body = (scopeRoot?.innerText || document.body?.innerText || '')
    .slice(0, 180000);

  const headings = [...scopeRoot.querySelectorAll(
    'h1,h2,h3,[data-testid*="candidate-name" i],'
    + '[data-testid*="name" i]'
  )]
    .map(x => (x.innerText || '').trim())
    .filter(Boolean)
    .slice(0, 30);

  const resumeNodes = [...scopeRoot.querySelectorAll(
    '[data-testid*="resume" i],'
    + '[id*="resume" i],'
    + '[class*="resume" i],'
    + '[aria-label*="resume" i],'
    + '[data-testid*="cv" i],'
    + '[id*="cv" i],'
    + '[class*="cv" i]'
  )];

  let resumeText = '';
  for (const n of resumeNodes) {
    const t = (n.innerText || '').trim();
    if (t.length > resumeText.length && t.length < 120000) {
      resumeText = t;
    }
  }

  // Read same-origin resume/profile frames if Indeed renders them in an iframe.
  for (const frame of [...scopeRoot.querySelectorAll('iframe')]) {
    try {
      const t = (frame.contentDocument?.body?.innerText || '').trim();
      if (t.length > resumeText.length && t.length < 120000) {
        resumeText = t;
      }
    } catch (_) {}
  }

  const links = [...scopeRoot.querySelectorAll('a[href]')].map(a => ({
    href: a.href || '',
    text: (a.innerText || a.textContent || '').trim().slice(0, 180)
  }));

  const embeddedUrls = [
    ...scopeRoot.querySelectorAll('iframe[src],embed[src],object[data]')
  ].map(el => el.src || el.data || '').filter(Boolean);

  const resourceUrls = [];

  const candidateUrls = [];

  const addCandidateUrl = (href, text='') => {
    if (!href) return;
    try {
      const u = new URL(href, location.href);
      if (!/indeed\./i.test(u.hostname)) return;

      const h = u.href.toLowerCase();
      const t = (text || '').toLowerCase();

      let score = 0;
      if (h.includes('resume') || h.includes('cv')) score += 7;
      if (h.includes('download')) score += 4;
      if (h.includes('.pdf') || h.includes('.docx')) score += 7;
      if (t.includes('resume') || t.includes('cv')) score += 5;
      if (t.includes('download')) score += 2;

      if (score >= 4) {
        candidateUrls.push({href: u.href, text, score});
      }
    } catch (_) {}
  };

  for (const x of links) addCandidateUrl(x.href, x.text);
  for (const x of embeddedUrls) addCandidateUrl(x, 'embedded resume');
  for (const x of resourceUrls) addCandidateUrl(x, 'resource resume');

  const unique = new Map();
  for (const x of candidateUrls.sort((a,b) => b.score-a.score)) {
    if (!unique.has(x.href)) unique.set(x.href, x);
  }

  const resumeLinks = [...unique.values()].slice(0, 18);

  let downloaded = null;
  let downloadError = null;
  let fetchedResumeText = '';

  const htmlToText = (html) => {
    try {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      return (doc.body?.innerText || '').trim();
    } catch (_) {
      return html;
    }
  };

  for (const link of resumeLinks) {
    try {
      const r = await fetch(link.href, {
        credentials: 'include',
        redirect: 'follow',
        cache: 'no-store'
      });
      if (!r.ok) continue;

      const ct = (r.headers.get('content-type') || '').toLowerCase();
      const buf = await r.arrayBuffer();

      if (buf.byteLength > 12000000) {
        downloadError = 'Resume file is larger than inline processing limit.';
        continue;
      }

      const bytes = new Uint8Array(buf);

      const isPdf =
        ct.includes('pdf') ||
        (
          bytes.length >= 4 &&
          bytes[0] === 37 &&
          bytes[1] === 80 &&
          bytes[2] === 68 &&
          bytes[3] === 70
        );

      const isDocx =
        ct.includes('wordprocessingml') ||
        ct.includes('msword');

      if (isPdf || isDocx) {
        let binary = '';
        const chunk = 0x7000;
        for (let i=0; i<bytes.length; i+=chunk) {
          binary += String.fromCharCode(...bytes.subarray(i, i+chunk));
        }

        downloaded = {
          mime: ct || (isPdf ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
          filename: isPdf ? 'indeed_resume.pdf' : 'indeed_resume.docx',
          base64: btoa(binary)
        };
        break;
      }

      // Some Indeed resume endpoints return HTML/text/JSON instead of a file.
      // Preserve useful text, especially contact details.
      if (
        ct.includes('text/') ||
        ct.includes('html') ||
        ct.includes('json') ||
        ct.includes('javascript')
      ) {
        const raw = new TextDecoder('utf-8').decode(bytes);
        let text = raw;

        if (ct.includes('html')) {
          text = htmlToText(raw);
        }

        if (
          text.length > fetchedResumeText.length &&
          text.length < 140000
        ) {
          fetchedResumeText = text;
        }
      }
    } catch (e) {
      downloadError = String(e);
    }
  }

  if (fetchedResumeText.length > resumeText.length) {
    resumeText = fetchedResumeText;
  }

  const expandedBody = (scopeRoot?.innerText || body).slice(0, 180000);

  const emailRe = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/ig;
  const phoneRe = /(?:\+?\d[\d\s().-]{7,}\d)/g;

  const emails = new Set();
  const phones = new Set();

  for (const a of [...scopeRoot.querySelectorAll('a[href^="mailto:"]')]) {
    const value = (a.href || '').replace(/^mailto:/i, '').split('?')[0].trim();
    if (value) emails.add(value);
  }

  for (const a of [...scopeRoot.querySelectorAll('a[href^="tel:"]')]) {
    const value = (a.href || '').replace(/^tel:/i, '').trim();
    if (value) phones.add(value);
  }

  // Resume evidence is always safe. Candidate-scope text is included only
  // when the detail/drawer could be positively tied to the selected applicant.
  for (const source of [resumeText]) {
    for (const match of (source.match(emailRe) || [])) {
      emails.add(match);
    }
    for (const match of (source.match(phoneRe) || [])) {
      phones.add(match);
    }
  }

  if (scopeIsCandidateSpecific) {
    for (const match of (expandedBody.match(emailRe) || [])) {
      emails.add(match);
    }
    for (const match of (expandedBody.match(phoneRe) || [])) {
      phones.add(match);
    }
  }

  const decodeHtml = (value) => {
    try {
      const textarea = document.createElement('textarea');
      textarea.innerHTML = value || '';
      return textarea.value || '';
    } catch (_) {
      return value || '';
    }
  };

  const htmlText = scopeIsCandidateSpecific
    ? decodeHtml((scopeRoot?.innerHTML || '').slice(0, 280000))
    : '';

  if (scopeIsCandidateSpecific) {
    for (const match of (htmlText.match(emailRe) || [])) {
      emails.add(match);
    }
  }

  // Job title + official Indeed job-description candidates.
  const jobCandidates = [];
  const jobLinks = [];

  const addJob = (value, score=0) => {
    const text = (value || '').replace(/\s+/g, ' ').trim();
    const low = text.toLowerCase();

    if (
      !text
      || text.length < 2
      || text.length > 180
      || [
        'the position',
        'position',
        'jobs',
        'job',
        'manage candidates',
        'candidates',
      ].includes(low)
    ) {
      return;
    }

    jobCandidates.push({text, score});
  };

  for (const a of [...scopeRoot.querySelectorAll('a[href]')]) {
    const rawHref = a.href || '';
    const href = rawHref.toLowerCase();
    const text = (a.innerText || a.textContent || '').trim();

    if (
      /(viewjob|jobdetail|jobkey|\/job\/|\/jobs\/)/i.test(href)
      && text
    ) {
      addJob(text, 100);
      if (/indeed\./i.test(rawHref)) {
        jobLinks.push({href: rawHref, text});
      }
    }
  }

  for (const el of scopeRoot.querySelectorAll(
    '[data-testid*="job-title" i],'
    + '[data-testid*="jobTitle" i],'
    + '[class*="jobTitle" i],'
    + '[aria-label*="job title" i]'
  )) {
    addJob(el.innerText || el.textContent || '', 110);
  }

  const bodyLines = expandedBody
    .split(/\n+/)
    .map(x => x.replace(/\s+/g, ' ').trim())
    .filter(Boolean);

  for (let i=0; i<bodyLines.length; i++) {
    const line = bodyLines[i];

    let m = line.match(/^Applied\s+to\s*:?\s*(.+)$/i);
    if (m) addJob(m[1], 130);

    m = line.match(/^(?:Job|Position|Application\s+for)\s*:?\s*(.+)$/i);
    if (m) addJob(m[1], 90);

    if (/^Applied\s+to\s*:?\s*$/i.test(line) && bodyLines[i+1]) {
      addJob(bodyLines[i+1], 125);
    }
  }

  if (
    TARGET_ROW_JOB
    && !['the position','position','job','jobs'].includes(
      normalize(TARGET_ROW_JOB).toLowerCase()
    )
  ) {
    addJob(TARGET_ROW_JOB, 200);
  }

  jobCandidates.sort((a,b) => b.score - a.score);

  let jobDescription = '';
  let jobDescriptionSource = '';
  let jobUrl = '';

  const cleanDescription = (value) =>
    (value || '')
      .replace(/\r/g, '\n')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();

  const extractJobPosting = (doc, fallbackUrl='') => {
    let title = '';
    let description = '';

    try {
      for (const script of [...doc.querySelectorAll('script[type="application/ld+json"]')]) {
        try {
          const raw = JSON.parse(script.textContent || 'null');
          const queue = Array.isArray(raw) ? [...raw] : [raw];
          while (queue.length) {
            const item = queue.shift();
            if (!item || typeof item !== 'object') continue;
            if (Array.isArray(item['@graph'])) queue.push(...item['@graph']);
            const kind = item['@type'];
            const types = Array.isArray(kind) ? kind : [kind];
            if (types.some(x => String(x || '').toLowerCase() === 'jobposting')) {
              title = String(item.title || item.name || '').trim();
              description = cleanDescription(htmlToText(String(item.description || '')));
              if (description.length >= 100) {
                return {title, description, url: fallbackUrl};
              }
            }
          }
        } catch (_) {}
      }
    } catch (_) {}

    const selectors = [
      '#jobDescriptionText',
      '[data-testid*="job-description" i]',
      '[data-testid*="jobDescription" i]',
      '[class*="jobDescription" i]',
      '[class*="job-description" i]',
      '[id*="jobDescription" i]',
      '[aria-label*="job description" i]'
    ];

    for (const selector of selectors) {
      try {
        for (const el of [...doc.querySelectorAll(selector)]) {
          const text = cleanDescription(el.innerText || el.textContent || '');
          if (text.length > description.length && text.length <= 40000) {
            description = text;
          }
        }
      } catch (_) {}
    }

    const titleNode = doc.querySelector(
      '[data-testid*="job-title" i],h1,[class*="jobTitle" i]'
    );
    if (!title && titleNode) {
      title = (titleNode.innerText || titleNode.textContent || '').trim();
    }

    return {title, description, url: fallbackUrl};
  };

  // Some candidate detail panels already contain the official description.
  try {
    const local = extractJobPosting(scopeRoot, location.href);
    if (local.description && local.description.length >= 100) {
      jobDescription = local.description;
      jobDescriptionSource = 'indeed_candidate_detail';
      jobUrl = local.url || location.href;
    }
  } catch (_) {}

  // Prefer the linked official job page. Fetch is same-origin/authenticated
  // and does not move the user's Candidates tab.
  if (!jobDescription) {
    const uniqueJobLinks = [];
    const seenJobLinks = new Set();
    for (const link of jobLinks) {
      if (!link.href || seenJobLinks.has(link.href)) continue;
      seenJobLinks.add(link.href);
      uniqueJobLinks.push(link);
      if (uniqueJobLinks.length >= 4) break;
    }

    for (const link of uniqueJobLinks) {
      try {
        const r = await fetch(link.href, {
          credentials: 'include',
          redirect: 'follow',
          cache: 'no-store'
        });
        if (!r.ok) continue;
        const raw = await r.text();
        const doc = new DOMParser().parseFromString(raw, 'text/html');
        const found = extractJobPosting(doc, r.url || link.href);
        if (found.title) addJob(found.title, 150);
        if (found.description && found.description.length >= 100) {
          jobDescription = found.description.slice(0, 40000);
          jobDescriptionSource = 'indeed_job_page';
          jobUrl = found.url || link.href;
          break;
        }
      } catch (_) {}
    }
  }

  return {
    url: location.href,
    title: document.title,
    body: expandedBody,
    headings,
    resumeText: resumeText.slice(0, 180000),
    resumeLinks,
    downloaded,
    downloadError,
    visibleEmails: [...emails].slice(0, 40),
    visiblePhones: [...phones].slice(0, 30),
    contactTexts: [
      resumeText.slice(0, 180000),
      ...(scopeIsCandidateSpecific
        ? [
            expandedBody.slice(0, 180000),
            htmlText.slice(0, 180000),
          ]
        : []),
    ],
    scopeIsCandidateSpecific,
    jobCandidates: jobCandidates.slice(0, 20).map(x => x.text),
    jobDescription: jobDescription.slice(0, 40000),
    jobDescriptionSource,
    jobUrl,
    expandedControls: [...clicked],
  };
})()
"""


def build_candidate_payload_js(entry):
    return (
        CANDIDATE_PAYLOAD_JS
        .replace(
            "__TARGET_CANDIDATE_NAME__",
            json.dumps(entry.get("label") or ""),
        )
        .replace(
            "__TARGET_ROW_JOB__",
            json.dumps(entry.get("job_title") or ""),
        )
    )


def save_downloaded_resume(source_key, downloaded):
    if not downloaded or not downloaded.get("base64"):
        return None

    mime = (downloaded.get("mime") or "").lower()
    if "pdf" in mime:
        ext = ".pdf"
    elif "wordprocessingml" in mime:
        ext = ".docx"
    else:
        return None

    try:
        data = base64.b64decode(downloaded["base64"], validate=False)
    except Exception:
        return None

    if len(data) > 10_000_000:
        return None

    path = RESUME_DIR / f"{source_key[:12]}_existing_chrome_resume{ext}"
    path.write_bytes(data)
    return str(path)



def _job_title_from_context(context_text):
    """
    Read the job directly from the Indeed candidate-list row.

    Live examples:
      Applied to: Purchase Executive
      Applied to: Marketing & Lead Coordination Executive
    """
    text = context_text or ""

    patterns = [
        r"(?im)^\s*applied\s+to\s*:\s*(.{2,160})$",
        r"(?im)^\s*applied\s+for\s*:\s*(.{2,160})$",
        r"(?im)^\s*job\s*:\s*(.{2,160})$",
        r"(?im)^\s*position\s*:\s*(.{2,160})$",
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue

        value = re.sub(r"\s+", " ", m.group(1)).strip(" |•·–—-")
        if value:
            return value[:160]

    # Fallback for wrapped row text.
    compact = re.sub(r"\s+", " ", text)
    m = re.search(
        r"\bApplied\s+to\s*:\s*(.{2,160}?)(?=\s+(?:Activity|New\b|Reviewing\b|Contacting\b|Interviewing\b|Rejected\b|Hired\b|$))",
        compact,
        flags=re.I,
    )
    if m:
        value = re.sub(r"\s+", " ", m.group(1)).strip(" |•·–—-")
        if value:
            return value[:160]

    return None


def _job_title_from_body(body):
    text = body or ""

    patterns = [
        r"(?im)^\s*applied\s+to\s*:?\s*(.{2,160})$",
        r"(?im)^\s*applied\s+for\s*:?\s*(.{2,160})$",
        r"(?im)^\s*application\s+for\s*:?\s*(.{2,160})$",
        r"(?im)^\s*job\s*:?\s*(.{2,160})$",
        r"(?im)^\s*position\s*:?\s*(.{2,160})$",
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip(" |•·–—-")
            if value and value.lower() not in {
                "the position","position","job","jobs"
            }:
                return value[:160]

    lines = [
        re.sub(r"\s+", " ", x).strip()
        for x in text.splitlines()
        if x.strip()
    ]

    for i, line in enumerate(lines):
        if re.fullmatch(r"(?i)applied\s+to\s*:?", line) and i + 1 < len(lines):
            value = lines[i + 1].strip(" |•·–—-")
            if value:
                return value[:160]

    return "the position"




def _candidate_link_from_page(c, target):
    """
    Inspect one already-open Indeed page for a Candidates / Applicants link.
    Returns a URL or None. This does not create a new Chrome connection.
    """
    sid = None
    try:
        sid = c.attach(target["targetId"])
        value = c.evaluate(
            sid,
            """(() => {
              const anchors = [...document.querySelectorAll('a[href]')];
              const scored = anchors.map(a => {
                const href = a.href || '';
                const text = (a.innerText || a.textContent || '').trim();
                const h = href.toLowerCase();
                const t = text.toLowerCase();
                let score = 0;

                if (!h.includes('indeed.')) return null;
                if (h.includes('/login')) return null;

                if (h.includes('candidate')) score += 20;
                if (h.includes('applicant')) score += 20;
                if (h.includes('application')) score += 10;

                if (t === 'candidates' || t.includes('view candidates')) score += 25;
                if (t === 'applicants' || t.includes('view applicants')) score += 25;
                if (t.includes('candidate')) score += 10;
                if (t.includes('applicant')) score += 10;

                return {href, text, score};
              }).filter(Boolean).filter(x => x.score >= 20);

              scored.sort((a, b) => b.score - a.score);
              return scored[0] || null;
            })()""",
            await_promise=False,
            timeout=15,
        )
        if isinstance(value, dict):
            href = value.get("href") or ""
            if "indeed." in href.lower() and "/login" not in href.lower():
                return href
    except Exception:
        return None
    finally:
        if sid:
            c.detach(sid)

    return None


def navigate_to_candidates_from_open_indeed():
    """
    If the user is already signed in on any Indeed Employer page (for example
    Jobs), find the Candidates/Applicants navigation link and open it in the
    SAME existing Chrome tab.

    Returns the detected Candidates page when successful.
    """
    c = get_shared_chrome()
    targets = c.targets()

    # Prefer Employer/Jobs pages; ignore login tabs.
    indeed_targets = []
    for target in targets:
        url = target.get("url") or ""
        low = url.lower()
        if "indeed." not in low or "/login" in low:
            continue

        score = 0
        title = (target.get("title") or "").lower()
        if "employer" in low or "employers" in low:
            score += 10
        if "jobs - indeed for employers" in title or "employer" in title:
            score += 10
        indeed_targets.append((score, target))

    indeed_targets.sort(key=lambda x: x[0], reverse=True)

    for _, target in indeed_targets:
        # It may already be the Candidates page.
        sid = None
        try:
            sid = c.attach(target["targetId"])
            info = c.evaluate(
                sid,
                """(() => ({
                  url: location.href,
                  title: document.title,
                  bodyPreview: (document.body?.innerText || '').slice(0, 8000)
                }))()""",
                await_promise=False,
                timeout=12,
            ) or {}

            if _looks_like_candidates_page(
                info.get("url") or target.get("url") or "",
                info.get("title") or target.get("title") or "",
                info.get("bodyPreview") or "",
            ):
                return {
                    "targetId": target["targetId"],
                    "url": info.get("url") or target.get("url") or "",
                    "title": info.get("title") or target.get("title") or "",
                }
        except Exception:
            pass
        finally:
            if sid:
                c.detach(sid)

        href = _candidate_link_from_page(c, target)
        if not href:
            continue

        sid = None
        try:
            sid = c.attach(target["targetId"])
            c.navigate(sid, href, timeout=45)
        finally:
            if sid:
                c.detach(sid)

        # Give the Employer SPA a moment to settle, then detect again.
        time.sleep(1.0)
        try:
            return detect_candidates_page()
        except Exception:
            continue

    raise ChromeConnectionError(
        "Chrome is connected, but the Candidates page could not be opened "
        "automatically. Sign in to Indeed Employer first; the console will "
        "detect Candidates automatically after login."
    )


def ensure_candidates_page():
    """
    Detect the real Candidates page, or automatically navigate there from an
    already-open Employer dashboard page.
    """
    try:
        return detect_candidates_page()
    except Exception:
        return navigate_to_candidates_from_open_indeed()


def detect_candidates_page():
    """
    Search every Indeed page using the already-approved persistent Chrome
    connection. No permission request is generated here.
    """
    c = get_shared_chrome()
    targets = c.targets()
    candidates = []

    for target in targets:
        url = target.get("url") or ""
        if "indeed." not in url.lower():
            continue
        if "/login" in url.lower():
            continue

        sid = None
        try:
            sid = c.attach(target["targetId"])
            data = c.evaluate(
                sid,
                """(() => ({
                  url: location.href,
                  title: document.title,
                  bodyPreview: (document.body?.innerText || '').slice(0, 8000)
                }))()""",
                await_promise=False,
                timeout=12,
            ) or {}

            if _looks_like_candidates_page(
                data.get("url") or url,
                data.get("title") or target.get("title") or "",
                data.get("bodyPreview") or "",
            ):
                candidates.append({
                    "targetId": target["targetId"],
                    "url": data.get("url") or url,
                    "title": data.get("title") or target.get("title") or "",
                })
        except Exception:
            pass
        finally:
            if sid:
                c.detach(sid)

    if not candidates:
        raise ChromeConnectionError(
            "Chrome is connected, but the Indeed Employer Candidates page "
            "is not open yet. Sign in to Indeed and open Candidates."
        )

    candidates.sort(
        key=lambda x: (
            0 if any(
                k in (x.get("url") or "").lower()
                for k in ["candidate", "applicant", "application"]
            ) else 1,
            len(x.get("url") or ""),
        )
    )
    return candidates[0]


def _looks_like_candidates_page(url, title, body_preview=""):
    low_url = (url or "").lower()
    low_title = (title or "").lower()
    low_body = (body_preview or "").lower()

    if "/login" in low_url:
        return False

    strong_url = any(
        k in low_url
        for k in ["candidate", "applicant", "application"]
    )
    strong_title = any(
        k in low_title
        for k in ["candidate", "applicant"]
    )
    body_signal = (
        "candidates" in low_body
        and any(
            k in low_body
            for k in [
                "awaiting review",
                "reviewed",
                "contacting",
                "application",
                "applicant",
            ]
        )
    )

    return strong_url or strong_title or body_signal


def _review_retry_due(existing):
    """
    Keep checking unresolved candidate details forever, without a user-facing
    timer setting.

    Adaptive internal backoff:
      early failures -> faster retries
      repeated no-resume/no-contact -> progressively calmer retries
    """
    if not existing:
        return True

    attempts = int(existing.get("extraction_attempts") or 0)

    # Internal only. This is deliberately not exposed in Settings.
    if attempts <= 1:
        delay_seconds = 20
    elif attempts <= 3:
        delay_seconds = 45
    elif attempts <= 6:
        delay_seconds = 90
    else:
        delay_seconds = 180

    stamp = (
        existing.get("last_seen_at")
        or existing.get("updated_at")
        or existing.get("first_seen_at")
    )

    if not stamp:
        return True

    try:
        previous = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)

        elapsed = (
            datetime.now(timezone.utc) - previous.astimezone(timezone.utc)
        ).total_seconds()

        return elapsed >= delay_seconds
    except Exception:
        return True


def select_entries_for_processing(entries, settings, fast_only=False):
    """
    No candidate batch size.

    ONE-TIME START
      - remember every candidate currently visible
      - process all candidates currently carrying Indeed's New status

    LIVE
      - every truly unseen candidate is processed immediately
      - unresolved resume/contact rows stay eligible forever via adaptive retry
      - SENT candidates are never reprocessed for mail
    """
    catch_up_pending = (
        settings.get("process_current_candidates_once", True)
        and settings.get("initial_catchup_new_only", True)
        and not backlog_completed()
    )

    selected = []

    if catch_up_pending:
        for entry in entries:
            current_new = bool(entry.get("current_new"))

            remember_seen_candidate(
                entry["source_key"],
                entry.get("url"),
                entry.get("label"),
                initial_new=current_new,
            )

        # No batch limit: take the entire currently-discovered New set.
        for entry in entries:
            ledger = seen_candidate(entry["source_key"]) or {}

            if (
                int(ledger.get("initial_new") or 0) == 1
                and int(ledger.get("processed") or 0) == 0
            ):
                selected.append(entry)

    else:
        for entry in entries:
            existing = get_by_source_key(entry["source_key"])

            # A new candidate that appeared after live start.
            if not is_seen_candidate(entry["source_key"]):
                remember_seen_candidate(
                    entry["source_key"],
                    entry.get("url"),
                    entry.get("label"),
                    initial_new=False,
                )
                selected.append(entry)
                continue

            # Fast live pass ignores old history, but an applicant who is
            # still in Indeed's current New queue gets a quick retry if the
            # first detail/resume opening happened while the SPA was loading.
            if fast_only:
                if existing and entry.get("current_new"):
                    status = existing.get("extraction_status") or ""
                    stamp = (
                        existing.get("last_seen_at")
                        or existing.get("updated_at")
                        or existing.get("first_seen_at")
                    )
                    retry_due = True

                    if stamp:
                        try:
                            previous = datetime.fromisoformat(
                                str(stamp).replace("Z", "+00:00")
                            )
                            if previous.tzinfo is None:
                                previous = previous.replace(
                                    tzinfo=timezone.utc
                                )
                            retry_due = (
                                datetime.now(timezone.utc)
                                - previous.astimezone(timezone.utc)
                            ).total_seconds() >= 3
                        except Exception:
                            retry_due = True

                    if status.startswith("NEEDS_REVIEW_") and retry_due:
                        selected.append(entry)

                continue

            # Full reconciliation pass retries unresolved details.
            if existing:
                status = existing.get("extraction_status") or ""
                sent_status = (existing.get("send_status") or "").upper()
                existing_job = re.sub(
                    r"\s+",
                    " ",
                    str(existing.get("job_title") or ""),
                ).strip().lower()

                # V11.4 migration: a historical acknowledgement already marked
                # SENT but still carrying a generic role is opened again only
                # to recover the exact role for the one-time correction.
                if (
                    sent_status == "SENT"
                    and existing_job in {
                        "", "the position", "position", "job",
                        "the job", "unknown",
                    }
                ):
                    selected.append(entry)
                    continue

                if (
                    status.startswith("NEEDS_REVIEW_")
                    and sent_status != "SENT"
                    and _review_retry_due(existing)
                ):
                    selected.append(entry)

    return selected, catch_up_pending


def scan_existing_chrome(fast_only=False):
    settings = load_settings()
    c = get_shared_chrome()
    sid = None

    try:
        saved = new_candidates_queue_url(
            (settings.get("indeed_candidates_url") or "").strip()
        )

        target = None

        # Prefer the saved Candidates page if its target is still open.
        if saved:
            try:
                detected = detect_candidates_page()
                if urldefrag(detected.get("url") or "")[0] == urldefrag(saved)[0]:
                    target = detected
            except Exception:
                pass

        if target is None:
            target = detect_candidates_page()

        target_id = target["targetId"]
        sid = c.attach(target_id)

        if saved and urldefrag(target.get("url") or "")[0] != urldefrag(saved)[0]:
            if "/login" not in saved.lower():
                c.navigate(sid, saved, timeout=45)

        try:
            c.evaluate(
                sid,
                WAIT_CANDIDATES_READY_JS,
                await_promise=True,
                timeout=12,
            )
        except Exception:
            pass

        collector_name = "fast-visible" if fast_only else "full"
        try:
            link_data = (
                c.evaluate(
                    sid,
                    (
                        COLLECT_VISIBLE_LINKS_JS
                        if fast_only
                        else COLLECT_LINKS_JS
                    ),
                    await_promise=True,
                    timeout=45,
                )
                or {}
            )
        except Exception as primary_error:
            # A fast DOM collector must never stop live monitoring. If Indeed
            # changed one fast-page selector/runtime path, retry once using the
            # independent full collector before declaring the scan failed.
            if not fast_only:
                raise ChromeConnectionError(
                    f"JavaScript error in Indeed page ({collector_name} collector): {primary_error}"
                )

            try:
                link_data = (
                    c.evaluate(
                        sid,
                        COLLECT_LINKS_JS,
                        await_promise=True,
                        timeout=45,
                    )
                    or {}
                )
                collector_name = "full-fallback"
            except Exception as fallback_error:
                raise ChromeConnectionError(
                    "JavaScript error in Indeed page: "
                    f"fast collector failed ({primary_error}); "
                    f"full fallback failed ({fallback_error})"
                )

        list_url = link_data.get("url") or target.get("url") or ""
        title = link_data.get("title") or ""
        body_preview = link_data.get("bodyPreview") or ""

        if not _looks_like_candidates_page(
            list_url,
            title,
            body_preview,
        ):
            raise ChromeConnectionError(
                "The connected Indeed page is not the Employer Candidates page."
            )

        scored = []
        seen_keys = set()

        for x in link_data.get("links", []):
            record_type = (x.get("recordType") or "link").lower()
            href = urldefrag(x.get("href") or "")[0]
            text = (
                x.get("candidateName")
                or x.get("text")
                or ""
            ).strip()
            context_text = x.get("contextText") or ""
            data_test_id = x.get("dataTestId") or ""
            row_job = (
                x.get("jobTitle")
                or _job_title_from_context(context_text)
            )
            location_text = x.get("locationText") or ""
            identity_hint = x.get("identityHint") or ""

            invalid_ui_names = {
                "all open and paused jobs",
                "all jobs",
                "jobs",
                "education",
                "yes",
                "no",
                "candidates",
                "candidate",
                "applicants",
                "manage candidates",
                "find candidates",
                "download cv",
                "download resume",
                "core skills",
                "resume",
                "contact information",
            }

            if text.strip().lower() in invalid_ui_names:
                continue

            if record_type == "row":
                if not text:
                    continue

                source_key = stable_row_candidate_key(
                    text,
                    row_job,
                    location_text,
                    identity_hint,
                )
                score = 100
            else:
                score = candidate_link_score(
                    href,
                    text,
                    context_text,
                    data_test_id,
                )

                if score < 10 or not href:
                    continue

                source_key = stable_candidate_key(href)

            if source_key in seen_keys:
                continue

            seen_keys.add(source_key)

            scored.append({
                "record_type": record_type,
                "url": href,
                "label": text[:120],
                "location_text": location_text[:120],
                "identity_hint": identity_hint[:2000],
                "context_text": context_text[:2000],
                "job_title": row_job,
                "score": score,
                "source_key": source_key,
                "new_status_hint": (
                    bool(x.get("currentNew"))
                    or is_new_status_hint(context_text)
                ),
                "current_new": (
                    bool(x.get("currentNew"))
                    or is_current_new_candidate(context_text)
                ),
                "indeed_status": candidate_pipeline_status(
                    context_text,
                    x.get("pipelineStatus"),
                ),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)

        scan_seen_at = datetime.now(timezone.utc).isoformat()

        # Update live presence for every existing applicant seen in this scan,
        # even when the resume/email does not need reprocessing.
        try:
            touch_visible_applications(
                [entry.get("source_key") for entry in scored],
                seen_at=scan_seen_at,
            )
        except Exception:
            pass

        set_state(
            "last_scan_at",
            __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        )
        set_state("last_visible_count", str(len(scored)))

        entries_to_process, catch_up_pending = (
            select_entries_for_processing(
                scored,
                settings,
                fast_only=fast_only,
            )
        )

        results = []

        for entry in entries_to_process:
            url = entry.get("url") or ""
            label = entry["label"]
            source_key = entry["source_key"]
            existing_before = get_by_source_key(source_key)

            try:
                # Use a real candidate URL when Indeed exposes one.
                if url:
                    c.navigate(sid, url, timeout=45)
                else:
                    # Live Manage Candidates often uses a JS-clickable row/card
                    # with no applicant URL. Click that exact row in the user's
                    # already-open Chrome session.
                    click_result = (
                        c.evaluate(
                            sid,
                            build_row_click_js(entry),
                            await_promise=True,
                            timeout=35,
                            user_gesture=True,
                        )
                        or {}
                    )

                    if not click_result.get("clicked"):
                        raise ChromeConnectionError(
                            "Candidate row could not be opened: "
                            + str(click_result.get("reason") or "unknown row click error")
                        )

                    time.sleep(0.8)


                # Wait for the exact selected candidate detail/drawer before
                # reading resume/contact data.
                try:
                    detail_wait_js = f"""(async () => {{
                      const target = {json.dumps(label)};
                      const low = (target || '').toLowerCase();
                      const sleep = ms => new Promise(r => setTimeout(r, ms));
                      const started = Date.now();

                      while (Date.now() - started < 7000) {{
                        const body = document.body?.innerText || '';
                        const dialogs = [
                          ...document.querySelectorAll(
                            '[role="dialog"],aside,'
                            + '[data-testid*="candidate-detail" i],'
                            + '[data-testid*="candidate-panel" i],'
                            + '[data-testid*="application-detail" i],'
                            + '[class*="candidateDetail" i],'
                            + '[class*="drawer" i]'
                          )
                        ];

                        const matched = dialogs.some(el => {{
                          const text = (el.innerText || '').toLowerCase();
                          return !low || text.includes(low);
                        }});

                        if (
                          matched
                          || (
                            body.toLowerCase().includes(low)
                            && /(resume|contact|applied\\s+to|application)/i.test(body)
                          )
                        ) {{
                          return true;
                        }}

                        await sleep(200);
                      }}

                      return false;
                    }})()"""

                    c.evaluate(
                        sid,
                        detail_wait_js,
                        await_promise=True,
                        timeout=10,
                    )
                except Exception:
                    pass

                payload = (
                    c.evaluate(
                        sid,
                        build_candidate_payload_js(entry),
                        await_promise=True,
                        timeout=100,
                        user_gesture=True,
                    )
                    or {}
                )

                resume_path = save_downloaded_resume(
                    source_key,
                    payload.get("downloaded"),
                )
                resume_text = payload.get("resumeText") or ""
                resume_found = bool(resume_path or resume_text)

                headings = payload.get("headings") or []
                candidate_name = (
                    label
                    or (headings[0] if headings else None)
                    or "Candidate"
                )

                if candidate_name.strip().lower() in GENERIC_CANDIDATE_LABELS:
                    candidate_name = "Candidate"

                body = payload.get("body") or ""

                detail_job = _job_title_from_body(body)
                row_job = entry.get("job_title")

                browser_jobs = [
                    re.sub(r"\s+", " ", str(x or "")).strip()
                    for x in (payload.get("jobCandidates") or [])
                ]
                browser_job = next(
                    (
                        x for x in browser_jobs
                        if x
                        and x.lower() not in {
                            "the position","position","job","jobs",
                            "candidates","manage candidates"
                        }
                    ),
                    None,
                )

                final_job = (
                    row_job
                    if row_job and row_job != "the position"
                    else (
                        browser_job
                        or (
                            detail_job
                            if detail_job and detail_job != "the position"
                            else "the position"
                        )
                    )
                )

                results.append({
                    "source_key": source_key,
                    "profile_url": payload.get("url") or url or list_url,
                    "candidate_name": candidate_name,
                    "job_title": final_job,
                    "job_description": payload.get("jobDescription") or "",
                    "job_description_source": payload.get("jobDescriptionSource") or "",
                    "job_url": payload.get("jobUrl") or "",
                    "resume_path": resume_path,
                    "resume_text": resume_text,
                    "resume_found": resume_found,
                    "resume_error": payload.get("downloadError"),
                    "candidate_page_text": body[:90000],
                    "profile_emails": (
                        payload.get("visibleEmails") or []
                        if payload.get("scopeIsCandidateSpecific")
                        else []
                    ),
                    "profile_phones": (
                        payload.get("visiblePhones") or []
                        if payload.get("scopeIsCandidateSpecific")
                        else []
                    ),
                    "contact_texts": payload.get("contactTexts") or [],
                    "scope_is_candidate_specific": bool(
                        payload.get("scopeIsCandidateSpecific")
                    ),
                    "new_applicant": existing_before is None,
                })

                if entry.get("record_type") == "row":
                    try:
                        c.navigate(
                            sid,
                            new_candidates_queue_url(list_url),
                            timeout=45,
                        )
                        try:
                            c.evaluate(
                                sid,
                                WAIT_CANDIDATES_READY_JS,
                                await_promise=True,
                                timeout=12,
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass

            except Exception as e:
                results.append({
                    "source_key": source_key,
                    "profile_url": url or list_url,
                    "candidate_name": (
                        label
                        if label.strip().lower() not in GENERIC_CANDIDATE_LABELS
                        else "Candidate"
                    ),
                    "job_title": entry.get("job_title") or "the position",
                    "resume_path": None,
                    "resume_text": "",
                    "resume_found": False,
                    "resume_error": f"Candidate page failed: {e}",
                    "candidate_page_text": "",
                    "new_applicant": existing_before is None,
                })

        try:
            c.navigate(
                sid,
                new_candidates_queue_url(list_url),
                timeout=45,
            )
        except Exception:
            pass

        truly_new = sum(
            1 for r in results if r.get("new_applicant")
        )
        set_state("last_new_count", str(truly_new))

        return {
            "current_url": new_candidates_queue_url(list_url),
            "collector": collector_name,
            "found_links": len(scored),
            "collection_complete": (
                False
                if fast_only
                else bool(link_data.get("collectionComplete"))
            ),
            "collected_link_count": int(link_data.get("collectedLinkCount") or 0),
            "new_candidates": truly_new,
            "processed_candidates": len(results),
            "catch_up_pending": catch_up_pending,
            "results": results,
            "fast_only": bool(fast_only),
            "message": "OK",
        }

    finally:
        if sid:
            c.detach(sid)
