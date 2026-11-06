"""
config/ai.py
─────────────
Shared helper for sending a PDF or Word (.docx) document to Claude and
getting structured JSON back.

Used by:
  jobs/screening.py      — scores a candidate's CV against the job
  assessments/tasks.py   — extracts test questions from an uploaded paper

The API key is read from settings.ANTHROPIC_API_KEY, which comes from the
ANTHROPIC_API_KEY environment variable (see config/settings.py).
"""

import base64
import io
import json
import logging

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
SUPPORTED_EXTENSIONS = (".pdf", ".docx")


class DocumentError(Exception):
    """The document couldn't be read, or Claude couldn't process it."""


class AINotConfigured(Exception):
    """ANTHROPIC_API_KEY hasn't been set."""


def is_configured() -> bool:
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def is_supported(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)


def _docx_text(data: bytes) -> str:
    import docx  # python-docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentError("Could not open the Word document.") from exc

    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def document_block(data: bytes, filename: str) -> dict:
    """Build a Claude content block for a PDF (sent as-is) or .docx (sent as text)."""
    name = filename.lower()

    if name.endswith(".pdf"):
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }

    if name.endswith(".docx"):
        text = _docx_text(data)
        if not text.strip():
            raise DocumentError("The Word document has no readable text.")
        return {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": text},
        }

    raise DocumentError("Only PDF and Word (.docx) files can be read.")


def ask_about_document(
    data: bytes,
    filename: str,
    instructions: str,
    schema: dict,
    effort: str = "medium",
) -> dict:
    """
    Send one document plus instructions to Claude and return the JSON answer,
    guaranteed to match `schema` (structured outputs).

    Raises AINotConfigured, DocumentError, or an anthropic.APIError subclass.
    """
    if not is_configured():
        raise AINotConfigured("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=300.0)

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        # If a safety classifier declines, retry on Anthropic's recommended
        # fallback model instead of failing.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[
            {
                "role": "user",
                "content": [
                    document_block(data, filename),
                    {"type": "text", "text": instructions},
                ],
            }
        ],
    )

    if response.stop_reason == "refusal":
        raise DocumentError("The AI declined to process this document.")
    if response.stop_reason == "max_tokens":
        raise DocumentError("The document is too long to process in one go.")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise DocumentError("The AI returned an empty answer.")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Unparseable AI response (request %s)", response._request_id)
        raise DocumentError("The AI returned an unreadable answer.") from exc
