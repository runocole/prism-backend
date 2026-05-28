"""
invites/tasks.py
─────────────────
Celery task for async email delivery.

Sending email synchronously would block the API response for 1-3 seconds
per email. With batch invites this is unacceptable — Celery handles it
in the background after the response is already returned to HR.

To run the worker locally:
  celery -A config worker --loglevel=info
"""

from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_invite_email(self, invite_id: int):
    """
    Send a test invite email to the candidate.
    Retries up to 3 times with a 60-second delay on SMTP failure.
    """
    from .models import Invite  # local import avoids circular import

    try:
        invite = Invite.objects.select_related("test").get(pk=invite_id)
    except Invite.DoesNotExist:
        return  # invite deleted before task ran — nothing to do

    # AFTER — matches frontend route /c/$token
    invite_url = f"https://{settings.ALLOWED_HOSTS[0]}/c/{invite.token}"

    subject = f"You've been invited to take: {invite.test.title}"
    message = (
        f"Hi {invite.candidate_name},\n\n"
        f"You have been invited to complete a technical assessment: "
        f'"{invite.test.title}".\n\n'
        f"Duration: {invite.test.duration_mins} minutes\n"
        f"Link expires: {invite.expires_at.strftime('%d %b %Y at %H:%M UTC')}\n\n"
        f"Start your test here:\n{invite_url}\n\n"
        f"Important:\n"
        f"  • Use a desktop browser (Chrome, Firefox, or Edge)\n"
        f"  • You will need a working webcam\n"
        f"  • The test must be completed in one sitting — no retakes\n\n"
        f"Good luck!\n"
        f"ProctorCore"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invite.candidate_email],
            fail_silently=False,
        )
    except Exception as exc:
        raise self.retry(exc=exc)