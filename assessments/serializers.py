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
    # e.g. "/media/question_images/ab12….png" (served by the backend host)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = ("id", "type", "section", "body", "payload", "points", "order_index", "image_url")

    def get_image_url(self, obj):
        return obj.image.url if obj.image else None

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

        if q_type == Question.Type.YES_NO:
            if payload.get("follow_up_on", "") not in ("", "yes", "no"):
                raise serializers.ValidationError({"payload": "The follow-up must open on Yes, No, or not at all."})

        if q_type == Question.Type.LIST:
            try:
                count = int(payload.get("count", 0))
            except (TypeError, ValueError):
                count = 0
            if not 1 <= count <= 20:
                raise serializers.ValidationError({"payload": "A list question needs between 1 and 20 answer rows."})

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
            "status", "total_points", "created_by_email", "instructions", "sections",
            "created_at", "updated_at", "questions",
        )

    def validate_sections(self, value):
        if not isinstance(value, list) or len(value) > 10:
            raise serializers.ValidationError("Up to 10 sections.")
        clean, keys = [], set()
        for i, s in enumerate(value):
            if not isinstance(s, dict):
                raise serializers.ValidationError("Each section needs a title.")
            key = str(s.get("key") or chr(65 + i))[:20]
            if key in keys:
                raise serializers.ValidationError("Two sections have the same key.")
            keys.add(key)
            clean.append({
                "key": key,
                "title": str(s.get("title") or f"Section {chr(65 + i)}")[:120],
                "description": str(s.get("description") or "")[:2000],
                "quantitative": bool(s.get("quantitative")),
            })
        return clean
        read_only_fields = (
            "status", "created_by_email", "created_at", "updated_at"
        )


class TestListSerializer(serializers.ModelSerializer):
    question_count = serializers.IntegerField(source="questions.count", read_only=True)

    class Meta:
        model = Test
        fields = (
            "id", "title", "description", "job_role", "duration_mins",
            "pass_mark_pct", "status", "question_count", "created_at", "updated_at",
        )