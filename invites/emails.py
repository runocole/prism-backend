"""
invites/emails.py
──────────────────
Sends the assessment invite email. Used by:
  - single / batch invites (invites/views.py)
  - "Email screened-in candidates" (jobs/views.py BatchEmailView)

Always called from a background thread (config.background.run_in_background)
so HR's request doesn't wait on SMTP.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Invite

logger = logging.getLogger(__name__)


def build_invite_email(invite: Invite, job_title: str | None = None) -> tuple[str, str]:
    role = job_title or invite.test.job_role or invite.test.title
    first_name = (invite.candidate_name or "").split(" ")[0] or "Candidate"
    exam_link = f"{settings.FRONTEND_URL}/c/{invite.token}"
    expires = timezone.localtime(invite.expires_at).strftime("%d %b %Y at %H:%M %Z")

    subject = f"APPLICATION ASSESSMENT — {role}"
    message = (
        f"Dear {first_name},\n\n"
        f"You have been invited to take an assessment for the {role} position "
        f"at OTIC Geosystems.\n\n"
        f"Please complete your assessment using the link below:\n"
        f"{exam_link}\n\n"
        f"IMPORTANT DETAILS:\n"
        f"- This link is unique to you. Do not share it.\n"
        f"- The link expires on {expires}.\n"
        f"- The assessment lasts {invite.test.duration_mins} minutes once started, "
        f"and must be completed in one sitting.\n"
        f"- Use a desktop browser (Chrome, Firefox or Edge) in a quiet place with a "
        f"stable internet connection.\n"
        f"- Your webcam will be required throughout the assessment.\n\n"
        f"If you have any issues, reply to this email.\n\n"
        f"Best regards,\nOTIC Recruitment Team\nadmin@oticgs.com"
    )
    return subject, message


def send_invite_email(invite: Invite, job_title: str | None = None) -> bool:
    """Send one invite email. Returns True on success, logs and returns False on failure."""
    subject, message = build_invite_email(invite, job_title)
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invite.candidate_email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Failed to send invite %s to %s", invite.id, invite.candidate_email)
        return False


def send_invite_emails(invite_ids: list[int]):
    """Background job for single/batch invites."""
    for invite in Invite.objects.select_related("test").filter(pk__in=invite_ids):
        send_invite_email(invite)
