"""
exam_sessions/serializers.py
─────────────────────────────
"""

from rest_framework import serializers
from .models import Session, Answer, Violation


class ViolationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Violation
        fields = ("id", "type", "occurred_at", "snapshot_path", "video_offset_seconds", "recording_part")
        read_only_fields = ("id", "occurred_at")


class AnswerSerializer(serializers.ModelSerializer):
    question_type = serializers.CharField(source="question.type", read_only=True)
    question_body = serializers.CharField(source="question.body", read_only=True)
    question_points = serializers.IntegerField(source="question.points", read_only=True)
    graded_by_name = serializers.CharField(source="graded_by.full_name", read_only=True, default=None)
    # Staff review only: the options and correct answer, to show what was picked
    question_payload = serializers.JSONField(source="question.payload", read_only=True)
    question_image_url = serializers.SerializerMethodField()

    def get_question_image_url(self, obj):
        return obj.question.image.url if obj.question.image else None

    class Meta:
        model = Answer
        fields = (
            "id", "question", "question_type", "question_body", "question_points",
            "question_payload", "question_image_url",
            "response", "is_correct", "auto_score", "manual_score", "reviewer_comment",
            "graded_by_name", "graded_at",
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
    reviewed_by_email = serializers.EmailField(source="reviewed_by.email", read_only=True, default=None)
    graded_by_name = serializers.CharField(read_only=True)

    class Meta:
        model = Session
        fields = (
            "id", "candidate_name", "candidate_email", "test_title",
            "status", "started_at", "submitted_at", "remaining_seconds",
            "recording_path", "violation_count", "answers", "violations",
            "decision", "decision_notes", "score_pct", "reviewed_by_email", "reviewed_at",
            "graded_by_name", "attendance_confirmed_at",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Seconds into the full recording for each violation (click → jump the video there)
        from .storage import part_start_seconds
        starts = part_start_seconds(instance.id)
        first_part = min(starts) if starts else 0
        for v in data["violations"]:
            offset = v.get("video_offset_seconds")
            part = v.get("recording_part") or first_part
            v["video_seconds"] = round(starts.get(part, 0.0) + offset, 1) if offset is not None else None
        return data


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