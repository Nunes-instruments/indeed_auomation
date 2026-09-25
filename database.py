from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent

LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
if LOCALAPPDATA:
    STATE_DIR = Path(LOCALAPPDATA) / "NunesRecruitmentConsole"
else:
    STATE_DIR = BASE_DIR / "data" / "persistent"

STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / "automation.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT UNIQUE,
    profile_url TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    candidate_name TEXT,
    candidate_email TEXT,
    candidate_phone TEXT,
    job_title TEXT,
    indeed_status TEXT,
    resume_filename TEXT,
    resume_path TEXT,
    email_source TEXT,
    extraction_status TEXT,
    extraction_attempts INTEGER NOT NULL DEFAULT 0,
    application_verified INTEGER NOT NULL DEFAULT 0,
    application_checked_at TEXT,
    decision_reason TEXT,
    send_status TEXT,
    send_error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_candidate_email
ON applications(candidate_email);

CREATE INDEX IF NOT EXISTS idx_app_send_status
ON applications(send_status);

CREATE TABLE IF NOT EXISTS seen_candidates (
    source_key TEXT PRIMARY KEY,
    profile_url TEXT,
    label TEXT,
    initial_new INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_initial_new
ON seen_candidates(initial_new, processed);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT,
    message TEXT,
    created_at TEXT NOT NULL
);

-- Permanent response ledger.
-- recipient_email is unique by design: one email address receives one
-- acknowledgement only, even if Indeed presents duplicate rows or the person
-- applies again through another row.
CREATE TABLE IF NOT EXISTS sent_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    candidate_name TEXT,
    candidate_phone TEXT,
    job_title TEXT,
    subject TEXT,
    body TEXT,
    source_key TEXT,
    application_id INTEGER,
    smtp_transport TEXT,
    message_id TEXT,
    sent_at TEXT NOT NULL,
    history_source TEXT NOT NULL DEFAULT 'live_send',
    created_at TEXT NOT NULL,
    correction_required INTEGER NOT NULL DEFAULT 0,
    correction_status TEXT,
    correction_subject TEXT,
    correction_body TEXT,
    correction_sent_at TEXT,
    correction_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sent_responses_sent_at
ON sent_responses(sent_at);

CREATE INDEX IF NOT EXISTS idx_sent_responses_source_key
ON sent_responses(source_key);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _legacy_db_candidates():
    candidates = []

    # Local DB used by V11.0 and older builds.
    candidates.append(BASE_DIR / "data" / "automation.db")

    roots = [BASE_DIR.parent]
    home = Path.home()

    for extra in [home / "Desktop", home / "Downloads"]:
        if extra.exists():
            roots.append(extra)

    patterns = [
        "Nunes_Indeed_Recruitment_Console*/data/automation.db",
        "NUNES_V11*/data/automation.db",
    ]

    for root in roots:
        for pattern in patterns:
            try:
                for p in root.glob(pattern):
                    candidates.append(p)
            except Exception:
                pass

    unique = []
    seen = set()

    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p

        try:
            current = DB_PATH.resolve()
        except Exception:
            current = DB_PATH

        if rp == current:
            continue

        key = str(rp).lower()
        if key in seen or not p.exists():
            continue

        try:
            if p.stat().st_size <= 0:
                continue
        except Exception:
            continue

        seen.add(key)
        unique.append(p)

    unique.sort(
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return unique


def _bootstrap_database():
    if DB_PATH.exists():
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy the newest existing Recruitment Console DB once. This carries the
    # user's already-scanned applicants and SENT records into V11.1.
    for candidate in _legacy_db_candidates():
        try:
            shutil.copy2(candidate, DB_PATH)
            return
        except Exception:
            continue


_bootstrap_database()


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _backfill_sent_history(c):
    """
    Older versions stored SENT only in applications. Import those entries into
    the new permanent response ledger.

    Exact message text was not historically saved, so subject/body remain NULL
    for backfilled records; the UI labels them as historical.
    """
    rows = c.execute(
        """
        SELECT *
        FROM applications
        WHERE send_status='SENT'
          AND candidate_email IS NOT NULL
          AND trim(candidate_email)<>''
        ORDER BY
            CASE WHEN sent_at IS NULL THEN 1 ELSE 0 END,
            sent_at ASC,
            id ASC
        """
    ).fetchall()

    for r in rows:
        sent_at = r["sent_at"] or r["updated_at"] or r["created_at"] or now()

        c.execute(
            """
            INSERT OR IGNORE INTO sent_responses(
                recipient_email,
                candidate_name,
                candidate_phone,
                job_title,
                subject,
                body,
                source_key,
                application_id,
                smtp_transport,
                message_id,
                sent_at,
                history_source,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                (r["candidate_email"] or "").strip().lower(),
                r["candidate_name"],
                r["candidate_phone"],
                r["job_title"],
                None,
                None,
                r["source_key"],
                r["id"],
                None,
                None,
                sent_at,
                "backfilled_application",
                now(),
            ),
        )


def _recover_interrupted_sends(c):
    """
    Do NOT automatically resend an SMTP transaction whose final DB write may
    have been interrupted. That is safer than risking a duplicate email.
    """
    c.execute(
        """
        UPDATE applications
        SET send_status='SEND_UNCERTAIN',
            send_error=COALESCE(
                send_error,
                'Previous send was interrupted; automatic resend blocked to prevent duplicates.'
            ),
            updated_at=?
        WHERE send_status='SENDING'
        """,
        (now(),),
    )



def _ensure_sent_response_correction_columns(c):
    cols = {r["name"] for r in c.execute("PRAGMA table_info(sent_responses)").fetchall()}
    additions = {
        "correction_required": "INTEGER NOT NULL DEFAULT 0",
        "correction_status": "TEXT",
        "correction_subject": "TEXT",
        "correction_body": "TEXT",
        "correction_sent_at": "TEXT",
        "correction_error": "TEXT",
    }
    for name, definition in additions.items():
        if name not in cols:
            c.execute(f"ALTER TABLE sent_responses ADD COLUMN {name} {definition}")


def _discover_historical_generic_sends(c):
    """
    Flag only recipients with concrete evidence that the older acknowledgement
    used a generic role. This is the one-time V11.4 correction migration.
    """
    generic_emails = set()

    for r in c.execute(
        "SELECT recipient_email, job_title, subject, body FROM sent_responses"
    ).fetchall():
        addr = (r["recipient_email"] or "").strip().lower()
        if not addr:
            continue
        job = (r["job_title"] or "").strip().lower()
        subject = (r["subject"] or "").lower()
        body = (r["body"] or "").lower()
        if (
            job in {"", "the position", "position", "job", "the job", "unknown"}
            or "the position" in subject
            or "the position" in body
        ):
            generic_emails.add(addr)

    generic_log_re = re.compile(
        r"(?i)thank-you\s+sent(?:\s+once)?\s+to\s+"
        r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})"
        r"\s+for\s+(?:the\s+position|position)\b"
    )

    for row in c.execute(
        "SELECT message FROM logs WHERE lower(message) LIKE '%thank-you sent%'"
    ).fetchall():
        m = generic_log_re.search(row["message"] or "")
        if m:
            generic_emails.add(m.group(1).lower())

    for addr in generic_emails:
        sent = c.execute(
            """
            SELECT id, job_title, correction_status, correction_sent_at
            FROM sent_responses
            WHERE lower(recipient_email)=?
            LIMIT 1
            """,
            (addr,),
        ).fetchone()
        if not sent or sent["correction_sent_at"]:
            continue
        if (sent["correction_status"] or "").upper() in {
            "SENT", "SEND_UNCERTAIN"
        }:
            continue

        role_ready = (sent["job_title"] or "").strip().lower() not in {
            "", "the position", "position", "job", "the job", "unknown"
        }

        c.execute(
            """
            UPDATE sent_responses
            SET correction_required=1,
                correction_status=CASE
                    WHEN correction_status='SENT' THEN 'SENT'
                    WHEN ? THEN 'PENDING'
                    ELSE 'WAITING_FOR_ROLE'
                END,
                correction_error=NULL
            WHERE id=?
            """,
            (1 if role_ready else 0, sent["id"]),
        )



def _recover_interrupted_corrections(c):
    """
    If SMTP may have accepted a correction before the local DB commit, never
    auto-resend it. Mark uncertain for manual review instead of risking a
    third/duplicate message.
    """
    c.execute(
        """
        UPDATE sent_responses
        SET correction_status='SEND_UNCERTAIN',
            correction_error=COALESCE(
                correction_error,
                'Correction send was interrupted; automatic resend blocked to prevent duplicates.'
            )
        WHERE correction_status='SENDING'
          AND correction_sent_at IS NULL
        """
    )



def _backup_database_once():
    """Create one safety copy before V11.10.2 migrations touch persistent data."""
    try:
        if not DB_PATH.exists() or DB_PATH.stat().st_size <= 0:
            return None

        backup_dir = STATE_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "automation_pre_v11_10_2.db"

        if not backup.exists():
            shutil.copy2(DB_PATH, backup)

        return str(backup)
    except Exception:
        return None


def _safe_int_state(key, default=0):
    value = get_state(key, str(default))
    try:
        return int(value)
    except Exception:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return int(default)


def database_health():
    """Small non-destructive status payload used by the production dashboard."""
    result = {
        "ok": True,
        "path": str(DB_PATH),
        "exists": DB_PATH.exists(),
        "size_bytes": 0,
        "applications": 0,
        "sent_responses": 0,
        "logs": 0,
        "error": None,
    }

    try:
        if DB_PATH.exists():
            result["size_bytes"] = DB_PATH.stat().st_size

        with conn() as c:
            result["applications"] = int(
                c.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            )
            result["sent_responses"] = int(
                c.execute("SELECT COUNT(*) FROM sent_responses").fetchone()[0]
            )
            result["logs"] = int(
                c.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            )
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)

    return result

def init_db():
    _backup_database_once()
    with conn() as c:
        c.executescript(SCHEMA)

        cols = {
            r["name"]
            for r in c.execute("PRAGMA table_info(applications)").fetchall()
        }
        if "extraction_attempts" not in cols:
            c.execute(
                "ALTER TABLE applications "
                "ADD COLUMN extraction_attempts INTEGER NOT NULL DEFAULT 0"
            )

        if "application_verified" not in cols:
            c.execute(
                "ALTER TABLE applications "
                "ADD COLUMN application_verified INTEGER NOT NULL DEFAULT 0"
            )

        if "application_checked_at" not in cols:
            c.execute(
                "ALTER TABLE applications "
                "ADD COLUMN application_checked_at TEXT"
            )

        if "decision_reason" not in cols:
            c.execute(
                "ALTER TABLE applications "
                "ADD COLUMN decision_reason TEXT"
            )

        if "indeed_status" not in cols:
            c.execute(
                "ALTER TABLE applications "
                "ADD COLUMN indeed_status TEXT"
            )

        _recover_interrupted_sends(c)
        _backfill_sent_history(c)
        _ensure_sent_response_correction_columns(c)
        _recover_interrupted_corrections(c)
        _discover_historical_generic_sends(c)


def log(level, message):
    with conn() as c:
        c.execute(
            "INSERT INTO logs(level,message,created_at) VALUES (?,?,?)",
            (level, str(message)[:4000], now()),
        )


def set_state(key, value):
    with conn() as c:
        c.execute(
            """
            INSERT INTO system_state(key,value,updated_at)
            VALUES (?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, str(value), now()),
        )


def get_state(key, default=None):
    with conn() as c:
        r = c.execute(
            "SELECT value FROM system_state WHERE key=?",
            (key,),
        ).fetchone()
        return r["value"] if r else default


def backlog_completed():
    return get_state("initial_backlog_completed", "0") == "1"


def mark_backlog_completed():
    set_state("initial_backlog_completed", "1")
    set_state("initial_backlog_completed_at", now())


def reset_backlog_once():
    set_state("initial_backlog_completed", "0")
    set_state("initial_backlog_completed_at", "")


def upsert_application(row):
    ts = now()
    keys = [
        "source_key","profile_url","first_seen_at","last_seen_at",
        "candidate_name","candidate_email","candidate_phone","job_title",
        "indeed_status","resume_filename","resume_path","email_source","extraction_status",
        "extraction_attempts","application_verified","application_checked_at",
        "decision_reason","send_status","send_error","sent_at"
    ]
    vals = [row.get(k) for k in keys]

    with conn() as c:
        c.execute(
            f"""
            INSERT INTO applications
            ({",".join(keys)},created_at,updated_at)
            VALUES ({",".join(["?"]*len(keys))},?,?)
            ON CONFLICT(source_key) DO UPDATE SET
                profile_url=COALESCE(
                    excluded.profile_url,
                    applications.profile_url
                ),
                last_seen_at=excluded.last_seen_at,
                candidate_name=COALESCE(
                    excluded.candidate_name,
                    applications.candidate_name
                ),
                candidate_email=COALESCE(
                    excluded.candidate_email,
                    applications.candidate_email
                ),
                candidate_phone=COALESCE(
                    excluded.candidate_phone,
                    applications.candidate_phone
                ),
                job_title=CASE
                    WHEN lower(trim(COALESCE(excluded.job_title,'')))
                         IN ('','the position','position')
                    THEN applications.job_title
                    ELSE excluded.job_title
                END,
                indeed_status=COALESCE(
                    NULLIF(trim(excluded.indeed_status),''),
                    applications.indeed_status
                ),
                resume_filename=COALESCE(
                    excluded.resume_filename,
                    applications.resume_filename
                ),
                resume_path=COALESCE(
                    excluded.resume_path,
                    applications.resume_path
                ),
                email_source=COALESCE(
                    excluded.email_source,
                    applications.email_source
                ),
                extraction_status=excluded.extraction_status,
                extraction_attempts=MAX(
                    applications.extraction_attempts,
                    excluded.extraction_attempts
                ),
                application_verified=MAX(
                    COALESCE(applications.application_verified,0),
                    COALESCE(excluded.application_verified,0)
                ),
                application_checked_at=COALESCE(
                    excluded.application_checked_at,
                    applications.application_checked_at
                ),
                decision_reason=CASE
                    WHEN applications.send_status='SENT'
                    THEN applications.decision_reason
                    ELSE COALESCE(
                        excluded.decision_reason,
                        applications.decision_reason
                    )
                END,
                send_status=CASE
                    WHEN applications.send_status='SENT' THEN 'SENT'
                    WHEN applications.send_status='DUPLICATE_SKIPPED'
                         THEN 'DUPLICATE_SKIPPED'
                    WHEN applications.send_status='SEND_UNCERTAIN'
                         THEN 'SEND_UNCERTAIN'
                    ELSE excluded.send_status
                END,
                send_error=CASE
                    WHEN applications.send_status IN (
                        'SENT','DUPLICATE_SKIPPED','SEND_UNCERTAIN'
                    )
                    THEN applications.send_error
                    ELSE excluded.send_error
                END,
                sent_at=COALESCE(
                    applications.sent_at,
                    excluded.sent_at
                ),
                updated_at=excluded.updated_at
            """,
            vals + [ts, ts],
        )



def touch_visible_applications(source_keys, seen_at=None):
    """
    Mark applications that are present in the CURRENT Indeed scan as seen now.
    first_seen_at is permanent history; last_seen_at is live-presence evidence.
    """
    keys = [
        str(value or "").strip()
        for value in (source_keys or [])
        if str(value or "").strip()
    ]

    if not keys:
        return 0

    ts = seen_at or now()
    touched = 0

    with conn() as c:
        for key in keys:
            cur = c.execute(
                """
                UPDATE applications
                SET last_seen_at=?
                WHERE source_key=?
                """,
                (ts, key),
            )
            touched += max(0, int(cur.rowcount or 0))

    return touched


def get_by_source_key(source_key):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM applications WHERE source_key=?",
            (source_key,),
        ).fetchone()
        return dict(r) if r else None


def get_by_id(app_id):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()
        return dict(r) if r else None


def list_applications(limit=500):
    with conn() as c:
        rows = c.execute(
            """
            SELECT *
            FROM applications
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_logs(limit=120):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_sent_responses(limit=1000):
    with conn() as c:
        rows = c.execute(
            """
            SELECT *
            FROM sent_responses
            ORDER BY datetime(sent_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def sent_response_for_email(candidate_email):
    if not candidate_email:
        return None

    with conn() as c:
        r = c.execute(
            """
            SELECT *
            FROM sent_responses
            WHERE lower(recipient_email)=lower(?)
            LIMIT 1
            """,
            (candidate_email.strip(),),
        ).fetchone()
        return dict(r) if r else None


def already_sent(candidate_email, job_title=None):
    """
    Global recipient protection.

    If this email address has already received the acknowledgement once, do not
    send it again for another duplicated Indeed row/job.
    """
    if not candidate_email:
        return False

    addr = candidate_email.strip().lower()

    with conn() as c:
        r = c.execute(
            """
            SELECT 1
            FROM sent_responses
            WHERE lower(recipient_email)=?
            LIMIT 1
            """,
            (addr,),
        ).fetchone()

        if r:
            return True

        # Compatibility protection for a legacy SENT application that has not
        # yet been backfilled for any reason.
        r = c.execute(
            """
            SELECT 1
            FROM applications
            WHERE lower(candidate_email)=?
              AND send_status='SENT'
            LIMIT 1
            """,
            (addr,),
        ).fetchone()
        return bool(r)



def list_ready_corrections(limit=None):
    sql = """
        SELECT *
        FROM sent_responses
        WHERE correction_required=1
          AND correction_status IN ('PENDING','FAILED')
          AND correction_sent_at IS NULL
          AND lower(trim(COALESCE(job_title,'')))
              NOT IN ('','the position','position','job','the job','unknown')
          AND lower(trim(COALESCE(candidate_name,'')))
              NOT IN (
                  '', 'candidate', 'download cv', 'download resume',
                  'core skills', 'resume', 'education', 'yes', 'no'
              )
        ORDER BY id ASC
    """
    with conn() as c:
        if limit is None:
            rows = c.execute(sql).fetchall()
        else:
            rows = c.execute(sql + " LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


def claim_correction_for_send(response_id):
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM sent_responses WHERE id=?", (int(response_id),)).fetchone()
        if not row:
            c.execute("ROLLBACK")
            return {"status": "missing", "response": None}
        data = dict(row)
        status = (data.get("correction_status") or "").upper()
        if data.get("correction_sent_at") or status == "SENT":
            c.execute("COMMIT")
            return {"status": "already_sent", "response": data}
        if status == "SENDING":
            c.execute("COMMIT")
            return {"status": "busy", "response": data}
        job = (data.get("job_title") or "").strip().lower()
        if job in {"", "the position", "position", "job", "the job", "unknown"}:
            c.execute("UPDATE sent_responses SET correction_status='WAITING_FOR_ROLE' WHERE id=?", (int(response_id),))
            c.execute("COMMIT")
            return {"status": "waiting_for_role", "response": data}
        if not int(data.get("correction_required") or 0):
            c.execute("COMMIT")
            return {"status": "not_required", "response": data}
        c.execute(
            "UPDATE sent_responses SET correction_status='SENDING', correction_error=NULL WHERE id=?",
            (int(response_id),),
        )
        c.execute("COMMIT")
        data["correction_status"] = "SENDING"
        return {"status": "claimed", "response": data}
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()


def release_correction_claim(response_id, error):
    with conn() as c:
        c.execute(
            """
            UPDATE sent_responses
            SET correction_status='FAILED', correction_error=?
            WHERE id=? AND correction_status='SENDING'
            """,
            (str(error)[:1000], int(response_id)),
        )


def finalize_correction_send(response_id, subject, body):
    with conn() as c:
        sent_at = now()
        c.execute(
            """
            UPDATE sent_responses
            SET correction_status='SENT',
                correction_subject=?,
                correction_body=?,
                correction_sent_at=?,
                correction_error=NULL
            WHERE id=? AND correction_required=1
            """,
            (subject, body, sent_at, int(response_id)),
        )
        return sent_at


def correction_stats():
    with conn() as c:
        pending = c.execute(
            """
            SELECT COUNT(*) FROM sent_responses
            WHERE correction_required=1
              AND correction_sent_at IS NULL
              AND correction_status IN ('PENDING','FAILED','WAITING_FOR_ROLE','SENDING')
            """
        ).fetchone()[0]
        sent = c.execute(
            """
            SELECT COUNT(*) FROM sent_responses
            WHERE correction_sent_at IS NOT NULL OR correction_status='SENT'
            """
        ).fetchone()[0]
        uncertain = c.execute(
            """
            SELECT COUNT(*) FROM sent_responses
            WHERE correction_required=1
              AND correction_status='SEND_UNCERTAIN'
            """
        ).fetchone()[0]
    return {"pending": pending, "sent": sent, "uncertain": uncertain}


def list_ready_to_send(limit=None):
    """
    Single-dispatch outbox.

    SENDING and SEND_UNCERTAIN are intentionally excluded. This avoids a second
    worker retrying a message while another worker owns it, and avoids an
    automatic duplicate after a process interruption.
    """
    sql = """
        SELECT *
        FROM applications
        WHERE candidate_email IS NOT NULL
          AND trim(candidate_email)<>''
          AND COALESCE(application_verified,0)=1
          AND extraction_status IN ('VERIFIED_EMAIL_AND_ROLE','VERIFIED_EMAIL_FOUND')
          AND email_source IN (
              'indeed_resume',
              'indeed_resume_ai_verified',
              'indeed_candidate_profile',
              'indeed_candidate_contact_verified'
          )
          AND lower(trim(COALESCE(job_title,'')))
              NOT IN ('','the position','position')
          AND lower(trim(COALESCE(candidate_name,'')))
              NOT IN (
                  '', 'candidate', 'download cv', 'download resume',
                  'core skills', 'resume', 'education', 'yes', 'no'
              )
          AND COALESCE(send_status,'') NOT IN (
              'SENT',
              'DUPLICATE_SKIPPED',
              'SENDING',
              'SEND_UNCERTAIN',
              'SKIPPED'
          )
        ORDER BY id ASC
    """

    with conn() as c:
        if limit is None:
            rows = c.execute(sql).fetchall()
        else:
            rows = c.execute(sql + " LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


def claim_application_for_send(source_key):
    """
    Atomic cross-thread send reservation.

    Only one thread can claim an application/email address. It also checks the
    permanent response history before SMTP begins.
    """
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row

    try:
        c.execute("BEGIN IMMEDIATE")

        row = c.execute(
            "SELECT * FROM applications WHERE source_key=?",
            (source_key,),
        ).fetchone()

        if not row:
            c.execute("ROLLBACK")
            return {"status": "missing", "application": None}

        app = dict(row)
        status = (app.get("send_status") or "").upper()

        if status == "SENT":
            c.execute("COMMIT")
            return {"status": "already_sent", "application": app}

        if status == "DUPLICATE_SKIPPED":
            c.execute("COMMIT")
            return {"status": "duplicate_skipped", "application": app}

        if status in {"SENDING", "SEND_UNCERTAIN"}:
            c.execute("COMMIT")
            return {"status": "busy_or_uncertain", "application": app}

        if not int(app.get("application_verified") or 0):
            c.execute("COMMIT")
            return {
                "status": "application_not_verified",
                "application": app,
            }

        if (app.get("extraction_status") or "") not in {
            "VERIFIED_EMAIL_AND_ROLE",
            "VERIFIED_EMAIL_FOUND",
        }:
            c.execute("COMMIT")
            return {
                "status": "details_not_verified",
                "application": app,
            }

        job = (app.get("job_title") or "").strip().lower()
        if job in {"", "the position", "position", "job", "the job", "unknown"}:
            c.execute("COMMIT")
            return {
                "status": "role_not_verified",
                "application": app,
            }

        addr = (app.get("candidate_email") or "").strip().lower()

        if not addr:
            c.execute("COMMIT")
            return {"status": "no_email", "application": app}

        # Permanent response ledger.
        sent = c.execute(
            """
            SELECT *
            FROM sent_responses
            WHERE lower(recipient_email)=?
            LIMIT 1
            """,
            (addr,),
        ).fetchone()

        # Also block while another duplicate application for the same recipient
        # is already in the act of sending.
        sending = c.execute(
            """
            SELECT source_key
            FROM applications
            WHERE lower(candidate_email)=?
              AND source_key<>?
              AND send_status='SENDING'
            LIMIT 1
            """,
            (addr, source_key),
        ).fetchone()

        legacy_sent = c.execute(
            """
            SELECT source_key
            FROM applications
            WHERE lower(candidate_email)=?
              AND source_key<>?
              AND send_status='SENT'
            LIMIT 1
            """,
            (addr, source_key),
        ).fetchone()

        if sent or legacy_sent:
            c.execute(
                """
                UPDATE applications
                SET send_status='DUPLICATE_SKIPPED',
                    send_error='Acknowledgement already sent to this email address',
                    decision_reason='Skipped: acknowledgement already sent to this email address',
                    updated_at=?
                WHERE source_key=?
                """,
                (now(), source_key),
            )
            c.execute("COMMIT")
            app["send_status"] = "DUPLICATE_SKIPPED"
            return {"status": "duplicate_skipped", "application": app}

        if sending:
            c.execute("COMMIT")
            return {"status": "busy", "application": app}

        c.execute(
            """
            UPDATE applications
            SET send_status='SENDING',
                send_error=NULL,
                updated_at=?
            WHERE source_key=?
            """,
            (now(), source_key),
        )

        c.execute("COMMIT")
        app["send_status"] = "SENDING"
        return {"status": "claimed", "application": app}

    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()


def release_send_claim(source_key, status, error=None):
    with conn() as c:
        c.execute(
            """
            UPDATE applications
            SET send_status=?,
                send_error=?,
                updated_at=?
            WHERE source_key=?
              AND send_status='SENDING'
            """,
            (status, error, now(), source_key),
        )


def finalize_sent_response(
    source_key,
    subject,
    body,
    smtp_transport=None,
    message_id=None,
):
    """
    Commit SENT + response ledger in one DB transaction.

    After one recipient is sent, every other application row with the same email
    is marked DUPLICATE_SKIPPED. This cleans up duplicate Indeed identities in
    the UI and guarantees no future resend to that email.
    """
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row

    try:
        c.execute("BEGIN IMMEDIATE")

        row = c.execute(
            "SELECT * FROM applications WHERE source_key=?",
            (source_key,),
        ).fetchone()

        if not row:
            raise RuntimeError("Applicant disappeared before send finalization.")

        app = dict(row)
        addr = (app.get("candidate_email") or "").strip().lower()

        if not addr:
            raise RuntimeError("Candidate email missing during send finalization.")

        sent_at = now()

        c.execute(
            """
            INSERT INTO sent_responses(
                recipient_email,
                candidate_name,
                candidate_phone,
                job_title,
                subject,
                body,
                source_key,
                application_id,
                smtp_transport,
                message_id,
                sent_at,
                history_source,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(recipient_email) DO NOTHING
            """,
            (
                addr,
                app.get("candidate_name"),
                app.get("candidate_phone"),
                app.get("job_title"),
                subject,
                body,
                source_key,
                app.get("id"),
                smtp_transport,
                message_id,
                sent_at,
                "live_send",
                sent_at,
            ),
        )

        c.execute(
            """
            UPDATE applications
            SET send_status='SENT',
                send_error=NULL,
                decision_reason='Sent successfully: verified application, email and applied role',
                sent_at=?,
                updated_at=?
            WHERE source_key=?
            """,
            (sent_at, sent_at, source_key),
        )

        c.execute(
            """
            UPDATE applications
            SET send_status='DUPLICATE_SKIPPED',
                send_error='Acknowledgement already sent to this email address',
                updated_at=?
            WHERE lower(candidate_email)=?
              AND source_key<>?
              AND send_status NOT IN ('SENT','SEND_UNCERTAIN')
            """,
            (sent_at, addr, source_key),
        )

        c.execute("COMMIT")

        return {
            "sent_at": sent_at,
            "recipient_email": addr,
        }

    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()



def repair_duplicate_email_metadata(
    candidate_email,
    job_title=None,
    candidate_name=None,
    candidate_phone=None,
):
    """
    Accuracy reconciliation for older/duplicate Indeed rows.

    When a later scan finds the real Applied-to role or better contact metadata,
    repair older rows for the SAME verified email address that still contain
    generic values such as "the position".
    """
    addr = (candidate_email or "").strip().lower()
    if not addr:
        return

    title = (job_title or "").strip()
    name = (candidate_name or "").strip()
    phone = (candidate_phone or "").strip()

    with conn() as c:
        if title and title.lower() not in {
            "the position",
            "position",
            "job",
            "the job",
            "unknown",
        }:
            c.execute(
                """
                UPDATE applications
                SET job_title=?,
                    updated_at=?
                WHERE lower(candidate_email)=?
                  AND lower(trim(COALESCE(job_title,'')))
                      IN ('','the position','position','job','the job','unknown')
                """,
                (title[:200], now(), addr),
            )

            # Historical rows can show corrected role metadata. We do not alter
            # the exact stored subject/body snapshot of messages already sent.
            c.execute(
                """
                UPDATE sent_responses
                SET job_title=?,
                    correction_status=CASE
                        WHEN correction_required=1
                         AND correction_sent_at IS NULL
                         AND correction_status<>'SENT'
                        THEN 'PENDING'
                        ELSE correction_status
                    END
                WHERE lower(recipient_email)=?
                  AND lower(trim(COALESCE(job_title,'')))
                      IN ('','the position','position','job','the job','unknown')
                """,
                (title[:200], addr),
            )

        if name:
            c.execute(
                """
                UPDATE applications
                SET candidate_name=CASE
                        WHEN candidate_name IS NULL
                          OR trim(candidate_name)=''
                          OR lower(trim(candidate_name)) IN (
                              'candidate','download cv','download resume'
                          )
                        THEN ?
                        ELSE candidate_name
                    END,
                    updated_at=?
                WHERE lower(candidate_email)=?
                """,
                (name[:120], now(), addr),
            )

        if name:
            c.execute(
                """
                UPDATE sent_responses
                SET candidate_name=CASE
                    WHEN candidate_name IS NULL
                      OR trim(candidate_name)=''
                      OR lower(trim(candidate_name)) IN ('candidate','download cv','download resume')
                    THEN ?
                    ELSE candidate_name
                END
                WHERE lower(recipient_email)=?
                """,
                (name[:120], addr),
            )

        if phone:
            c.execute(
                """
                UPDATE applications
                SET candidate_phone=COALESCE(NULLIF(candidate_phone,''), ?),
                    updated_at=?
                WHERE lower(candidate_email)=?
                """,
                (phone[:60], now(), addr),
            )


def update_send(source_key, status, error=None, sent_at=None):
    with conn() as c:
        c.execute(
            """
            UPDATE applications
            SET send_status=?,send_error=?,sent_at=?,updated_at=?
            WHERE source_key=?
            """,
            (status, error, sent_at, now(), source_key),
        )


def remember_seen_candidate(
    source_key,
    profile_url=None,
    label=None,
    initial_new=False,
):
    ts = now()
    with conn() as c:
        c.execute(
            """
            INSERT INTO seen_candidates(
                source_key,
                profile_url,
                label,
                initial_new,
                processed,
                first_seen_at,
                updated_at
            )
            VALUES (?,?,?,?,0,?,?)
            ON CONFLICT(source_key) DO UPDATE SET
                profile_url=COALESCE(excluded.profile_url, seen_candidates.profile_url),
                label=COALESCE(excluded.label, seen_candidates.label),
                initial_new=CASE
                    WHEN seen_candidates.initial_new=1 THEN 1
                    ELSE excluded.initial_new
                END,
                updated_at=excluded.updated_at
            """,
            (
                source_key,
                profile_url,
                label,
                1 if initial_new else 0,
                ts,
                ts,
            ),
        )


def is_seen_candidate(source_key):
    with conn() as c:
        r = c.execute(
            "SELECT 1 FROM seen_candidates WHERE source_key=?",
            (source_key,),
        ).fetchone()
        return bool(r)


def seen_candidate(source_key):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM seen_candidates WHERE source_key=?",
            (source_key,),
        ).fetchone()
        return dict(r) if r else None


def mark_seen_processed(source_key):
    with conn() as c:
        c.execute(
            """
            UPDATE seen_candidates
            SET processed=1, updated_at=?
            WHERE source_key=?
            """,
            (now(), source_key),
        )


def pending_initial_new_count():
    with conn() as c:
        return c.execute(
            """
            SELECT COUNT(*)
            FROM seen_candidates
            WHERE initial_new=1 AND processed=0
            """
        ).fetchone()[0]


def seen_count():
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM seen_candidates"
        ).fetchone()[0]



def _parse_iso(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _local_date_for_iso(value):
    dt = _parse_iso(value)
    if not dt:
        return None

    try:
        return dt.astimezone().date()
    except Exception:
        return dt.date()


def live_detection_snapshot(stale_after_seconds=15):
    """
    Explicit proof that the live detector is actually running now.

    'Detected today' means first detected by this local console today in the
    Windows machine's local timezone. It is intentionally not guessed from an
    Indeed date label.
    """
    heartbeat = get_state("live_monitor_heartbeat_at")
    success_at = get_state("live_monitor_last_success_at") or get_state("last_scan_at")
    started_at = get_state("live_monitor_started_at")
    scan_mode = get_state("live_monitor_scan_mode", "waiting")
    last_error = get_state("live_monitor_last_error")
    consecutive_failures = _safe_int_state(
        "live_monitor_consecutive_failures", 0
    )
    last_found = _safe_int_state("live_monitor_last_found_links", 0)
    last_new = _safe_int_state("last_new_count", 0)

    now_local = datetime.now().astimezone()
    now_utc = datetime.now(timezone.utc)

    success_dt = _parse_iso(success_at)
    heartbeat_dt = _parse_iso(heartbeat)

    seconds_since_success = None
    if success_dt:
        seconds_since_success = max(
            0,
            int(
                (
                    now_utc
                    - success_dt.astimezone(timezone.utc)
                ).total_seconds()
            ),
        )

    seconds_since_heartbeat = None
    if heartbeat_dt:
        seconds_since_heartbeat = max(
            0,
            int(
                (
                    now_utc
                    - heartbeat_dt.astimezone(timezone.utc)
                ).total_seconds()
            ),
        )

    automation_on = get_state("live_monitor_enabled", "1") == "1"

    if not automation_on:
        status = "OFF"
        healthy = False
    elif consecutive_failures > 0 and (
        seconds_since_success is None
        or seconds_since_success > stale_after_seconds
    ):
        status = "ERROR"
        healthy = False
    elif seconds_since_success is None:
        status = "STARTING"
        healthy = False
    elif seconds_since_success > stale_after_seconds:
        status = "STALE"
        healthy = False
    else:
        status = "LIVE"
        healthy = True

    today = now_local.date()

    with conn() as c:
        app_rows = c.execute(
            """
            SELECT
                id,
                source_key,
                candidate_name,
                candidate_email,
                job_title,
                extraction_status,
                send_status,
                application_verified,
                decision_reason,
                first_seen_at,
                last_seen_at,
                created_at
            FROM applications
            ORDER BY id DESC
            """
        ).fetchall()

        sent_rows = c.execute(
            """
            SELECT sent_at
            FROM sent_responses
            ORDER BY id DESC
            """
        ).fetchall()

    detected_today = 0
    verified_today = 0
    review_today = 0
    last_new_record = None

    for row in app_rows:
        first_seen = row["first_seen_at"] or row["created_at"]

        if _local_date_for_iso(first_seen) == today:
            detected_today += 1

            if (row["extraction_status"] or "") in {
                "VERIFIED_EMAIL_AND_ROLE",
                "VERIFIED_EMAIL_FOUND",
            }:
                verified_today += 1

            if (row["extraction_status"] or "").startswith("NEEDS_REVIEW_"):
                review_today += 1

            if last_new_record is None:
                last_new_record = {
                    "candidate_name": row["candidate_name"],
                    "candidate_email": row["candidate_email"],
                    "job_title": row["job_title"],
                    "detected_at": first_seen,
                    "send_status": row["send_status"],
                    "extraction_status": row["extraction_status"],
                }

    sent_today = sum(
        1
        for row in sent_rows
        if _local_date_for_iso(row["sent_at"]) == today
    )

    skipped_today = 0
    seen_today = 0
    last_seen_today_record = None

    for row in app_rows:
        first_seen = row["first_seen_at"] or row["created_at"]

        if _local_date_for_iso(first_seen) == today:
            if (
                (row["send_status"] or "") in {"SKIPPED", "DUPLICATE_SKIPPED"}
                or (row["extraction_status"] or "").startswith("SKIPPED_")
            ):
                skipped_today += 1

        if _local_date_for_iso(row["last_seen_at"]) == today:
            seen_today += 1

            if last_seen_today_record is None:
                last_seen_today_record = {
                    "candidate_name": row["candidate_name"],
                    "candidate_email": row["candidate_email"],
                    "job_title": row["job_title"],
                    "first_detected_at": row["first_seen_at"] or row["created_at"],
                    "last_seen_at": row["last_seen_at"],
                    "send_status": row["send_status"],
                    "extraction_status": row["extraction_status"],
                }

    return {
        "healthy": healthy,
        "status": status,
        "started_at": started_at,
        "heartbeat_at": heartbeat,
        "last_success_at": success_at,
        "seconds_since_success": seconds_since_success,
        "seconds_since_heartbeat": seconds_since_heartbeat,
        "scan_mode": scan_mode,
        "last_error": last_error,
        "consecutive_failures": consecutive_failures,
        "last_found_links": last_found,
        "new_last_scan": last_new,
        "detected_today": detected_today,
        "verified_today": verified_today,
        "review_today": review_today,
        "sent_today": sent_today,
        "skipped_today": skipped_today,
        "seen_today": seen_today,
        "last_seen_today_applicant": last_seen_today_record,
        "last_new_applicant": last_new_record,
        "local_date": today.isoformat(),
    }


def stats():
    with conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]

        ready = c.execute(
            """
            SELECT COUNT(*)
            FROM applications
            WHERE candidate_email IS NOT NULL
              AND trim(candidate_email)<>''
              AND COALESCE(application_verified,0)=1
              AND extraction_status IN ('VERIFIED_EMAIL_AND_ROLE','VERIFIED_EMAIL_FOUND')
              AND lower(trim(COALESCE(job_title,'')))
                  NOT IN ('','the position','position')
              AND COALESCE(send_status,'') NOT IN (
                  'SENT',
                  'DUPLICATE_SKIPPED',
                  'SENDING',
                  'SEND_UNCERTAIN'
              )
            """
        ).fetchone()[0]

        # Accurate SENT count = unique recipient ledger, not duplicate Indeed
        # application rows.
        sent = c.execute(
            "SELECT COUNT(*) FROM sent_responses"
        ).fetchone()[0]

        review = c.execute(
            """
            SELECT COUNT(*)
            FROM applications
            WHERE extraction_status LIKE 'NEEDS_REVIEW%'
            """
        ).fetchone()[0]

        skipped = c.execute(
            """
            SELECT COUNT(*)
            FROM applications
            WHERE send_status IN ('SKIPPED','DUPLICATE_SKIPPED')
               OR extraction_status LIKE 'SKIPPED_%'
            """
        ).fetchone()[0]

        application_verified = c.execute(
            """
            SELECT COUNT(*)
            FROM applications
            WHERE COALESCE(application_verified,0)=1
            """
        ).fetchone()[0]

    return {
        "total": total,
        "ready": ready,
        "sent": sent,
        "review": review,
        "skipped": skipped,
        "application_verified": application_verified,
        "last_scan_at": get_state("last_scan_at"),
        "last_new_count": get_state("last_new_count", "0"),
        "last_visible_count": get_state("last_visible_count", "0"),
        "backlog_completed": backlog_completed(),
        "backlog_completed_at": get_state("initial_backlog_completed_at"),
        "seen_count": seen_count(),
        "pending_initial_new": pending_initial_new_count(),
        "smtp_verified": get_state("smtp_verified", "0") == "1",
        "smtp_verified_at": get_state("smtp_verified_at"),
        "smtp_last_error": get_state("smtp_last_error"),
        "smtp_transport": get_state("smtp_transport"),
        "persistent_db_path": str(DB_PATH),
        "corrections": correction_stats(),
        "live_detection": live_detection_snapshot(),
    }
