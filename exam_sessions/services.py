"""
exam_sessions/services.py
──────────────────────────
Business logic kept out of views to keep views thin and readable.

  auto_score_mcq(answer)  — scores one MCQ answer, saves to DB
  score_session(session)  — scores all MCQ answers in a session
  submit_session(session) — finalises submission and triggers scoring
"""

from django.utils import timezone
from .models import Answer, Session
from assessments.models import Question


def auto_score_mcq(answer: Answer) -> float:
    """
    Compare selected_index against correct_index from the question payload.
    Returns points earned (full points or 0 — no partial credit).
    """
    correct_index = answer.question.payload.get("correct_index")
    selected_index = answer.response.get("selected_index")

    is_correct = (
        correct_index is not None
        and selected_index is not None
        and int(selected_index) == int(correct_index)
    )
    score = float(answer.question.points) if is_correct else 0.0

    answer.is_correct = is_correct
    answer.auto_score = score
    answer.save(update_fields=["is_correct", "auto_score"])
    return score


def score_session(session: Session):
    """
    Auto-score all MCQ answers in a session.
    Short answer answers are skipped — they need a human reviewer.
    """
    for answer in session.answers.select_related("question").all():
        if answer.question.type == Question.Type.MCQ:
            auto_score_mcq(answer)


def submit_session(session: Session):
    """
    Finalise a session:
      1. Set status to submitted
      2. Record submission timestamp
      3. Mark the invite as submitted (token can no longer be used)
      4. Auto-score all MCQ answers

    Safe to call multiple times — idempotent.
    """
    if session.status == Session.Status.SUBMITTED:
        return  # already done

    session.status = Session.Status.SUBMITTED
    session.submitted_at = timezone.now()
    session.save(update_fields=["status", "submitted_at"])

    # Invalidate the invite token
    session.invite.status = "submitted"
    session.invite.save(update_fields=["status"])

    # Run MCQ auto-scoring
    score_session(session)