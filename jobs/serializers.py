"""
jobs/serializers.py
────────────────────
"""

import re

from django.conf import settings
from django.utils.text import slugify
from rest_framework import serializers

from .models import JobPost, JobLink, Application, Blacklist, CvImport
from .questions import QuestionError, strip_preferences, validate_questions


class JobPostSerializer(serializers.ModelSerializer):
    application_count = serializers.IntegerField(source="applications.count", read_only=True)
    # Public URL once the job is posted with a link
    application_url = serializers.SerializerMethodField()
    apply_link_name = serializers.CharField(source="apply_link.name", read_only=True, default=None)

    class Meta:
        model = JobPost
        fields = (
            "id", "title", "department", "description", "requirements",
            "slug", "status", "cv_ai_mode", "apply_link", "apply_link_name", "preferences",
            "application_count", "application_url", "created_at",
        )
        read_only_fields = ("slug", "status", "apply_link", "preferences", "created_at", "application_url")

    def validate_department(self, value):
        value = " ".join((value or "").split())
        if not value:
            raise serializers.ValidationError("Department is required.")
        return value[:255]

    def get_application_url(self, obj):
        return f"{settings.FRONTEND_URL}/apply/{obj.slug}" if obj.apply_link_id else None


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SLUGS = {"admin", "api", "hr", "c", "apply", "login", "static", "media"}


def clean_job_slug(value: str, job=None) -> str:
    """Validate the custom part of a job's URL. Raises serializers.ValidationError."""
    value = slugify(value or "")[:120]
    if not value or not SLUG_RE.match(value):
        raise serializers.ValidationError("Use letters, numbers and dashes, e.g. gis-analyst-2026.")
    if value in RESERVED_SLUGS:
        raise serializers.ValidationError("That address is reserved. Pick another.")
    clash = JobPost.objects.filter(slug=value)
    if job:
        clash = clash.exclude(pk=job.pk)
    if clash.exists():
        raise serializers.ValidationError("That link address is already used by another job.")
    return value


class JobLinkSerializer(serializers.ModelSerializer):
    """A reusable, named set of questions."""
    response_count = serializers.SerializerMethodField()
    used_by = serializers.SerializerMethodField()

    class Meta:
        model = JobLink
        fields = ("id", "name", "intro", "questions", "is_active", "response_count", "used_by", "created_at")
        read_only_fields = ("created_at",)

    def get_response_count(self, obj):
        return obj.applications.exclude(status=Application.Status.AWAITING_FORM).count()

    def get_used_by(self, obj):
        """Jobs posted with this link."""
        return list(JobPost.objects.filter(apply_link=obj).values("id", "title", "status"))

    def validate_name(self, value):
        value = " ".join((value or "").split())
        if not value:
            raise serializers.ValidationError("Give the link a name, e.g. Entry level.")
        return value[:255]

    def validate_questions(self, value):
        try:
            return strip_preferences(validate_questions(value))
        except QuestionError as exc:
            raise serializers.ValidationError(str(exc))


class ApplicationSerializer(serializers.ModelSerializer):
    job_title = serializers.CharField(source="job.title", read_only=True)
    department = serializers.CharField(source="job.department", read_only=True)
    link_name = serializers.CharField(source="link.name", read_only=True, default=None)

    class Meta:
        model = Application
        fields = (
            "id", "job", "job_title", "department", "source", "link", "link_name",
            "first_name", "last_name", "email", "phone", "phone2", "dob",
            "cv", "cover_letter", "linkedin", "twitter", "github",
            "other_social", "screening_answers", "status", "ai_score", "ai_details",
            "ai_summary", "ai_screened_at", "submitted_at",
            "form_sent_at", "form_completed_at", "rejection_emailed_at",
        )
        read_only_fields = fields


class PublicApplicationSerializer(serializers.ModelSerializer):
    """Applying through a Job link: personal details + documents."""

    class Meta:
        model = Application
        fields = (
            "first_name", "last_name", "email", "phone", "phone2", "dob",
            "cv", "cover_letter",
            "linkedin", "twitter", "github", "other_social",
        )
        extra_kwargs = {
            "phone": {"required": True, "allow_blank": False},
            "cover_letter": {"required": True, "allow_null": False},
        }

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


class CvImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = CvImport
        fields = (
            "id", "job", "link", "status", "total", "processed", "created_count",
            "duplicate_count", "emailed_count", "problems", "created_at",
        )
        read_only_fields = fields


class BlacklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Blacklist
        fields = ("id", "name", "email", "phone", "role", "reason", "created_at")
        read_only_fields = ("created_at",)
