"""
assessments/models.py
──────────────────────
Two models:

  Test     — a published assessment (title, duration, pass mark)
  Question — belongs to a Test; type is either mcq or short_answer

Question payload (JSONField) differs by type:
  MCQ:          { "options": ["A","B","C","D"], "correct_index": 2 }
  Short answer: { "word_limit": 300 }
"""

from django.db import models
from django.conf import settings


class Test(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    job_role = models.CharField(max_length=255, blank=True, default="")
    duration_mins = models.PositiveIntegerField(
        help_text="Total time allowed in minutes"
    )
    pass_mark_pct = models.PositiveIntegerField(
        default=50,
        help_text="Minimum percentage score to pass"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="tests_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} [{self.status}]"

    @property
    def total_points(self):
        """Sum of all question point values in this test."""
        return sum(q.points for q in self.questions.all())


class Question(models.Model):

    class Type(models.TextChoices):
        MCQ = "mcq", "Multiple Choice"
        SHORT_ANSWER = "short_answer", "Short Answer / Essay"

    test = models.ForeignKey(
        Test, on_delete=models.CASCADE, related_name="questions"
    )
    type = models.CharField(max_length=20, choices=Type.choices)

    # Question text — stored as plain text or HTML string from the rich-text editor
    body = models.TextField()

    # Flexible payload — shape depends on question type (see docstring above)
    payload = models.JSONField(default=dict)

    points = models.PositiveIntegerField(default=1)
    order_index = models.PositiveIntegerField(
        default=0, help_text="Display order within the test"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "questions"
        ordering = ["order_index"]

    def __str__(self):
        return f"Q{self.order_index + 1} [{self.type}] — {self.test.title}"