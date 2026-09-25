from __future__ import annotations

import os
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from config import load_settings, save_settings
from openai_ranker import (
    ranking_key_status,
    save_ranking_api_key,
    remove_ranking_api_key,
)
from database import (
    init_db,
    list_applications,
    list_logs,
    list_sent_responses,
    stats,
    live_detection_snapshot,
    database_health,
    get_by_id,
    log,
    mark_backlog_completed,
    reset_backlog_once,
    pending_initial_new_count,
    mark_seen_processed,
    get_state,
    set_state,
    now,
)
from automation import (
    process_indeed_results,
    send_thank_you,
    send_all_ready,
    send_all_ready_corrections,
    smtp_health_check,
)
from chrome_cdp import (
    chrome_status,
    scan_existing_chrome,
    devtools_active_port_path,
    open_indeed_in_existing_chrome,
    detect_candidates_page,
    ensure_candidates_page,
    new_candidates_queue_url,
    discover_employer_job_descriptions,
    cdp_session_recovery_snapshot,
    open_whatsapp_login_tab,
    ensure_whatsapp_web_ready,
)

from review_engine import (
    init_role_review_db,
    list_roles,
    save_role_description,
    role_review_payload,
    analyze_role,
    analyze_pending_reviews,
    sync_job_descriptions_from_scan,
    ingest_discovered_job_descriptions,
    analyze_scan_results,
    ranking_status,
)
from recruitment_pipeline import (
    init_recruitment_pipeline_db,
    sync_discovered_jobs,
    queue_live_candidate_outreach,
    queue_changed_role_reports,
    queue_daily_consolidated_report,
    process_notifications_once,
    resume_config_waiting_notifications,
    hr_report_mail_health_check,
    decorate_role_payload,
    decorate_roles,
    approve_candidate_for_interview,
    approve_top_candidates_for_interview,
    interview_schedule_preview,
    pipeline_status,
    operations_overview,
)

BASE_DIR = Path(__file__).resolve().parent
API_PORT = 5286
APP_VERSION = "V11.11.5"

app = Flask(__name__)
app.secret_key = "nunes-recruitment-console-v10"

scan_lock = threading.Lock()
stop_event = threading.Event()

# Wake signals make Start Automation and newly-verified applicants react
# immediately instead of waiting for the next one-second polling boundary.
scan_wake_event = threading.Event()
outbox_wake_event = threading.Event()
role_review_wake_event = threading.Event()
pipeline_wake_event = threading.Event()

AUTO_CONNECT_RETRY_SECONDS = 90
AUTO_READY_RETRY_SECONDS = 5
AUTO_OPEN_RETRY_SECONDS = 120

# Internal live-operation cadence. These are implementation details, not user
# settings. Candidate scans and mail delivery run independently.
LIVE_CANDIDATE_IDLE_SECONDS = 1
LIVE_OUTBOX_IDLE_SECONDS = 1
MAIL_REVERIFY_SECONDS = 60
FULL_RECONCILE_SECONDS = 180
ROLE_REVIEW_IDLE_SECONDS = 3
ROLE_DESCRIPTION_DISCOVERY_SECONDS = 60

auto_connection_lock = threading.RLock()
auto_connection_state = {
    "phase": "starting",
    "message": "Starting automatic browser connection",
    "last_error": None,
    "last_connected_at": None,
    "last_candidates_at": None,
    "next_connect_attempt_at": 0.0,
    "next_indeed_open_at": 0.0,
}

_runtime_schema_lock = threading.RLock()
_runtime_schema_ready = False
_runtime_schema_error = None


def ensure_runtime_schema():
    """Idempotent persistent-data migration/health gate."""
    global _runtime_schema_ready, _runtime_schema_error

    if _runtime_schema_ready:
        return True

    with _runtime_schema_lock:
        if _runtime_schema_ready:
            return True

        try:
            init_db()
            init_role_review_db()
            init_recruitment_pipeline_db()
            _runtime_schema_ready = True
            _runtime_schema_error = None
            return True
        except Exception as exc:
            _runtime_schema_error = str(exc)
            return False


def runtime_schema_status():
    return {
        "ready": bool(_runtime_schema_ready),
        "error": _runtime_schema_error,
    }


@app.before_request
def _ensure_schema_before_request():
    # Normally ready before app.run(). This retry makes upgrade recovery robust
    # if a first migration attempt was interrupted.
    if not _runtime_schema_ready:
        ensure_runtime_schema()




def critical_runtime_self_test():
    """Local production self-test. No live Indeed/Gmail traffic is generated."""
    checks = {}
    errors = []

    def check(name, fn):
        try:
            value = fn()
            checks[name] = {"ok": True, "value": value}
            return value
        except Exception as exc:
            checks[name] = {"ok": False, "error": str(exc)}
            errors.append(f"{name}: {exc}")
            return None

    check("clock", lambda: datetime.now(timezone.utc).isoformat())
    check("database_schema", lambda: ensure_runtime_schema())
    check("settings", lambda: public_settings().get("company_email"))
    check("database_stats", lambda: stats().get("total", 0))
    check("live_state", lambda: live_detection_snapshot().get("status"))
    check("role_ranking_schema", lambda: ranking_status().get("total_roles", 0))
    check("recruitment_pipeline", lambda: pipeline_status().get("roles", {}).get("total", 0))

    # State round-trip catches missing imports/state regressions before a user
    # clicks Start Automation.
    marker = datetime.now(timezone.utc).isoformat()
    check(
        "state_round_trip",
        lambda: (
            set_state("runtime_self_test_at", marker),
            get_state("runtime_self_test_at"),
        )[1],
    )

    return {
        "ok": not errors,
        "version": APP_VERSION,
        "checks": checks,
        "errors": errors,
    }


def _automation_connection_kick():
    """Chrome/Indeed connection work is never allowed to break Start API."""
    try:
        automatic_connection_tick(force_connect=True)
    except Exception as exc:
        try:
            log("WARN", f"Automation connection recovery: {exc}")
        except Exception:
            pass


def _clear_stale_candidates_binding_if_needed(error_text, failure_count):
    """After repeated target/session failures force a clean Candidates rebind."""
    text = str(error_text or "").lower()
    recoverable = any(token in text for token in (
        "session with given id not found",
        "-32001",
        "target closed",
        "no target",
        "candidate page failed",
        "page.navigate failed",
        "not the employer candidates page",
        "javascript error in indeed page",
    ))

    if not recoverable or int(failure_count or 0) < 2:
        return False

    try:
        settings = load_settings()
        if settings.get("indeed_candidates_url"):
            settings["indeed_candidates_url"] = ""
            save_settings(settings)
        set_state("live_monitor_scan_mode", "rebinding_candidates")
        set_state("live_monitor_last_found_links", "0")
        return True
    except Exception:
        return False

def _set_auto_connection_state(**changes):
    with auto_connection_lock:
        auto_connection_state.update(changes)


def auto_connection_snapshot():
    with auto_connection_lock:
        out = dict(auto_connection_state)

    now_mono = time.monotonic()
    retry_at = float(out.get("next_connect_attempt_at") or 0.0)
    out["retry_in_seconds"] = max(0, int(retry_at - now_mono))
    return out


def find_chrome():
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def open_chrome_url(url):
    chrome = find_chrome()
    if chrome:
        subprocess.Popen(
            [chrome, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    return False


def sender_configured():
    s = load_settings()
    return bool(
        (s.get("company_email") or "").strip()
        and (s.get("smtp_app_password") or "").replace(" ", "").strip()
    )


def enforce_always_on_mode():
    """V11.11.2 runs recruitment continuously while the local service is open."""
    s = load_settings()
    changed = False
    for key in ("automation_enabled", "monitoring_enabled", "auto_scan", "auto_send"):
        if not s.get(key, False):
            s[key] = True
            changed = True
    if not s.get("whatsapp_enabled", True):
        s["whatsapp_enabled"] = True
        changed = True
    if s.get("whatsapp_auto_connect") is not True:
        s["whatsapp_auto_connect"] = True
        changed = True
    if changed:
        save_settings(s)
    return s


def monitoring_enabled_by_user(settings):
    return bool(
        settings.get("automation_enabled", True)
        and settings.get("monitoring_enabled", True)
        and settings.get("auto_scan", True)
    )


def monitoring_ready(settings):
    return bool(
        monitoring_enabled_by_user(settings)
        and (settings.get("indeed_candidates_url") or "").strip()
    )


def bind_candidates_page_if_available(reset_catchup=False):
    """
    Attach the real Candidates page to this installation as soon as it becomes
    available. The user does not need to press a separate Detect/Activate step.
    """
    detected = ensure_candidates_page()

    s = load_settings()
    previous = (s.get("indeed_candidates_url") or "").strip()
    detected_url = new_candidates_queue_url(
        (detected.get("url") or "").strip()
    )

    if not detected_url:
        raise RuntimeError("Candidates page URL was not available.")

    changed = previous != detected_url

    s["indeed_candidates_url"] = detected_url
    # Do not change automation_enabled here. The user-controlled ON/OFF switch
    # is authoritative.
    s["monitoring_enabled"] = True
    s["auto_scan"] = True
    s["auto_send"] = True
    s["process_current_candidates_once"] = True
    save_settings(s)

    if reset_catchup or (changed and not previous):
        reset_backlog_once()

    return detected



def desired_indeed_url(settings=None):
    s = settings or load_settings()
    raw = (
        (s.get("indeed_candidates_url") or "").strip()
        or (s.get("indeed_start_url") or "").strip()
        or "https://employers.indeed.com/candidates?statusName=New&tab=manage"
    )
    return new_candidates_queue_url(raw)


def automatic_connection_tick(force_connect=False):
    """
    One iteration of the hands-free connection manager.

    - reconnects to the already-running normal Chrome automatically
    - reuses the same approved CDP session
    - opens the saved/direct Indeed Candidates URL when no Indeed tab exists
    - waits for a one-time Indeed sign-in when required
    - activates Candidates/monitoring automatically after sign-in

    Chrome's own remote-debugging approval may still be required once for a
    newly started browser instance. Failed connection attempts are throttled so
    Chrome is not spammed with repeated permission dialogs.
    """
    now_mono = time.monotonic()
    s = load_settings()

    if not s.get("automation_enabled", True):
        _set_auto_connection_state(
            phase="paused",
            message="Automation is turned off",
            last_error=None,
        )
        return auto_connection_snapshot()

    if not s.get("auto_connect_chrome", True):
        _set_auto_connection_state(
            phase="disabled",
            message="Automatic browser connection is disabled",
        )
        return auto_connection_snapshot()

    current = chrome_status()

    if not current.get("ok"):
        snapshot = auto_connection_snapshot()
        retry_at = float(snapshot.get("next_connect_attempt_at") or 0.0)

        if not force_connect and now_mono < retry_at:
            return snapshot

        _set_auto_connection_state(
            phase="connecting",
            message="Connecting to your existing Chrome automatically",
            last_error=None,
        )

        connected = chrome_status(connect_if_needed=True)
        if not connected.get("ok"):
            err = str(connected.get("error") or "Chrome connection is not ready")
            phase = "permission_required" if (
                "Remote Debugging" in err
                or "click Allow" in err
                or "WebSocket" in err
            ) else "reconnecting"

            _set_auto_connection_state(
                phase=phase,
                message=(
                    "Chrome approval is required once for this browser instance"
                    if phase == "permission_required"
                    else "Chrome is unavailable; automatic reconnect will retry"
                ),
                last_error=err,
                next_connect_attempt_at=now_mono + AUTO_CONNECT_RETRY_SECONDS,
            )
            return auto_connection_snapshot()

        current = connected
        _set_auto_connection_state(
            phase="connected",
            message="Chrome connected automatically",
            last_error=None,
            last_connected_at=time.time(),
            next_connect_attempt_at=now_mono + AUTO_READY_RETRY_SECONDS,
        )
        log("INFO", "AUTO CONNECTION: Chrome connected automatically.")

    # We have an approved Chrome connection. Locate or open Indeed.
    try:
        detected = bind_candidates_page_if_available(reset_catchup=False)
        _set_auto_connection_state(
            phase="ready",
            message="Indeed Candidates is active",
            last_error=None,
            last_candidates_at=time.time(),
            next_connect_attempt_at=now_mono + AUTO_READY_RETRY_SECONDS,
        )
        return auto_connection_snapshot()
    except Exception as e:
        err = str(e)

    current = chrome_status()
    if current.get("ok"):
        if not current.get("indeed_found") and s.get("auto_open_indeed", True):
            snapshot = auto_connection_snapshot()
            next_open = float(snapshot.get("next_indeed_open_at") or 0.0)

            if now_mono >= next_open:
                try:
                    open_indeed_in_existing_chrome(desired_indeed_url(s))
                    _set_auto_connection_state(
                        phase="opening_indeed",
                        message="Opening Indeed Candidates automatically",
                        last_error=None,
                        next_indeed_open_at=now_mono + AUTO_OPEN_RETRY_SECONDS,
                    )
                    log("INFO", "AUTO CONNECTION: Opened Indeed Candidates in existing Chrome.")
                    return auto_connection_snapshot()
                except Exception as open_error:
                    err = str(open_error)

        # If an Indeed page exists but is a login page, do not keep opening tabs.
        _set_auto_connection_state(
            phase="waiting_for_sign_in",
            message=(
                "Waiting for Indeed sign-in; Candidates will open automatically afterwards"
            ),
            last_error=err,
            next_connect_attempt_at=now_mono + AUTO_READY_RETRY_SECONDS,
        )
        return auto_connection_snapshot()

    _set_auto_connection_state(
        phase="reconnecting",
        message="Chrome connection was lost; reconnecting automatically",
        last_error=err,
        next_connect_attempt_at=now_mono + 10,
    )
    return auto_connection_snapshot()


def auto_connection_loop():
    stop_event.wait(1.5)

    while not stop_event.is_set():
        try:
            automatic_connection_tick()
        except Exception as e:
            _set_auto_connection_state(
                phase="error",
                message="Automatic browser connection encountered an error",
                last_error=str(e),
                next_connect_attempt_at=time.monotonic() + AUTO_CONNECT_RETRY_SECONDS,
            )
            log("WARN", "AUTO CONNECTION: " + str(e))

        stop_event.wait(AUTO_READY_RETRY_SECONDS)


def execute_monitor_check(fast_only=False):
    with scan_lock:
        scan_started = datetime.now(timezone.utc).isoformat()
        set_state("live_monitor_heartbeat_at", scan_started)
        set_state(
            "live_monitor_scan_mode",
            "fast_new_watch" if fast_only else "full_reconcile",
        )

        data = scan_existing_chrome(fast_only=fast_only)
        result = process_indeed_results(data.get("results", []))

        # New verified applications keep the existing email acknowledgement and
        # also queue the WhatsApp acknowledgement for the same application.
        try:
            wa_queued = queue_live_candidate_outreach(data.get("results", []))
            if wa_queued:
                pipeline_wake_event.set()
        except Exception as exc:
            log("WARN", f"Live WhatsApp acknowledgement queue waiting: {exc}")

        # Mail delivery has priority over ranking.
        if result.get("ready", 0):
            outbox_wake_event.set()

        try:
            sync_job_descriptions_from_scan(data.get("results", []))
        except Exception as exc:
            log("WARN", f"Role description sync waiting: {exc}")

        if data.get("results"):
            role_review_wake_event.set()

        # Any attempted applicant now has an application record and is considered
        # processed for the one-time New queue. NEEDS_REVIEW rows are retried later
        # by the normal retry rule.
        for item in data.get("results", []):
            source_key = item.get("source_key")
            if source_key:
                mark_seen_processed(source_key)

        # Candidate monitoring never sends mail in V11.1.
        # The dedicated live_outbox_loop is the single delivery owner.
        pending_initial = pending_initial_new_count()

        # Complete the one-time New catch-up only after:
        #   1) the collector reached the bottom/current end of the list, and
        #   2) every discovered initial-New identity has had one processing attempt.
        if (
            not fast_only
            and data.get("catch_up_pending")
            and data.get("collection_complete")
            and pending_initial == 0
        ):
            mark_backlog_completed()

        success_at = datetime.now(timezone.utc).isoformat()
        set_state("live_monitor_heartbeat_at", success_at)
        set_state("live_monitor_last_success_at", success_at)
        set_state("live_monitor_last_error", "")
        set_state("live_monitor_consecutive_failures", "0")
        set_state(
            "live_monitor_last_found_links",
            str(data.get("found_links", 0)),
        )

        new_rows = [
            row
            for row in data.get("results", [])
            if row.get("new_applicant")
        ]

        if new_rows:
            newest = new_rows[0]
            set_state(
                "live_monitor_last_new_candidate",
                newest.get("candidate_name") or "Candidate",
            )
            set_state(
                "live_monitor_last_new_candidate_at",
                success_at,
            )
            set_state(
                "live_monitor_last_new_role",
                newest.get("job_title") or "",
            )

        return {
            **data,
            "processed": result.get("processed", 0),
            "verified": result.get("ready", 0),
            "sent_now": 0,
            "pending_initial_new": pending_initial,
        }


def background_loop():
    """
    Live Today watcher with automatic Candidates-page recovery.

    Automation ON no longer depends on a previously-saved Candidates URL.
    If the binding is missing/stale, the worker reconnects Chrome, binds the
    current Indeed Candidates page, then performs a fresh scan.
    """
    scan_wake_event.wait(timeout=0.35)
    scan_wake_event.clear()

    did_initial_full = False
    next_full_reconcile = 0.0
    set_state("live_monitor_started_at", datetime.now(timezone.utc).isoformat())

    while not stop_event.is_set():
        heartbeat_at = datetime.now(timezone.utc).isoformat()
        set_state("live_monitor_heartbeat_at", heartbeat_at)

        settings = load_settings()
        user_enabled = monitoring_enabled_by_user(settings)
        set_state("live_monitor_enabled", "1" if user_enabled else "0")

        if user_enabled:
            try:
                if not monitoring_ready(settings):
                    set_state("live_monitor_scan_mode", "connecting_candidates")

                    try:
                        automatic_connection_tick(force_connect=False)
                    except Exception:
                        pass

                    settings = load_settings()

                    if not monitoring_ready(settings):
                        try:
                            bind_candidates_page_if_available(
                                reset_catchup=False
                            )
                        except Exception as bind_error:
                            set_state(
                                "live_monitor_last_error",
                                (
                                    "Waiting for live Indeed Candidates page: "
                                    + str(bind_error)
                                )[:1200],
                            )

                    settings = load_settings()

                if monitoring_ready(settings):
                    now_mono = time.monotonic()

                    do_full = (
                        not did_initial_full
                        or now_mono >= next_full_reconcile
                    )

                    result = execute_monitor_check(
                        fast_only=not do_full
                    )

                    if do_full:
                        did_initial_full = True
                        next_full_reconcile = (
                            time.monotonic() + FULL_RECONCILE_SECONDS
                        )

                    if (
                        result.get("new_candidates", 0)
                        or result.get("processed", 0)
                    ):
                        log(
                            "INFO",
                            (
                                "LIVE NEW APPLICANT CHECK: "
                                if result.get("fast_only")
                                else "FULL CANDIDATE RECONCILE: "
                            )
                            + f"visible={result.get('found_links',0)}, "
                            + f"new={result.get('new_candidates',0)}, "
                            + f"processed={result.get('processed',0)}"
                        )
                else:
                    set_state(
                        "live_monitor_scan_mode",
                        "waiting_for_candidates",
                    )

            except Exception as e:
                previous_failures = int(
                    get_state(
                        "live_monitor_consecutive_failures",
                        "0",
                    )
                    or 0
                )

                set_state(
                    "live_monitor_consecutive_failures",
                    str(previous_failures + 1),
                )
                set_state(
                    "live_monitor_last_error",
                    str(e)[:1200],
                )
                set_state(
                    "live_monitor_scan_mode",
                    "recovering",
                )

                _clear_stale_candidates_binding_if_needed(
                    e,
                    previous_failures + 1,
                )

                try:
                    automatic_connection_tick(force_connect=True)
                except Exception:
                    pass

                log(
                    "WARN",
                    "Live candidate monitoring recovering: " + str(e),
                )
        else:
            set_state("live_monitor_scan_mode", "off")

        scan_wake_event.wait(timeout=LIVE_CANDIDATE_IDLE_SECONDS)
        scan_wake_event.clear()


def live_outbox_loop():
    """
    Independent mail reconciliation worker.

    Every verified candidate whose acknowledgement is not SENT remains in the
    outbox. The worker continuously retries pending mail while Gmail is healthy.

    If Gmail becomes unavailable/rejects authentication, it periodically
    re-verifies the saved App Password and resumes the outbox automatically
    when service recovers.
    """
    outbox_wake_event.wait(timeout=0.35)
    outbox_wake_event.clear()

    next_health_check = 0.0

    while not stop_event.is_set():
        settings = load_settings()

        if (
            settings.get("automation_enabled", True)
            and settings.get("auto_send", True)
            and sender_configured()
        ):
            verified = get_state("smtp_verified", "0") == "1"

            if not verified and time.monotonic() >= next_health_check:
                check = smtp_health_check(settings)
                verified = bool(check.get("ok"))
                next_health_check = (
                    time.monotonic() + MAIL_REVERIFY_SECONDS
                )

                if verified:
                    log(
                        "INFO",
                        "LIVE OUTBOX: Gmail authentication recovered."
                    )

            if verified:
                try:
                    result = send_all_ready()

                    if (
                        result.get("attempted", 0)
                        or result.get("sent", 0)
                    ):
                        log(
                            "INFO",
                            "LIVE OUTBOX: "
                            f"attempted={result.get('attempted',0)}, "
                            f"sent={result.get('sent',0)}, "
                            f"remaining={result.get('remaining',0)}"
                        )

                    correction_result = send_all_ready_corrections()

                    if (
                        correction_result.get("attempted", 0)
                        or correction_result.get("sent", 0)
                    ):
                        log(
                            "INFO",
                            "ONE-TIME CORRECTION OUTBOX: "
                            f"attempted={correction_result.get('attempted',0)}, "
                            f"sent={correction_result.get('sent',0)}, "
                            f"remaining={correction_result.get('remaining',0)}"
                        )

                except Exception as e:
                    log("WARN", f"Live outbox reconciliation failed: {e}")

        outbox_wake_event.wait(timeout=LIVE_OUTBOX_IDLE_SECONDS)
        outbox_wake_event.clear()



def role_review_loop():
    """Automatic evidence-ranking worker with new-applicant priority."""
    role_review_wake_event.wait(timeout=0.8)
    role_review_wake_event.clear()
    next_description_discovery = time.monotonic() + 8

    while not stop_event.is_set():
        try:
            settings = load_settings()
            if settings.get("automation_enabled", True):
                analyzed = analyze_pending_reviews(limit=80)
                if analyzed:
                    log("INFO", f"AUTO ROLE RANKING: processed {analyzed} waiting/new applicant(s).")

                now_mono = time.monotonic()
                if now_mono >= next_description_discovery:
                    acquired = scan_lock.acquire(blocking=False)
                    if acquired:
                        try:
                            discovered = discover_employer_job_descriptions(max_jobs=25)
                            lifecycle_changes = sync_discovered_jobs(discovered)
                            updated_roles = ingest_discovered_job_descriptions(discovered)
                            if lifecycle_changes:
                                log("INFO", "AUTO ROLE LIFECYCLE: synced " + ", ".join(lifecycle_changes[:25]))
                            if updated_roles:
                                log("INFO", "AUTO ROLE DESCRIPTIONS: synced " + ", ".join(updated_roles[:25]))
                                analyze_pending_reviews(limit=120)
                            if lifecycle_changes or updated_roles:
                                pipeline_wake_event.set()
                        except Exception as exc:
                            log("WARN", "Automatic Indeed role-description sync waiting: " + str(exc))
                        finally:
                            scan_lock.release()
                    next_description_discovery = time.monotonic() + ROLE_DESCRIPTION_DISCOVERY_SECONDS
        except Exception as exc:
            log("WARN", f"Automatic role ranking waiting: {exc}")

        role_review_wake_event.wait(timeout=ROLE_REVIEW_IDLE_SECONDS)
        role_review_wake_event.clear()


def whatsapp_auto_login_loop():
    """Keep WhatsApp Web ready in the same approved Chrome profile.

    If the account is not linked yet, the QR/login tab is opened automatically.
    After the user links once, future launches reuse the saved Chrome session.
    """
    stop_event.wait(2.0)
    last_open_attempt = 0.0

    while not stop_event.is_set():
        try:
            settings = load_settings()
            if settings.get("whatsapp_enabled", True) and settings.get("whatsapp_auto_connect", True):
                connection_phase = str(auto_connection_snapshot().get("phase") or "").lower()
                if connection_phase not in {"ready", "connected", "candidates_ready"}:
                    set_state("whatsapp_web_status", "WAITING_FOR_CHROME")
                else:
                    state = str(get_state("whatsapp_web_status", "NOT_OPEN") or "NOT_OPEN").upper()
                    now_mono = time.monotonic()
                    # Probe frequently when linked, but avoid spawning repeated QR tabs.
                    if state in {"NOT_OPEN", "LOGIN_REQUIRED", "LOGIN_TAB_OPEN", "LOADING", "WAITING_FOR_CHROME", ""} or now_mono - last_open_attempt > 60:
                        result = ensure_whatsapp_web_ready()
                        status = str(result.get("status") or "LOADING").upper()
                        set_state("whatsapp_web_status", status)
                        last_open_attempt = now_mono
                        if status == "CONNECTED":
                            pipeline_wake_event.set()
        except Exception as exc:
            # Chrome may still be reconnecting; this is expected during startup.
            set_state("whatsapp_web_status", "WAITING_FOR_CHROME")
            try:
                log("WARN", f"WhatsApp auto-connect waiting: {exc}")
            except Exception:
                pass

        stop_event.wait(12)


def recruitment_pipeline_loop():
    """Delivery worker for candidate WhatsApp, HR interviews and one daily HR report."""
    pipeline_wake_event.wait(timeout=0.8)
    pipeline_wake_event.clear()
    next_daily_report_check = 0.0

    while not stop_event.is_set():
        try:
            settings = load_settings()
            if settings.get("automation_enabled", True):
                now_mono = time.monotonic()
                if now_mono >= next_daily_report_check:
                    daily = queue_daily_consolidated_report(force=False)
                    if daily.get("queued"):
                        log(
                            "INFO",
                            f"DAILY RECRUITMENT REPORT: queued one consolidated report for "
                            f"{daily.get('roles',0)} ongoing role(s).",
                        )
                    next_daily_report_check = now_mono + 30

                result = process_notifications_once(limit=16)
                if result.get("sent"):
                    log("INFO", f"RECRUITMENT PIPELINE: delivered {result.get('sent')} notification(s).")
        except Exception as exc:
            log("WARN", f"Recruitment pipeline waiting: {exc}")

        pipeline_wake_event.wait(timeout=1)
        pipeline_wake_event.clear()


def public_settings():
    s = load_settings()
    return {
        "company_name": s.get("company_name", ""),
        "company_email": "nuneslead@gmail.com",
        "smtp_app_password_set": bool(
            (s.get("smtp_app_password") or "").strip()
        ),
        "indeed_start_url": s.get("indeed_start_url", ""),
        "indeed_candidates_url": s.get("indeed_candidates_url", ""),
        "automation_enabled": bool(s.get("automation_enabled", True)),
        "monitoring_enabled": bool(s.get("monitoring_enabled", True)),
        "auto_scan": bool(s.get("auto_scan", True)),
        "auto_send": bool(s.get("auto_send", True)),
        "allow_candidate_page_email_fallback": bool(
            s.get("allow_candidate_page_email_fallback", False)
        ),
        "local_ai_enabled": bool(s.get("local_ai_enabled", False)),
        "ollama_url": s.get("ollama_url", ""),
        "ollama_model": s.get("ollama_model", ""),
        "openai_ranking_enabled": bool(s.get("openai_ranking_enabled", True)),
        "openai_ranking_model": s.get("openai_ranking_model", "gpt-5.6-luna"),
        "openai_ranking_min_local_score": float(s.get("openai_ranking_min_local_score", 45.0)),
        "openai_ranking_max_local_score": float(s.get("openai_ranking_max_local_score", 90.0)),
        "openai_ranking_ai_weight": float(s.get("openai_ranking_ai_weight", 0.12)),
        "openai_ranking_daily_call_limit": int(s.get("openai_ranking_daily_call_limit", 40)),
        "openai_ranking_key_configured": bool(ranking_key_status().get("configured")),
        "openai_ranking_key_storage": ranking_key_status().get("storage"),
        "subject_template": s.get("subject_template", ""),
        "body_template": s.get("body_template", ""),
        "whatsapp_ack_template": s.get("whatsapp_ack_template", ""),
        "interview_subject_template": s.get("interview_subject_template", ""),
        "interview_body_template": s.get("interview_body_template", ""),
        "whatsapp_interview_template": s.get("whatsapp_interview_template", ""),
        "hr_report_sender_email": "nunescbe@gmail.com",
        "hr_report_recipient": s.get("hr_report_recipient", "nunescbe@gmail.com"),
        "hr_report_smtp_app_password_set": bool(
            (s.get("hr_report_smtp_app_password") or "").strip()
        ),
        "auto_send_role_reports": False,
        "daily_consolidated_report_enabled": bool(s.get("daily_consolidated_report_enabled", True)),
        "daily_report_time": s.get("daily_report_time", "19:00"),
        "whatsapp_enabled": True,
        "whatsapp_auto_connect": bool(s.get("whatsapp_auto_connect", True)),
        "whatsapp_default_country_code": s.get("whatsapp_default_country_code", "91"),
    }


def cached_dashboard_chrome_status():
    """
    Fast, non-blocking browser status for UI requests.

    The background connection manager owns Chrome/CDP work. Dashboard HTTP
    requests must never attach/evaluate an Indeed page because that can keep the
    browser on the loading screen for several seconds.
    """
    state = auto_connection_snapshot()
    phase = (state.get("phase") or "").lower()
    connected = phase in {"ready", "candidates_ready", "connected"}
    settings = load_settings()
    candidate_url = (settings.get("indeed_candidates_url") or "").strip()

    return {
        "ok": connected,
        "chrome_connected": connected,
        "indeed_found": bool(connected and candidate_url),
        "url": candidate_url if connected else "",
        "title": "Indeed Candidates" if connected and candidate_url else "",
        "message": state.get("message") or (
            "CHROME CONNECTED" if connected else "CHROME CONNECTING"
        ),
        "error": state.get("last_error"),
        "cached": True,
    }


def dashboard_payload(lite=False):
    """
    Resilient overview payload.

    One broken subsystem must not turn the whole dashboard into HTTP 500 or
    make saved candidates/settings appear to be missing.
    """
    errors = []

    def safe(label, fn, default):
        try:
            return fn()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            try:
                log("ERROR", f"Dashboard {label} failed: {exc}")
            except Exception:
                pass
            return default

    settings = safe(
        "settings",
        public_settings,
        {
            "company_name": "Nunes",
            "company_email": "nuneslead@gmail.com",
            "smtp_app_password_set": False,
            "indeed_start_url": "",
            "indeed_candidates_url": "",
            "automation_enabled": False,
            "monitoring_enabled": True,
            "auto_scan": True,
            "auto_send": True,
            "allow_candidate_page_email_fallback": True,
            "local_ai_enabled": False,
            "ollama_url": "",
            "ollama_model": "",
            "subject_template": "Thank you for applying – {job_title}",
            "body_template": "",
            "hr_report_sender_email": "nunescbe@gmail.com",
            "hr_report_recipient": "nunescbe@gmail.com",
            "hr_report_smtp_app_password_set": False,
            "auto_send_role_reports": False,
            "daily_consolidated_report_enabled": True,
            "daily_report_time": "19:00",
            "whatsapp_enabled": True,
            "whatsapp_default_country_code": "91",
        },
    )

    empty_stats = {
        "total": 0,
        "ready": 0,
        "sent": 0,
        "review": 0,
        "skipped": 0,
        "application_verified": 0,
        "last_scan_at": None,
        "last_new_count": "0",
        "last_visible_count": "0",
        "backlog_completed": False,
        "seen_count": 0,
        "pending_initial_new": 0,
    }
    st = safe("database statistics", stats, empty_stats)
    chrome = safe("browser status", cached_dashboard_chrome_status, {
        "ok": False,
        "chrome_connected": False,
        "indeed_found": False,
        "message": "Browser status is starting",
        "cached": True,
    })
    auto_state = safe("automatic connection", auto_connection_snapshot, {
        "phase": "starting",
        "message": "Starting automatic browser connection",
    })
    live = safe("live detection", live_detection_snapshot, {
        "healthy": False,
        "status": "STARTING",
        "consecutive_failures": 0,
        "last_found_links": 0,
        "new_last_scan": 0,
        "detected_today": 0,
        "verified_today": 0,
        "review_today": 0,
        "sent_today": 0,
        "skipped_today": 0,
        "seen_today": 0,
        "last_seen_today_applicant": None,
    })

    role_rows = []
    role_status = {}
    role_error = None
    if not lite:
        try:
            role_status = ranking_status()
            role_rows = list_roles()
        except Exception as exc:
            role_error = str(exc)
            errors.append(f"role ranking: {exc}")

    detected = None
    if chrome.get("indeed_found"):
        detected = {
            "url": settings.get("indeed_candidates_url", ""),
            "title": "Indeed Candidates",
        }

    configured = safe("email configuration", sender_configured, False)
    smtp_verified = safe(
        "email verification state",
        lambda: get_state("smtp_verified", "0") == "1",
        False,
    )

    payload = {
        "ok": len(errors) == 0,
        "version": APP_VERSION,
        "settings": settings,
        "stats": st,
        "chrome": chrome,
        "detected_candidates_page": detected,
        "detected_candidates_error": None,
        "email_configured": configured,
        "email_verified": bool(configured and smtp_verified),
        "email_last_error": safe(
            "email error state", lambda: get_state("smtp_last_error"), None
        ),
        "email_transport": safe(
            "email transport state", lambda: get_state("smtp_transport"), None
        ),
        "automation_enabled": bool(settings.get("automation_enabled", True)),
        "live_detection": live,
        "auto_connection": auto_state,
        "applicants": [],
        "sent_responses": [],
        "logs": [],
        "role_review_roles": role_rows,
        "role_ranking_status": role_status,
        "role_ranking_error": role_error,
        "recruitment_pipeline": safe("recruitment pipeline", pipeline_status, {}),
        "backend_health": {
            "schema": runtime_schema_status(),
            "database": safe("database health", database_health, {"ok": False}),
            "settings_saved_at": safe(
                "settings saved state", lambda: get_state("settings_saved_at"), None
            ),
            "errors": errors,
        },
    }

    if not lite:
        payload["applicants"] = safe(
            "applicant list", lambda: list_applications(500), []
        )
        payload["sent_responses"] = safe(
            "sent history", lambda: list_sent_responses(1000), []
        )
        payload["logs"] = safe("activity list", lambda: list_logs(100), [])

    return payload


@app.get("/")
def root():
    return jsonify({
        "ok": True,
        "name": "Nunes Recruitment Console API",
        "version": APP_VERSION,
        "port": API_PORT,
    })


@app.get("/version")
def version():
    return jsonify({
        "version": APP_VERSION,
        "port": API_PORT,
        "ui_port": 5285,
    })


@app.get("/api/role-ranking-status")
def api_role_ranking_status():
    try:
        return jsonify({"role_ranking_status": ranking_status()})
    except Exception as exc:
        return jsonify({
            "role_ranking_status": {},
            "message": str(exc),
        }), 200


@app.get("/api/applicants")
def api_applicants():
    try:
        limit = min(max(int(request.args.get("limit", 500)), 1), 1500)
    except Exception:
        limit = 500
    return jsonify({"applicants": list_applications(limit)})


@app.get("/api/sent-responses")
def api_sent_responses():
    try:
        limit = min(max(int(request.args.get("limit", 1000)), 1), 2500)
    except Exception:
        limit = 1000
    return jsonify({"sent_responses": list_sent_responses(limit)})


@app.get("/api/activity")
def api_activity():
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    except Exception:
        limit = 100
    return jsonify({"logs": list_logs(limit)})


@app.get("/api/dashboard")
def api_dashboard():
    try:
        lite = str(request.args.get("lite", "0")).lower() in {"1", "true", "yes"}
        return jsonify(dashboard_payload(lite=lite))
    except Exception as exc:
        try:
            log("ERROR", f"Dashboard payload failed: {exc}")
        except Exception:
            pass
        # Keep the frontend usable while a schema/component repairs itself.
        return jsonify({
            "ok": False,
            "message": "Dashboard backend is recovering.",
            "detail": str(exc),
            "version": APP_VERSION,
            "settings": public_settings(),
            "stats": {"total": 0, "ready": 0, "sent": 0, "review": 0, "skipped": 0},
            "backend_health": {"schema": runtime_schema_status(), "errors": [str(exc)]},
        }), 200


@app.post("/api/connect")
def api_connect():
    state = automatic_connection_tick(force_connect=True)

    if state.get("phase") == "ready":
        return jsonify({
            "ok": True,
            "message": "Chrome and Indeed Candidates are connected.",
            "auto_connection": state,
        })

    return jsonify({
        "ok": state.get("phase") not in {"error", "permission_required"},
        "message": state.get("message") or "Automatic connection is running.",
        "detail": state.get("last_error"),
        "auto_connection": state,
    }), (409 if state.get("phase") == "permission_required" else 200)


@app.post("/api/open-indeed")
def api_open_indeed():
    try:
        result = open_indeed_in_existing_chrome(
            load_settings().get("indeed_start_url")
        )
        return jsonify({
            "ok": True,
            "message": "Indeed opened in your existing Chrome.",
            "page": result,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "message": str(e),
        }), 500


@app.post("/api/activate")
def api_activate():
    try:
        detected = bind_candidates_page_if_available(reset_catchup=True)
        return jsonify({
            "ok": True,
            "message": "Candidates detected. Monitoring is active.",
            "page": detected,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "message": str(e),
        }), 409



@app.post("/api/automation/start")
def api_automation_start():
    s = load_settings()
    s["automation_enabled"] = True
    s["monitoring_enabled"] = True
    set_state("live_monitor_enabled", "1")
    set_state("live_monitor_last_error", "")
    set_state("live_monitor_consecutive_failures", "0")
    set_state("live_monitor_last_success_at", "")
    set_state("live_monitor_scan_mode", "starting")
    set_state(
        "live_monitor_started_at",
        datetime.now(timezone.utc).isoformat(),
    )
    set_state("live_monitor_last_found_links", "0")
    set_state("last_new_count", "0")
    s["auto_scan"] = True
    s["auto_send"] = True
    save_settings(s)

    # Immediate production wake-up. The background threads remain the owners
    # of scanning/sending, but they do not wait for their next timer tick.
    scan_wake_event.set()
    outbox_wake_event.set()
    role_review_wake_event.set()
    pipeline_wake_event.set()

    _set_auto_connection_state(
        phase="starting",
        message="Automation starting",
        last_error=None,
        next_connect_attempt_at=0.0,
        next_indeed_open_at=0.0,
    )

    # Return Start immediately. Chrome/Indeed can take time to render and must
    # never make the Start button return HTTP 500 or sit waiting.
    threading.Thread(
        target=_automation_connection_kick,
        daemon=True,
        name="automation-start-connection",
    ).start()

    return jsonify({
        "ok": True,
        "message": "Automation is ON.",
        "settings": public_settings(),
    })


@app.post("/api/automation/stop")
def api_automation_stop():
    # V11.11.2 is deliberately always-on while the local recruitment service
    # is running. STOP.bat remains the explicit full-service emergency stop.
    enforce_always_on_mode()
    scan_wake_event.set()
    outbox_wake_event.set()
    role_review_wake_event.set()
    pipeline_wake_event.set()
    return jsonify({
        "ok": False,
        "message": "24/7 Live mode is enabled. Use STOP.bat to stop the local recruitment service.",
        "settings": public_settings(),
    }), 409


@app.post("/api/check-now")
def api_check_now():
    if scan_lock.locked():
        return jsonify({
            "ok": False,
            "message": "A monitoring check is already running.",
        }), 409

    try:
        result = execute_monitor_check()
        return jsonify({
            "ok": True,
            "message": "Monitoring check completed.",
            "result": result,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "message": str(e),
        }), 409


@app.get("/api/settings")
def api_settings_get():
    try:
        cdp_diag = cdp_session_recovery_snapshot()
    except Exception as exc:
        cdp_diag = {"recoveries": 0, "last_recovery_at": None, "error": str(exc)}

    return jsonify({
        "ok": True,
        "settings": public_settings(),
        "email_configured": sender_configured(),
        "email_verified": get_state("smtp_verified", "0") == "1",
        "email_last_error": get_state("smtp_last_error"),
        "email_transport": get_state("smtp_transport"),
        "settings_saved_at": get_state("settings_saved_at"),
        "cdp_session_recovery": cdp_diag,
    })


def _verify_mail_after_settings_save():
    """Do SMTP/network work after Save has already returned to the UI."""
    try:
        current = load_settings()
        if not (current.get("smtp_app_password") or "").replace(" ", "").strip():
            set_state("smtp_verified", "0")
            return

        result = smtp_health_check(current)
        if result.get("ok"):
            try:
                send_all_ready()
            except Exception as exc:
                log("WARN", f"Pending mail retry after settings save: {exc}")
    except Exception as exc:
        try:
            log("WARN", f"Background Gmail verification after settings save: {exc}")
        except Exception:
            pass


@app.post("/api/settings")
def api_settings():
    payload = request.get_json(silent=True) or {}
    s = load_settings()

    allowed = [
        "company_name",
        "indeed_start_url",
        "monitoring_enabled",
        "auto_scan",
        "auto_send",
        "allow_candidate_page_email_fallback",
        "local_ai_enabled",
        "ollama_url",
        "ollama_model",
        "openai_ranking_enabled",
        "openai_ranking_model",
        "openai_ranking_min_local_score",
        "openai_ranking_max_local_score",
        "openai_ranking_ai_weight",
        "openai_ranking_daily_call_limit",
        "subject_template",
        "body_template",
        "whatsapp_ack_template",
        "interview_subject_template",
        "interview_body_template",
        "whatsapp_interview_template",
        "hr_report_recipient",
        "daily_consolidated_report_enabled",
        "daily_report_time",
        "whatsapp_enabled",
        "whatsapp_auto_connect",
        "whatsapp_default_country_code",
    ]

    for key in allowed:
        if key in payload:
            s[key] = payload[key]

    # V11.11.2 always-on policy. These cannot be disabled from the browser UI.
    s["automation_enabled"] = True
    s["monitoring_enabled"] = True
    s["auto_scan"] = True
    s["auto_send"] = True
    s["whatsapp_enabled"] = True
    s["whatsapp_auto_connect"] = True

    if "smtp_app_password" in payload:
        pw = str(payload.get("smtp_app_password") or "").replace(" ", "").strip()
        if pw:
            s["smtp_app_password"] = pw

    if "hr_report_smtp_app_password" in payload:
        hr_pw = str(payload.get("hr_report_smtp_app_password") or "").replace(" ", "").strip()
        if hr_pw:
            s["hr_report_smtp_app_password"] = hr_pw

    saved = save_settings(s)
    set_state("settings_saved_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    try:
        resume_config_waiting_notifications()
        pipeline_wake_event.set()
    except Exception as exc:
        log("WARN", f"Recruitment pipeline settings refresh waiting: {exc}")

    # Saving is local and immediate. Gmail verification can involve DNS/network
    # and must never make the Save button look broken or lose the just-saved UI.
    threading.Thread(
        target=_verify_mail_after_settings_save,
        daemon=True,
        name="settings-mail-verify",
    ).start()

    return jsonify({
        "ok": True,
        "message": (
            "Settings saved. Gmail verification is running in the background."
            if sender_configured()
            else "Settings saved. Add the Gmail App Password to enable sending."
        ),
        "settings": public_settings(),
        "email_configured": sender_configured(),
        "email_verified": get_state("smtp_verified", "0") == "1",
        "settings_saved_at": get_state("settings_saved_at"),
    })



@app.post("/api/mail/test")
def api_mail_test():
    result = smtp_health_check(load_settings())

    return jsonify({
        "ok": bool(result.get("ok")),
        "message": result.get("message"),
        "transport": result.get("transport"),
    }), (200 if result.get("ok") else 409)


@app.post("/api/mail/retry")
def api_mail_retry():
    check = smtp_health_check(load_settings())

    if not check.get("ok"):
        return jsonify({
            "ok": False,
            "message": check.get("message") or "Gmail authentication failed.",
            "mail_check": check,
        }), 409

    result = send_all_ready()

    return jsonify({
        "ok": True,
        "message": (
            f"Retry completed. {result.get('sent', 0)} message(s) sent "
            f"from {result.get('attempted', 0)} pending candidate(s)."
        ),
        "result": result,
    })


@app.post("/api/send/<int:app_id>")
def api_send(app_id):
    row = get_by_id(app_id)

    if not row:
        return jsonify({
            "ok": False,
            "message": "Applicant not found.",
        }), 404

    try:
        result = send_thank_you(row["source_key"])
        return jsonify({
            "ok": True,
            "message": f"Email result: {result['status']}.",
            "result": result,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "message": str(e),
        }), 500


@app.get("/resume/<int:app_id>")
def resume(app_id):
    row = get_by_id(app_id)

    if not row or not row.get("resume_path"):
        return jsonify({
            "ok": False,
            "message": "No resume is available.",
        }), 404

    p = Path(row["resume_path"])

    if not p.exists():
        return jsonify({
            "ok": False,
            "message": "Resume file is missing.",
        }), 404

    return send_from_directory(
        p.parent,
        p.name,
        as_attachment=False,
    )



@app.get("/api/role-review/roles")
def api_role_review_roles():
    try:
        return jsonify({"ok": True, "roles": decorate_roles(list_roles())})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.get("/api/role-review/role")
def api_role_review_role():
    title = str(request.args.get("job_title") or "").strip()
    if not title:
        return jsonify({"ok": False, "message": "job_title is required."}), 400
    try:
        return jsonify({"ok": True, **decorate_role_payload(role_review_payload(title))})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.post("/api/role-review/description")
def api_role_review_description():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("job_title") or "").strip()
    description = str(payload.get("job_description") or "")
    try:
        role = save_role_description(title, description)
        result = analyze_role(title, force=True, limit=800)
        return jsonify({
            "ok": True,
            "message": "Job description saved and resume evidence refreshed.",
            "role": role,
            "analysis": result,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.post("/api/role-review/analyze")
def api_role_review_analyze():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("job_title") or "").strip()
    if not title:
        return jsonify({"ok": False, "message": "job_title is required."}), 400
    try:
        result = analyze_role(title, force=True, limit=800)
        return jsonify({
            "ok": True,
            "message": f"Resume evidence refreshed for {result.get('analyzed', 0)} applicant(s).",
            "result": result,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.post("/api/role-review/candidate/<int:app_id>/status")
def api_role_review_candidate_status(app_id):
    return jsonify({
        "ok": False,
        "message": (
            "Manual shortlist is disabled in V11.10. "
            "Role ranking and shortlist are automatic."
        ),
    }), 410


@app.get("/api/ranking/openai/status")
def api_openai_ranking_status():
    s = load_settings()
    status = ranking_key_status()
    return jsonify({
        "ok": True,
        "configured": bool(status.get("configured")),
        "storage": status.get("storage"),
        "enabled": bool(s.get("openai_ranking_enabled", True)),
        "model": s.get("openai_ranking_model", "gpt-5.6-luna"),
        "daily_call_limit": int(s.get("openai_ranking_daily_call_limit", 40)),
        "calls_today": int(get_state(
            f"openai_ranking_calls:{datetime.now().astimezone().date().isoformat()}",
            "0",
        ) or 0),
        "policy": "keyword-first, low-cost semantic refinement only",
    })


@app.post("/api/ranking/openai/key")
def api_openai_ranking_key_save():
    payload = request.get_json(silent=True) or {}
    key = str(payload.get("api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "message": "API key is required."}), 400
    try:
        result = save_ranking_api_key(key)
        set_state("openai_ranking_key_saved_at", now())
        log("INFO", "OpenAI ranking key saved securely for ranking-only use.")
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.delete("/api/ranking/openai/key")
def api_openai_ranking_key_remove():
    try:
        result = remove_ranking_api_key()
        log("INFO", "OpenAI ranking key removed from local secure storage.")
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.get("/api/recruitment/status")
def api_recruitment_status():
    return jsonify({"ok": True, "pipeline": pipeline_status()})


@app.get("/api/recruitment/overview")
def api_recruitment_overview():
    try:
        role_limit = min(max(int(request.args.get("roles", 6)), 1), 25)
    except Exception:
        role_limit = 6
    try:
        recent_limit = min(max(int(request.args.get("recent", 8)), 1), 50)
    except Exception:
        recent_limit = 8

    try:
        return jsonify({
            "ok": True,
            **operations_overview(role_limit, recent_limit),
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "roles": [],
            "recent": [],
            "message": str(exc),
        }), 200


@app.post("/api/recruitment/approve/<int:app_id>")
def api_recruitment_approve(app_id):
    try:
        result = approve_candidate_for_interview(app_id)
        pipeline_wake_event.set()
        return jsonify({
            "ok": True,
            "message": f"HR approval saved. Interview email and WhatsApp are queued for {result.get('flow', {}).get('interview_date') or interview_schedule_preview().get('interview_date')}.",
            **result,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.post("/api/recruitment/approve-top")
def api_recruitment_approve_top():
    payload = request.get_json(silent=True) or {}
    try:
        result = approve_top_candidates_for_interview(
            payload.get("job_title"),
            payload.get("count"),
        )
        pipeline_wake_event.set()
        return jsonify({
            "ok": True,
            "message": (
                f"HR approved the top {result.get('selected_count', 0)} candidate(s). "
                f"Interview email + WhatsApp are queued for {result.get('interview_date')}."
            ),
            **result,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.get("/api/recruitment/interview-schedule")
def api_recruitment_interview_schedule():
    return jsonify({"ok": True, **interview_schedule_preview()})


@app.post("/api/recruitment/hr-mail/test")
def api_hr_report_mail_test():
    result = hr_report_mail_health_check()
    if result.get("ok"):
        resume_config_waiting_notifications()
        pipeline_wake_event.set()
        return jsonify(result)
    return jsonify(result), 409


@app.post("/api/recruitment/daily-report/send-now")
def api_daily_report_send_now():
    result = queue_daily_consolidated_report(force=True)
    if result.get("queued") or result.get("already_exists"):
        pipeline_wake_event.set()
    return jsonify({"ok": True, "result": result})


@app.post("/api/whatsapp/connect")
def api_whatsapp_connect():
    try:
        result = open_whatsapp_login_tab()
        pipeline_wake_event.set()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409


@app.errorhandler(Exception)
def json_error_handler(exc):
    if isinstance(exc, HTTPException):
        return jsonify({
            "ok": False,
            "message": exc.description or exc.name,
            "status": exc.code,
        }), exc.code

    detail = str(exc)
    try:
        stack = traceback.format_exc(limit=12)
        log("ERROR", f"Unhandled backend error: {detail}\n{stack}")
    except Exception:
        pass
    return jsonify({
        "ok": False,
        "message": "Internal backend error",
        "detail": detail,
    }), 500



@app.get("/api/self-test")
def api_self_test():
    result = critical_runtime_self_test()
    return jsonify(result), (200 if result.get("ok") else 500)


@app.get("/api/data-status")
def api_data_status():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "schema": runtime_schema_status(),
        "database": database_health(),
        "settings": {
            "company_name": public_settings().get("company_name"),
            "sender": public_settings().get("company_email"),
            "gmail_password_saved": public_settings().get("smtp_app_password_set"),
            "saved_at": get_state("settings_saved_at"),
        },
    })


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "stats": stats(),
        "chrome": chrome_status(),
        "auto_connection": auto_connection_snapshot(),
        "devtools_active_port_file": str(devtools_active_port_path()),
    })


if __name__ == "__main__":
    enforce_always_on_mode()
    if not ensure_runtime_schema():
        print(
            "[ERROR] Persistent-data migration failed: "
            + str(_runtime_schema_error),
            flush=True,
        )
        raise RuntimeError(_runtime_schema_error or "Runtime schema initialization failed")

    startup_test = critical_runtime_self_test()
    if not startup_test.get("ok"):
        print(
            "[ERROR] Runtime self-test failed: "
            + "; ".join(startup_test.get("errors") or []),
            flush=True,
        )
        raise RuntimeError("Runtime self-test failed")

    log("INFO", "V11.11.4 always-on runtime self-test passed.")

    threading.Thread(
        target=auto_connection_loop,
        daemon=True,
        name="chrome-auto-connection",
    ).start()

    threading.Thread(
        target=background_loop,
        daemon=True,
        name="indeed-monitor",
    ).start()

    threading.Thread(
        target=live_outbox_loop,
        daemon=True,
        name="mail-outbox",
    ).start()

    threading.Thread(
        target=role_review_loop,
        daemon=True,
        name="role-review-evidence",
    ).start()

    threading.Thread(
        target=recruitment_pipeline_loop,
        daemon=True,
        name="recruitment-pipeline",
    ).start()

    threading.Thread(
        target=whatsapp_auto_login_loop,
        daemon=True,
        name="whatsapp-auto-login",
    ).start()

    app.run(
        host="127.0.0.1",
        port=API_PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
