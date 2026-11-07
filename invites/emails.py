"""
invites/emails.py
──────────────────
The assessment invitation email (branded — see config/emails.py).

Shows when the assessment opens (opens_at, e.g. tomorrow 8:00 AM) and when the
link expires, with a "Start assessment" button. Always sent from a background
thread (config.background.run_in_background).
"""

import logging

from django.conf import settings
from django.utils import timezone

from config.emails import send_branded

from .models import Invite

logger = logging.getLogger(__name__)


def when(dt) -> str:
    """e.g. 'Monday, 9 November 2026 at 8:00 AM' in the server's time zone (Lagos)."""
    local = timezone.localtime(dt)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.strftime('%A')}, {local.day} {local.strftime('%B %Y')} at {hour}:{local.strftime('%M %p')}"


def _role(invite: Invite, job_title: str | None) -> str:
    if job_title:
        return job_title
    if invite.job_id:
        return invite.job.title
    return invite.test.job_role or invite.test.title


def send_invite_email(invite: Invite, job_title: str | None = None) -> bool:
    role = _role(invite, job_title)
    first = (invite.candidate_name or "").split(" ")[0] or "there"
    link = f"{settings.FRONTEND_URL}/c/{invite.token}"
    details = [("Position", role)]
    if invite.opens_at:
        details.append(("Assessment opens", when(invite.opens_at)))
    details += [
        ("Link expires", when(invite.expires_at)),
        ("Duration", f"{invite.test.duration_mins} minutes, in one sitting"),
    ]
    paragraphs = [
        f"Dear {first},",
        f"Thank you for your interest in the {role} role at {settings.COMPANY_NAME}. "
        "You have been invited to take an online assessment, the next step in our recruitment process.",
    ]
    if invite.opens_at:
        paragraphs.append("Your link will work from the opening time below. We've sent it early so you have time to prepare.")
    return send_branded(
        invite.candidate_email,
        f"Your assessment for {role} – {settings.COMPANY_NAME}",
        eyebrow="Assessment invitation",
        heading="You're invited to an assessment",
        preheader=f"Your {role} assessment" + (f" opens {when(invite.opens_at)}" if invite.opens_at else ""),
        paragraphs=paragraphs,
        details=details,
        details_title="Assessment details",
        button=("Start assessment", link),
        after=[
            "Before you begin: use a laptop or desktop with a working webcam and microphone, in a quiet, well-lit place "
            "with a stable internet connection. When you start, you'll wait briefly while a member of our team checks you in.",
            "This link is personal to you. Please don't share it.",
        ],
        stage=2,
    )


def send_invite_emails(invite_ids: list[int]):
    """Background job for single/batch invites."""
    for invite in Invite.objects.select_related("test", "job").filter(pk__in=invite_ids):
        send_invite_email(invite)
