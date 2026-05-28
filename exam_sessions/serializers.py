"""
exam_sessions/serializers.py
─────────────────────────────
"""

from rest_framework import serializers
from .models import Session, Answer, Violation


class ViolationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Violation
        fields = ("id", "type", "occurred_at", "snapshot_path")
        read_only_fields = ("id", "occurred_at")


class AnswerSerializer(serializers.ModelSerializer):
    question_type = serializers.CharField(source="question.type", read_only=True)
    question_body = serializers.CharField(source="question.body", read_only=True)
    question_points = serializers.IntegerField(source="question.points", read_only=True)

    class Meta:
        model = Answer
        fields = (
            "id", "question", "question_type", "question_body", "question_points",
            "response", "is_correct", "auto_score", "manual_score", "reviewer_comment",
        )
        read_only_fields = ("is_correct", "auto_score")


class SessionSerializer(serializers.ModelSerializer):
    """Full session detail — used in the reviewer interface."""

    answers = AnswerSerializer(many=True, read_only=True)
    violations = ViolationSerializer(many=True, read_only=True)
    remaining_seconds = serializers.IntegerField(read_only=True)
    violation_count = serializers.IntegerField(source="violations.count", read_only=True)
    candidate_name = serializers.CharField(source="invite.candidate_name", read_only=True)
    candidate_email = serializers.CharField(source="invite.candidate_email", read_only=True)
    test_title = serializers.CharField(source="invite.test.title", read_only=True)

    class Meta:
        model = Session
        fields = (
            "id", "candidate_name", "candidate_email", "test_title",
            "status", "started_at", "submitted_at", "remaining_seconds",
            "recording_path", "violation_count", "answers", "violations",
        )


class SaveDraftSerializer(serializers.Serializer):
    """
    Auto-draft payload sent every 2 minutes from the client.
    Accepts a list of { question_id, response } objects.
    """

    class AnswerDraftSerializer(serializers.Serializer):
        question_id = serializers.IntegerField()
        response = serializers.JSONField()

    answers = AnswerDraftSerializer(many=True)


class ReviewScoreSerializer(serializers.Serializer):
    """Payload for a reviewer scoring a short-answer response."""

    manual_score = serializers.FloatField(min_value=0)
    reviewer_comment = serializers.CharField(required=False, allow_blank=True)