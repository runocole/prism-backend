"""
exam_sessions/emails.py
────────────────────────
Result emails after the reviewer's decision (sent from Results → "Email candidates").

  passed → congratulations + next stage: interview date, time, address and
           what to bring (HR fills these; time/date can differ per person)
  failed → polite rejection, optionally with the reviewer's remarks

Called from a background thread.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import Session

logger = logging.getLogger(__name__)


def _role(session: Session) -> str:
    invite = session.invite
    if invite.job_id:
        return invite.job.title
    return invite.test.job_role or invite.test.title


def _first_name(session: Session) -> str:
    return (session.invite.candidate_name or "").split(" ")[0] or "Candidate"


def passed_email(session: Session, date: str, time: str, address: str, notes: str) -> tuple[str, str]:
    company = settings.COMPANY_NAME
    lines = [
        f"Dear {_first_name(session)},",
        "",
        f"Congratulations! Following your assessment for the {_role(session)} role at {company}, "
        "we are pleased to let you know that you have moved on to the next stage.",
        "",
        "Please come for the next stage as follows:",
        f"  Date: {date}",
        f"  Time: {time}",
        f"  Address: {address}",
    ]
    if notes.strip():
        lines += ["", "Please note / bring along:", notes.strip()]
    lines += [
        "",
        "Kindly reply to this email to confirm that you will attend.",
        "",
        f"Best regards,\n{company} Recruitment Team",
    ]
    return f"Next stage: {_role(session)} at {company}", "\n".join(lines)


def failed_email(session: Session, include_remarks: bool) -> tuple[str, str]:
    company = settings.COMPANY_NAME
    lines = [
        f"Dear {_first_name(session)},",
        "",
        f"Thank you for taking the time to complete the assessment for the {_role(session)} role at {company}.",
        "",
        "Unfortunately, after careful review, we will not be moving forward with your application at this time.",
    ]
    remarks = (session.decision_notes or "").strip()
    if include_remarks and remarks:
        lines += ["", "Feedback from our review:", remarks]
    lines += [
        "",
        "We appreciate your interest and wish you every success in your search.",
        "",
        f"Best regards,\n{company} Recruitment Team",
    ]
    return f"Your application for {_role(session)} at {company}", "\n".join(lines)


def send_outcome_emails(passed: list[dict], failed_ids: list[int], address: str, notes: str, include_remarks: bool):
    """
    passed: [{"session_id": 1, "date": "Mon 10 Nov 2026", "time": "10:00 AM"}, ...]
    Marks each session as emailed when its email goes out.
    """
    jobs = [(p["session_id"], "passed", p) for p in passed] + [(sid, "failed", None) for sid in failed_ids]
    for session_id, kind, p in jobs:
        session = Session.objects.select_related("invite__test", "invite__job").filter(pk=session_id).first()
        if not session:
            continue
        if kind == "passed":
            subject, body = passed_email(session, p["date"], p["time"], address, notes)
        else:
            subject, body = failed_email(session, include_remarks)
        try:
            EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [session.invite.candidate_email]).send()
        except Exception:
            logger.exception("Result email failed for session %s", session_id)
            continue
        Session.objects.filter(pk=session_id).update(outcome_email_kind=kind, outcome_emailed_at=timezone.now())
