"""
jobs/emails.py
───────────────
Candidate emails at the application stage (branded — see config/emails.py):

  send_screening_link   imported CVs: "please answer a few questions" + personal link
  send_application_received   after someone applies, or answers their screening link
"""

from django.conf import settings
from django.utils import timezone

from config.emails import send_branded

from .models import Application


def reference(app: Application) -> str:
    return f"{timezone.localtime(app.submitted_at).year}-{app.id:05d}"


def send_screening_link(app: Application, url: str) -> bool:
    first = app.first_name or "there"
    return send_branded(
        app.email,
        f"{app.job.title} at {settings.COMPANY_NAME}: next step in your application",
        eyebrow="Application update",
        heading="Tell us a little more about you",
        preheader="A few short questions about your application (about 5 minutes).",
        paragraphs=[
            f"Dear {first},",
            f"Thank you for your interest in the {app.job.title} role at {settings.COMPANY_NAME}. "
            "To move your application forward, please answer a few short questions. It takes about 5 minutes.",
        ],
        details=[("Position", app.job.title)],
        button=("Answer the questions", url),
        after=["This link is personal to you. Please don't share it."],
        stage=1,
    )


def send_application_received(app_id: int):
    """Background job: confirm we've got their application / answers."""
    app = Application.objects.select_related("job").filter(pk=app_id).first()
    if not app:
        return
    first = app.first_name or "there"
    send_branded(
        app.email,
        f"Application received – {app.job.title} – {settings.COMPANY_NAME}",
        eyebrow="Application received",
        heading=f"Thank you for applying, {first}!",
        preheader=f"We've received your application for {app.job.title}.",
        paragraphs=[
            f"We've received your application for the {app.job.title} role at {settings.COMPANY_NAME}. "
            "Our recruitment team will review it carefully.",
            "If your application is shortlisted, we'll email you the next step: an online assessment.",
        ],
        details=[
            ("Position", app.job.title),
            ("Application reference", reference(app)),
            ("Submitted", timezone.localtime(app.form_completed_at or app.submitted_at).strftime("%d %B %Y")),
        ],
        details_title="Your application",
        after=["Please keep your application reference for any questions."],
        stage=1,
    )
