"""
assessments/serializers.py
───────────────────────────
QuestionSerializer validates the payload shape based on question type:
  mcq          → requires options (list, 2–6 items) + correct_index (int)
  short_answer → requires word_limit (int)
"""

from rest_framework import serializers
from .models import Test, Question


class QuestionSerializer(serializers.ModelSerializer):

    class Meta:
        model = Question
        fields = ("id", "type", "body", "payload", "points", "order_index")

    def validate(self, data):
        # Use existing type if doing a partial update
        q_type = data.get("type") or getattr(self.instance, "type", None)
        payload = data.get("payload", {})

        if q_type == Question.Type.MCQ:
            options = payload.get("options", [])
            if not (2 <= len(options) <= 6):
                raise serializers.ValidationError(
                    {"payload": "MCQ requires between 2 and 6 options."}
                )
            if "correct_index" not in payload:
                raise serializers.ValidationError(
                    {"payload": "MCQ requires a correct_index."}
                )

        if q_type == Question.Type.SHORT_ANSWER:
            if "word_limit" not in payload:
                raise serializers.ValidationError(
                    {"payload": "Short answer requires a word_limit."}
                )

        return data


class TestSerializer(serializers.ModelSerializer):
    """Full test detail with nested questions. Used for create and retrieve."""

    questions = QuestionSerializer(many=True, read_only=True)
    total_points = serializers.IntegerField(read_only=True)
    created_by_email = serializers.CharField(
        source="created_by.email", read_only=True
    )

    class Meta:
        model = Test
        fields = (
            "id", "title", "description", "job_role", "duration_mins", "pass_mark_pct",
            "status", "total_points", "created_by_email",
            "created_at", "updated_at", "questions",
        )
        read_only_fields = (
            "status", "created_by_email", "created_at", "updated_at"
        )


class TestListSerializer(serializers.ModelSerializer):
    question_count = serializers.IntegerField(source="questions.count", read_only=True)

    class Meta:
        model = Test
        fields = (
            "id", "title", "description", "job_role", "duration_mins",
            "pass_mark_pct", "status", "question_count", "created_at",
        )