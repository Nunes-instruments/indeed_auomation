from __future__ import annotations
import re, json, smtplib, ssl, socket, html, zipfile, unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import requests
from pypdf import PdfReader
from docx import Document

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None


from config import load_settings
from recruitment_pipeline import role_accepts_new_applications
from database import (
    log,
    upsert_application,
    get_by_source_key,
    update_send,
    already_sent,
    list_ready_to_send,
    now,
    set_state,
    get_state,
    claim_application_for_send,
    release_send_claim,
    finalize_sent_response,
    repair_duplicate_email_metadata,
    list_ready_corrections,
    claim_correction_for_send,
    release_correction_claim,
    finalize_correction_send,
)

EMAIL_RE = re.compile(
    r'(?i)(?<![\w.+-])([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})(?![\w.-])'
)
PHONE_RE = re.compile(r'(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{9}(?!\d)')


def normalize_contact_text(text):
    """
    Normalize common resume formatting/OCR spacing without inventing data.

    Examples safely normalized:
      name @ gmail.com
      name@gmail . com
      name [at] gmail [dot] com
      name (at) gmail (dot) com

    A local part, domain and TLD must all already exist in the source.
    """
    value = html.unescape(str(text or ""))
    value = unicodedata.normalize("NFKC", value)
    value = (
        value.replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )

    # Obfuscated at/dot forms.
    value = re.sub(
        r"(?i)([A-Z0-9._%+\-]{1,80})\s*"
        r"(?:\[at\]|\(at\)|\{at\}|\bat\b|@)\s*"
        r"([A-Z0-9\-]{1,63}(?:\s*\.\s*[A-Z0-9\-]{1,63})*)\s*"
        r"(?:\[dot\]|\(dot\)|\{dot\}|\bdot\b|\.)\s*"
        r"([A-Z]{2,24})",
        lambda m: (
            re.sub(r"\s+", "", m.group(1))
            + "@"
            + re.sub(r"\s*\.\s*", ".", m.group(2))
            + "."
            + m.group(3)
        ),
        value,
    )

    # Ordinary address with layout whitespace around @ or dots.
    value = re.sub(
        r"(?i)([A-Z0-9._%+\-]{1,80})\s*@\s*"
        r"([A-Z0-9\-]{1,63}(?:\s*\.\s*[A-Z0-9\-]{1,63})+)",
        lambda m: (
            re.sub(r"\s+", "", m.group(1))
            + "@"
            + re.sub(r"\s*\.\s*", ".", m.group(2))
        ),
        value,
    )

    return value


def _docx_complete_text(path):
    chunks = []

    try:
        doc = Document(str(path))

        for p in doc.paragraphs:
            if p.text:
                chunks.append(p.text)

        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text:
                        chunks.append(cell.text)

        for section in doc.sections:
            for container in [section.header, section.footer]:
                for p in container.paragraphs:
                    if p.text:
                        chunks.append(p.text)
                for table in container.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            if cell.text:
                                chunks.append(cell.text)
    except Exception:
        pass

    # python-docx can miss text boxes/shapes. Read every Word XML text node too.
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not (
                    name.startswith("word/")
                    and name.lower().endswith(".xml")
                ):
                    continue
                raw = zf.read(name)
                try:
                    root = ET.fromstring(raw)
                except Exception:
                    continue
                words = []
                for node in root.iter():
                    if node.tag.endswith("}t") and node.text:
                        words.append(node.text)
                if words:
                    chunks.append(" ".join(words))
    except Exception:
        pass

    return "\n".join(dict.fromkeys(x for x in chunks if x))


def _pdf_complete_text(path):
    chunks = []

    # Primary pypdf extraction.
    try:
        reader = PdfReader(str(path))
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(text)
    except Exception as e:
        log("WARN", f"pypdf extraction fallback required for {Path(path).name}: {e}")

    # PyMuPDF often succeeds on PDFs whose text layout pypdf misses.
    if fitz is not None:
        try:
            with fitz.open(str(path)) as doc:
                for page in doc:
                    text = page.get_text("text") or ""
                    if text.strip():
                        chunks.append(text)
        except Exception as e:
            log("WARN", f"PyMuPDF extraction failed for {Path(path).name}: {e}")

    return "\n".join(dict.fromkeys(x for x in chunks if x))


def _binary_contact_fallback(path):
    """
    Last text-level fallback for legacy DOC/PDF files. We only keep decoded
    content if it contains an email-like marker; no values are generated.
    """
    try:
        data = Path(path).read_bytes()
    except Exception:
        return ""

    candidates = []
    for enc in ("utf-8", "latin-1", "utf-16le", "utf-16be"):
        try:
            text = data.decode(enc, errors="ignore")
        except Exception:
            continue
        normalized = normalize_contact_text(text)
        if "@" in normalized or "[at]" in text.lower() or "(at)" in text.lower():
            candidates.append(normalized[:250000])

    return "\n".join(candidates)


def extract_text_from_resume(path):
    if not path:
        return ""

    path = Path(path)
    if not path.exists():
        return ""

    try:
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            text = _pdf_complete_text(path)
        elif suffix == ".docx":
            text = _docx_complete_text(path)
        elif suffix in {".txt", ".rtf", ".html", ".htm", ".csv"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
        else:
            text = ""

        # Always add a conservative binary contact fallback. This catches email
        # strings in some legacy files even when layout/text extraction fails.
        binary = _binary_contact_fallback(path)
        if binary:
            text = (text + "\n" + binary).strip()

        return normalize_contact_text(text)

    except Exception as e:
        log("ERROR", f"Resume extraction failed for {path.name}: {e}")
        return ""


def valid_candidate_email(addr, settings):
    addr = normalize_contact_text(addr).strip().lower().strip(".,;:()[]<>")
    if not EMAIL_RE.fullmatch(addr):
        return False

    local, domain = addr.rsplit("@", 1)
    own = (settings.get("company_email") or "").strip().lower()

    if own and addr == own:
        return False

    if local in {x.lower() for x in settings["blocked_email_local_parts"]}:
        return False

    for d in settings["blocked_email_domains"]:
        d = d.lower()
        if domain == d or domain.endswith("." + d):
            return False

    return True


def all_candidate_emails(text, settings):
    normalized = normalize_contact_text(text)
    out = []
    seen = set()

    for m in EMAIL_RE.finditer(normalized):
        addr = m.group(1).lower().strip(".,;:()[]<>")
        if addr in seen:
            continue
        seen.add(addr)
        if valid_candidate_email(addr, settings):
            out.append(addr)

    return out


def _email_name_score(addr, candidate_name):
    local, domain = addr.lower().rsplit("@", 1)
    local_compact = re.sub(r"[^a-z0-9]", "", local)

    tokens = [
        re.sub(r"[^a-z0-9]", "", x.lower())
        for x in re.findall(r"[A-Za-z0-9]+", candidate_name or "")
    ]
    tokens = [x for x in tokens if len(x) >= 3]

    score = 0

    if domain == "gmail.com":
        # Gmail is common in the user's applicant resumes, but it is only a
        # ranking preference. Other real candidate domains remain valid.
        score += 5

    for token in tokens:
        if token and token in local_compact:
            score += 10

    if len(tokens) >= 2:
        joined = "".join(tokens[:2])
        reverse = "".join(reversed(tokens[:2]))
        if joined and joined in local_compact:
            score += 15
        if reverse and reverse in local_compact:
            score += 10

    return score


def choose_candidate_email(item, resume_text, candidate_name, settings):
    """
    Strict same-applicant email selection.

    Production rule:
      - prefer a real address from THIS candidate's resume;
      - otherwise use explicit contact/mailto values captured from THIS
        candidate detail panel;
      - never use a page-wide email pool;
      - when multiple real addresses exist and ownership is ambiguous, return
        no email rather than risk messaging the wrong person.
    """
    scored = {}

    def add(addr, source, base_score, ownership="scoped"):
        addr = normalize_contact_text(addr).strip().lower().strip(".,;:()[]<>")
        if not valid_candidate_email(addr, settings):
            return

        name_score = _email_name_score(addr, candidate_name)
        score = base_score + name_score

        old = scored.get(addr)
        candidate = {
            "address": addr,
            "source": source,
            "score": score,
            "name_score": name_score,
            "ownership": ownership,
        }

        if not old or score > old["score"]:
            scored[addr] = candidate

    # 1) Complete resume is the primary evidence.
    resume_emails = all_candidate_emails(resume_text, settings)
    for addr in resume_emails:
        add(addr, "indeed_resume", 180, "resume")

    # 2) Explicit mailto/contact values from the selected candidate detail.
    for value in item.get("profile_emails") or []:
        for addr in all_candidate_emails(value, settings):
            add(
                addr,
                "indeed_candidate_contact_verified",
                190,
                "candidate_contact",
            )

    # 3) Scoped contact/resume text only. V11.3 intentionally does NOT scan
    # the complete Indeed page/body/HTML for recipient addresses because that
    # can contain another applicant or the employer account.
    for value in item.get("contact_texts") or []:
        for addr in all_candidate_emails(value, settings):
            add(
                addr,
                "indeed_candidate_contact_verified",
                160,
                "candidate_scope",
            )

    if not scored:
        return None, None, "NO_VERIFIED_EMAIL"

    ranked = sorted(
        scored.values(),
        key=lambda x: (-x["score"], x["address"]),
    )

    best = ranked[0]

    # One real address in the candidate-specific evidence is safe.
    if len(ranked) == 1:
        return best["address"], best["source"], "VERIFIED_SINGLE_EMAIL"

    second = ranked[1]

    # Explicit candidate contact is safe even if resume contains another
    # address such as a reference/recruiter.
    if best["ownership"] == "candidate_contact":
        return best["address"], best["source"], "VERIFIED_CONTACT_EMAIL"

    # With multiple resume/scoped addresses, require a clear name match and a
    # meaningful margin over the next address.
    if (
        best["name_score"] >= 10
        and best["score"] - second["score"] >= 10
    ):
        return best["address"], best["source"], "VERIFIED_NAME_MATCH"

    # Ambiguous multiple emails: do not send.
    return None, None, "AMBIGUOUS_MULTIPLE_EMAILS"


def first_candidate_email(text, settings):
    emails = all_candidate_emails(text, settings)
    return emails[0] if emails else None


def first_phone(text):
    if not text:
        return None
    compact = re.sub(r'[\s()\-]+', '', text)
    m = PHONE_RE.search(compact)
    return m.group(0) if m else None

def name_from_resume(text):
    lines = [re.sub(r'\s+',' ',x).strip(" |-") for x in (text or "").splitlines()]
    for line in lines[:15]:
        if not 3 <= len(line) <= 65:
            continue
        low = line.lower()
        if any(x in low for x in ["resume","curriculum","vitae","email","phone","mobile","objective","profile","summary"]):
            continue
        if EMAIL_RE.search(line) or any(ch.isdigit() for ch in line):
            continue
        parts = line.split()
        if 1 <= len(parts) <= 5 and all(re.match(r"^[A-Za-z][A-Za-z.'-]*$",x) for x in parts):
            return line.title()
    return None

def local_ai_extract(resume_text, settings):
    if not settings.get("local_ai_enabled") or not resume_text.strip():
        return {}
    prompt = (
        "Extract candidate_name, candidate_email and candidate_phone from this job applicant resume. "
        "Return JSON only. Never invent a value; use null if missing.\n\nRESUME:\n"
        + resume_text[:14000]
    )
    try:
        r = requests.post(
            (settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/api/generate",
            json={
                "model": settings.get("ollama_model") or "llama3.2:3b",
                "prompt": prompt, "stream": False, "format": "json"
            },
            timeout=45
        )
        r.raise_for_status()
        data = json.loads(r.json().get("response","{}"))
        if not isinstance(data,dict):
            return {}
        ai_email = (data.get("candidate_email") or "").strip().lower()
        # AI is never trusted to invent an email.
        if ai_email and (ai_email not in resume_text.lower() or not valid_candidate_email(ai_email,settings)):
            data["candidate_email"] = None
        return data
    except Exception as e:
        log("WARN", f"Local AI unavailable: {e}")
        return {}

def sender_configured(settings):
    return bool(
        (settings.get("company_email") or "").strip()
        and (settings.get("smtp_app_password") or "").replace(" ", "").strip()
    )



def first_verified_profile_email(values, settings):
    for value in values or []:
        addr = (value or "").strip().lower()
        if valid_candidate_email(addr, settings):
            return addr
    return None


def first_verified_profile_phone(values):
    for value in values or []:
        value = (value or "").strip()
        if not value:
            continue
        phone = first_phone(value)
        if phone:
            return phone
    return None



INVALID_CANDIDATE_NAMES = {
    "all open and paused jobs",
    "all jobs",
    "jobs",
    "job",
    "education",
    "yes",
    "no",
    "candidates",
    "candidate",
    "applicants",
    "applicant",
    "activity",
    "interest",
    "matches",
    "matches to job post",
    "manage candidates",
    "find candidates",
    "download cv",
    "download resume",
    "core skills",
    "resume",
    "contact information",
}


def plausible_candidate_name(value):
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    low = name.lower()

    if not name or len(name) < 2 or len(name) > 100:
        return False

    if low in INVALID_CANDIDATE_NAMES:
        return False

    if low.startswith(
        (
            "all open",
            "all applications",
            "your job",
            "job title",
            "sort by",
            "filter",
        )
    ):
        return False

    if not re.search(r"[A-Za-z]", name):
        return False

    return True


def meaningful_job_title(value):
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    return bool(
        title
        and title.lower() not in {
            "the position",
            "position",
            "job",
            "the job",
            "unknown",
        }
    )


def process_indeed_results(results):
    settings = load_settings()
    processed = 0
    ready = 0

    for item in results:
        source_key = item.get("source_key")
        if not source_key:
            continue

        raw_name = item.get("candidate_name") or ""
        if not plausible_candidate_name(raw_name):
            log(
                "WARN",
                f"Skipped non-candidate Indeed UI row: {raw_name!r}"
            )
            continue

        existing = get_by_source_key(source_key)
        attempts = int(existing.get("extraction_attempts") or 0) if existing else 0
        attempts += 1

        resume_path = item.get("resume_path")
        resume_text = (
            item.get("resume_text")
            or extract_text_from_resume(resume_path)
        )

        ai = (
            local_ai_extract(resume_text, settings)
            if resume_text
            else {}
        )

        candidate_name_hint = (
            name_from_resume(resume_text)
            or (ai.get("candidate_name") if ai else None)
            or item.get("candidate_name")
            or "Candidate"
        )

        email_addr, email_source, email_verification = choose_candidate_email(
            item=item,
            resume_text=resume_text,
            candidate_name=candidate_name_hint,
            settings=settings,
        )

        # AI may help only when it extracted an address that is literally
        # present in the resume. It never invents a missing email.
        if not email_addr and ai.get("candidate_email"):
            ai_email = normalize_contact_text(ai["candidate_email"]).lower()
            if (
                ai_email in normalize_contact_text(resume_text).lower()
                and valid_candidate_email(ai_email, settings)
            ):
                email_addr = ai_email
                email_source = "indeed_resume_ai_verified"
                email_verification = "VERIFIED_AI_LITERAL_RESUME_EMAIL"

        candidate_name = candidate_name_hint

        phone = (
            first_phone(resume_text)
            or (ai.get("candidate_phone") if ai else None)
            or first_verified_profile_phone(item.get("profile_phones"))
        )

        incoming_job = item.get("job_title")
        existing_job = existing.get("job_title") if existing else None

        if meaningful_job_title(incoming_job):
            job_title = incoming_job
        elif meaningful_job_title(existing_job):
            job_title = existing_job
        else:
            job_title = "the position"

        application_verified = bool(
            item.get("scope_is_candidate_specific")
        )
        application_checked_at = now() if application_verified else None

        # Production decision order:
        # exact application -> verified email -> exact role -> send.
        if not application_verified:
            extraction_status = "NEEDS_REVIEW_APPLICATION"
            send_status = "NOT_SENT"
            decision_reason = (
                "Waiting: exact Indeed application detail is not verified yet"
            )

        elif email_addr and meaningful_job_title(job_title):
            extraction_status = "VERIFIED_EMAIL_AND_ROLE"

            if not role_accepts_new_applications(job_title):
                send_status = "SKIPPED"
                decision_reason = (
                    "Skipped: this Indeed job is paused or closed, so new applicant outreach is stopped"
                )
            elif already_sent(email_addr):
                send_status = "DUPLICATE_SKIPPED"
                decision_reason = (
                    "Skipped: acknowledgement already sent to this email address"
                )
            else:
                send_status = (
                    "SENT"
                    if existing and existing.get("send_status") == "SENT"
                    else "READY"
                )
                decision_reason = (
                    "Ready: application, candidate email and applied role verified"
                )
                ready += 1

        elif email_addr and not meaningful_job_title(job_title):
            extraction_status = "NEEDS_REVIEW_ROLE"
            send_status = "NOT_SENT"
            decision_reason = "Waiting: exact applied role not verified yet"

        elif email_verification == "AMBIGUOUS_MULTIPLE_EMAILS":
            extraction_status = "SKIPPED_AMBIGUOUS_EMAIL"
            send_status = "SKIPPED"
            decision_reason = (
                "Skipped: multiple candidate emails found and ownership is ambiguous"
            )

        elif item.get("resume_found") and not resume_text.strip():
            extraction_status = "NEEDS_REVIEW_RESUME_NO_TEXT"
            send_status = "NOT_SENT"
            decision_reason = (
                "Waiting: resume was found but readable resume text is not available yet"
            )

        elif not item.get("resume_found"):
            extraction_status = "NEEDS_REVIEW_NO_RESUME_FOUND"
            send_status = "NOT_SENT"
            decision_reason = "Waiting: candidate resume is not available yet"

        else:
            extraction_status = "SKIPPED_NO_EMAIL_IN_RESUME"
            send_status = "SKIPPED"
            decision_reason = (
                "Skipped: no valid candidate email found in the resume/contact data"
            )

        row = {
            "source_key": source_key,
            "profile_url": item.get("profile_url"),
            "first_seen_at": existing.get("first_seen_at") if existing else now(),
            "last_seen_at": now(),
            "candidate_name": str(candidate_name)[:120],
            "candidate_email": email_addr,
            "candidate_phone": str(phone)[:60] if phone else None,
            "job_title": str(job_title)[:200],
            "indeed_status": (
                item.get("indeed_status")
                or (existing.get("indeed_status") if existing else None)
            ),
            "resume_filename": Path(resume_path).name if resume_path else None,
            "resume_path": resume_path,
            "email_source": email_source if email_addr else None,
            "extraction_status": extraction_status,
            "extraction_attempts": attempts,
            "application_verified": 1 if application_verified else 0,
            "application_checked_at": (
                application_checked_at
                or (existing.get("application_checked_at") if existing else None)
            ),
            "decision_reason": decision_reason,
            "send_status": send_status,
            "send_error": (
                item.get("resume_error")
                or (
                    email_verification
                    if (
                        extraction_status.startswith("NEEDS_REVIEW_")
                        or extraction_status.startswith("SKIPPED_")
                    )
                    else None
                )
            ),
            "sent_at": existing.get("sent_at") if existing else None,
        }

        upsert_application(row)

        if email_addr:
            repair_duplicate_email_metadata(
                candidate_email=email_addr,
                job_title=job_title,
                candidate_name=candidate_name,
                candidate_phone=phone,
            )

        processed += 1

    log(
        "INFO",
        f"Indeed monitor processed {processed} applicant(s); "
        f"{ready} with verified email."
    )

    return {"processed": processed, "ready": ready}


def send_all_ready():
    """
    Single-dispatch live outbox.

    The DB claim prevents the background outbox, Save Settings retry and a
    manual Send click from delivering the same recipient twice.
    """
    settings = load_settings()

    if not settings.get("auto_send", True):
        return {
            "attempted": 0,
            "sent": 0,
            "skipped": 0,
            "remaining": len(list_ready_to_send()),
        }

    if not sender_configured(settings):
        return {
            "attempted": 0,
            "sent": 0,
            "skipped": 0,
            "remaining": len(list_ready_to_send()),
        }

    pending = list_ready_to_send()
    attempted = 0
    sent = 0
    skipped = 0

    for app in pending:
        attempted += 1

        try:
            result = send_thank_you(app["source_key"])
            status = result.get("status")

            if status == "sent":
                sent += 1
            elif status in {
                "already_sent",
                "duplicate_skipped",
                "busy",
                "busy_or_uncertain",
            }:
                skipped += 1

        except Exception as e:
            log(
                "ERROR",
                f"Live outbox send failed for "
                f"{app.get('candidate_email')}: {e}"
            )

            if get_state("smtp_verified", "0") != "1":
                break

    remaining = len(list_ready_to_send())

    return {
        "attempted": attempted,
        "sent": sent,
        "skipped": skipped,
        "remaining": remaining,
    }



def send_all_ready_corrections():
    """
    One-time correction sender for proven historical generic-role mistakes.
    """
    settings = load_settings()

    if not settings.get("auto_send", True) or not sender_configured(settings):
        return {
            "attempted": 0,
            "sent": 0,
            "remaining": len(list_ready_corrections()),
        }

    attempted = 0
    sent = 0

    for row in list_ready_corrections():
        attempted += 1
        claim = claim_correction_for_send(row["id"])
        if claim.get("status") != "claimed":
            continue

        response = claim["response"]
        addr = (response.get("recipient_email") or "").strip().lower()
        job = re.sub(r"\s+", " ", response.get("job_title") or "").strip()
        candidate_name = response.get("candidate_name") or "Candidate"

        smtp_accepted = False

        try:
            if not valid_candidate_email(addr, settings):
                raise RuntimeError("Historical candidate email is not valid.")
            if not meaningful_job_title(job):
                raise RuntimeError("Correction is waiting for the exact applied role.")

            company_name = settings.get("company_name") or "our company"
            subject = f"Application acknowledgement update – {job}"
            body = (
                f"Dear {candidate_name},\n\n"
                f"We previously sent you an acknowledgement regarding your application. "
                f"We are writing to confirm that your application is for the {job} "
                f"position at {company_name}.\n\n"
                f"Please treat this as a clarification to our earlier acknowledgement. "
                f"Thank you for applying.\n\n"
                f"Regards,\n{company_name}"
            )

            _smtp_send(addr, subject, body, settings)
            smtp_accepted = True
            finalize_correction_send(response["id"], subject, body)
            log("INFO", f"One-time role correction sent to {addr} for {job}.")
            sent += 1

        except Exception as e:
            friendly = friendly_smtp_error(e)

            if not smtp_accepted:
                release_correction_claim(response["id"], friendly)
            else:
                # Keep SENDING. On restart the DB migrator changes it to
                # SEND_UNCERTAIN and blocks automatic resend.
                log(
                    "ERROR",
                    f"Correction SMTP accepted for {addr}, but local history "
                    f"finalization failed. Automatic resend is blocked."
                )

            log("ERROR", f"One-time correction failed for {addr}: {friendly}")
            if get_state("smtp_verified", "0") != "1":
                break

    return {
        "attempted": attempted,
        "sent": sent,
        "remaining": len(list_ready_corrections()),
    }


def _normalize_app_password(value):
    # Google displays App Passwords in four groups. Spaces are not part of it.
    return re.sub(r"\s+", "", str(value or "")).strip()


def _decode_smtp_bytes(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def friendly_smtp_error(exc):
    """
    Convert low-level SMTP/socket errors into a useful dashboard message.
    Never includes the App Password.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        code = getattr(exc, "smtp_code", "")
        detail = _decode_smtp_bytes(getattr(exc, "smtp_error", ""))
        low = detail.lower()

        if str(code) in {"534", "535"} or "username and password not accepted" in low:
            return (
                "Gmail rejected the App Password. Use a Google App Password "
                "for nuneslead@gmail.com (not the normal Gmail password)."
            )

        return f"Gmail authentication failed ({code}). {detail}".strip()

    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "The mail server did not support the required secure login method."

    if isinstance(exc, smtplib.SMTPConnectError):
        return (
            f"Could not connect to Gmail SMTP ({getattr(exc, 'smtp_code', '')}). "
            f"{_decode_smtp_bytes(getattr(exc, 'smtp_error', ''))}"
        ).strip()

    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "Gmail rejected the candidate email address."

    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "Gmail rejected the fixed sender account nuneslead@gmail.com."

    if isinstance(exc, smtplib.SMTPDataError):
        return (
            "Gmail rejected the message after login. "
            + _decode_smtp_bytes(getattr(exc, "smtp_error", ""))
        ).strip()

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Timed out while connecting to Gmail. Check internet/firewall access."

    if isinstance(exc, OSError):
        return f"Network connection to Gmail failed: {exc}"

    return str(exc) or exc.__class__.__name__


def _smtp_modes(settings):
    host = (settings.get("smtp_host") or "smtp.gmail.com").strip()
    preferred = int(settings.get("smtp_port") or 587)
    fallback_ssl = int(settings.get("smtp_ssl_fallback_port") or 465)

    modes = []

    # Respect configured preference first.
    if preferred == 465:
        modes.append(("ssl", host, 465))
    else:
        modes.append(("starttls", host, preferred))

    # Gmail's alternate secure SMTP mode.
    alternate = ("ssl", host, fallback_ssl)
    if alternate not in modes:
        modes.append(alternate)

    if ("starttls", host, 587) not in modes:
        modes.append(("starttls", host, 587))

    return modes


def _open_authenticated_smtp(settings, timeout=30):
    user = (settings.get("company_email") or "").strip()
    pw = _normalize_app_password(settings.get("smtp_app_password"))

    if not user or not pw:
        raise RuntimeError(
            "Gmail App Password is required for the fixed sender account."
        )

    context = ssl.create_default_context()
    errors = []

    for mode, host, port in _smtp_modes(settings):
        smtp = None
        try:
            if mode == "ssl":
                smtp = smtplib.SMTP_SSL(
                    host,
                    port,
                    timeout=timeout,
                    context=context,
                )
                smtp.ehlo()
            else:
                smtp = smtplib.SMTP(host, port, timeout=timeout)
                smtp.ehlo()
                if not smtp.has_extn("starttls"):
                    raise smtplib.SMTPNotSupportedError(
                        "STARTTLS is not advertised by the SMTP server."
                    )
                smtp.starttls(context=context)
                smtp.ehlo()

            smtp.login(user, pw)

            transport = f"{mode.upper()} {host}:{port}"
            set_state("smtp_verified", "1")
            set_state("smtp_verified_at", now())
            set_state("smtp_last_error", "")
            set_state("smtp_transport", transport)

            return smtp, transport

        except Exception as e:
            errors.append((mode, host, port, e))
            try:
                if smtp:
                    smtp.quit()
            except Exception:
                try:
                    if smtp:
                        smtp.close()
                except Exception:
                    pass

            # Authentication rejection will normally be identical on both ports,
            # but still try the alternate secure transport once because some
            # networks block one port.
            continue

    last = errors[-1][3] if errors else RuntimeError("SMTP connection failed.")
    friendly = friendly_smtp_error(last)

    set_state("smtp_verified", "0")
    set_state("smtp_last_error", friendly)
    set_state("smtp_transport", "")

    # Include transport diagnostics without exposing credentials.
    attempts = ", ".join(
        f"{mode}:{port}={friendly_smtp_error(err)}"
        for mode, _host, port, err in errors
    )
    raise RuntimeError(
        friendly + (f" [Attempts: {attempts}]" if attempts else "")
    )


def smtp_health_check(settings=None):
    """
    Verify login only; no email is sent.
    """
    settings = settings or load_settings()

    if not sender_configured(settings):
        set_state("smtp_verified", "0")
        set_state("smtp_last_error", "Gmail App Password has not been configured.")
        set_state("smtp_transport", "")
        return {
            "ok": False,
            "message": "Gmail App Password has not been configured.",
            "transport": None,
        }

    smtp = None
    try:
        smtp, transport = _open_authenticated_smtp(settings, timeout=20)
        try:
            smtp.noop()
        except Exception:
            pass

        return {
            "ok": True,
            "message": "Gmail authentication verified.",
            "transport": transport,
        }

    except Exception as e:
        return {
            "ok": False,
            "message": friendly_smtp_error(e),
            "transport": None,
        }

    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass


def _smtp_send(to_addr, subject, body, settings):
    user = (settings.get("company_email") or "").strip()

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg["Reply-To"] = user
    msg.set_content(body)

    smtp = None
    try:
        smtp, transport = _open_authenticated_smtp(settings, timeout=30)

        # The fallback decision is completed BEFORE sending. send_message is
        # called only once, preventing accidental duplicate delivery.
        refused = smtp.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)

        return {
            "transport": transport,
            "message_id": str(msg["Message-ID"] or ""),
        }

    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass


def send_thank_you(source_key):
    """
    Exactly-one acknowledgement path.

    Every manual/live request goes through the same atomic DB claim. The
    candidate scanner itself never sends mail.
    """
    settings = load_settings()

    claim = claim_application_for_send(source_key)
    claim_status = claim.get("status")
    app = claim.get("application")

    if claim_status in {"already_sent", "duplicate_skipped"}:
        return {"status": claim_status}

    if claim_status in {"busy", "busy_or_uncertain"}:
        return {"status": claim_status}

    if claim_status == "missing":
        raise RuntimeError("Applicant not found.")

    if claim_status == "no_email":
        raise RuntimeError("No verified candidate email.")

    if claim_status != "claimed" or not app:
        raise RuntimeError(
            f"Candidate could not be reserved for sending ({claim_status})."
        )

    addr = app.get("candidate_email")

    if not plausible_candidate_name(app.get("candidate_name") or ""):
        release_send_claim(
            source_key,
            "NOT_SENT",
            "Candidate identity is not verified",
        )
        raise RuntimeError(
            "Candidate identity must be verified before sending."
        )

    if not addr or not valid_candidate_email(addr, settings):
        release_send_claim(
            source_key,
            "NOT_SENT",
            "No verified candidate email",
        )
        raise RuntimeError("No verified candidate email.")

    if not int(app.get("application_verified") or 0):
        release_send_claim(
            source_key,
            "NOT_SENT",
            "Exact Indeed application is not verified",
        )
        raise RuntimeError(
            "Exact Indeed application must be verified before sending."
        )

    if app.get("extraction_status") not in {
        "VERIFIED_EMAIL_AND_ROLE",
        "VERIFIED_EMAIL_FOUND",
    }:
        release_send_claim(
            source_key,
            "NOT_SENT",
            "Candidate email and role are not both verified",
        )
        raise RuntimeError(
            "Candidate email and role must both be verified before sending."
        )

    if app.get("email_source") not in {
        "indeed_resume",
        "indeed_resume_ai_verified",
        "indeed_candidate_profile",
        "indeed_candidate_contact_verified",
    }:
        release_send_claim(
            source_key,
            "NOT_SENT",
            "Unapproved email source",
        )
        raise RuntimeError("Candidate email source is not approved.")

    if not meaningful_job_title(app.get("job_title")):
        release_send_claim(
            source_key,
            "NOT_SENT",
            "Waiting for verified job title",
        )
        raise RuntimeError(
            "Waiting for the real Indeed job title before sending."
        )

    if not role_accepts_new_applications(app.get("job_title")):
        release_send_claim(
            source_key,
            "SKIPPED",
            "Role is paused/closed; new applicant outreach is stopped",
        )
        return {"status": "role_closed_skipped"}

    # A second permanent check after the claim makes manual/API edge cases safe.
    if already_sent(addr):
        release_send_claim(
            source_key,
            "DUPLICATE_SKIPPED",
            "Acknowledgement already sent to this email address",
        )
        return {"status": "duplicate_skipped"}

    values = {
        "candidate_name": app.get("candidate_name") or "Candidate",
        "candidate_email": addr,
        "job_title": app.get("job_title"),
        "company_name": settings.get("company_name") or "our company",
        "company_email": settings.get("company_email") or "",
    }

    smtp_accepted = False

    try:
        subject = settings["subject_template"].format(**values)
        body = settings["body_template"].format(**values)

        smtp_result = _smtp_send(
            addr,
            subject,
            body,
            settings,
        )
        smtp_accepted = True

        receipt = finalize_sent_response(
            source_key=source_key,
            subject=subject,
            body=body,
            smtp_transport=smtp_result.get("transport"),
            message_id=smtp_result.get("message_id"),
        )

        log(
            "INFO",
            f"Thank-you sent once to {addr} for {values['job_title']}."
        )

        return {
            "status": "sent",
            "sent_at": receipt.get("sent_at"),
        }

    except Exception as e:
        friendly = friendly_smtp_error(e)

        if not smtp_accepted:
            # SMTP did not confirm acceptance: normal retry is safe.
            release_send_claim(
                source_key,
                "SEND_FAILED",
                friendly,
            )
        else:
            # SMTP accepted the message but local finalization failed. Leave
            # SENDING in the DB. On the next app start init_db() converts it to
            # SEND_UNCERTAIN, which is deliberately excluded from automatic
            # retries. This is safer than potentially emailing the person twice.
            log(
                "ERROR",
                f"SMTP accepted mail for {addr}, but local send-history "
                f"finalization failed. Automatic resend is blocked: {e}"
            )

        set_state("smtp_last_error", friendly)

        log(
            "ERROR",
            f"Send failed to {addr}: {friendly} "
            f"(raw={e.__class__.__name__}: {e})"
        )
        raise RuntimeError(friendly) from e
