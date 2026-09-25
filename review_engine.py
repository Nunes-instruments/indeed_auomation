from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from database import (
    conn,
    get_by_id,
    get_by_source_key,
    list_applications,
    log,
    now,
    get_state,
    set_state,
)
from automation import extract_text_from_resume
from config import load_settings
from openai_ranker import semantic_refine_score, ranking_key_status
from recruitment_pipeline import role_accepts_ranking

GENERIC_ROLES = {
    "",
    "the position",
    "position",
    "job",
    "the job",
    "unknown",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "of", "on", "or", "our", "the",
    "their", "this", "to", "with", "will", "you", "your", "candidate",
    "candidates", "requirement", "requirements", "ability", "strong",
    "good", "excellent", "knowledge", "skills", "skill", "work", "working",
    "required", "preferred", "must", "mandatory", "minimum", "essential",
    "desirable",
}

EDUCATION_HINTS = (
    "bachelor", "master", "degree", "diploma", "b.e", "b.tech", "m.tech",
    "bcom", "b.com", "mcom", "m.com", "mba", "bba", "b.sc", "m.sc",
    "engineering", "university", "college",
)

EXPERIENCE_HINTS = (
    "experience", "worked as", "working as", "years", "year", "employment",
    "professional experience", "work history", "career",
)

AUTO_SHORTLIST_THRESHOLD = 70.0

TERMINAL_INDEED_STATUSES = {
    "hired", "selected", "not selected", "rejected", "withdrawn", "archived",
}


def _normalized_indeed_status(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def terminal_ranking_reason(application):
    status = _normalized_indeed_status((application or {}).get("indeed_status"))
    if status not in TERMINAL_INDEED_STATUSES:
        return None
    label = str((application or {}).get("indeed_status") or status).strip()
    return f"Removed from active ranking: Indeed status is {label}."



PROTECTED_REQUIREMENT_RE = re.compile(
    r"(?i)\b("
    r"gender|male|female|woman|women|man|men|sex|sexual|"
    r"age|years\s+old|date\s+of\s+birth|dob|"
    r"religion|religious|caste|race|racial|ethnic|ethnicity|"
    r"marital|married|unmarried|pregnan|"
    r"disability|disabled|medical\s+condition|health\s+condition|"
    r"sexual\s+orientation|gay|lesbian|bisexual|transgender|"
    r"political|party\s+affiliation|trade\s+union|union\s+member|"
    r"nationality|citizenship"
    r")\b"
)


def init_role_review_db():
    """
    Create/migrate role-review tables safely across V11.9 -> V11.10+.

    Important: indexes that reference new V11.10 columns are created only
    AFTER ALTER TABLE migrations. Older V11.9 databases do not yet contain
    rank_position / auto_shortlisted, so creating those indexes first causes
    SQLite `no such column` and breaks the whole Role Review API.
    """
    with conn() as c:
        # Tables first. Keep this schema compatible with both fresh and older DBs.
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS role_profiles (
                job_title TEXT PRIMARY KEY COLLATE NOCASE,
                job_description TEXT NOT NULL DEFAULT '',
                description_source TEXT NOT NULL DEFAULT '',
                indeed_job_url TEXT,
                description_status TEXT NOT NULL DEFAULT 'WAITING',
                description_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS candidate_reviews (
                application_id INTEGER PRIMARY KEY,
                job_title TEXT,
                analysis_status TEXT NOT NULL DEFAULT 'PENDING',
                resume_fingerprint TEXT,
                role_fingerprint TEXT,
                requirements_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                summary_json TEXT NOT NULL DEFAULT '{}',
                requirements_evidenced INTEGER NOT NULL DEFAULT 0,
                requirements_total INTEGER NOT NULL DEFAULT 0,
                match_score REAL NOT NULL DEFAULT 0,
                rank_position INTEGER,
                auto_shortlisted INTEGER NOT NULL DEFAULT 0,
                auto_bucket TEXT NOT NULL DEFAULT 'WAITING',
                score_breakdown_json TEXT NOT NULL DEFAULT '{}',
                completed_at TEXT,
                manual_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
                reviewer_note TEXT,
                analyzed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        # Migrate ROLE table before any query relies on the new fields.
        role_cols = {
            row["name"]
            for row in c.execute("PRAGMA table_info(role_profiles)").fetchall()
        }
        role_additions = {
            "description_source": "TEXT NOT NULL DEFAULT ''",
            "indeed_job_url": "TEXT",
            "description_status": "TEXT NOT NULL DEFAULT 'WAITING'",
            "description_checked_at": "TEXT",
        }
        for name, definition in role_additions.items():
            if name not in role_cols:
                c.execute(
                    f"ALTER TABLE role_profiles ADD COLUMN {name} {definition}"
                )

        # Migrate CANDIDATE REVIEW table before creating V11.10 indexes.
        review_cols = {
            row["name"]
            for row in c.execute("PRAGMA table_info(candidate_reviews)").fetchall()
        }
        review_additions = {
            "match_score": "REAL NOT NULL DEFAULT 0",
            "rank_position": "INTEGER",
            "auto_shortlisted": "INTEGER NOT NULL DEFAULT 0",
            "auto_bucket": "TEXT NOT NULL DEFAULT 'WAITING'",
            "score_breakdown_json": "TEXT NOT NULL DEFAULT '{}'",
            "completed_at": "TEXT",
        }
        for name, definition in review_additions.items():
            if name not in review_cols:
                c.execute(
                    f"ALTER TABLE candidate_reviews ADD COLUMN {name} {definition}"
                )

        # Normalize old role rows so the frontend sees the correct state.
        c.execute(
            """
            UPDATE role_profiles
            SET description_status=CASE
                    WHEN trim(COALESCE(job_description,''))<>'' THEN 'READY'
                    ELSE 'WAITING'
                END
            WHERE description_status IS NULL
               OR trim(description_status)=''
               OR description_status='WAITING'
            """
        )

        # Indexes LAST: all referenced columns now definitely exist.
        c.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_reviews_job
            ON candidate_reviews(job_title);

            CREATE INDEX IF NOT EXISTS idx_candidate_reviews_rank
            ON candidate_reviews(job_title, rank_position);

            CREATE INDEX IF NOT EXISTS idx_candidate_reviews_auto_shortlist
            ON candidate_reviews(job_title, auto_shortlisted, rank_position);
            """
        )


def meaningful_role(value: str | None) -> bool:
    return (value or "").strip().lower() not in GENERIC_ROLES


def _clean_role(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:200]


def _clean_description(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|li|div|section|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:40000]


def sync_roles_from_applications():
    init_role_review_db()
    seen = set()
    ts = now()

    for row in list_applications(10000):
        title = _clean_role(row.get("job_title"))
        if not meaningful_role(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        with conn() as c:
            c.execute(
                """
                INSERT INTO role_profiles(
                    job_title, job_description, description_source,
                    description_status, created_at, updated_at
                )
                VALUES (?, '', '', 'WAITING', ?, ?)
                ON CONFLICT(job_title) DO NOTHING
                """,
                (title, ts, ts),
            )


def get_role_profile(job_title: str):
    init_role_review_db()
    with conn() as c:
        row = c.execute(
            "SELECT * FROM role_profiles WHERE lower(job_title)=lower(?) LIMIT 1",
            (_clean_role(job_title),),
        ).fetchone()
        return dict(row) if row else None


def _description_hash(description: str) -> str:
    return hashlib.sha256(
        str(description or "").encode("utf-8", "ignore")
    ).hexdigest()


def upsert_role_description(
    job_title: str,
    job_description: str,
    source: str = "indeed",
    job_url: str | None = None,
):
    init_role_review_db()
    title = _clean_role(job_title)
    description = _clean_description(job_description)

    if not meaningful_role(title):
        return None
    if len(description) < 100:
        return None

    ts = now()
    previous = get_role_profile(title) or {}
    changed = (
        _clean_description(previous.get("job_description")) != description
    )

    with conn() as c:
        c.execute(
            """
            INSERT INTO role_profiles(
                job_title, job_description, description_source,
                indeed_job_url, description_status, description_checked_at,
                created_at, updated_at
            )
            VALUES (?,?,?,?, 'READY', ?, ?, ?)
            ON CONFLICT(job_title) DO UPDATE SET
                job_description=excluded.job_description,
                description_source=excluded.description_source,
                indeed_job_url=COALESCE(excluded.indeed_job_url, role_profiles.indeed_job_url),
                description_status='READY',
                description_checked_at=excluded.description_checked_at,
                updated_at=excluded.updated_at
            """,
            (
                title,
                description,
                str(source or "indeed")[:80],
                str(job_url or "")[:1200] or None,
                ts,
                ts,
                ts,
            ),
        )

        if changed:
            c.execute(
                """
                UPDATE candidate_reviews
                SET analysis_status='PENDING',
                    rank_position=NULL,
                    auto_shortlisted=0,
                    auto_bucket='WAITING',
                    updated_at=?
                WHERE lower(job_title)=lower(?)
                """,
                (ts, title),
            )

    return get_role_profile(title)


def save_role_description(job_title: str, job_description: str):
    # Backward-compatible API. V11.10 obtains descriptions automatically from
    # Indeed and the UI no longer requires manual setup.
    result = upsert_role_description(
        job_title,
        job_description,
        source="legacy_manual",
    )
    if not result:
        raise ValueError("A real role title and job description are required.")
    return result


def sync_job_descriptions_from_scan(results):
    updated = []
    for item in results or []:
        title = _clean_role(item.get("job_title"))
        description = item.get("job_description") or ""
        if not meaningful_role(title) or len(str(description).strip()) < 100:
            continue
        role = upsert_role_description(
            title,
            description,
            source=item.get("job_description_source") or "indeed_candidate_job",
            job_url=item.get("job_url"),
        )
        if role:
            updated.append(title)
    return list(dict.fromkeys(updated))


def ingest_discovered_job_descriptions(rows):
    updated = []
    for row in rows or []:
        title = _clean_role(row.get("job_title") or row.get("title"))
        description = row.get("job_description") or row.get("description") or ""
        if not meaningful_role(title) or len(str(description).strip()) < 100:
            continue
        role = upsert_role_description(
            title,
            description,
            source=row.get("source") or "indeed_jobs",
            job_url=row.get("job_url") or row.get("url"),
        )
        if role:
            updated.append(title)
    return list(dict.fromkeys(updated))


def list_roles():
    sync_roles_from_applications()

    with conn() as c:
        rows = c.execute(
            """
            SELECT
                rp.job_title,
                rp.job_description,
                rp.description_source,
                rp.indeed_job_url,
                rp.description_status,
                rp.description_checked_at,
                rp.updated_at,
                COUNT(a.id) AS applicant_count,
                SUM(CASE WHEN lower(trim(COALESCE(a.indeed_status,''))) IN (
                    'hired','selected','not selected','rejected','withdrawn','archived'
                ) THEN 0 ELSE 1 END) AS active_applicant_count,
                SUM(CASE WHEN cr.analysis_status='READY' THEN 1 ELSE 0 END)
                    AS analyzed_count,
                SUM(CASE WHEN cr.analysis_status IN ('READY','EXCLUDED_TERMINAL_STATUS')
                         THEN 0 ELSE 1 END) AS waiting_count,
                SUM(CASE WHEN cr.auto_shortlisted=1 THEN 1 ELSE 0 END)
                    AS auto_shortlisted_count,
                SUM(CASE WHEN cr.analysis_status='EXCLUDED_TERMINAL_STATUS'
                         THEN 1 ELSE 0 END) AS removed_count
            FROM role_profiles rp
            LEFT JOIN applications a
              ON lower(trim(a.job_title)) = lower(trim(rp.job_title))
            LEFT JOIN candidate_reviews cr
              ON cr.application_id = a.id
            GROUP BY
                rp.job_title, rp.job_description, rp.description_source,
                rp.indeed_job_url, rp.description_status,
                rp.description_checked_at, rp.updated_at
            ORDER BY lower(rp.job_title)
            """
        ).fetchall()
        return [dict(r) for r in rows]


def _normalize_text(value: str) -> str:
    value = str(value or "").lower()
    value = re.sub(r"[^a-z0-9+#./&\- ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str):
    return [
        x
        for x in re.findall(r"[a-z0-9+#.]{2,}", _normalize_text(value))
        if x not in STOPWORDS
    ]


def parse_requirements(job_description: str):
    text = str(job_description or "").replace("\r", "\n")
    if not text.strip():
        return []

    raw_parts = []
    for line in text.split("\n"):
        line = re.sub(r"^\s*(?:[-*•▪◦]+|\d+[.)]|[A-Za-z][.)])\s*", "", line)
        line = re.sub(r"\s+", " ", line).strip(" -:;\t")
        if not line:
            continue
        if len(line) > 300:
            raw_parts.extend(re.split(r"(?<=[.;])\s+", line))
        else:
            raw_parts.append(line)

    if len(raw_parts) <= 2:
        raw_parts = [
            x.strip()
            for x in re.split(r"(?<=[.;])\s+|\s*;\s*", text)
            if x.strip()
        ]

    out = []
    seen = set()
    headings = {
        "requirements", "responsibilities", "job description", "skills",
        "qualifications", "preferred qualifications", "key responsibilities",
        "what you'll do", "what you will do", "about the role", "benefits",
    }

    for part in raw_parts:
        part = re.sub(r"\s+", " ", part).strip(" -:;\t")
        low = part.lower().rstrip(":")
        if low in headings:
            continue
        # Employment ranking deliberately excludes protected/sensitive
        # personal attributes even if they appear in a job description.
        if PROTECTED_REQUIREMENT_RE.search(part):
            continue

        tokens = _tokens(part)
        if len(tokens) < 1 or len(part) < 3:
            continue
        key = _normalize_text(part)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(part[:280])
        if len(out) >= 32:
            break

    return out


def _resume_chunks(text: str):
    chunks = []
    for piece in re.split(r"[\n\r]+|(?<=[.!?])\s+", str(text or "")):
        piece = re.sub(r"\s+", " ", piece).strip(" -•\t")
        if 18 <= len(piece) <= 520:
            chunks.append(piece)
    return chunks[:2000]


def _requirement_weight(requirement: str) -> float:
    low = str(requirement or "").lower()
    if any(x in low for x in ["must", "mandatory", "required", "minimum", "essential"]):
        return 1.35
    if any(x in low for x in ["preferred", "advantage", "plus", "nice to have", "desirable"]):
        return 0.75
    return 1.0


def _requirement_evidence(requirement: str, chunks: list[str], normalized_resume: str):
    req_norm = _normalize_text(requirement)
    req_tokens = list(dict.fromkeys(_tokens(requirement)))[:18]
    weight = _requirement_weight(requirement)

    if req_norm and len(req_norm) >= 8 and req_norm in normalized_resume:
        for chunk in chunks:
            if req_norm in _normalize_text(chunk):
                return {
                    "requirement": requirement,
                    "status": "EVIDENCE_FOUND",
                    "evidence": chunk[:420],
                    "matched_terms": req_tokens,
                    "coverage": 1.0,
                    "weight": weight,
                }

    if not req_tokens:
        return {
            "requirement": requirement,
            "status": "NOT_FOUND",
            "evidence": "",
            "matched_terms": [],
            "coverage": 0.0,
            "weight": weight,
        }

    best = None
    best_terms = []

    for chunk in chunks:
        chunk_norm = _normalize_text(chunk)
        matched = [token for token in req_tokens if token in chunk_norm]
        if len(matched) > len(best_terms):
            best_terms = matched
            best = chunk

    coverage = min(1.0, len(best_terms) / max(1, len(req_tokens)))
    needed = 1 if len(req_tokens) == 1 else 2
    if len(req_tokens) >= 5:
        needed = 3
    if len(req_tokens) >= 9:
        needed = 4

    evidence_found = len(best_terms) >= needed

    # A requirement can still receive partial evidence credit without being
    # presented as a confirmed match. This produces more stable role ranking.
    score_coverage = coverage if evidence_found else min(coverage, 0.45)

    return {
        "requirement": requirement,
        "status": "EVIDENCE_FOUND" if evidence_found else "NOT_FOUND",
        "evidence": (best or "")[:420] if best_terms else "",
        "matched_terms": best_terms[:16],
        "coverage": round(score_coverage, 4),
        "weight": weight,
    }


def _extract_lines(text: str, hints: Iterable[str], limit=5):
    found = []
    for chunk in _resume_chunks(text):
        low = chunk.lower()
        if any(hint in low for hint in hints):
            found.append(chunk[:340])
            if len(found) >= limit:
                break
    return found


def _fingerprint(path: str | None, description: str):
    h = hashlib.sha256()
    h.update(str(description or "").encode("utf-8", "ignore"))
    if path:
        p = Path(path)
        h.update(str(p).encode("utf-8", "ignore"))
        try:
            stat = p.stat()
            h.update(str(stat.st_size).encode())
            h.update(str(stat.st_mtime_ns).encode())
        except Exception:
            pass
    return h.hexdigest()


def _existing_review(application_id: int):
    with conn() as c:
        row = c.execute(
            "SELECT * FROM candidate_reviews WHERE application_id=?",
            (int(application_id),),
        ).fetchone()
        return dict(row) if row else None


def _bucket_for_score(score):
    value = float(score or 0)
    if value >= AUTO_SHORTLIST_THRESHOLD:
        return "AUTO_SHORTLIST"
    if value >= 55:
        return "STRONG_MATCH"
    if value >= 35:
        return "POSSIBLE_MATCH"
    return "LOW_EVIDENCE"


def _score_evidence(evidence):
    total_weight = 0.0
    achieved = 0.0

    for item in evidence or []:
        weight = float(item.get("weight") or 1.0)
        coverage = max(0.0, min(1.0, float(item.get("coverage") or 0.0)))
        total_weight += weight
        achieved += weight * coverage

    raw_score = 0.0 if total_weight <= 0 else (achieved / total_weight) * 100.0

    # Strict scoring: deterministic keyword/evidence matching is the primary
    # score and is deliberately capped below 100. A perfect-looking resume is
    # still not treated as certainty. Equal scores are resolved by application
    # time in refresh_role_ranks().
    score = round(min(98.0, raw_score), 1)
    bucket = _bucket_for_score(score)

    found = [x for x in evidence or [] if x.get("status") == "EVIDENCE_FOUND"]
    missing = [x for x in evidence or [] if x.get("status") != "EVIDENCE_FOUND"]

    breakdown = {
        "method": "strict_weighted_keyword_evidence",
        "threshold": AUTO_SHORTLIST_THRESHOLD,
        "raw_keyword_score": round(raw_score, 1),
        "local_keyword_score": score,
        "matched_requirements": len(found),
        "total_requirements": len(evidence or []),
        "top_matches": [x.get("requirement") for x in found[:5]],
        "top_missing": [x.get("requirement") for x in missing[:5]],
        "ai_used": False,
    }

    return score, bucket, breakdown


def _openai_daily_budget_available(settings):
    limit = max(0, int(settings.get("openai_ranking_daily_call_limit") or 0))
    if limit <= 0:
        return False, 0, 0
    today = datetime.now().astimezone().date().isoformat()
    key = f"openai_ranking_calls:{today}"
    used = int(get_state(key, "0") or 0)
    return used < limit, used, limit


def _record_openai_call():
    today = datetime.now().astimezone().date().isoformat()
    key = f"openai_ranking_calls:{today}"
    used = int(get_state(key, "0") or 0) + 1
    set_state(key, str(used))
    set_state("openai_ranking_last_call_at", now())
    return used


def _maybe_refine_with_openai(local_score, requirements, evidence, evidenced):
    settings = load_settings()
    breakdown = {
        "ai_used": False,
        "ai_reason": "local_only",
        "ai_model": settings.get("openai_ranking_model") or "gpt-5.6-luna",
    }

    if not bool(settings.get("openai_ranking_enabled", True)):
        breakdown["ai_reason"] = "disabled"
        return float(local_score), breakdown

    key_status = ranking_key_status()
    if not key_status.get("configured"):
        breakdown["ai_reason"] = "api_key_not_configured"
        return float(local_score), breakdown

    minimum = float(settings.get("openai_ranking_min_local_score") or 45.0)
    maximum = float(settings.get("openai_ranking_max_local_score") or 90.0)
    if float(local_score) < minimum:
        breakdown["ai_reason"] = "below_keyword_gate"
        return float(local_score), breakdown
    if float(local_score) > maximum:
        # Very clear deterministic matches do not need a paid API call.
        breakdown["ai_reason"] = "clear_local_match"
        return float(local_score), breakdown
    if int(evidenced or 0) < 2:
        breakdown["ai_reason"] = "insufficient_local_evidence"
        return float(local_score), breakdown

    allowed, used, limit = _openai_daily_budget_available(settings)
    breakdown["ai_daily_used_before"] = used
    breakdown["ai_daily_limit"] = limit
    if not allowed:
        breakdown["ai_reason"] = "daily_cost_cap_reached"
        return float(local_score), breakdown

    try:
        result = semantic_refine_score(
            requirements=requirements,
            evidence=evidence,
            local_score=local_score,
            model=settings.get("openai_ranking_model") or "gpt-5.6-luna",
            max_input_chars=int(settings.get("openai_ranking_max_input_chars") or 7000),
        )
        if not result.get("used"):
            breakdown["ai_reason"] = result.get("reason") or "not_used"
            return float(local_score), breakdown

        _record_openai_call()
        semantic = float(result.get("semantic_score") or local_score)
        # The paid model is only a small refinement after deterministic keyword
        # matching. It cannot swing the score wildly.
        semantic = max(float(local_score) - 15.0, min(float(local_score) + 15.0, semantic))
        configured_weight = max(0.0, min(0.20, float(settings.get("openai_ranking_ai_weight") or 0.12)))
        confidence = max(0.0, min(1.0, float(result.get("confidence") or 0.0)))
        effective_weight = configured_weight * max(0.35, confidence)
        final_score = (float(local_score) * (1.0 - effective_weight)) + (semantic * effective_weight)
        final_score = round(min(98.5, max(0.0, final_score)), 1)

        breakdown.update({
            "ai_used": True,
            "ai_reason": "semantic_refinement",
            "ai_semantic_score": round(semantic, 1),
            "ai_confidence": confidence,
            "ai_weight": round(effective_weight, 4),
            "ai_model": result.get("model"),
            "ai_note": result.get("note"),
            "ai_input_tokens": result.get("input_tokens", 0),
            "ai_output_tokens": result.get("output_tokens", 0),
        })
        return final_score, breakdown
    except Exception as exc:
        log("WARN", f"OpenAI ranking refinement skipped; local score retained: {exc}")
        breakdown["ai_reason"] = "api_error_local_fallback"
        breakdown["ai_error"] = str(exc)[:500]
        return float(local_score), breakdown


def _save_review(
    application_id,
    job_title,
    analysis_status,
    requirements,
    evidence,
    summary,
    evidenced,
    total,
    resume_fingerprint,
    role_fingerprint,
    ts,
    match_score=0.0,
    auto_bucket="WAITING",
    score_breakdown=None,
):
    ready = analysis_status == "READY"
    auto_shortlisted = int(
        ready
        and float(match_score or 0) >= AUTO_SHORTLIST_THRESHOLD
        and int(total or 0) > 0
    )

    with conn() as c:
        c.execute(
            """
            INSERT INTO candidate_reviews(
                application_id, job_title, analysis_status,
                resume_fingerprint, role_fingerprint,
                requirements_json, evidence_json, summary_json,
                requirements_evidenced, requirements_total,
                match_score, rank_position, auto_shortlisted,
                auto_bucket, score_breakdown_json, completed_at,
                manual_status, reviewer_note, analyzed_at,
                created_at, updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(application_id) DO UPDATE SET
                job_title=excluded.job_title,
                analysis_status=excluded.analysis_status,
                resume_fingerprint=excluded.resume_fingerprint,
                role_fingerprint=excluded.role_fingerprint,
                requirements_json=excluded.requirements_json,
                evidence_json=excluded.evidence_json,
                summary_json=excluded.summary_json,
                requirements_evidenced=excluded.requirements_evidenced,
                requirements_total=excluded.requirements_total,
                match_score=excluded.match_score,
                rank_position=CASE
                    WHEN excluded.analysis_status='READY'
                    THEN candidate_reviews.rank_position
                    ELSE NULL
                END,
                auto_shortlisted=excluded.auto_shortlisted,
                auto_bucket=excluded.auto_bucket,
                score_breakdown_json=excluded.score_breakdown_json,
                completed_at=excluded.completed_at,
                analyzed_at=excluded.analyzed_at,
                updated_at=excluded.updated_at
            """,
            (
                int(application_id),
                job_title,
                analysis_status,
                resume_fingerprint,
                role_fingerprint,
                json.dumps(requirements, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                int(evidenced),
                int(total),
                float(match_score or 0),
                None,
                auto_shortlisted,
                auto_bucket,
                json.dumps(score_breakdown or {}, ensure_ascii=False),
                ts if ready else None,
                "UNREVIEWED",
                None,
                ts,
                ts,
                ts,
            ),
        )
    return get_review(application_id)


def _decode_review(row: dict):
    out = dict(row)
    for key, default in [
        ("requirements_json", []),
        ("evidence_json", []),
        ("summary_json", {}),
        ("score_breakdown_json", {}),
    ]:
        try:
            out[key.replace("_json", "")] = json.loads(
                out.get(key) or json.dumps(default)
            )
        except Exception:
            out[key.replace("_json", "")] = default
    return out


def get_review(application_id: int):
    row = _existing_review(application_id)
    return _decode_review(row) if row else None


def analyze_application(application_id: int, force=False):
    init_role_review_db()
    app = get_by_id(int(application_id))
    if not app:
        raise ValueError("Applicant not found.")

    job_title = _clean_role(app.get("job_title"))
    existing = _existing_review(application_id)
    ts = now()

    terminal_reason = terminal_ranking_reason(app)
    if terminal_reason:
        return _save_review(
            application_id, job_title, "EXCLUDED_TERMINAL_STATUS",
            [], [], {
                "ranking_basis": terminal_reason,
                "indeed_status": app.get("indeed_status"),
            }, 0, 0, None, None, ts,
            match_score=0.0, auto_bucket="REMOVED",
            score_breakdown={
                "method": "workflow_status_exclusion",
                "reason": terminal_reason,
            },
        )

    if not meaningful_role(job_title):
        return _save_review(
            application_id, job_title, "WAITING_FOR_ROLE", [], [], {}, 0, 0,
            None, None, ts,
        )

    if not role_accepts_ranking(job_title):
        # Freeze completed ranking/history when the Indeed job is paused/closed.
        # A candidate that never finished ranking is simply held, not deleted.
        if existing and existing.get("analysis_status") == "READY":
            return _decode_review(existing)
        return _save_review(
            application_id, job_title, "ROLE_CLOSED", [], [], {
                "ranking_basis": "Role is paused/closed on Indeed; active ranking is stopped."
            }, 0, 0, None, None, ts,
            match_score=0.0, auto_bucket="ROLE_CLOSED",
        )

    sync_roles_from_applications()
    profile = get_role_profile(job_title) or {}
    description = _clean_description(profile.get("job_description"))

    if not description:
        return _save_review(
            application_id, job_title, "WAITING_FOR_JOB_DESCRIPTION",
            [], [], {}, 0, 0, None, None, ts,
        )

    requirements = parse_requirements(description)
    role_fp = _description_hash(description)

    if not requirements:
        return _save_review(
            application_id, job_title, "WAITING_FOR_JOB_DESCRIPTION",
            [], [], {}, 0, 0, None, role_fp, ts,
        )

    resume_path = app.get("resume_path")
    if not resume_path or not Path(resume_path).exists():
        return _save_review(
            application_id, job_title, "WAITING_FOR_RESUME",
            requirements, [], {}, 0, len(requirements),
            None, role_fp, ts,
        )

    fp = _fingerprint(resume_path, description)
    if (
        not force
        and existing
        and existing.get("resume_fingerprint") == fp
        and existing.get("role_fingerprint") == role_fp
        and existing.get("analysis_status") == "READY"
    ):
        # Completed candidates are intentionally skipped until either the
        # resume or the official Indeed job description changes.
        return _decode_review(existing)

    resume_text = extract_text_from_resume(resume_path)
    if not resume_text.strip():
        return _save_review(
            application_id, job_title, "WAITING_FOR_RESUME_TEXT",
            requirements, [], {}, 0, len(requirements),
            fp, role_fp, ts,
        )

    chunks = _resume_chunks(resume_text)
    normalized_resume = _normalize_text(resume_text)
    evidence = [
        _requirement_evidence(req, chunks, normalized_resume)
        for req in requirements
    ]
    evidenced = sum(
        1 for item in evidence if item.get("status") == "EVIDENCE_FOUND"
    )
    local_score, bucket, breakdown = _score_evidence(evidence)
    score, ai_breakdown = _maybe_refine_with_openai(
        local_score, requirements, evidence, evidenced
    )
    breakdown.update(ai_breakdown)
    breakdown["final_score"] = score
    bucket = _bucket_for_score(score)

    summary = {
        "education_evidence": _extract_lines(resume_text, EDUCATION_HINTS, 6),
        "experience_evidence": _extract_lines(resume_text, EXPERIENCE_HINTS, 6),
        "resume_character_count": len(resume_text),
        "ranking_basis": "Official job requirements versus resume evidence; protected personal attributes are excluded",
    }

    return _save_review(
        application_id,
        job_title,
        "READY",
        requirements,
        evidence,
        summary,
        evidenced,
        len(requirements),
        fp,
        role_fp,
        ts,
        match_score=score,
        auto_bucket=bucket,
        score_breakdown=breakdown,
    )


def refresh_role_ranks(job_title: str):
    title = _clean_role(job_title)
    if not meaningful_role(title):
        return 0

    if not role_accepts_ranking(title):
        # Final ranking is intentionally frozen when the job is paused/closed.
        with conn() as c:
            row = c.execute(
                """
                SELECT COUNT(*) AS n FROM candidate_reviews
                WHERE lower(trim(job_title))=lower(trim(?))
                  AND analysis_status='READY' AND rank_position IS NOT NULL
                """,
                (title,),
            ).fetchone()
            return int(row["n"] or 0)

    with conn() as c:
        terminal_rows = c.execute(
            """
            SELECT a.id, a.indeed_status
            FROM applications a
            WHERE lower(trim(a.job_title))=lower(trim(?))
              AND lower(trim(COALESCE(a.indeed_status,''))) IN (
                  'hired','selected','not selected','rejected','withdrawn','archived'
              )
            """,
            (title,),
        ).fetchall()

        for terminal in terminal_rows:
            reason = (
                "Removed from active ranking: Indeed status is "
                + str(terminal["indeed_status"] or "terminal") + "."
            )
            c.execute(
                """
                UPDATE candidate_reviews
                SET analysis_status='EXCLUDED_TERMINAL_STATUS',
                    rank_position=NULL, auto_shortlisted=0,
                    auto_bucket='REMOVED', score_breakdown_json=?, updated_at=?
                WHERE application_id=?
                """,
                (json.dumps({"method":"workflow_status_exclusion","reason":reason},
                            ensure_ascii=False), now(), terminal["id"]),
            )

        ready = c.execute(
            """
            SELECT cr.application_id, cr.match_score, cr.requirements_evidenced,
                   cr.requirements_total, cr.completed_at
            FROM candidate_reviews cr
            JOIN applications a ON a.id=cr.application_id
            WHERE lower(trim(cr.job_title))=lower(trim(?))
              AND cr.analysis_status='READY'
              AND lower(trim(COALESCE(a.indeed_status,''))) NOT IN (
                  'hired','selected','not selected','rejected','withdrawn','archived'
              )
            ORDER BY cr.match_score DESC,
                     datetime(COALESCE(a.first_seen_at,a.created_at,'')) ASC,
                     requirements_evidenced DESC,
                     requirements_total DESC,
                     application_id ASC
            """,
            (title,),
        ).fetchall()

        c.execute(
            """
            UPDATE candidate_reviews
            SET rank_position=NULL
            WHERE lower(trim(job_title))=lower(trim(?))
              AND analysis_status<>'READY'
            """,
            (title,),
        )

        for index, row in enumerate(ready, start=1):
            c.execute(
                """
                UPDATE candidate_reviews
                SET rank_position=?,
                    auto_shortlisted=CASE WHEN match_score>=? THEN 1 ELSE 0 END,
                    auto_bucket=CASE
                        WHEN match_score>=? THEN 'AUTO_SHORTLIST'
                        WHEN match_score>=55 THEN 'STRONG_MATCH'
                        WHEN match_score>=35 THEN 'POSSIBLE_MATCH'
                        ELSE 'LOW_EVIDENCE'
                    END,
                    updated_at=?
                WHERE application_id=?
                """,
                (
                    index,
                    AUTO_SHORTLIST_THRESHOLD,
                    AUTO_SHORTLIST_THRESHOLD,
                    now(),
                    row["application_id"],
                ),
            )

    return len(ready)


def analyze_role(job_title: str, force=False, limit=1500):
    title = _clean_role(job_title)
    if meaningful_role(title) and not role_accepts_ranking(title):
        return {"analyzed": 0, "skipped_completed": 0, "ranked": refresh_role_ranks(title), "errors": 0, "role_closed": True}
    analyzed = 0
    skipped_completed = 0
    errors = 0

    with conn() as c:
        rows = c.execute(
            """
            SELECT a.id,
                   cr.analysis_status,
                   cr.resume_fingerprint,
                   cr.role_fingerprint
            FROM applications a
            LEFT JOIN candidate_reviews cr ON cr.application_id=a.id
            WHERE lower(trim(a.job_title))=lower(trim(?))
            ORDER BY
                CASE WHEN lower(trim(COALESCE(a.indeed_status,'')))='new'
                     THEN 0 ELSE 1 END,
                datetime(COALESCE(a.last_seen_at,a.first_seen_at,a.created_at)) DESC,
                a.id DESC
            LIMIT ?
            """,
            (title, int(limit)),
        ).fetchall()

    for row in rows:
        try:
            before = _existing_review(row["id"])
            result = analyze_application(row["id"], force=force)
            if (
                before
                and before.get("analysis_status") == "READY"
                and result
                and result.get("analysis_status") == "READY"
                and before.get("resume_fingerprint") == result.get("resume_fingerprint")
                and before.get("role_fingerprint") == result.get("role_fingerprint")
            ):
                skipped_completed += 1
            else:
                analyzed += 1
        except Exception as exc:
            errors += 1
            log(
                "WARN",
                f"Automatic role ranking failed for application {row['id']}: {exc}",
            )

    ranked = refresh_role_ranks(title)
    return {
        "analyzed": analyzed,
        "skipped_completed": skipped_completed,
        "ranked": ranked,
        "errors": errors,
    }


def analyze_scan_results(results, limit=24):
    roles = set()
    processed = 0
    waiting = 0

    ordered_results = sorted(
        list(results or []),
        key=lambda item: 0 if (
            item.get("new_applicant")
            or item.get("current_new")
            or _normalized_indeed_status(item.get("indeed_status")) == "new"
        ) else 1,
    )

    for item in ordered_results[: int(limit)]:
        source_key = item.get("source_key")
        if not source_key:
            continue
        app = get_by_source_key(source_key)
        if not app:
            continue
        title = _clean_role(app.get("job_title"))
        if not meaningful_role(title):
            continue
        roles.add(title)
        try:
            review = analyze_application(app["id"], force=False)
            if review and review.get("analysis_status") == "READY":
                processed += 1
            else:
                waiting += 1
        except Exception as exc:
            waiting += 1
            log("WARN", f"Immediate role ranking waiting for {source_key}: {exc}")

    for title in roles:
        refresh_role_ranks(title)

    return {
        "processed": processed,
        "waiting": waiting,
        "roles": sorted(roles),
    }


def analyze_pending_reviews(limit=40):
    init_role_review_db()
    sync_roles_from_applications()

    with conn() as c:
        rows = c.execute(
            """
            SELECT a.id, a.job_title
            FROM applications a
            LEFT JOIN candidate_reviews cr ON cr.application_id=a.id
            LEFT JOIN role_profiles rp
              ON lower(trim(rp.job_title))=lower(trim(a.job_title))
            WHERE lower(trim(COALESCE(a.job_title,'')))
                    NOT IN ('','the position','position','job','the job','unknown')
              AND lower(trim(COALESCE(a.indeed_status,''))) NOT IN (
                    'hired','selected','not selected','rejected','withdrawn','archived'
                  )
              AND rp.job_description IS NOT NULL
              AND trim(rp.job_description)<>''
              AND (
                    cr.application_id IS NULL
                    OR cr.analysis_status NOT IN ('READY','EXCLUDED_TERMINAL_STATUS')
                  )
            ORDER BY
                CASE WHEN lower(trim(COALESCE(a.indeed_status,'')))='new'
                     THEN 0 ELSE 1 END,
                datetime(COALESCE(a.last_seen_at,a.first_seen_at,a.created_at)) DESC,
                a.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    done = 0
    touched_roles = set()
    for row in rows:
        try:
            title = _clean_role(row["job_title"])
            if not role_accepts_ranking(title):
                continue
            analyze_application(row["id"], force=False)
            touched_roles.add(title)
            done += 1
        except Exception as exc:
            log(
                "WARN",
                f"Background automatic role ranking failed for {row['id']}: {exc}",
            )

    for title in touched_roles:
        refresh_role_ranks(title)

    return done


def set_manual_status(application_id: int, status: str, note: str | None = None):
    raise ValueError(
        "Manual shortlist is disabled. V11.10 maintains the automated role ranking."
    )


def ranking_status():
    init_role_review_db()
    sync_roles_from_applications()
    with conn() as c:
        role_row = c.execute(
            """
            SELECT
                COUNT(*) AS total_roles,
                SUM(CASE WHEN trim(job_description)<>'' THEN 1 ELSE 0 END)
                    AS roles_ready,
                SUM(CASE WHEN trim(job_description)='' THEN 1 ELSE 0 END)
                    AS roles_waiting_description
            FROM role_profiles
            """
        ).fetchone()
        review_row = c.execute(
            """
            SELECT
                SUM(CASE WHEN analysis_status='READY' THEN 1 ELSE 0 END)
                    AS ranked_candidates,
                SUM(CASE WHEN analysis_status<>'READY' THEN 1 ELSE 0 END)
                    AS waiting_candidates,
                SUM(CASE WHEN auto_shortlisted=1 THEN 1 ELSE 0 END)
                    AS auto_shortlisted,
                SUM(CASE WHEN analysis_status='EXCLUDED_TERMINAL_STATUS'
                         THEN 1 ELSE 0 END) AS removed_candidates
            FROM candidate_reviews
            """
        ).fetchone()

    return {
        "total_roles": int(role_row["total_roles"] or 0),
        "roles_ready": int(role_row["roles_ready"] or 0),
        "roles_waiting_description": int(role_row["roles_waiting_description"] or 0),
        "ranked_candidates": int(review_row["ranked_candidates"] or 0),
        "waiting_candidates": int(review_row["waiting_candidates"] or 0),
        "auto_shortlisted": int(review_row["auto_shortlisted"] or 0),
        "removed_candidates": int(review_row["removed_candidates"] or 0),
        "shortlist_threshold": AUTO_SHORTLIST_THRESHOLD,
    }


def role_review_payload(job_title: str):
    init_role_review_db()
    title = _clean_role(job_title)
    profile = get_role_profile(title)
    refresh_role_ranks(title)

    with conn() as c:
        rows = c.execute(
            """
            SELECT
                a.*,
                cr.analysis_status,
                cr.requirements_json,
                cr.evidence_json,
                cr.summary_json,
                cr.requirements_evidenced,
                cr.requirements_total,
                cr.match_score,
                cr.rank_position,
                cr.auto_shortlisted,
                cr.auto_bucket,
                cr.score_breakdown_json,
                cr.completed_at,
                cr.analyzed_at
            FROM applications a
            LEFT JOIN candidate_reviews cr ON cr.application_id=a.id
            WHERE lower(trim(a.job_title))=lower(trim(?))
            ORDER BY
                CASE WHEN cr.analysis_status='READY' THEN 0 ELSE 1 END,
                COALESCE(cr.rank_position, 999999) ASC,
                a.id DESC
            """,
            (title,),
        ).fetchall()

    applicants = []
    for row in rows:
        item = dict(row)
        for key, default in [
            ("requirements_json", []),
            ("evidence_json", []),
            ("summary_json", {}),
            ("score_breakdown_json", {}),
        ]:
            try:
                item[key.replace("_json", "")] = json.loads(
                    item.get(key) or json.dumps(default)
                )
            except Exception:
                item[key.replace("_json", "")] = default
        applicants.append(item)

    removed = [
        x for x in applicants
        if x.get("analysis_status") == "EXCLUDED_TERMINAL_STATUS"
        or _normalized_indeed_status(x.get("indeed_status")) in TERMINAL_INDEED_STATUSES
    ]
    active_applicants = [x for x in applicants if x not in removed]
    auto_shortlist = [
        x for x in active_applicants
        if int(x.get("auto_shortlisted") or 0) == 1
        and x.get("analysis_status") == "READY"
    ]
    remaining = [x for x in active_applicants if x not in auto_shortlist]

    return {
        "role": profile or {
            "job_title": title,
            "job_description": "",
            "description_source": "",
            "description_status": "WAITING",
        },
        "applicants": applicants,
        "active_applicants": active_applicants,
        "auto_shortlist": auto_shortlist,
        "remaining": remaining,
        "removed": removed,
        "ranking": {
            "ranked": sum(1 for x in active_applicants if x.get("analysis_status") == "READY"),
            "waiting": sum(1 for x in active_applicants if x.get("analysis_status") != "READY"),
            "auto_shortlisted": len(auto_shortlist),
            "removed": len(removed),
            "threshold": AUTO_SHORTLIST_THRESHOLD,
        },
        "notice": (
            "Ranking uses only the official role requirements and resume evidence. "
            "Terminal Indeed workflow states are removed from active ranking but retained in history. "
            "Gender/sex is not inferred or used."
        ),
    }
