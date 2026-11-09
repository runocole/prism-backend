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

from django.conf import settings
from django.db import models
from django.utils import timezone


class Session(models.Model):

    class Status(models.TextChoices):
        # Candidate is in the waiting room: camera on, reading the terms, no
        # questions and no timer until HR clicks "Start test" on Live.
        WAITING = "waiting", "Waiting for HR"
        IN_PROGRESS = "in_progress", "In Progress"
        SUBMITTED = "submitted", "Submitted"
        TIMED_OUT = "timed_out", "Timed Out"
        REVIEWED = "reviewed", "Reviewed"

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
    # Extra time HR gave (e.g. after a network drop), in seconds
    extra_seconds = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Object storage path to the assembled recording file
    recording_path = models.CharField(max_length=500, blank=True)

    # Live monitor — updated by the candidate's browser every ~10 seconds
    last_seen_at = models.DateTimeField(null=True, blank=True)
    answered_count = models.PositiveIntegerField(default=0)
    snapshot_path = models.CharField(max_length=500, blank=True)
    snapshot_at = models.DateTimeField(null=True, blank=True)
    # Face check running in the candidate's browser: "on", "loading" or "failed: <reason>"
    face_status = models.CharField(max_length=120, blank=True)

    # Result email sent to the candidate after the decision ("passed" / "failed")
    outcome_email_kind = models.CharField(max_length=10, blank=True)
    outcome_emailed_at = models.DateTimeField(null=True, blank=True)
    # Next-stage details sent to a passed candidate, and their confirmation
    interview_details = models.JSONField(default=dict, blank=True)
    attendance_confirmed_at = models.DateTimeField(null=True, blank=True)

    # Reviewer's final decision (set by SubmitResultView)
    class Decision(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        HOLD = "hold", "Hold"

    decision = models.CharField(max_length=10, choices=Decision.choices, blank=True)
    decision_notes = models.TextField(blank=True)
    score_pct = models.FloatField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

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
        duration = self.invite.test.duration_mins * 60 + self.extra_seconds
        if self.status == self.Status.WAITING:
            return duration  # the clock hasn't started yet
        return max(duration - self.elapsed_seconds, 0)

    @property
    def graded_by_name(self):
        """Who made the final decision, or else who last graded an answer."""
        if self.reviewed_by:
            return self.reviewed_by.full_name
        last = (
            self.answers.filter(graded_by__isnull=False)
            .select_related("graded_by")
            .order_by("-graded_at")
            .first()
        )
        return last.graded_by.full_name if last else None

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
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answers_graded",
    )
    graded_at = models.DateTimeField(null=True, blank=True)

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
        TAB_SWITCH = "tab_switch", "Switched tab"
        COPY = "copy", "Copy Attempt"
        PASTE = "paste", "Paste Attempt"
        CUT = "cut", "Cut Attempt"
        FULLSCREEN_EXIT = "fullscreen_exit", "Exited Full Screen"
        WEBCAM_LOST = "webcam_lost", "Webcam Disconnected"
        RIGHT_CLICK = "right_click", "Right Click"
        FACE_NOT_VISIBLE = "face_not_visible", "Face not visible"
        MULTIPLE_FACES = "multiple_faces", "More than one face"
        LOOKING_AWAY = "looking_away", "Looking away"
        SCREENSHOT_ATTEMPT = "screenshot_attempt", "Screenshot attempt"
        FOCUS_LOST = "focus_lost", "Switched to another window or app"

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="violations"
    )
    type = models.CharField(max_length=30, choices=Type.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    # Where in the recording this happened: seconds from the start of the
    # recording part (a part = one page load; usually just one per session)
    video_offset_seconds = models.FloatField(null=True, blank=True)
    recording_part = models.BigIntegerField(null=True, blank=True)

    # Optional snapshot image taken at moment of violation
    snapshot_path = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "violations"
        ordering = ["occurred_at"]

    def __str__(self):
        return f"{self.type} @ {self.occurred_at.strftime('%H:%M:%S')} — Session {self.session_id}"