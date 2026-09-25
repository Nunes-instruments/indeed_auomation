from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from config import STATE_DIR

API_URL = "https://api.openai.com/v1/responses"
SECRET_DIR = STATE_DIR / "secrets"
SECRET_FILE = SECRET_DIR / "openai_ranking_key.dpapi"
ENV_KEY_NAME = "NUNES_OPENAI_RANKING_API_KEY"


def _powershell_exe():
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        from shutil import which
        found = which(name)
        if found:
            return found
    return None


def save_ranking_api_key(api_key: str):
    """Persist the ranking-only API key with Windows user DPAPI.

    The secret is never written to settings.json and is never returned by the API.
    On non-Windows test systems, callers should use the environment variable instead.
    """
    value = str(api_key or "").strip()
    if not value:
        raise ValueError("OpenAI ranking API key cannot be blank.")
    if not value.startswith("sk-"):
        raise ValueError("This does not look like an OpenAI API key.")

    SECRET_DIR.mkdir(parents=True, exist_ok=True)

    ps = _powershell_exe()
    if os.name != "nt" or not ps:
        raise RuntimeError(
            "Secure key storage is available on Windows through DPAPI. "
            f"Alternatively set {ENV_KEY_NAME} for this process/user."
        )

    env = os.environ.copy()
    env["NUNES_RANKING_KEY_TO_SAVE"] = value
    env["NUNES_RANKING_KEY_FILE"] = str(SECRET_FILE)
    script = r'''
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $env:NUNES_RANKING_KEY_FILE
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$sec = ConvertTo-SecureString $env:NUNES_RANKING_KEY_TO_SAVE -AsPlainText -Force
$enc = ConvertFrom-SecureString $sec
Set-Content -LiteralPath $env:NUNES_RANKING_KEY_FILE -Value $enc -Encoding ASCII
'''
    proc = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "Could not save ranking API key securely.").strip())
    return {"configured": True, "storage": "windows-dpapi"}


def remove_ranking_api_key():
    try:
        SECRET_FILE.unlink(missing_ok=True)
    except TypeError:
        if SECRET_FILE.exists():
            SECRET_FILE.unlink()
    return {"configured": bool(os.environ.get(ENV_KEY_NAME)), "storage": "environment" if os.environ.get(ENV_KEY_NAME) else None}


def _decrypt_dpapi_key():
    if not SECRET_FILE.exists() or os.name != "nt":
        return None
    ps = _powershell_exe()
    if not ps:
        return None
    env = os.environ.copy()
    env["NUNES_RANKING_KEY_FILE"] = str(SECRET_FILE)
    script = r'''
$ErrorActionPreference = 'Stop'
$enc = Get-Content -LiteralPath $env:NUNES_RANKING_KEY_FILE -Raw
$sec = ConvertTo-SecureString $enc
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
'''
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            value = proc.stdout.strip()
            return value if value.startswith("sk-") else None
    except Exception:
        pass
    return None


def get_ranking_api_key():
    env_value = str(os.environ.get(ENV_KEY_NAME) or "").strip()
    if env_value:
        return env_value
    return _decrypt_dpapi_key()


def ranking_key_status():
    env_value = bool(str(os.environ.get(ENV_KEY_NAME) or "").strip())
    dpapi = SECRET_FILE.exists()
    return {
        "configured": bool(env_value or dpapi),
        "storage": "environment" if env_value else ("windows-dpapi" if dpapi else None),
    }


def _extract_response_text(payload: dict):
    # SDK-independent parsing of the Responses API HTTP payload.
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                value = content.get("text")
                if isinstance(value, str):
                    chunks.append(value)
    return "\n".join(chunks).strip()


def _json_object_from_text(text: str):
    value = str(text or "").strip()
    try:
        obj = json.loads(value)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"\{.*\}", value, flags=re.S)
    if not m:
        raise ValueError("OpenAI ranking response did not contain JSON.")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("OpenAI ranking response was not a JSON object.")
    return obj


def semantic_refine_score(*, requirements, evidence, local_score, model="gpt-5.6-luna", max_input_chars=7000):
    """Low-cost, ranking-only semantic refinement.

    Sends only role requirements plus already-extracted evidence snippets. It does
    not send the entire resume and it never asks the model to make a hiring
    decision. Protected/sensitive traits are explicitly excluded.
    """
    api_key = get_ranking_api_key()
    if not api_key:
        return {"used": False, "reason": "api_key_not_configured"}

    reqs = [str(x)[:280] for x in (requirements or [])[:24]]
    evidence_rows = []
    for item in (evidence or [])[:24]:
        evidence_rows.append({
            "requirement": str(item.get("requirement") or "")[:280],
            "status": str(item.get("status") or ""),
            "coverage": float(item.get("coverage") or 0),
            "evidence": str(item.get("evidence") or "")[:360],
            "matched_terms": [str(x)[:80] for x in (item.get("matched_terms") or [])[:12]],
        })

    compact = json.dumps({
        "local_keyword_evidence_score": round(float(local_score or 0), 1),
        "requirements": reqs,
        "evidence": evidence_rows,
    }, ensure_ascii=False)
    compact = compact[: max(1500, int(max_input_chars or 7000))]

    system = (
        "You are a strict recruitment evidence checker. Evaluate ONLY job-relevant "
        "skills, experience and education against the supplied role requirements. "
        "Ignore and never infer gender, age, race, religion, caste, disability, "
        "nationality, marital status, political affiliation or other protected traits. "
        "Do not make a hiring decision. Return only compact JSON with keys "
        "semantic_score (0-100), confidence (0-1), and note (max 18 words). "
        "Do not give 100 unless every supplied requirement has explicit evidence."
    )
    user = "Refine the evidence strength conservatively. Data:\n" + compact

    body = {
        "model": str(model or "gpt-5.6-luna"),
        "reasoning": {"effort": "none"},
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ],
        "max_output_tokens": 120,
    }

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"OpenAI ranking API HTTP {exc.code}: {detail}")
    except Exception as exc:
        raise RuntimeError(f"OpenAI ranking API request failed: {exc}")

    parsed = _json_object_from_text(_extract_response_text(payload))
    semantic = max(0.0, min(100.0, float(parsed.get("semantic_score") or 0)))
    confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0)))
    note = re.sub(r"\s+", " ", str(parsed.get("note") or "")).strip()[:180]

    usage = payload.get("usage") or {}
    return {
        "used": True,
        "semantic_score": round(semantic, 1),
        "confidence": round(confidence, 3),
        "note": note,
        "model": str(model or "gpt-5.6-luna"),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }
