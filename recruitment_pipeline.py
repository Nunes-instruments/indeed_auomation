from __future__ import annotations

import hashlib
import json
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from config import load_settings
from database import conn, get_by_id, get_by_source_key, get_state, set_state, log, now


HR_REPORT_SENDER = "nunescbe@gmail.com"
ACTIVE_ROLE_STATES = {"OPEN", "UNKNOWN"}
INACTIVE_ROLE_STATES = {"PAUSED", "CLOSED"}
TERMINAL_CANDIDATE_STATES = {
    "hired", "selected", "not selected", "rejected", "withdrawn", "archived"
}

DEFAULT_WHATSAPP_ACK_TEMPLATE = (
    "Dear {candidate_name}, thank you for applying for the {job_title} position at {company_name}. "
    "We have received your application. Our recruitment team will review your profile and contact you regarding the next steps."
)
DEFAULT_INTERVIEW_SUBJECT_TEMPLATE = "Interview invitation – {job_title} – {interview_date}"
DEFAULT_INTERVIEW_BODY_TEMPLATE = (
    "Dear {candidate_name},\n\n"
    "Our HR team has reviewed your application for the {job_title} position at {company_name} "
    "and selected you for the interview stage.\n\n"
    "Your interview is scheduled for {interview_date}. Our HR team will contact you with the "
    "time and venue/meeting details.\n\n"
    "Regards,\n{company_name}"
)
DEFAULT_WHATSAPP_INTERVIEW_TEMPLATE = (
    "Dear {candidate_name}, our HR team has selected your application for the {job_title} position "
    "at {company_name}. Your interview is scheduled for {interview_date}. Our HR team will contact "
    "you with the time and venue/meeting details."
)


def _clean_role(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:200]


def _norm_status(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    if not text:
        return "UNKNOWN"
    if any(x in text for x in ("closed", "expired", "filled")):
        return "CLOSED"
    if any(x in text for x in ("paused", "pause", "inactive")):
        return "PAUSED"
    if any(x in text for x in ("open", "active", "live", "published")):
        return "OPEN"
    return "UNKNOWN"


def _safe_json(value, default):
    try:
        parsed = json.loads(value or json.dumps(default))
        return parsed
    except Exception:
        return default


def init_recruitment_pipeline_db():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS recruitment_role_state (
                job_title TEXT PRIMARY KEY COLLATE NOCASE,
                lifecycle_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                indeed_status TEXT,
                indeed_job_url TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                closed_at TEXT,
                ranking_hash TEXT,
                report_status TEXT NOT NULL DEFAULT 'IDLE',
                report_sent_at TEXT,
                report_error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recruitment_candidate_flow (
                application_id INTEGER PRIMARY KEY,
                hr_status TEXT NOT NULL DEFAULT 'PENDING',
                hr_approved_at TEXT,
                interview_date TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recruitment_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_key TEXT NOT NULL UNIQUE,
                application_id INTEGER,
                job_title TEXT,
                channel TEXT NOT NULL,
                message_type TEXT NOT NULL,
                recipient TEXT,
                sender TEXT,
                subject TEXT,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_recruitment_notifications_status
            ON recruitment_notifications(status, channel, message_type);

            CREATE INDEX IF NOT EXISTS idx_recruitment_notifications_app
            ON recruitment_notifications(application_id, message_type);

            CREATE INDEX IF NOT EXISTS idx_recruitment_role_state_status
            ON recruitment_role_state(lifecycle_status, updated_at);
            """
        )

        # A process interruption after a network send is deliberately not retried.
        c.execute(
            """
            UPDATE recruitment_notifications
            SET status='SEND_UNCERTAIN',
                error=CASE
                    WHEN trim(COALESCE(error,''))='' THEN
                        'Previous process ended while delivery was in progress. Automatic resend is blocked.'
                    ELSE error
                END,
                updated_at=?
            WHERE status='SENDING'
            """,
            (now(),),
        )

    if not get_state("recruitment_pipeline_activation_at"):
        set_state("recruitment_pipeline_activation_at", now())


def _pause_role_outreach(job_title, reason):
    """Stop queued candidate-facing outreach when an Indeed role is paused/closed."""
    title = _clean_role(job_title)
    if not title:
        return 0

    with conn() as c:
        cur = c.execute(
            """
            UPDATE recruitment_notifications
            SET status='PAUSED', error=?, updated_at=?
            WHERE lower(job_title)=lower(?)
              AND message_type IN ('ACK_WHATSAPP','INTERVIEW_EMAIL','INTERVIEW_WHATSAPP')
              AND status IN ('QUEUED','SEND_FAILED','WAITING_LOGIN','WAITING_CONFIG')
            """,
            (str(reason or 'Role is paused/closed.')[:2000], now(), title),
        )
        return max(0, int(cur.rowcount or 0))


def ensure_role_state(job_title, lifecycle_status="UNKNOWN", indeed_status=None, job_url=None):
    init_recruitment_pipeline_db()
    title = _clean_role(job_title)
    if not title or title.lower() in {"the position", "position", "job", "unknown"}:
        return None

    ts = now()
    normalized = _norm_status(lifecycle_status or indeed_status)

    with conn() as c:
        existing = c.execute(
            "SELECT * FROM recruitment_role_state WHERE lower(job_title)=lower(?)",
            (title,),
        ).fetchone()

        old_status = (existing["lifecycle_status"] if existing else "UNKNOWN") or "UNKNOWN"
        new_status = normalized
        closed_at = existing["closed_at"] if existing else None

        if new_status == "CLOSED" and old_status != "CLOSED":
            closed_at = ts
        elif new_status in ACTIVE_ROLE_STATES:
            closed_at = None

        c.execute(
            """
            INSERT INTO recruitment_role_state(
                job_title, lifecycle_status, indeed_status, indeed_job_url,
                first_seen_at, last_seen_at, closed_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(job_title) DO UPDATE SET
                lifecycle_status=excluded.lifecycle_status,
                indeed_status=COALESCE(excluded.indeed_status, recruitment_role_state.indeed_status),
                indeed_job_url=COALESCE(excluded.indeed_job_url, recruitment_role_state.indeed_job_url),
                last_seen_at=excluded.last_seen_at,
                closed_at=excluded.closed_at,
                updated_at=excluded.updated_at
            """,
            (
                title,
                new_status,
                str(indeed_status or "")[:120] or None,
                str(job_url or "")[:1200] or None,
                existing["first_seen_at"] if existing else ts,
                ts,
                closed_at,
                ts,
            ),
        )

    if old_status != new_status:
        log("INFO", f"ROLE LIFECYCLE: {title} -> {new_status}.")
        set_state("recruitment_role_lifecycle_changed_at", ts)

        if new_status in INACTIVE_ROLE_STATES:
            paused = _pause_role_outreach(
                title,
                f"Role changed to {new_status} on Indeed; candidate outreach is stopped.",
            )
            if paused:
                log(
                    "INFO",
                    f"ROLE LIFECYCLE: paused {paused} queued candidate outreach item(s) for {title}.",
                )

    return get_role_state(title)


def sync_discovered_jobs(rows):
    """Synchronize explicit Open/Paused/Closed status from the Indeed Jobs screen."""
    changed = []
    for row in rows or []:
        title = _clean_role(row.get("job_title") or row.get("title"))
        if not title:
            continue
        raw_status = row.get("job_status") or row.get("status") or "OPEN"
        before = get_role_state(title)
        after = ensure_role_state(
            title,
            lifecycle_status=raw_status,
            indeed_status=raw_status,
            job_url=row.get("job_url") or row.get("url"),
        )
        if after and (not before or before.get("lifecycle_status") != after.get("lifecycle_status")):
            changed.append(title)
    return changed


def get_role_state(job_title):
    init_recruitment_pipeline_db()
    title = _clean_role(job_title)
    if not title:
        return None
    with conn() as c:
        row = c.execute(
            "SELECT * FROM recruitment_role_state WHERE lower(job_title)=lower(?) LIMIT 1",
            (title,),
        ).fetchone()
        return dict(row) if row else None


def role_accepts_new_applications(job_title):
    state = get_role_state(job_title)
    if not state:
        return True
    return (state.get("lifecycle_status") or "UNKNOWN").upper() in ACTIVE_ROLE_STATES


def role_accepts_ranking(job_title):
    return role_accepts_new_applications(job_title)


def _notification(unique_key):
    with conn() as c:
        row = c.execute(
            "SELECT * FROM recruitment_notifications WHERE unique_key=? LIMIT 1",
            (unique_key,),
        ).fetchone()
        return dict(row) if row else None


def _phone_identity(value):
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    # The same mobile may appear once with +91 and once without it. Comparing
    # the stable subscriber tail prevents a duplicate stage-1 thank-you.
    return digits[-10:] if len(digits) >= 10 else digits


def _ack_whatsapp_already_reserved(phone, application_id=None):
    identity = _phone_identity(phone)
    if not identity:
        return False
    with conn() as c:
        rows = c.execute(
            """
            SELECT application_id, recipient, status
            FROM recruitment_notifications
            WHERE message_type='ACK_WHATSAPP'
              AND status IN ('QUEUED','SENDING','WAITING_LOGIN','SENT','SEND_UNCERTAIN')
            """
        ).fetchall()
    for row in rows:
        if application_id is not None and int(row["application_id"] or 0) == int(application_id):
            continue
        if _phone_identity(row["recipient"]) == identity:
            return True
    return False


def queue_notification(
    unique_key,
    *,
    channel,
    message_type,
    body,
    recipient=None,
    sender=None,
    subject=None,
    application_id=None,
    job_title=None,
    initial_status="QUEUED",
    error=None,
):
    init_recruitment_pipeline_db()
    ts = now()
    with conn() as c:
        c.execute(
            """
            INSERT INTO recruitment_notifications(
                unique_key, application_id, job_title, channel, message_type,
                recipient, sender, subject, body, status, attempts, error,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(unique_key) DO UPDATE SET
                recipient=CASE
                    WHEN recruitment_notifications.status IN ('SENT','SEND_UNCERTAIN')
                    THEN recruitment_notifications.recipient
                    ELSE excluded.recipient
                END,
                sender=CASE
                    WHEN recruitment_notifications.status IN ('SENT','SEND_UNCERTAIN')
                    THEN recruitment_notifications.sender
                    ELSE excluded.sender
                END,
                subject=CASE
                    WHEN recruitment_notifications.status IN ('SENT','SEND_UNCERTAIN')
                    THEN recruitment_notifications.subject
                    ELSE excluded.subject
                END,
                body=CASE
                    WHEN recruitment_notifications.status IN ('SENT','SEND_UNCERTAIN')
                    THEN recruitment_notifications.body
                    ELSE excluded.body
                END,
                status=CASE
                    WHEN recruitment_notifications.status IN ('SENT','SEND_UNCERTAIN')
                    THEN recruitment_notifications.status
                    WHEN recruitment_notifications.status='SENDING'
                    THEN recruitment_notifications.status
                    ELSE excluded.status
                END,
                error=CASE
                    WHEN recruitment_notifications.status IN ('SENT','SEND_UNCERTAIN','SENDING')
                    THEN recruitment_notifications.error
                    ELSE excluded.error
                END,
                updated_at=excluded.updated_at
            """,
            (
                unique_key,
                application_id,
                _clean_role(job_title),
                channel,
                message_type,
                str(recipient or "")[:500] or None,
                str(sender or "")[:500] or None,
                str(subject or "")[:500] or None,
                str(body or "")[:20000],
                initial_status,
                0,
                str(error or "")[:2000] or None,
                ts,
                ts,
            ),
        )
    return _notification(unique_key)


def _ack_whatsapp_body(app, settings):
    template = settings.get("whatsapp_ack_template") or DEFAULT_WHATSAPP_ACK_TEMPLATE
    return template.format(
        candidate_name=app.get("candidate_name") or "Candidate",
        job_title=app.get("job_title") or "the position",
        company_name=settings.get("company_name") or "Nunes Instruments",
    )


def _recent_application_for_ack(app, hours=48):
    """Allow a same/new-day retry to send the acknowledgement once contact extraction finishes.

    The old implementation only queued WhatsApp on the exact discovery pass.
    If Indeed rendered the phone/resume a few seconds later, the retry was no
    longer marked ``new_applicant`` and WhatsApp could be missed. Historical
    applications are intentionally excluded to avoid contacting an old backlog.
    """
    raw = str((app or {}).get("first_seen_at") or (app or {}).get("created_at") or "").strip()
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.astimezone()
        current = datetime.now().astimezone()
        return 0 <= (current - when.astimezone()).total_seconds() <= int(hours * 3600)
    except Exception:
        return False


def queue_live_candidate_outreach(scan_results):
    """Queue the stage-1 WhatsApp acknowledgement as soon as a new/recent application is verified.

    Email acknowledgement is owned by the independent Gmail outbox. This worker
    owns WhatsApp. When both email and phone exist, both channels are released
    independently and immediately. The unique notification key prevents a
    duplicate WhatsApp acknowledgement.
    """
    init_recruitment_pipeline_db()
    settings = load_settings()
    queued = 0

    for item in scan_results or []:
        source_key = item.get("source_key")
        if not source_key:
            continue
        app = get_by_source_key(source_key)
        if not app or not int(app.get("application_verified") or 0):
            continue

        # A contact/resume may finish rendering on a retry. Treat a recent
        # verified application as eligible even when this exact retry is not
        # flagged new_applicant. Old historical rows are not contacted.
        if not item.get("new_applicant") and not _recent_application_for_ack(app):
            continue

        title = _clean_role(app.get("job_title"))
        if not role_accepts_new_applications(title):
            continue

        phone = str(app.get("candidate_phone") or "").strip()
        body = _ack_whatsapp_body(app, settings)

        duplicate_phone = bool(phone) and _ack_whatsapp_already_reserved(
            phone, application_id=app.get("id")
        )
        if duplicate_phone:
            status = "DUPLICATE_SKIPPED"
            error = "Stage-1 WhatsApp acknowledgement already exists for this phone number."
        else:
            status = "QUEUED" if phone else "SKIPPED_NO_PHONE"
            error = None if phone else "Candidate phone number is not available."

        before = _notification(f"ACK_WHATSAPP:{app['id']}")
        queue_notification(
            f"ACK_WHATSAPP:{app['id']}",
            channel="WHATSAPP",
            message_type="ACK_WHATSAPP",
            application_id=app["id"],
            job_title=title,
            recipient=phone,
            body=body,
            initial_status=status,
            error=error,
        )
        if phone and not duplicate_phone and (not before or str(before.get("status") or "").upper() not in {"SENT", "SEND_UNCERTAIN", "SENDING", "QUEUED", "WAITING_LOGIN"}):
            queued += 1

    return queued


def _next_working_day_label():
    """HR interview scheduling rule.

    Mon-Wed approval -> next day. Thu-Fri approval -> Monday.
    Weekend approval -> Monday.
    """
    today = datetime.now().astimezone().date()
    weekday = today.weekday()
    if weekday <= 2:
        day = today + timedelta(days=1)
    else:
        days_until_monday = (7 - weekday) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        day = today + timedelta(days=days_until_monday)
    return day.strftime("%d %b %Y")


def interview_schedule_preview():
    return {
        "interview_date": _next_working_day_label(),
        "rule": "Mon-Wed approval → next day; Thu-Fri approval → Monday",
    }


def approve_candidate_for_interview(application_id):
    init_recruitment_pipeline_db()
    app = get_by_id(int(application_id))
    if not app:
        raise ValueError("Candidate application was not found.")

    status = re.sub(r"\s+", " ", str(app.get("indeed_status") or "")).strip().lower()
    if status in TERMINAL_CANDIDATE_STATES:
        raise ValueError(f"Candidate is already in terminal Indeed status: {app.get('indeed_status')}.")

    if not role_accepts_new_applications(app.get("job_title")):
        raise ValueError("This role is paused/closed on Indeed, so interview outreach is stopped.")

    with conn() as c:
        review = c.execute(
            """
            SELECT analysis_status, rank_position, match_score
            FROM candidate_reviews
            WHERE application_id=?
            LIMIT 1
            """,
            (int(application_id),),
        ).fetchone()

    if not review or review["analysis_status"] != "READY" or review["rank_position"] is None:
        raise ValueError(
            "Candidate ranking is not complete yet. HR approval is available after the role/resume ranking is ready."
        )

    interview_date = _next_working_day_label()
    ts = now()
    settings = load_settings()

    with conn() as c:
        c.execute(
            """
            INSERT INTO recruitment_candidate_flow(
                application_id, hr_status, hr_approved_at, interview_date, updated_at
            ) VALUES (?, 'APPROVED', ?, ?, ?)
            ON CONFLICT(application_id) DO UPDATE SET
                hr_status='APPROVED',
                hr_approved_at=COALESCE(recruitment_candidate_flow.hr_approved_at, excluded.hr_approved_at),
                interview_date=excluded.interview_date,
                updated_at=excluded.updated_at
            """,
            (int(application_id), ts, interview_date, ts),
        )

    candidate = app.get("candidate_name") or "Candidate"
    role = app.get("job_title") or "the position"
    company = settings.get("company_name") or "Nunes Instruments"

    subject_template = settings.get("interview_subject_template") or DEFAULT_INTERVIEW_SUBJECT_TEMPLATE
    body_template = settings.get("interview_body_template") or DEFAULT_INTERVIEW_BODY_TEMPLATE

    subject = subject_template.format(
        candidate_name=candidate,
        job_title=role,
        interview_date=interview_date,
        company_name=company,
    )
    body = body_template.format(
        candidate_name=candidate,
        job_title=role,
        interview_date=interview_date,
        company_name=company,
    )

    email = str(app.get("candidate_email") or "").strip()
    queue_notification(
        f"INTERVIEW_EMAIL:{application_id}",
        channel="EMAIL_CANDIDATE",
        message_type="INTERVIEW_EMAIL",
        application_id=int(application_id),
        job_title=role,
        recipient=email,
        sender=settings.get("company_email") or "nuneslead@gmail.com",
        subject=subject,
        body=body,
        initial_status="QUEUED" if email else "SKIPPED_NO_EMAIL",
        error=None if email else "Candidate email is not available.",
    )

    phone = str(app.get("candidate_phone") or "").strip()
    wa_template = settings.get("whatsapp_interview_template") or DEFAULT_WHATSAPP_INTERVIEW_TEMPLATE
    wa_body = wa_template.format(
        candidate_name=candidate,
        job_title=role,
        interview_date=interview_date,
        company_name=company,
    )
    queue_notification(
        f"INTERVIEW_WHATSAPP:{application_id}",
        channel="WHATSAPP",
        message_type="INTERVIEW_WHATSAPP",
        application_id=int(application_id),
        job_title=role,
        recipient=phone,
        body=wa_body,
        initial_status="QUEUED" if phone else "SKIPPED_NO_PHONE",
        error=None if phone else "Candidate phone number is not available.",
    )

    log("INFO", f"HR APPROVAL: {candidate} approved for interview on {interview_date} for {role}.")
    return candidate_flow(application_id)


def approve_top_candidates_for_interview(job_title, count):
    """Approve the top N ranked active candidates after an explicit HR action."""
    init_recruitment_pipeline_db()
    title = _clean_role(job_title)
    if not title:
        raise ValueError("job_title is required.")
    try:
        requested = int(count)
    except Exception:
        raise ValueError("Interview count must be a whole number.")
    if requested < 1 or requested > 200:
        raise ValueError("Interview count must be between 1 and 200.")
    if not role_accepts_new_applications(title):
        raise ValueError("This role is paused/closed on Indeed, so interview outreach is stopped.")

    terminal = tuple(sorted(TERMINAL_CANDIDATE_STATES))
    placeholders = ",".join("?" for _ in terminal)
    with conn() as c:
        rows = c.execute(
            f"""
            SELECT a.id, a.candidate_name, cr.rank_position, cr.match_score,
                   COALESCE(cf.hr_status,'PENDING') AS hr_status
            FROM applications a
            JOIN candidate_reviews cr ON cr.application_id=a.id
            LEFT JOIN recruitment_candidate_flow cf ON cf.application_id=a.id
            WHERE lower(trim(a.job_title))=lower(trim(?))
              AND cr.analysis_status='READY'
              AND cr.rank_position IS NOT NULL
              AND lower(trim(COALESCE(a.indeed_status,''))) NOT IN ({placeholders})
            ORDER BY cr.rank_position ASC, cr.match_score DESC, a.id ASC
            LIMIT ?
            """,
            [title, *terminal, requested],
        ).fetchall()

    if not rows:
        raise ValueError("No ranked active candidates are ready for HR interview approval yet.")

    selected = [dict(row) for row in rows]
    newly_approved = 0
    already_approved = 0
    failures = []
    interview_date = _next_working_day_label()

    for row in selected:
        if str(row.get("hr_status") or "").upper() == "APPROVED":
            already_approved += 1
            continue
        try:
            approve_candidate_for_interview(int(row["id"]))
            newly_approved += 1
        except Exception as exc:
            failures.append({
                "application_id": int(row["id"]),
                "candidate_name": row.get("candidate_name"),
                "error": str(exc),
            })

    log(
        "INFO",
        f"HR BATCH APPROVAL: {title} top {requested}; selected={len(selected)}, "
        f"new={newly_approved}, existing={already_approved}, failures={len(failures)}, "
        f"interview={interview_date}.",
    )
    return {
        "job_title": title,
        "requested_count": requested,
        "selected_count": len(selected),
        "newly_approved": newly_approved,
        "already_approved": already_approved,
        "interview_date": interview_date,
        "failures": failures,
        "selected": selected,
    }


def candidate_flow(application_id):
    init_recruitment_pipeline_db()
    with conn() as c:
        flow = c.execute(
            "SELECT * FROM recruitment_candidate_flow WHERE application_id=?",
            (int(application_id),),
        ).fetchone()
        notifications = c.execute(
            """
            SELECT message_type, channel, status, error, sent_at, updated_at
            FROM recruitment_notifications
            WHERE application_id=?
            ORDER BY id ASC
            """,
            (int(application_id),),
        ).fetchall()

    return {
        "flow": dict(flow) if flow else {
            "application_id": int(application_id),
            "hr_status": "PENDING",
            "hr_approved_at": None,
            "interview_date": None,
        },
        "notifications": [dict(x) for x in notifications],
    }


def _ranking_rows(job_title):
    title = _clean_role(job_title)
    with conn() as c:
        rows = c.execute(
            """
            SELECT
                a.id, a.candidate_name, a.candidate_email, a.candidate_phone,
                a.indeed_status, a.send_status,
                cr.analysis_status, cr.rank_position, cr.match_score,
                cr.requirements_evidenced, cr.requirements_total,
                cr.auto_bucket
            FROM applications a
            LEFT JOIN candidate_reviews cr ON cr.application_id=a.id
            WHERE lower(trim(a.job_title))=lower(trim(?))
              AND lower(trim(COALESCE(a.indeed_status,''))) NOT IN (
                  'hired','selected','not selected','rejected','withdrawn','archived'
              )
            ORDER BY
                CASE WHEN cr.analysis_status='READY' THEN 0 ELSE 1 END,
                COALESCE(cr.rank_position,999999) ASC,
                a.id ASC
            """,
            (title,),
        ).fetchall()
    return [dict(r) for r in rows]


def _report_snapshot(job_title):
    title = _clean_role(job_title)
    state = get_role_state(title) or ensure_role_state(title)
    rows = _ranking_rows(title)

    serial = {
        "job_title": title,
        "lifecycle_status": (state or {}).get("lifecycle_status", "UNKNOWN"),
        "rows": [
            {
                "id": r.get("id"),
                "rank": r.get("rank_position"),
                "score": round(float(r.get("match_score") or 0), 2),
                "analysis": r.get("analysis_status"),
                "status": r.get("indeed_status"),
                "send_status": r.get("send_status"),
            }
            for r in rows
        ],
    }
    digest = hashlib.sha256(
        json.dumps(serial, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return state or {}, rows, digest


def _build_role_report(job_title, state, rows):
    title = _clean_role(job_title)
    lifecycle = (state.get("lifecycle_status") or "UNKNOWN").upper()
    subject = (
        f"Recruitment Ranking CLOSED – {title}"
        if lifecycle == "CLOSED"
        else f"Recruitment Ranking – {title}"
    )

    ready = [r for r in rows if r.get("analysis_status") == "READY"]
    waiting = [r for r in rows if r.get("analysis_status") != "READY"]

    lines = [
        f"Role: {title}",
        f"Role status: {lifecycle}",
        f"Updated: {datetime.now().astimezone().strftime('%d %b %Y, %I:%M %p')}",
        "",
        f"Ranked applicants: {len(ready)}",
        f"Waiting for complete ranking data: {len(waiting)}",
        "",
        "CURRENT RANKING",
        "---------------",
    ]

    if not ready:
        lines.append("No completed rankings yet.")
    else:
        for row in ready:
            lines.append(
                f"#{row.get('rank_position') or '-'} | "
                f"{row.get('candidate_name') or 'Candidate'} | "
                f"{float(row.get('match_score') or 0):.1f}% | "
                f"{row.get('candidate_email') or 'no email'} | "
                f"{row.get('candidate_phone') or 'no phone'} | "
                f"Evidence {row.get('requirements_evidenced') or 0}/{row.get('requirements_total') or 0} | "
                f"Indeed {row.get('indeed_status') or 'active'}"
            )

    if waiting:
        lines.extend(["", "WAITING / INCOMPLETE", "--------------------"])
        for row in waiting:
            lines.append(
                f"- {row.get('candidate_name') or 'Candidate'} | "
                f"{row.get('analysis_status') or 'PENDING'} | "
                f"{row.get('candidate_email') or 'no email'}"
            )

    lines.extend([
        "",
        "Ranking is an HR review aid based on role requirements and resume evidence. "
        "HR approval remains the gate before an interview message is sent.",
    ])
    return subject, "\n".join(lines)


def _daily_report_clock(settings):
    value = str(settings.get("daily_report_time") or "19:00").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value)
    if not match:
        return 19, 0, "19:00"
    hour = int(match.group(1))
    minute = int(match.group(2))
    return hour, minute, f"{hour:02d}:{minute:02d}"


def _daily_report_roles():
    """Return every ongoing role currently known to the Indeed workflow.

    Open roles are included even when they received zero applications that day,
    so the single EOD email is also a complete snapshot of current job openings.
    """
    with conn() as c:
        rows = c.execute(
            """
            SELECT job_title, lifecycle_status
            FROM (
                SELECT rrs.job_title AS job_title,
                       COALESCE(rrs.lifecycle_status, 'UNKNOWN') AS lifecycle_status
                FROM recruitment_role_state rrs
                WHERE upper(trim(COALESCE(rrs.lifecycle_status,'UNKNOWN'))) NOT IN ('PAUSED','CLOSED')

                UNION

                SELECT rp.job_title AS job_title,
                       COALESCE(rrs.lifecycle_status, 'UNKNOWN') AS lifecycle_status
                FROM role_profiles rp
                LEFT JOIN recruitment_role_state rrs
                  ON lower(trim(rrs.job_title))=lower(trim(rp.job_title))
                WHERE upper(trim(COALESCE(rrs.lifecycle_status,'UNKNOWN'))) NOT IN ('PAUSED','CLOSED')

                UNION

                SELECT DISTINCT a.job_title AS job_title,
                       COALESCE(rrs.lifecycle_status, 'UNKNOWN') AS lifecycle_status
                FROM applications a
                LEFT JOIN recruitment_role_state rrs
                  ON lower(trim(rrs.job_title))=lower(trim(a.job_title))
                WHERE upper(trim(COALESCE(rrs.lifecycle_status,'UNKNOWN'))) NOT IN ('PAUSED','CLOSED')
            )
            WHERE lower(trim(COALESCE(job_title,''))) NOT IN
                  ('','the position','position','job','the job','unknown')
            ORDER BY lower(job_title)
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _daily_role_candidates(job_title):
    with conn() as c:
        rows = c.execute(
            """
            SELECT
                a.id, a.candidate_name, a.candidate_email, a.candidate_phone,
                a.indeed_status, a.first_seen_at, a.send_status,
                cr.analysis_status, cr.rank_position, cr.match_score,
                COALESCE(cf.hr_status,'PENDING') AS hr_status,
                cf.interview_date,
                MAX(CASE WHEN rn.message_type='ACK_WHATSAPP' THEN rn.status END) AS ack_whatsapp_status,
                MAX(CASE WHEN rn.message_type='INTERVIEW_EMAIL' THEN rn.status END) AS interview_email_status,
                MAX(CASE WHEN rn.message_type='INTERVIEW_WHATSAPP' THEN rn.status END) AS interview_whatsapp_status
            FROM applications a
            LEFT JOIN candidate_reviews cr ON cr.application_id=a.id
            LEFT JOIN recruitment_candidate_flow cf ON cf.application_id=a.id
            LEFT JOIN recruitment_notifications rn ON rn.application_id=a.id
            WHERE lower(trim(a.job_title))=lower(trim(?))
              AND lower(trim(COALESCE(a.indeed_status,''))) NOT IN
                  ('hired','selected','not selected','rejected','withdrawn','archived')
            GROUP BY a.id
            ORDER BY
                CASE WHEN cr.rank_position IS NULL THEN 1 ELSE 0 END,
                cr.rank_position ASC,
                cr.match_score DESC,
                COALESCE(a.first_seen_at, a.created_at) ASC,
                a.id ASC
            """,
            (job_title,),
        ).fetchall()
    return [dict(row) for row in rows]


def _build_daily_consolidated_report(report_date=None):
    local_now = datetime.now().astimezone()
    report_date = report_date or local_now.date().isoformat()
    roles = _daily_report_roles()

    lines = [
        "NUNES RECRUITMENT – END OF DAY REPORT",
        "=====================================",
        f"Date: {local_now.strftime('%d %b %Y')}",
        f"Ongoing roles: {len(roles)}",
        "",
        "This is one consolidated report. Each active Indeed role is listed separately below.",
    ]

    total_candidates = 0
    total_ranked = 0
    total_hr_approved = 0
    total_stage2_pending = 0

    for index, role in enumerate(roles, start=1):
        title = role.get("job_title") or "Role"
        candidates = _daily_role_candidates(title)
        total_candidates += len(candidates)
        ranked = sum(1 for row in candidates if row.get("rank_position") is not None)
        approved = sum(1 for row in candidates if str(row.get("hr_status") or "").upper() == "APPROVED")
        stage2_pending = sum(
            1 for row in candidates
            if str(row.get("hr_status") or "").upper() != "APPROVED"
            or (
                row.get("interview_email_status") not in {"SENT", "SKIPPED_NO_EMAIL"}
                and row.get("candidate_email")
            )
            or (
                row.get("interview_whatsapp_status") not in {"SENT", "SKIPPED_NO_PHONE"}
                and row.get("candidate_phone")
            )
        )
        total_ranked += ranked
        total_hr_approved += approved
        total_stage2_pending += stage2_pending

        lines.extend([
            "",
            f"ROLE {index}: {title}",
            "-" * min(70, max(12, len(title) + 9)),
            f"Active candidates: {len(candidates)} | Ranked: {ranked} | HR approved: {approved}",
        ])

        if not candidates:
            lines.append("No active candidates for this role.")
            continue

        for row in candidates:
            rank = row.get("rank_position")
            score = row.get("match_score")
            score_text = f"{float(score):.1f}%" if score is not None else "waiting"
            email_ack = str(row.get("send_status") or "NOT SENT").upper()
            wa_ack = str(row.get("ack_whatsapp_status") or ("NOT SENT" if row.get("candidate_phone") else "NO PHONE")).upper()
            hr = str(row.get("hr_status") or "PENDING").upper()
            i_email = str(row.get("interview_email_status") or ("NOT SENT" if row.get("candidate_email") else "NO EMAIL")).upper()
            i_wa = str(row.get("interview_whatsapp_status") or ("NOT SENT" if row.get("candidate_phone") else "NO PHONE")).upper()
            applied = str(row.get("first_seen_at") or "-")
            lines.extend([
                f"#{rank if rank is not None else '-'} | {row.get('candidate_name') or 'Candidate'} | Match {score_text}",
                f"  Contact: {row.get('candidate_email') or 'no email'} | {row.get('candidate_phone') or 'no phone'}",
                f"  Applied: {applied} | Indeed: {row.get('indeed_status') or 'active'}",
                f"  Stage 1: Email {email_ack} | WhatsApp {wa_ack}",
                f"  HR: {hr} | Interview date: {row.get('interview_date') or '-'}",
                f"  Stage 2: Email {i_email} | WhatsApp {i_wa}",
            ])

    lines.extend([
        "",
        "DAILY TOTALS",
        "------------",
        f"Ongoing roles: {len(roles)}",
        f"Active candidates: {total_candidates}",
        f"Ranked candidates: {total_ranked}",
        f"HR approved: {total_hr_approved}",
        f"Candidates still requiring/finishing Stage 2 action: {total_stage2_pending}",
        "",
        "Ranking is job-requirement/resume evidence support for HR. HR approval remains the interview gate.",
    ])

    subject = f"Daily Recruitment Report – {local_now.strftime('%d %b %Y')} – {len(roles)} ongoing role(s)"
    return subject, "\n".join(lines), roles


def queue_daily_consolidated_report(force=False):
    """Queue exactly one end-of-day email containing every ongoing role.

    The report uses the Windows local clock. Default send time is 19:00 and is
    editable in Settings. If the service starts after the configured time, that
    day's report is queued immediately as long as it has not already been queued.
    """
    init_recruitment_pipeline_db()
    settings = load_settings()
    if not settings.get("daily_consolidated_report_enabled", True) and not force:
        return {"queued": False, "reason": "disabled"}

    local_now = datetime.now().astimezone()
    date_key = local_now.date().isoformat()
    hour, minute, label = _daily_report_clock(settings)
    due = (local_now.hour, local_now.minute) >= (hour, minute)
    if not force and not due:
        return {"queued": False, "reason": "not_due", "time": label}

    if not force and get_state("daily_report_last_queued_date") == date_key:
        return {"queued": False, "reason": "already_queued", "date": date_key}

    subject, body, roles = _build_daily_consolidated_report(date_key)
    if not roles:
        return {"queued": False, "reason": "no_ongoing_roles", "date": date_key}

    recipient = (settings.get("hr_report_recipient") or HR_REPORT_SENDER).strip()
    key = f"DAILY_CONSOLIDATED_REPORT:{date_key}"
    existing = _notification(key)
    queue_notification(
        key,
        channel="EMAIL_REPORT",
        message_type="DAILY_CONSOLIDATED_REPORT",
        job_title="ALL ONGOING ROLES",
        recipient=recipient,
        sender=HR_REPORT_SENDER,
        subject=subject,
        body=body,
    )
    set_state("daily_report_last_queued_date", date_key)
    set_state("daily_report_last_queued_at", now())
    return {
        "queued": existing is None,
        "already_exists": existing is not None,
        "date": date_key,
        "roles": len(roles),
        "recipient": recipient,
        "subject": subject,
    }


def daily_report_status():
    settings = load_settings()
    _, _, label = _daily_report_clock(settings)
    return {
        "enabled": bool(settings.get("daily_consolidated_report_enabled", True)),
        "time": label,
        "sender": HR_REPORT_SENDER,
        "recipient": settings.get("hr_report_recipient") or HR_REPORT_SENDER,
        "last_queued_date": get_state("daily_report_last_queued_date"),
        "last_queued_at": get_state("daily_report_last_queued_at"),
        "last_sent_date": get_state("daily_report_last_sent_date"),
        "last_sent_at": get_state("daily_report_last_sent_at"),
        "last_error": get_state("daily_report_last_error"),
    }


def queue_changed_role_reports():
    init_recruitment_pipeline_db()
    settings = load_settings()
    if not settings.get("auto_send_role_reports", True):
        return 0

    # Seed role-state rows for every role already present in applications.
    with conn() as c:
        titles = [
            r["job_title"]
            for r in c.execute(
                """
                SELECT DISTINCT job_title FROM applications
                WHERE lower(trim(COALESCE(job_title,''))) NOT IN
                      ('','the position','position','job','the job','unknown')
                """
            ).fetchall()
        ]

    queued = 0
    for title in titles:
        state = get_role_state(title) or ensure_role_state(title)
        lifecycle = (state.get("lifecycle_status") or "UNKNOWN").upper()
        if lifecycle == "PAUSED":
            continue

        state, rows, digest = _report_snapshot(title)
        if not rows:
            continue
        if lifecycle != "CLOSED" and not any(r.get("analysis_status") == "READY" for r in rows):
            continue

        current_hash = state.get("ranking_hash")
        if current_hash == digest:
            continue

        subject, body = _build_role_report(title, state, rows)
        recipient = (settings.get("hr_report_recipient") or HR_REPORT_SENDER).strip()
        key = f"ROLE_REPORT:{title.lower()}:{digest}"

        # Only the newest unsent ranking snapshot should be delivered. Older
        # queued snapshots are retained in history but marked superseded.
        with conn() as c:
            c.execute(
                """
                UPDATE recruitment_notifications
                SET status='SUPERSEDED', updated_at=?
                WHERE lower(job_title)=lower(?)
                  AND message_type='ROLE_RANKING_REPORT'
                  AND status IN ('QUEUED','WAITING_CONFIG','SEND_FAILED')
                  AND unique_key<>?
                """,
                (now(), title, key),
            )

        queue_notification(
            key,
            channel="EMAIL_REPORT",
            message_type="ROLE_RANKING_REPORT",
            job_title=title,
            recipient=recipient,
            sender=HR_REPORT_SENDER,
            subject=subject,
            body=body,
        )
        with conn() as c:
            c.execute(
                """
                UPDATE recruitment_role_state
                SET ranking_hash=?, report_status='QUEUED', report_error=NULL, updated_at=?
                WHERE lower(job_title)=lower(?)
                """,
                (digest, now(), title),
            )
        queued += 1
    return queued


def hr_report_mail_health_check():
    settings = load_settings()
    password = re.sub(r"\s+", "", str(settings.get("hr_report_smtp_app_password") or ""))
    if not password:
        set_state("hr_report_smtp_verified", "0")
        return {"ok": False, "message": f"Add the Google App Password for {HR_REPORT_SENDER}."}

    host = str(settings.get("smtp_host") or "smtp.gmail.com").strip()
    ports = [("starttls", int(settings.get("smtp_port") or 587)), ("ssl", int(settings.get("smtp_ssl_fallback_port") or 465))]
    context = ssl.create_default_context()
    errors = []
    for mode, port in ports:
        smtp = None
        try:
            if mode == "ssl":
                smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
                smtp.ehlo()
            else:
                smtp = smtplib.SMTP(host, port, timeout=20)
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(HR_REPORT_SENDER, password)
            try:
                smtp.quit()
            except Exception:
                pass
            set_state("hr_report_smtp_verified", "1")
            set_state("hr_report_smtp_last_error", "")
            return {"ok": True, "message": f"{HR_REPORT_SENDER} verified via {mode}:{port}."}
        except Exception as exc:
            errors.append(str(exc))
            try:
                if smtp:
                    smtp.quit()
            except Exception:
                pass

    error = errors[-1] if errors else "HR Gmail verification failed."
    set_state("hr_report_smtp_verified", "0")
    set_state("hr_report_smtp_last_error", error)
    return {"ok": False, "message": error}


def _report_smtp_send(to_addr, subject, body, settings):
    password = re.sub(r"\s+", "", str(settings.get("hr_report_smtp_app_password") or ""))
    if not password:
        raise RuntimeError(
            f"HR ranking Gmail App Password is not configured for {HR_REPORT_SENDER}."
        )

    host = str(settings.get("smtp_host") or "smtp.gmail.com").strip()
    preferred_port = int(settings.get("smtp_port") or 587)
    fallback_port = int(settings.get("smtp_ssl_fallback_port") or 465)
    context = ssl.create_default_context()

    message = EmailMessage()
    message["From"] = HR_REPORT_SENDER
    message["To"] = to_addr
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="gmail.com")
    message.set_content(body)

    errors = []
    for mode, port in [("starttls", preferred_port), ("ssl", fallback_port)]:
        smtp = None
        try:
            if mode == "ssl":
                smtp = smtplib.SMTP_SSL(host, port, timeout=25, context=context)
                smtp.ehlo()
            else:
                smtp = smtplib.SMTP(host, port, timeout=25)
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(HR_REPORT_SENDER, password)
            smtp.send_message(message)
            try:
                smtp.quit()
            except Exception:
                pass
            set_state("hr_report_smtp_verified", "1")
            set_state("hr_report_smtp_last_error", "")
            return f"{mode}:{port}"
        except Exception as exc:
            errors.append(str(exc))
            try:
                if smtp:
                    smtp.quit()
            except Exception:
                pass

    set_state("hr_report_smtp_verified", "0")
    set_state("hr_report_smtp_last_error", errors[-1] if errors else "SMTP failed")
    raise RuntimeError(errors[-1] if errors else "HR ranking email could not be sent.")


def _claim_notification(notification_id):
    with conn() as c:
        row = c.execute(
            "SELECT * FROM recruitment_notifications WHERE id=?",
            (int(notification_id),),
        ).fetchone()
        if not row:
            return None
        if row["status"] not in {"QUEUED", "SEND_FAILED", "WAITING_LOGIN"}:
            return None
        c.execute(
            """
            UPDATE recruitment_notifications
            SET status='SENDING', attempts=attempts+1, error=NULL, updated_at=?
            WHERE id=?
            """,
            (now(), int(notification_id)),
        )
        return dict(row)


def _finish_notification(notification_id, status, error=None):
    with conn() as c:
        c.execute(
            """
            UPDATE recruitment_notifications
            SET status=?, error=?, sent_at=CASE WHEN ?='SENT' THEN ? ELSE sent_at END,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                str(error or "")[:2000] or None,
                status,
                now(),
                now(),
                int(notification_id),
            ),
        )


def resume_config_waiting_notifications():
    """Release notifications that were waiting only for a configured mail credential."""
    settings = load_settings()
    hr_ready = bool(re.sub(r"\s+", "", str(settings.get("hr_report_smtp_app_password") or "")))
    candidate_ready = bool(re.sub(r"\s+", "", str(settings.get("smtp_app_password") or "")))
    with conn() as c:
        if hr_ready:
            c.execute(
                """
                UPDATE recruitment_notifications
                SET status='QUEUED', error=NULL, updated_at=?
                WHERE status='WAITING_CONFIG' AND channel='EMAIL_REPORT'
                """,
                (now(),),
            )
        if candidate_ready:
            c.execute(
                """
                UPDATE recruitment_notifications
                SET status='QUEUED', error=NULL, updated_at=?
                WHERE status='WAITING_CONFIG' AND channel='EMAIL_CANDIDATE'
                """,
                (now(),),
            )


def process_notifications_once(limit=12):
    init_recruitment_pipeline_db()
    settings = load_settings()
    if not settings.get("automation_enabled", True):
        return {"attempted": 0, "sent": 0}

    with conn() as c:
        rows = c.execute(
            """
            SELECT * FROM recruitment_notifications
            WHERE status IN ('QUEUED','SEND_FAILED')
               OR (status='WAITING_LOGIN' AND datetime(updated_at) <= datetime('now','-10 seconds'))
            ORDER BY
                CASE message_type
                    WHEN 'ACK_WHATSAPP' THEN 0
                    WHEN 'INTERVIEW_EMAIL' THEN 1
                    WHEN 'INTERVIEW_WHATSAPP' THEN 2
                    WHEN 'DAILY_CONSOLIDATED_REPORT' THEN 3
                    WHEN 'ROLE_RANKING_REPORT' THEN 4
                    ELSE 9
                END,
                id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    attempted = 0
    sent = 0
    for raw in rows:
        claimed = _claim_notification(raw["id"])
        if not claimed:
            continue
        attempted += 1
        try:
            channel = claimed.get("channel")
            if channel == "EMAIL_REPORT":
                if not re.sub(r"\s+", "", str(settings.get("hr_report_smtp_app_password") or "")):
                    _finish_notification(claimed["id"], "WAITING_CONFIG", f"Add the Google App Password for {HR_REPORT_SENDER} in Settings.")
                    if claimed.get("message_type") == "ROLE_RANKING_REPORT":
                        with conn() as c:
                            c.execute(
                                """
                                UPDATE recruitment_role_state
                                SET report_status='WAITING_CONFIG', report_error=?, updated_at=?
                                WHERE lower(job_title)=lower(?)
                                """,
                                (f"Add the Google App Password for {HR_REPORT_SENDER} in Settings.", now(), claimed.get("job_title") or ""),
                            )
                    elif claimed.get("message_type") == "DAILY_CONSOLIDATED_REPORT":
                        set_state("daily_report_last_error", f"Add the Google App Password for {HR_REPORT_SENDER} in Settings.")
                    continue
                transport = _report_smtp_send(
                    claimed.get("recipient") or HR_REPORT_SENDER,
                    claimed.get("subject") or "Recruitment ranking",
                    claimed.get("body") or "",
                    settings,
                )
                _finish_notification(claimed["id"], "SENT")
                if claimed.get("message_type") == "ROLE_RANKING_REPORT":
                    with conn() as c:
                        c.execute(
                            """
                            UPDATE recruitment_role_state
                            SET report_status='SENT', report_sent_at=?, report_error=NULL, updated_at=?
                            WHERE lower(job_title)=lower(?)
                            """,
                            (now(), now(), claimed.get("job_title") or ""),
                        )
                    log("INFO", f"ROLE REPORT: sent {claimed.get('job_title')} using {HR_REPORT_SENDER} ({transport}).")
                elif claimed.get("message_type") == "DAILY_CONSOLIDATED_REPORT":
                    local_date = datetime.now().astimezone().date().isoformat()
                    set_state("daily_report_last_sent_date", local_date)
                    set_state("daily_report_last_sent_at", now())
                    set_state("daily_report_last_error", "")
                    log("INFO", f"DAILY RECRUITMENT REPORT: sent consolidated ongoing-role report using {HR_REPORT_SENDER} ({transport}).")
                sent += 1

            elif channel == "EMAIL_CANDIDATE":
                if not re.sub(r"\s+", "", str(settings.get("smtp_app_password") or "")):
                    _finish_notification(claimed["id"], "WAITING_CONFIG", "Candidate Gmail App Password is not configured.")
                    continue
                # Lazy import avoids a module cycle during startup.
                from automation import _smtp_send, valid_candidate_email

                recipient = (claimed.get("recipient") or "").strip().lower()
                if not valid_candidate_email(recipient, settings):
                    raise RuntimeError("Candidate email is not valid for interview delivery.")
                _smtp_send(
                    recipient,
                    claimed.get("subject") or "Interview invitation",
                    claimed.get("body") or "",
                    settings,
                )
                _finish_notification(claimed["id"], "SENT")
                log("INFO", f"INTERVIEW EMAIL: sent to {recipient}.")
                sent += 1

            elif channel == "WHATSAPP":
                if claimed.get("message_type") == "ACK_WHATSAPP" and not role_accepts_new_applications(claimed.get("job_title")):
                    _finish_notification(claimed["id"], "PAUSED", "Role is paused/closed; applicant acknowledgement stopped.")
                    continue
                if not settings.get("whatsapp_enabled", True):
                    _finish_notification(claimed["id"], "PAUSED", "WhatsApp automation is disabled.")
                    continue
                from chrome_cdp import send_whatsapp_web_message

                result = send_whatsapp_web_message(
                    claimed.get("recipient") or "",
                    claimed.get("body") or "",
                    default_country_code=str(settings.get("whatsapp_default_country_code") or "91"),
                )
                if not result.get("ok"):
                    code = result.get("code") or "WHATSAPP_FAILED"
                    message = result.get("message") or code
                    if code == "LOGIN_REQUIRED":
                        _finish_notification(claimed["id"], "WAITING_LOGIN", message)
                        set_state("whatsapp_web_status", "LOGIN_REQUIRED")
                    else:
                        raise RuntimeError(message)
                else:
                    _finish_notification(claimed["id"], "SENT")
                    set_state("whatsapp_web_status", "CONNECTED")
                    set_state("whatsapp_last_sent_at", now())
                    log("INFO", f"WHATSAPP {claimed.get('message_type')}: sent to {claimed.get('recipient')}.")
                    sent += 1
            else:
                raise RuntimeError(f"Unsupported notification channel: {channel}")

        except Exception as exc:
            _finish_notification(claimed["id"], "SEND_FAILED", str(exc))
            if claimed.get("channel") == "EMAIL_REPORT":
                if claimed.get("message_type") == "ROLE_RANKING_REPORT":
                    with conn() as c:
                        c.execute(
                            """
                            UPDATE recruitment_role_state
                            SET report_status='SEND_FAILED', report_error=?, updated_at=?
                            WHERE lower(job_title)=lower(?)
                            """,
                            (str(exc)[:2000], now(), claimed.get("job_title") or ""),
                        )
                elif claimed.get("message_type") == "DAILY_CONSOLIDATED_REPORT":
                    set_state("daily_report_last_error", str(exc)[:2000])
            log("WARN", f"Recruitment notification waiting: {exc}")

    return {"attempted": attempted, "sent": sent}


def _notification_map(application_id):
    with conn() as c:
        rows = c.execute(
            """
            SELECT message_type, status, error, sent_at
            FROM recruitment_notifications
            WHERE application_id=?
            """,
            (int(application_id),),
        ).fetchall()
    return {r["message_type"]: dict(r) for r in rows}


def decorate_role_payload(payload):
    init_recruitment_pipeline_db()
    out = dict(payload or {})
    role = dict(out.get("role") or {})
    title = _clean_role(role.get("job_title"))
    state = get_role_state(title) or ensure_role_state(title)
    role["lifecycle"] = state or {"lifecycle_status": "UNKNOWN"}
    out["role"] = role
    out["interview_schedule"] = interview_schedule_preview()

    decorated = []
    for item in out.get("applicants") or []:
        row = dict(item)
        flow = candidate_flow(row["id"])
        row["hr_flow"] = flow.get("flow")
        row["notifications"] = flow.get("notifications")
        decorated.append(row)

    by_id = {x["id"]: x for x in decorated}
    out["applicants"] = decorated
    for key in ("active_applicants", "auto_shortlist", "remaining", "removed"):
        out[key] = [by_id.get(x["id"], x) for x in (out.get(key) or [])]

    return out


def decorate_roles(roles):
    result = []
    for role in roles or []:
        item = dict(role)
        state = get_role_state(item.get("job_title")) or ensure_role_state(item.get("job_title"))
        item["lifecycle_status"] = (state or {}).get("lifecycle_status", "UNKNOWN")
        item["report_status"] = (state or {}).get("report_status", "IDLE")
        item["report_sent_at"] = (state or {}).get("report_sent_at")
        item["report_error"] = (state or {}).get("report_error")
        result.append(item)
    return result



def operations_overview(limit_roles=6, limit_recent=8):
    """Lightweight, read-only data for the operations dashboard.

    This keeps the Next.js overview fast: one backend request returns role-level
    pipeline counts plus the latest applicants instead of issuing one heavy role
    payload request per role.
    """
    init_recruitment_pipeline_db()
    role_limit = max(1, min(int(limit_roles or 6), 25))
    recent_limit = max(1, min(int(limit_recent or 8), 50))

    terminal_sql = """
        lower(trim(COALESCE(a.indeed_status,''))) IN
        ('hired','selected','not selected','rejected','withdrawn','archived')
    """

    with conn() as c:
        roles = c.execute(
            f"""
            SELECT
                rp.job_title,
                rp.description_status,
                COALESCE(rrs.lifecycle_status, 'UNKNOWN') AS lifecycle_status,
                COALESCE(rrs.report_status, 'IDLE') AS report_status,
                COUNT(a.id) AS applicant_count,
                SUM(CASE WHEN {terminal_sql} THEN 0 ELSE 1 END) AS active_applicant_count,
                SUM(CASE WHEN NOT ({terminal_sql})
                              AND upper(trim(COALESCE(a.send_status,'')))='SENT'
                         THEN 1 ELSE 0 END) AS acknowledged_count,
                SUM(CASE WHEN NOT ({terminal_sql})
                              AND cr.analysis_status='READY'
                         THEN 1 ELSE 0 END) AS ranked_count,
                SUM(CASE WHEN NOT ({terminal_sql})
                              AND cr.analysis_status='READY'
                              AND upper(trim(COALESCE(cf.hr_status,'PENDING'))) <> 'APPROVED'
                         THEN 1 ELSE 0 END) AS hr_review_count,
                SUM(CASE WHEN NOT ({terminal_sql})
                              AND upper(trim(COALESCE(cf.hr_status,'')))='APPROVED'
                         THEN 1 ELSE 0 END) AS interview_count,
                SUM(CASE WHEN {terminal_sql} THEN 1 ELSE 0 END) AS completed_count,
                SUM(CASE WHEN NOT ({terminal_sql})
                              AND COALESCE(cr.analysis_status,'PENDING') <> 'READY'
                         THEN 1 ELSE 0 END) AS waiting_count,
                SUM(CASE WHEN NOT ({terminal_sql})
                              AND COALESCE(cr.auto_shortlisted,0)=1
                         THEN 1 ELSE 0 END) AS top_match_count
            FROM role_profiles rp
            LEFT JOIN applications a
              ON lower(trim(a.job_title)) = lower(trim(rp.job_title))
            LEFT JOIN candidate_reviews cr
              ON cr.application_id = a.id
            LEFT JOIN recruitment_candidate_flow cf
              ON cf.application_id = a.id
            LEFT JOIN recruitment_role_state rrs
              ON lower(trim(rrs.job_title)) = lower(trim(rp.job_title))
            GROUP BY
                rp.job_title,
                rp.description_status,
                rrs.lifecycle_status,
                rrs.report_status
            ORDER BY
                CASE COALESCE(rrs.lifecycle_status,'UNKNOWN')
                    WHEN 'OPEN' THEN 0
                    WHEN 'UNKNOWN' THEN 1
                    WHEN 'PAUSED' THEN 2
                    ELSE 3
                END,
                COUNT(a.id) DESC,
                lower(rp.job_title)
            LIMIT ?
            """,
            (role_limit,),
        ).fetchall()

        recent = c.execute(
            """
            SELECT
                a.id,
                a.candidate_name,
                a.candidate_email,
                a.candidate_phone,
                a.job_title,
                a.indeed_status,
                a.profile_url,
                a.extraction_status,
                a.application_verified,
                a.decision_reason,
                a.send_status,
                a.sent_at,
                a.first_seen_at,
                a.last_seen_at,
                cr.analysis_status,
                cr.match_score,
                cr.rank_position,
                cr.auto_shortlisted,
                cf.hr_status,
                cf.interview_date
            FROM applications a
            LEFT JOIN candidate_reviews cr
              ON cr.application_id = a.id
            LEFT JOIN recruitment_candidate_flow cf
              ON cf.application_id = a.id
            ORDER BY
                COALESCE(a.first_seen_at, a.created_at) DESC,
                a.id DESC
            LIMIT ?
            """,
            (recent_limit,),
        ).fetchall()

    recent_rows = []
    for row in recent:
        item = dict(row)
        item["hr_flow"] = {
            "hr_status": item.pop("hr_status", None),
            "interview_date": item.pop("interview_date", None),
        }
        recent_rows.append(item)

    return {
        "roles": [dict(row) for row in roles],
        "recent": recent_rows,
        "generated_at": now(),
    }

def pipeline_status():
    init_recruitment_pipeline_db()
    settings = load_settings()
    with conn() as c:
        role = c.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN lifecycle_status='OPEN' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN lifecycle_status='PAUSED' THEN 1 ELSE 0 END) AS paused_count,
                SUM(CASE WHEN lifecycle_status='CLOSED' THEN 1 ELSE 0 END) AS closed_count,
                SUM(CASE WHEN report_status='QUEUED' THEN 1 ELSE 0 END) AS reports_queued,
                SUM(CASE WHEN report_status='SEND_FAILED' THEN 1 ELSE 0 END) AS reports_failed
            FROM recruitment_role_state
            """
        ).fetchone()
        notices = c.execute(
            """
            SELECT
                SUM(CASE WHEN status='QUEUED' THEN 1 ELSE 0 END) AS queued,
                SUM(CASE WHEN status='SENT' THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status='SEND_FAILED' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='WAITING_LOGIN' THEN 1 ELSE 0 END) AS waiting_login
            FROM recruitment_notifications
            """
        ).fetchone()
        approvals = c.execute(
            "SELECT COUNT(*) AS n FROM recruitment_candidate_flow WHERE hr_status='APPROVED'"
        ).fetchone()["n"]

    return {
        "roles": {
            "total": int(role["total"] or 0),
            "open": int(role["open_count"] or 0),
            "paused": int(role["paused_count"] or 0),
            "closed": int(role["closed_count"] or 0),
        },
        "notifications": {
            "queued": int(notices["queued"] or 0),
            "sent": int(notices["sent"] or 0),
            "failed": int(notices["failed"] or 0),
            "waiting_login": int(notices["waiting_login"] or 0),
        },
        "hr_approved": int(approvals or 0),
        "report_sender": HR_REPORT_SENDER,
        "report_recipient": settings.get("hr_report_recipient") or HR_REPORT_SENDER,
        "report_gmail_configured": bool(
            re.sub(r"\s+", "", str(settings.get("hr_report_smtp_app_password") or ""))
        ),
        "report_gmail_verified": get_state("hr_report_smtp_verified", "0") == "1",
        "report_gmail_error": get_state("hr_report_smtp_last_error"),
        "daily_report": daily_report_status(),
        "whatsapp_enabled": True,
        "whatsapp_auto_connect": bool(settings.get("whatsapp_auto_connect", True)),
        "whatsapp_status": get_state("whatsapp_web_status", "NOT_CONNECTED"),
        "whatsapp_last_sent_at": get_state("whatsapp_last_sent_at"),
        "interview_schedule": interview_schedule_preview(),
        "activation_at": get_state("recruitment_pipeline_activation_at"),
    }
