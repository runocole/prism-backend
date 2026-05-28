"""
jobs/serializers.py
────────────────────
"""

from rest_framework import serializers
from .models import JobPost, Application, Blacklist

class JobPostSerializer(serializers.ModelSerializer):
    application_count = serializers.IntegerField(
        source="applications.count", read_only=True
    )
    application_url = serializers.SerializerMethodField()

    class Meta:
        model = JobPost
        fields = (
            "id", "title", "department", "description", "requirements",
            "slug", "status", "screening_questions", "preferred_answers",
            "application_count", "application_url", "created_at",
        )
        read_only_fields = ("slug", "created_at", "application_url")

    def get_application_url(self, obj):
        return f"https://screening.oticgs.com/apply/{obj.slug}"

class ApplicationSerializer(serializers.ModelSerializer):
    job_title = serializers.CharField(source="job.title", read_only=True)
    department = serializers.CharField(source="job.department", read_only=True)

    class Meta:
        model = Application
        fields = (
            "id", "job", "job_title", "department",
            "first_name", "last_name", "email", "phone", "phone2", "dob",
            "cv", "cover_letter", "linkedin", "twitter", "github",
            "other_social", "screening_answers", "status", "ai_score",
            "ai_summary", "ai_screened_at", "submitted_at",
        )
        read_only_fields = (
            "status", "ai_score", "ai_summary", "ai_screened_at", "submitted_at",
        )

class PublicApplicationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Application
        fields = (
            "first_name", "last_name", "email", "phone", "phone2", "dob",
            "cv", "cover_letter",
            "linkedin", "twitter", "github", "other_social",
            "screening_answers",
        )

    def validate_cv(self, value):
        if not value.name.endswith(".pdf"):
            raise serializers.ValidationError("CV must be a PDF file.")
        return value

    def validate_cover_letter(self, value):
        if not value.name.endswith(".pdf"):
            raise serializers.ValidationError("Cover letter must be a PDF file.")
        return value


class BlacklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Blacklist
        fields = ("id", "name", "email", "phone", "role", "reason", "created_at")
        read_only_fields = ("created_at",)