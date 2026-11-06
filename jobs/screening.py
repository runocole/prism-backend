"""
jobs/screening.py
──────────────────
Screening pipeline for job applications.

Pipeline:
  1. Blacklist check — phone match, fuzzy name fallback
  2. CV strength check — Claude reads the CV (PDF / Word) and scores it 0-100
  3. MCQ answers check — against HR's preferred answers

screen_applications() runs the pipeline for many applications and is
started in a background thread by ScreenJobView.
"""

import logging
from difflib import SequenceMatcher

import anthropic
from django.conf import settings
from django.utils import timezone

from config import ai
from .models import Application, Blacklist

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


# ── Step 2: CV strength check via Groq ───────────────────────────────────────

CV_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "summary": {"type": "string"},
    },
    "required": ["score", "summary"],
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

Give "score" as a whole number from 0 to 100:
- 0-39: clearly unqualified for this role, or the document is not a real CV
- 40-59: partly relevant, with significant gaps
- 60-79: a good fit
- 80-100: a strong fit

Judge only skills, qualifications and experience relevant to the role. Ignore name, age, gender, religion, ethnicity, marital status and photos.

Give "summary" as 2-3 plain sentences for HR: the main strengths and the main gaps against the requirements."""


def check_cv_strength(application: Application) -> tuple[bool, str, float | None]:
    """
    Claude reads the CV and scores it against the job.
    Returns (passes, summary, score).

    If the AI can't run (no key, unreadable file, API down) the candidate
    passes this step with a note, so nobody is rejected because of a
    technical problem — HR sees the note and can check the CV by hand.
    """
    if not ai.is_configured():
        return True, "CV not AI-reviewed (ANTHROPIC_API_KEY not set).", None

    cv = application.cv
    if not cv or not ai.is_supported(cv.name):
        return True, "CV not AI-reviewed (file type not supported) — check manually.", None

    try:
        with cv.open("rb") as f:
            data = f.read()
        result = ai.ask_about_document(
            data, cv.name, _cv_instructions(application.job), CV_SCHEMA, effort="medium"
        )
    except ai.DocumentError as exc:
        return True, f"CV not AI-reviewed ({exc}) — check manually.", None
    except (anthropic.APIError, OSError):
        logger.exception("CV check failed for application %s", application.id)
        return True, "CV AI check failed (service error) — check manually.", None

    score = max(0, min(100, int(result.get("score", 0))))
    summary = (result.get("summary") or "").strip()
    passes = score >= settings.CV_PASS_SCORE
    verdict = "passes" if passes else f"below the pass mark of {settings.CV_PASS_SCORE}"
    return passes, f"CV {score}/100 ({verdict}): {summary}", float(score)

# ── Step 3: MCQ answers check ─────────────────────────────────────────────────

def check_mcq_answers(application: Application) -> tuple[bool, str]:
    preferred = application.job.preferred_answers or {}
    candidate_answers = application.screening_answers or {}

    failed_questions = []

    for question_key, preferred_values in preferred.items():
        if not preferred_values:
            continue

        candidate_answer = candidate_answers.get(question_key)

        if candidate_answer is None:
            continue

        # Salary expectation — numeric comparison
        if question_key == "salary_expectation":
            try:
                # Extract first number from candidate's answer
                max_acceptable = float(str(preferred_values[0]).replace(",", "").replace("₦", "").strip())
                # Extract first number from candidate answer
                import re
                nums = re.findall(r"[\d,]+", str(candidate_answer).replace(",", ""))
                if nums:
                    candidate_salary = float(nums[0])
                    if candidate_salary > max_acceptable:
                        failed_questions.append("salary expectation too high")
            except (ValueError, TypeError):
                pass  # Can't parse — skip
            continue

        # All other MCQ fields
        if candidate_answer not in preferred_values:
            failed_questions.append(question_key.replace("_", " "))

    if failed_questions:
        return False, f"Answer mismatch on: {', '.join(failed_questions)}"

    return True, "All answers match preferred criteria."


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_screening(application: Application) -> dict:
    """
    Run all three screening checks on an application.
    Sets application.status and returns a result dict.
    """
    result = {
        "blacklisted": False,
        "cv_passes": True,
        "mcq_passes": True,
        "summary": "",
    }

    # 1. Blacklist check
    if check_blacklist(application):
        application.status = Application.Status.BLACKLISTED
        application.ai_summary = "Candidate matched blacklist."
        application.ai_screened_at = timezone.now()
        application.save(update_fields=["status", "ai_summary", "ai_screened_at"])
        result["blacklisted"] = True
        result["summary"] = "Blacklisted"
        return result

    # 2. CV strength check
    cv_passes, cv_summary, cv_score = check_cv_strength(application)
    result["cv_passes"] = cv_passes
    application.ai_score = cv_score

    # 3. MCQ answers check
    mcq_passes, mcq_summary = check_mcq_answers(application)
    result["mcq_passes"] = mcq_passes

    # Combine summaries
    summaries = [s for s in [cv_summary, mcq_summary] if s]
    full_summary = " | ".join(summaries)

    # Final decision
    if cv_passes and mcq_passes:
        application.status = Application.Status.SCREENED_IN
    else:
        application.status = Application.Status.SCREENED_OUT

    application.ai_summary = full_summary
    application.ai_screened_at = timezone.now()
    application.save(update_fields=["status", "ai_score", "ai_summary", "ai_screened_at"])

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
