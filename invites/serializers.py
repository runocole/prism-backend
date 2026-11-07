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

    class Meta:
        model = Invite
        fields = (
            "id", "test", "test_title", "test_job_role", "job", "job_title", "candidate_name",
            "candidate_email", "token", "status", "expires_at",
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
    candidates = CandidateSerializer(many=True)
    expires_at = serializers.DateTimeField(required=False)
    job_id = serializers.IntegerField(required=False, allow_null=True)