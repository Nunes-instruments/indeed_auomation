from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from threading import RLock

BASE_DIR = Path(__file__).resolve().parent

# V11.1 uses one stable per-Windows-user state directory. This means the Gmail
# App Password, templates and Indeed workspace survive when the program is
# stopped, restarted, or a newer ZIP is extracted into a different folder.
LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
if LOCALAPPDATA:
    STATE_DIR = Path(LOCALAPPDATA) / "NunesRecruitmentConsole"
else:
    # Portable/test fallback.
    STATE_DIR = BASE_DIR / "data" / "persistent"

STATE_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = STATE_DIR / "settings.json"
BUNDLED_SETTINGS_FILE = BASE_DIR / "settings.json"

_lock = RLock()

DEFAULTS = {
    "company_name": "Your Company Name",
    "company_email": "nuneslead@gmail.com",
    "smtp_app_password": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_ssl_fallback_port": 465,

    "indeed_start_url": "https://employers.indeed.com/candidates?statusName=New&tab=manage",
    "indeed_candidates_url": "",

    "automation_enabled": True,
    "monitoring_enabled": True,
    "auto_scan": True,
    "auto_send": True,
    "auto_connect_chrome": True,
    "auto_open_indeed": True,
    "process_current_candidates_once": True,
    "initial_catchup_new_only": True,

    "allow_candidate_page_email_fallback": True,

    "subject_template": "Thank you for applying – {job_title}",
    "body_template": (
        "Dear {candidate_name},\n\n"
        "Thank you for applying for the {job_title} position at {company_name}.\n\n"
        "We have received your application and our team will review your profile. "
        "If your experience matches the role, we will contact you regarding the next steps.\n\n"
        "Regards,\n"
        "{company_name}"
    ),

    "blocked_email_domains": [
        "indeed.com",
        "indeedemail.com",
        "indeedmail.com",
        "example.com",
    ],
    "blocked_email_local_parts": [
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "mailer-daemon",
    ],

    "local_ai_enabled": False,
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "llama3.2:3b",

    # Cost-controlled OpenAI ranking refinement. The API key is NOT stored in
    # settings.json and is used only by review_engine.py through openai_ranker.py.
    "openai_ranking_enabled": True,
    "openai_ranking_model": "gpt-5.6-luna",
    "openai_ranking_min_local_score": 45.0,
    "openai_ranking_max_local_score": 90.0,
    "openai_ranking_ai_weight": 0.12,
    "openai_ranking_daily_call_limit": 40,
    "openai_ranking_max_input_chars": 7000,

    # Role-wise HR ranking reports. The sender is fixed by business workflow.
    "hr_report_sender_email": "nunescbe@gmail.com",
    "hr_report_recipient": "nunescbe@gmail.com",
    "hr_report_smtp_app_password": "",
    "auto_send_role_reports": False,
    "daily_consolidated_report_enabled": True,
    "daily_report_time": "19:00",

    # WhatsApp Web uses the same already-approved Chrome browser. A one-time QR
    # login may be required; the Chrome profile keeps the session afterwards.
    "whatsapp_enabled": True,
    "whatsapp_auto_connect": True,
    "whatsapp_default_country_code": "91",
    "whatsapp_ack_template": (
        "Dear {candidate_name}, thank you for applying for the {job_title} position at "
        "{company_name}. We have received your application. Our recruitment team will "
        "review your profile and contact you regarding the next steps."
    ),
    "interview_subject_template": "Interview invitation – {job_title} – {interview_date}",
    "interview_body_template": (
        "Dear {candidate_name},\n\n"
        "Our HR team has reviewed your application for the {job_title} position at "
        "{company_name} and selected you for the interview stage.\n\n"
        "Your interview is scheduled for {interview_date}. Our HR team will contact you "
        "with the time and venue/meeting details.\n\n"
        "Regards,\n{company_name}"
    ),
    "whatsapp_interview_template": (
        "Dear {candidate_name}, our HR team has selected your application for the "
        "{job_title} position at {company_name}. Your interview is scheduled for "
        "{interview_date}. Our HR team will contact you with the time and venue/meeting details."
    ),
}


def _legacy_settings_candidates():
    candidates = []

    # Current extracted folder (useful if a user copied settings manually).
    candidates.append(BUNDLED_SETTINGS_FILE)

    roots = [BASE_DIR.parent]

    home = Path.home()
    for extra in [home / "Desktop", home / "Downloads"]:
        if extra.exists():
            roots.append(extra)

    patterns = [
        "Nunes_Indeed_Recruitment_Console*/settings.json",
        "NUNES_V11*/settings.json",
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
        if rp == SETTINGS_FILE.resolve():
            continue
        key = str(rp).lower()
        if key in seen or not p.exists():
            continue
        seen.add(key)
        unique.append(p)

    unique.sort(
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return unique


def _bootstrap_settings():
    if SETTINGS_FILE.exists():
        return

    # Prefer the newest previous-version settings so the Gmail App Password and
    # user message template do not need to be re-entered after an upgrade.
    for candidate in _legacy_settings_candidates():
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out = DEFAULTS.copy()
                out.update(data)
                out["company_email"] = "nuneslead@gmail.com"
                out["hr_report_sender_email"] = "nunescbe@gmail.com"
                SETTINGS_FILE.write_text(
                    json.dumps(out, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return
        except Exception:
            continue

    SETTINGS_FILE.write_text(
        json.dumps(DEFAULTS, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_settings():
    with _lock:
        _bootstrap_settings()
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        out = DEFAULTS.copy()
        out.update(data)
        out["company_email"] = "nuneslead@gmail.com"
        out["hr_report_sender_email"] = "nunescbe@gmail.com"

        # A blank template is almost always an interrupted/old save. Keep the
        # production acknowledgement usable instead of showing an empty editor.
        if not str(out.get("subject_template") or "").strip():
            out["subject_template"] = DEFAULTS["subject_template"]
        if not str(out.get("body_template") or "").strip():
            out["body_template"] = DEFAULTS["body_template"]
        for key in ("whatsapp_ack_template", "interview_subject_template", "interview_body_template", "whatsapp_interview_template"):
            if not str(out.get(key) or "").strip():
                out[key] = DEFAULTS[key]
        if not str(out.get("company_name") or "").strip():
            out["company_name"] = DEFAULTS["company_name"]

        return out


def save_settings(data):
    with _lock:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        out = DEFAULTS.copy()
        out.update(data)
        out["company_email"] = "nuneslead@gmail.com"
        out["hr_report_sender_email"] = "nunescbe@gmail.com"

        if not str(out.get("subject_template") or "").strip():
            out["subject_template"] = DEFAULTS["subject_template"]
        if not str(out.get("body_template") or "").strip():
            out["body_template"] = DEFAULTS["body_template"]
        for key in ("whatsapp_ack_template", "interview_subject_template", "interview_body_template", "whatsapp_interview_template"):
            if not str(out.get(key) or "").strip():
                out[key] = DEFAULTS[key]
        if not str(out.get("company_name") or "").strip():
            out["company_name"] = DEFAULTS["company_name"]

        # Atomic write: a power loss/interrupted process can no longer leave a
        # half-written JSON file that later looks like settings disappeared.
        temp_file = SETTINGS_FILE.with_suffix(".json.tmp")
        backup_file = SETTINGS_FILE.with_suffix(".json.bak")

        if SETTINGS_FILE.exists():
            try:
                shutil.copy2(SETTINGS_FILE, backup_file)
            except Exception:
                pass

        temp_file.write_text(
            json.dumps(out, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp_file, SETTINGS_FILE)

        # Read-back verification so the API never claims Save succeeded when
        # the file was not actually persisted.
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise RuntimeError("Saved settings could not be verified.")

        return out
