"""
jobs/imports.py
────────────────
"Import CVs": HR uploads a batch of CV files (PDF / Word) for a job.
For each file, in the background:
  1. read the candidate's name, email and phone from the CV
     (AI if ANTHROPIC_API_KEY is set, otherwise a plain text search)
  2. create an Application with status "awaiting_form" and a personal token
  3. email them their personal screening link

Their answers then arrive through the public form like any applicant.
"""

import logging
import os
import re
import uuid

import anthropic
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import EmailMessage
from django.utils import timezone

from config import ai

from .models import Application, CvImport

logger = logging.getLogger(__name__)

CONTACT_SCHEMA = {
    "type": "object",
    "properties": {
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
    },
    "required": ["first_name", "last_name", "email", "phone"],
    "additionalProperties": False,
}

CONTACT_INSTRUCTIONS = (
    "The document above is a job applicant's CV. Copy the applicant's own contact details exactly "
    "as written: first_name, last_name, email and phone (their main phone number). Use an empty "
    'string for anything that is not in the CV. Do not use referees\' or employers\' details.'
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+?\d[\d\s\-()]{8,}\d)")


def _plain_text(data: bytes, filename: str) -> str:
    """Best-effort text for the no-AI fallback."""
    if filename.lower().endswith(".docx"):
        try:
            return ai._docx_text(data)
        except ai.DocumentError:
            return ""
    try:
        import pypdf
        import io
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:3])
    except Exception:
        return ""


def extract_contact(data: bytes, filename: str) -> dict:
    if ai.is_configured():
        try:
            result = ai.ask_about_document(data, filename, CONTACT_INSTRUCTIONS, CONTACT_SCHEMA, effort="low")
            return {k: (result.get(k) or "").strip() for k in ("first_name", "last_name", "email", "phone")}
        except (ai.DocumentError, anthropic.APIError):
            logger.exception("AI contact extraction failed for %s; using text search", filename)

    text = _plain_text(data, filename)
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    stem = os.path.splitext(os.path.basename(filename))[0]
    words = re.sub(r"[_\-.()\d]+", " ", stem).split()
    name = [w for w in words if w.lower() not in {"cv", "resume", "curriculum", "vitae", "updated", "new", "final"}]
    return {
        "first_name": name[0].title() if name else "",
        "last_name": " ".join(name[1:]).title() if len(name) > 1 else "",
        "email": email.group(0) if email else "",
        "phone": phone.group(0).strip() if phone else "",
    }


def form_url(application: Application) -> str:
    return f"{application.link.url}?t={application.form_token}"


def send_form_email(application: Application) -> bool:
    """Email an imported candidate their personal screening link."""
    job = application.job
    company = settings.COMPANY_NAME
    first = application.first_name or "there"
    body = (
        f"Dear {first},\n\n"
        f"Thank you for your interest in the {job.title} role at {company}.\n\n"
        f"To move your application forward, please answer a few short questions here "
        f"(it takes about 5 minutes):\n"
        f"{form_url(application)}\n\n"
        f"This link is personal to you, so please don't share it.\n\n"
        f"Best regards,\n{company} Recruitment Team"
    )
    message = EmailMessage(
        subject=f"{job.title} at {company}: next step in your application",
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[application.email],
    )
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Failed to send screening link to %s", application.email)
        return False
    application.form_sent_at = timezone.now()
    application.save(update_fields=["form_sent_at"])
    return True


def process_cv_import(import_id, stored_files: list[tuple[str, str]]):
    """
    Background job. stored_files = [(original_filename, storage_path), ...]
    saved by the view before this thread starts.
    """
    batch = CvImport.objects.select_related("job", "link").get(pk=import_id)
    job, link = batch.job, batch.link

    for original_name, path in stored_files:
        problem = None
        try:
            with default_storage.open(path, "rb") as f:
                data = f.read()
            contact = extract_contact(data, original_name)
            email = contact["email"].lower()

            if not EMAIL_RE.fullmatch(email or ""):
                problem = "No email address found in the CV"
            elif Application.objects.filter(job=job, email__iexact=email).exists():
                batch.duplicate_count += 1
            else:
                application = Application(
                    job=job,
                    link=link,
                    source=Application.Source.IMPORT,
                    status=Application.Status.AWAITING_FORM,
                    first_name=contact["first_name"][:150] or "Candidate",
                    last_name=contact["last_name"][:150],
                    email=email,
                    phone=contact["phone"][:50],
                    form_token=uuid.uuid4(),
                )
                application.cv.save(original_name, ContentFile(data), save=False)
                application.save()
                batch.created_count += 1
                if send_form_email(application):
                    batch.emailed_count += 1
                else:
                    problem = f"Imported {email}, but the email failed to send (use Resend)"
        except Exception:
            logger.exception("Import failed for %s", original_name)
            problem = "Could not read this file"
        finally:
            default_storage.delete(path)

        if problem:
            batch.problems = batch.problems + [{"file": original_name, "reason": problem}]
        batch.processed += 1
        batch.save(update_fields=[
            "processed", "created_count", "duplicate_count", "emailed_count", "problems",
        ])

    batch.status = CvImport.Status.DONE
    batch.save(update_fields=["status"])
