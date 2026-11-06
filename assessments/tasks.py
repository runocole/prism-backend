"""
assessments/tasks.py
─────────────────────
Background job: read an uploaded test paper (PDF / Word) with Claude and
turn it into questions for the test builder.
"""

import logging
import uuid

import anthropic

from config import ai

from .models import QuestionImport

logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are extracting exam questions from the test paper above so they can be loaded into an online test builder.

Rules:
- Skip all header text, company name, address, instructions, and name/date/phone input fields.
- For each numbered question, decide if it is "mcq" (it has lettered options such as a/b/c/d) or "short_answer".
- For mcq: put the full question in "prompt", every option in "options" without its letter prefix (no "A." / "b)"), and the 0-based index of the correct option in "correct_index". If the paper does not mark the correct answer, use 0.
- For short_answer: put the full question text in "prompt", set "options" to [] and "correct_index" to 0.
- Split multi-part questions such as 5a) and 5b) into separate questions, each with its full text so it makes sense on its own.
- Only add "[See diagram]" to a prompt if the question explicitly refers to a diagram, figure or table on the paper.
- Set "points" to 5 for every question.
- Keep questions in the order they appear on the paper."""

SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["mcq", "short_answer"]},
                    "prompt": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "correct_index": {"type": "integer"},
                    "points": {"type": "integer"},
                },
                "required": ["type", "prompt", "options", "correct_index", "points"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def _clean(raw_questions: list) -> list:
    """Normalise to the shape the frontend expects."""
    cleaned = []
    for q in raw_questions:
        prompt = (q.get("prompt") or "").strip()
        if not prompt:
            continue
        points = q.get("points") or 5
        options = [o.strip() for o in q.get("options") or [] if o and o.strip()]

        if q.get("type") == "mcq" and len(options) >= 2:
            index = q.get("correct_index") or 0
            if not 0 <= index < len(options):
                index = 0
            cleaned.append({
                "type": "mcq",
                "prompt": prompt,
                "options": options,
                "correct_index": index,
                "points": points,
            })
        else:
            cleaned.append({"type": "short_answer", "prompt": prompt, "points": points})
    return cleaned


def process_question_import(import_id: uuid.UUID, data: bytes, filename: str):
    job = QuestionImport.objects.get(pk=import_id)
    try:
        # Plain transcription — low effort keeps it quick.
        result = ai.ask_about_document(data, filename, INSTRUCTIONS, SCHEMA, effort="low")
        questions = _clean(result.get("questions", []))
        if not questions:
            raise ai.DocumentError("No questions were found in this document.")
        job.questions = questions
        job.status = QuestionImport.Status.DONE
    except (ai.DocumentError, ai.AINotConfigured) as exc:
        job.status = QuestionImport.Status.FAILED
        job.error = str(exc)
    except anthropic.APIError:
        logger.exception("Claude API error while importing %s", filename)
        job.status = QuestionImport.Status.FAILED
        job.error = "The AI service is unavailable right now. Please try again in a few minutes."
    except Exception:
        logger.exception("Unexpected error while importing %s", filename)
        job.status = QuestionImport.Status.FAILED
        job.error = "Something went wrong reading this document."
    job.save(update_fields=["status", "questions", "error"])
