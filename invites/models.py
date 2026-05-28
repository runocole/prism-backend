"""
invites/models.py
──────────────────
An Invite ties a candidate to a specific Test via a one-time UUID token.

Status lifecycle:
  pending   → invite sent, candidate hasn't opened it yet
  active    → candidate opened the link and started the test
  submitted → candidate submitted the test
  expired   → time window passed without submission
"""

import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


def default_expiry():
    """Default expiry = now + INVITE_EXPIRY_HOURS from settings."""
    hours = getattr(settings, "INVITE_EXPIRY_HOURS", 72)
    return timezone.now() + timedelta(hours=hours)


class Invite(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUBMITTED = "submitted", "Submitted"
        EXPIRED = "expired", "Expired"

    # The test this invite is for
    test = models.ForeignKey(
        "assessments.Test",
        on_delete=models.CASCADE,
        related_name="invites",
    )

    # Candidate identity — no account, just name + email
    candidate_name = models.CharField(max_length=255)
    candidate_email = models.EmailField()

    # Unique token embedded in the invite URL: /test/<token>
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    expires_at = models.DateTimeField(default=default_expiry)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="invites_sent",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "invites"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.candidate_email} → {self.test.title} [{self.status}]"

    @property
    def is_valid(self):
        """True if the invite can still be used to start a test."""
        return (
            self.status == self.Status.PENDING
            and timezone.now() < self.expires_at
        )