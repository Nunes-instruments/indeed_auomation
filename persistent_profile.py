from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from threading import RLock

from config import STATE_DIR, load_settings

PROFILE_FILE = STATE_DIR / "persistent_profile.json"
PROFILE_BACKUP = STATE_DIR / "persistent_profile.json.bak"
PROFILE_SCHEMA_VERSION = 1
GITHUB_REPOSITORY = "https://github.com/Nunes-instruments/indeed_auomation.git"

_lock = RLock()


def _stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(payload):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = PROFILE_FILE.with_suffix(".json.tmp")

    if PROFILE_FILE.exists():
        try:
            shutil.copy2(PROFILE_FILE, PROFILE_BACKUP)
        except Exception:
            pass

    temp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp, PROFILE_FILE)


def _load_existing():
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def sync_persistent_profile(settings=None, openai_status=None):
    """
    Store durable connection/preferences metadata only.

    Credentials are deliberately not copied into this file:
    - Gmail app passwords remain in the stable Windows-user settings store.
    - OpenAI key remains in Windows DPAPI storage.
    - Indeed/WhatsApp login remains in the user's normal Chrome profile.
    - GitHub authentication remains in Windows Git Credential Manager.

    This file records what the product should REUSE after restart/upgrade.
    """
    with _lock:
        s = settings or load_settings()
        previous = _load_existing()

        openai_status = openai_status or {}
        openai_configured = bool(openai_status.get("configured"))

        payload = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "saved_at": _stamp(),
            "storage_scope": "windows_user",
            "connection_policy": "reuse_saved_until_user_changes",
            "connections": {
                "candidate_gmail": {
                    "account": "nuneslead@gmail.com",
                    "saved": bool((s.get("smtp_app_password") or "").strip()),
                    "reuse": True,
                },
                "hr_report_gmail": {
                    "account": "nunescbe@gmail.com",
                    "saved": bool((s.get("hr_report_smtp_app_password") or "").strip()),
                    "reuse": True,
                },
                "openai_ranking": {
                    "saved": openai_configured,
                    "storage": openai_status.get("storage") or "windows_dpapi",
                    "reuse": True,
                },
                "indeed": {
                    "saved_workspace_url": str(s.get("indeed_candidates_url") or ""),
                    "reuse_chrome_profile": True,
                    "auto_reconnect": True,
                },
                "whatsapp": {
                    "enabled": bool(s.get("whatsapp_enabled", True)),
                    "auto_connect": bool(s.get("whatsapp_auto_connect", True)),
                    "country_code": str(s.get("whatsapp_default_country_code") or "91"),
                    "reuse_chrome_profile": True,
                },
                "github": {
                    "repository": GITHUB_REPOSITORY,
                    "branch": "main",
                    "reuse_windows_git_credentials": True,
                },
            },
            "preferences": {
                "company_name": str(s.get("company_name") or ""),
                "daily_report_time": str(s.get("daily_report_time") or "19:00"),
                "daily_report_enabled": bool(s.get("daily_consolidated_report_enabled", True)),
                "openai_ranking_enabled": bool(s.get("openai_ranking_enabled", True)),
                "openai_ranking_daily_call_limit": int(s.get("openai_ranking_daily_call_limit", 40) or 0),
            },
        }

        # Preserve future metadata written by a newer build when possible.
        if isinstance(previous.get("custom"), dict):
            payload["custom"] = previous["custom"]

        _atomic_write(payload)
        return payload


def persistence_status(settings=None, openai_status=None):
    s = settings or load_settings()

    with _lock:
        current = _load_existing()

        # First run or old version upgrade: create the profile automatically.
        if not current:
            current = sync_persistent_profile(
                settings=s,
                openai_status=openai_status,
            )

    connections = current.get("connections") or {}

    return {
        "saved_at": current.get("saved_at"),
        "policy": current.get("connection_policy") or "reuse_saved_until_user_changes",
        "scope": current.get("storage_scope") or "windows_user",
        "candidate_gmail_saved": bool(
            (connections.get("candidate_gmail") or {}).get("saved")
            or (s.get("smtp_app_password") or "").strip()
        ),
        "hr_gmail_saved": bool(
            (connections.get("hr_report_gmail") or {}).get("saved")
            or (s.get("hr_report_smtp_app_password") or "").strip()
        ),
        "openai_saved": bool(
            (connections.get("openai_ranking") or {}).get("saved")
            or (openai_status or {}).get("configured")
        ),
        "indeed_reuses_chrome": True,
        "whatsapp_reuses_chrome": True,
        "github_repository": (
            (connections.get("github") or {}).get("repository")
            or GITHUB_REPOSITORY
        ),
        "github_branch": (
            (connections.get("github") or {}).get("branch")
            or "main"
        ),
        "message": (
            "Saved connections and preferences are reused after restart and "
            "software updates. They change only when you explicitly save or replace them."
        ),
    }
