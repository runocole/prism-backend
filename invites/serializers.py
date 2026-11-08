"""
invites/serializers.py
───────────────────────
"""

from django.conf import settings
from rest_framework import serializers
from .models import Invite


class InviteSerializer(serializers.ModelSerializer):
    test_title = serializers.CharField(source="test.title", read_only=True)
    test_job_role = serializers.CharField(source="test.job_role", read_only=True)
    invite_url = serializers.SerializerMethodField()
    job_title = serializers.CharField(source="job.title", read_only=True, default=None)
    # The exam session (if they started) — for the "Review" link on Live
    session_id = serializers.SerializerMethodField()

    def get_session_id(self, obj):
        session = getattr(obj, "session", None)
        return session.id if session else None

    class Meta:
        model = Invite
        fields = (
            "id", "test", "test_title", "test_job_role", "job", "job_title", "session_id", "candidate_name",
            "candidate_email", "token", "status", "expires_at", "opens_at",
            "invite_url", "created_at",
        )
        read_only_fields = (
            "token", "status", "expires_at", "created_at", "invite_url"
        )

    def get_invite_url(self, obj):
        return f"{settings.FRONTEND_URL}/c/{obj.token}"


class SingleInviteCreateSerializer(serializers.Serializer):
    """Validates a single invite creation request."""

    test_id = serializers.IntegerField()
    candidate_name = serializers.CharField(max_length=255)
    candidate_email = serializers.EmailField()
    expires_at = serializers.DateTimeField(required=False)
    opens_at = serializers.DateTimeField(required=False, allow_null=True)
    job_id = serializers.IntegerField(required=False, allow_null=True)


class BatchInviteCreateSerializer(serializers.Serializer):
    """
    Validates a batch invite request.
    Payload: { "test_id": 1, "candidates": [{"name": "...", "email": "..."}, ...] }
    """

    class CandidateSerializer(serializers.Serializer):
        name = serializers.CharField(max_length=255)
        email = serializers.EmailField()

    test_id = serializers.IntegerField()
    # Checked one by one in the view, so one bad line doesn't fail the batch
    candidates = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    expires_at = serializers.DateTimeField(required=False)
    opens_at = serializers.DateTimeField(required=False, allow_null=True)
    job_id = serializers.IntegerField(required=False, allow_null=True)