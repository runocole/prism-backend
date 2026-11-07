"""
exam_sessions/emails.py
────────────────────────
Result emails after the reviewer's decision (Results → "Email candidates"),
in the branded design (config/emails.py):

  passed → "Congratulations" + next-stage details (date, time, address, what to
           bring) + a "Confirm attendance" button (opens a reply to HR)
  failed → a polite, generic message (reviewer remarks are never included)

Called from a background thread.
"""

import logging
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from config.emails import hr_address, send_branded

from .models import Session

logger = logging.getLogger(__name__)


def _role(session: Session) -> str:
    invite = session.invite
    if invite.job_id:
        return invite.job.title
    return invite.test.job_role or invite.test.title


def _first_name(session: Session) -> str:
    return (session.invite.candidate_name or "").split(" ")[0] or "Candidate"


def send_passed(session: Session, date: str, time: str, address: str, notes: str) -> bool:
    role = _role(session)
    name = session.invite.candidate_name
    subject = quote(f"Confirming attendance – {name} – {role}")
    body = quote(f"Hello,\n\nI confirm I will attend on {date} at {time}.\n\nKind regards,\n{name}")
    details = [("Position", role), ("Date", date), ("Time", time), ("Address", address)]
    if notes.strip():
        details.append(("Please bring / note", notes.strip()))
    return send_branded(
        session.invite.candidate_email,
        f"Congratulations – next stage for {role} – {settings.COMPANY_NAME}",
        eyebrow="Application update",
        heading=f"Congratulations, {_first_name(session)}!",
        preheader=f"You've progressed to the next stage for {role}.",
        paragraphs=[
            f"Following your assessment for the {role} role at {settings.COMPANY_NAME}, we're pleased to let you know "
            "that you've progressed to the next stage of our recruitment process.",
        ],
        details=details,
        details_title="Your interview details",
        button=("Confirm your attendance", f"mailto:{hr_address()}?subject={subject}&body={body}"),
        after=["We look forward to meeting you. If you can't make this time, please reply to this email."],
        stage=3,
    )


def send_failed(session: Session) -> bool:
    return send_branded(
        session.invite.candidate_email,
        f"Update on Your Application – {settings.COMPANY_NAME}",
        eyebrow="Application update",
        heading="Update on your application",
        paragraphs=[
            f"Dear {session.invite.candidate_name},",
            "Thank you for taking the time to participate in our recruitment assessment for the position you "
            f"applied for at {settings.COMPANY_NAME}.",
            "After carefully reviewing your assessment results, we regret to inform you that you did not meet the "
            "required pass mark to progress to the next stage of our recruitment process.",
            "We sincerely appreciate your interest in joining our team and the time and effort you invested in the "
            "application process.",
            "Although we will not be progressing with your application at this time, we encourage you to keep "
            f"developing your skills and to consider future opportunities with {settings.COMPANY_NAME}.",
            "We wish you all the best in your future endeavours.",
        ],
        stage=4,
    )


def send_outcome_emails(passed: list[dict], failed_ids: list[int], address: str, notes: str, include_remarks: bool = False):
    """
    passed: [{"session_id": 1, "date": "Monday, 10 November 2026", "time": "10:00 AM"}, ...]
    Marks each session as emailed when its email goes out. (include_remarks is
    ignored: rejection emails are always the generic message.)
    """
    for p in passed:
        session = Session.objects.select_related("invite__test", "invite__job").filter(pk=p["session_id"]).first()
        if session and send_passed(session, p["date"], p["time"], address, notes):
            Session.objects.filter(pk=session.pk).update(outcome_email_kind="passed", outcome_emailed_at=timezone.now())
    for sid in failed_ids:
        session = Session.objects.select_related("invite__test", "invite__job").filter(pk=sid).first()
        if session and send_failed(session):
            Session.objects.filter(pk=session.pk).update(outcome_email_kind="failed", outcome_emailed_at=timezone.now())
