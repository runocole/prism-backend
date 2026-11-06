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
            "other_social", "screening_answers", "status", "ai_score", "ai_details",
            "ai_summary", "ai_screened_at", "submitted_at",
        )
        read_only_fields = (
            "status", "ai_score", "ai_details", "ai_summary", "ai_screened_at", "submitted_at",
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

    ALLOWED_EXTENSIONS = (".pdf", ".docx")
    MAX_BYTES = 10 * 1024 * 1024

    def _validate_document(self, value, label):
        if not value.name.lower().endswith(self.ALLOWED_EXTENSIONS):
            raise serializers.ValidationError(f"{label} must be a PDF or Word (.docx) file.")
        if value.size > self.MAX_BYTES:
            raise serializers.ValidationError(f"{label} must be 10 MB or smaller.")
        return value

    def validate_cv(self, value):
        return self._validate_document(value, "CV")

    def validate_cover_letter(self, value):
        return self._validate_document(value, "Cover letter")


class BlacklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Blacklist
        fields = ("id", "name", "email", "phone", "role", "reason", "created_at")
        read_only_fields = ("created_at",)