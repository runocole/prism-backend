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

def finish_out_of_time_sessions():
    """Submit every running test whose time is up (candidate closed the page or lost connection)."""
    running = Session.objects.filter(status=Session.Status.IN_PROGRESS).select_related("invite__test")
    for session in running:
        if session.is_expired:
            session.status = Session.Status.TIMED_OUT
            session.save(update_fields=["status"])
            submit_session(session)


def session_score_pct(session: Session) -> float:
    total_points = session.invite.test.total_points
    earned = sum((a.auto_score or 0) + (a.manual_score or 0) for a in session.answers.all())
    return round((earned / total_points * 100), 1) if total_points else 0


def rescore_question(question) -> int:
    """
    The answer key of an MCQ was changed: re-mark everyone who already
    answered it, and update the saved score of results already decided.
    Returns how many answers were re-marked.
    """
    if question.type != Question.Type.MCQ:
        return 0
    answers = list(Answer.objects.filter(question=question).select_related("question", "session__invite__test"))
    for answer in answers:
        auto_score_mcq(answer)
    for session in {a.session for a in answers if a.session.score_pct is not None}:
        Session.objects.filter(pk=session.pk).update(score_pct=session_score_pct(session))
    return len(answers)
