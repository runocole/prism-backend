"""
exam_sessions/models.py
────────────────────────
Three models:

  Session   — one test attempt per candidate. Created on "Begin Test".
  Answer    — one row per question per session (upserted on every draft save).
  Violation — one row per proctoring event. Append-only, never deleted.

Session tracks the server-authoritative timer so the client
cannot cheat by manipulating the browser countdown.
"""

from django.db import models
from django.utils import timezone


class Session(models.Model):

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        SUBMITTED = "submitted", "Submitted"
        TIMED_OUT = "timed_out", "Timed Out"

    invite = models.OneToOneField(
        "invites.Invite",
        on_delete=models.CASCADE,
        related_name="session",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IN_PROGRESS
    )

    # Server-authoritative timer — client syncs against these every 60s
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Object storage path to the assembled recording file
    recording_path = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "sessions"

    def __str__(self):
        return f"Session {self.id} — {self.invite.candidate_email}"

    @property
    def elapsed_seconds(self):
        """Seconds elapsed since the test started."""
        end = self.submitted_at or timezone.now()
        return int((end - self.started_at).total_seconds())

    @property
    def remaining_seconds(self):
        """
        Server-authoritative seconds remaining.
        Frontend syncs its countdown against this every 60 seconds.
        """
        duration = self.invite.test.duration_mins * 60
        return max(duration - self.elapsed_seconds, 0)

    @property
    def is_expired(self):
        return self.remaining_seconds == 0


class Answer(models.Model):
    """
    One row per question per session.

    response JSONField shape by question type:
      MCQ:          { "selected_index": 2 }
      Short answer: { "text": "The answer is..." }
    """

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="answers"
    )
    question = models.ForeignKey(
        "assessments.Question", on_delete=models.CASCADE
    )
    response = models.JSONField(default=dict)

    # Set automatically on submission for MCQ questions
    is_correct = models.BooleanField(null=True, blank=True)
    auto_score = models.FloatField(null=True, blank=True)

    # Set manually by a reviewer for short answer questions
    manual_score = models.FloatField(null=True, blank=True)
    reviewer_comment = models.TextField(blank=True)

    saved_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "answers"
        unique_together = ("session", "question")  # one answer per question

    def __str__(self):
        return f"Answer — Session {self.session_id} / Q{self.question_id}"


class Violation(models.Model):
    """
    A proctoring event detected in the browser.
    Violations are logged only — HR decides what action to take.
    """

    class Type(models.TextChoices):
        TAB_SWITCH = "tab_switch", "Tab Switch"
        COPY = "copy", "Copy Attempt"
        PASTE = "paste", "Paste Attempt"
        CUT = "cut", "Cut Attempt"
        FULLSCREEN_EXIT = "fullscreen_exit", "Exited Full Screen"
        WEBCAM_LOST = "webcam_lost", "Webcam Disconnected"
        RIGHT_CLICK = "right_click", "Right Click"

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="violations"
    )
    type = models.CharField(max_length=30, choices=Type.choices)
    occurred_at = models.DateTimeField(default=timezone.now)

    # Optional snapshot image taken at moment of violation
    snapshot_path = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "violations"
        ordering = ["occurred_at"]

    def __str__(self):
        return f"{self.type} @ {self.occurred_at.strftime('%H:%M:%S')} — Session {self.session_id}"