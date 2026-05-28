"""
jobs/screening.py
──────────────────
Screening pipeline for job applications.

Pipeline:
  1. Blacklist check — name fuzzy match
  2. CV strength check — via Groq AI (Llama 3.3 70B)
  3. MCQ answers check — against HR's preferred answers
"""

import json
import re
import os
import urllib.request
from difflib import SequenceMatcher

from django.conf import settings
from .models import Application, Blacklist


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

def check_cv_strength(application: Application) -> tuple[bool, str]:
    """CV strength check — disabled until server deployment."""
    return True, ""

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
    from django.utils import timezone

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
    cv_passes, cv_summary = check_cv_strength(application)
    result["cv_passes"] = cv_passes

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
    application.save(update_fields=["status", "ai_summary", "ai_screened_at"])

    result["summary"] = full_summary
    return result