"""
jobs/screening.py
──────────────────
Screening pipeline for job applications.

Pipeline:
  1. Blacklist check — phone match, fuzzy name fallback
  2. CV check (optional) — Claude reads the CV (PDF / Word) and scores it 0-100
  3. Preferred answers — the candidate's answers on their link vs HR's preferred answers

The CV check is optional per job (JobPost.cv_ai_mode): off / rank only / rank + screen out.

screen_applications() runs the pipeline for many applications and is
started in a background thread by ScreenJobView.
"""

import logging
from difflib import SequenceMatcher

import anthropic
from django.conf import settings
from django.utils import timezone

from config import ai
from .models import Application, Blacklist, JobPost
from .questions import check_preferred

logger = logging.getLogger(__name__)


# ── Step 1: Blacklist check ───────────────────────────────────────────────────

def check_blacklist(application: Application) -> bool:
    """
    Returns True if the candidate is blacklisted.
    Checks by phone number (exact match after cleaning).
    Falls back to fuzzy name match if no phone provided.
    """
    # Clean phone — remove spaces, dashes, +234 prefix normalization
    def clean_phone(phone: str) -> str:
        phone = str(phone).strip().replace(" ", "").replace("-", "").replace("+", "")
        # Normalize Nigerian numbers: 234XXXXXXXXXX -> 0XXXXXXXXXX
        if phone.startswith("234") and len(phone) == 13:
            phone = "0" + phone[3:]
        # Remove leading zero for comparison
        if phone.startswith("0") and len(phone) == 11:
            phone = phone[1:]
        return phone

    candidate_phones = [clean_phone(p) for p in [application.phone or "", application.phone2 or ""] if p]

    for entry in Blacklist.objects.all():
        # Phone match
        if candidate_phones and entry.phone:
            entry_phone = clean_phone(entry.phone)
            if entry_phone in candidate_phones:
                return True

        # Fallback — fuzzy name match if no phone
        if not candidate_phones or not entry.phone:
            name = (application.first_name + " " + application.last_name).strip().lower()
            entry_name = entry.name.strip().lower()
            ratio = SequenceMatcher(None, name, entry_name).ratio()
            if ratio >= 0.85:
                return True

    return False


# ── Step 2: CV strength check via Claude ─────────────────────────────────────

CV_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "summary": {"type": "string"},
        "relevant_experience_years": {"type": "integer"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "partial", "missing"]},
                    "evidence": {"type": "string"},
                },
                "required": ["requirement", "status", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["score", "summary", "relevant_experience_years", "requirements"],
    "additionalProperties": False,
}


def _cv_instructions(job) -> str:
    return f"""You are screening job applications for OTIC Geosystems. The document above is a candidate's CV.

Rate how well the candidate fits this role:
Job title: {job.title}
Department: {job.department}
Description:
{job.description or "(none given)"}
Requirements:
{job.requirements or "(none given)"}

"requirements": list each distinct requirement from the Requirements above (at most 10, in the order given, each in a few words). If no requirements are given, use the 3-6 most important ones implied by the description. For each:
- "status": "met", "partial" (some but not all, or only indirectly), or "missing" (nothing in the CV shows it)
- "evidence": a short phrase from what the CV says, e.g. "4 years as GIS analyst at X" or "not mentioned"

"relevant_experience_years": whole years of work experience relevant to this role (0 if none or unclear).

"score": a whole number from 0 to 100:
- 0-39: clearly unqualified for this role, or the document is not a real CV
- 40-59: partly relevant, with significant gaps
- 60-79: a good fit
- 80-100: a strong fit

"summary": 1-2 plain sentences for HR with the main strength and the main gap.

Judge only skills, qualifications and experience relevant to the role. Ignore name, age, gender, religion, ethnicity, marital status and photos. Do not guess: if the CV doesn't show something, mark it missing."""


def _clean_cv_details(result: dict) -> dict:
    reqs = []
    for r in result.get("requirements") or []:
        text = (r.get("requirement") or "").strip()
        if text:
            status = r.get("status") if r.get("status") in ("met", "partial", "missing") else "missing"
            reqs.append({"requirement": text, "status": status, "evidence": (r.get("evidence") or "").strip()})
    return {
        "requirements": reqs[:10],
        "relevant_experience_years": max(0, int(result.get("relevant_experience_years") or 0)),
    }


def check_cv_strength(application: Application) -> tuple[bool, str, float | None, dict]:
    """
    Claude reads the CV and scores it against the job.
    Returns (passes, summary, score, details) — details holds the
    requirement checklist and years of relevant experience.

    If the AI can't run (no key, unreadable file, API down) the candidate
    passes this step with a note, so nobody is rejected because of a
    technical problem — HR sees the note and can check the CV by hand.
    """
    if not ai.is_configured():
        return True, "CV not AI-reviewed (ANTHROPIC_API_KEY not set).", None, {}

    cv = application.cv
    if not cv or not ai.is_supported(cv.name):
        return True, "CV not AI-reviewed (file type not supported) — check manually.", None, {}

    try:
        with cv.open("rb") as f:
            data = f.read()
        result = ai.ask_about_document(
            data, cv.name, _cv_instructions(application.job), CV_SCHEMA, effort="medium"
        )
    except ai.DocumentError as exc:
        return True, f"CV not AI-reviewed ({exc}) — check manually.", None, {}
    except (anthropic.APIError, OSError):
        logger.exception("CV check failed for application %s", application.id)
        return True, "CV AI check failed (service error) — check manually.", None, {}

    score = max(0, min(100, int(result.get("score", 0))))
    summary = (result.get("summary") or "").strip()
    passes = score >= settings.CV_PASS_SCORE
    verdict = "passes" if passes else f"below the pass mark of {settings.CV_PASS_SCORE}"
    return passes, f"CV {score}/100 ({verdict}): {summary}", float(score), _clean_cv_details(result)


# ── Step 3: Preferred answers check ──────────────────────────────────────────

def check_answers(application: Application) -> tuple[bool, str]:
    """Compare the candidate's answers with HR's preferred answers on their link."""
    link = application.link
    if not link:
        return True, ""
    mismatches = check_preferred(link.questions or [], application.screening_answers or {})
    if mismatches:
        return False, f"Answer mismatch on: {'; '.join(mismatches)}"
    return True, "All answers match preferred criteria."


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_screening(application: Application) -> dict:
    """
    Blacklist → (optional) CV check → preferred answers.
    The CV check only runs if the job's cv_ai_mode is "rank" or "screen";
    with "rank" the score is for sorting only and never screens anyone out.
    Sets application.status and returns a result dict.
    """
    result = {"blacklisted": False, "cv_passes": True, "answers_pass": True, "summary": ""}

    # 1. Blacklist check
    if check_blacklist(application):
        application.status = Application.Status.BLACKLISTED
        application.ai_summary = "Candidate matched blacklist."
        application.ai_screened_at = timezone.now()
        application.save(update_fields=["status", "ai_summary", "ai_screened_at"])
        result["blacklisted"] = True
        result["summary"] = "Blacklisted"
        return result

    summaries = []

    # 2. CV check (AI) — only if switched on for this job
    mode = application.job.cv_ai_mode
    if mode in (JobPost.CvAiMode.RANK, JobPost.CvAiMode.SCREEN):
        cv_passes, cv_summary, cv_score, cv_details = check_cv_strength(application)
        application.ai_score = cv_score
        application.ai_details = cv_details
        if mode == JobPost.CvAiMode.SCREEN:
            result["cv_passes"] = cv_passes
        elif cv_score is not None:
            cv_summary = cv_summary.replace(" (passes)", "").replace(
                f" (below the pass mark of {settings.CV_PASS_SCORE})", ""
            )
        summaries.append(cv_summary)

    # 3. Preferred answers
    answers_pass, answers_summary = check_answers(application)
    result["answers_pass"] = answers_pass
    summaries.append(answers_summary)

    full_summary = " | ".join(s for s in summaries if s)
    passes = result["cv_passes"] and answers_pass
    application.status = (
        Application.Status.SCREENED_IN if passes else Application.Status.SCREENED_OUT
    )
    application.ai_summary = full_summary
    application.ai_screened_at = timezone.now()
    application.save(update_fields=["status", "ai_score", "ai_details", "ai_summary", "ai_screened_at"])

    result["summary"] = full_summary
    return result


def screen_applications(application_ids: list[int]):
    """
    Background job: screen each application in turn.
    An application that errors goes back to "pending" so the next
    "Screen applicants" click retries it.
    """
    for app_id in application_ids:
        try:
            application = Application.objects.select_related("job").get(pk=app_id)
        except Application.DoesNotExist:
            continue
        if application.status != Application.Status.SCREENING:
            continue  # someone changed it meanwhile (e.g. manual screen-in)
        try:
            run_screening(application)
        except Exception:
            logger.exception("Screening failed for application %s", app_id)
            Application.objects.filter(pk=app_id).update(
                status=Application.Status.PENDING,
                ai_summary="Screening failed — click Screen again to retry.",
            )
