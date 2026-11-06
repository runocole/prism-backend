"""
jobs/serializers.py
────────────────────
"""

import re

from django.utils.text import slugify
from rest_framework import serializers

from .models import JobPost, JobLink, Application, Blacklist, CvImport
from .questions import QuestionError, validate_questions


class JobPostSerializer(serializers.ModelSerializer):
    application_count = serializers.IntegerField(
        source="applications.count", read_only=True
    )
    # URL of the job's first active "Job link", if it has been posted
    application_url = serializers.SerializerMethodField()
    link_count = serializers.IntegerField(source="links.count", read_only=True)

    class Meta:
        model = JobPost
        fields = (
            "id", "title", "department", "description", "requirements",
            "slug", "status", "cv_ai_mode",
            "application_count", "application_url", "link_count", "created_at",
        )
        read_only_fields = ("slug", "status", "created_at", "application_url")

    def get_application_url(self, obj):
        link = obj.links.filter(kind=JobLink.Kind.APPLY, is_active=True).order_by("created_at").first()
        return link.url if link else None


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SLUGS = {"admin", "api", "hr", "c", "apply", "login", "static", "media"}


class JobLinkSerializer(serializers.ModelSerializer):
    url = serializers.CharField(read_only=True)
    job_title = serializers.CharField(source="job.title", read_only=True)
    job_status = serializers.CharField(source="job.status", read_only=True)
    response_count = serializers.SerializerMethodField()
    # Plain text in; validate_slug turns "GIS Analyst 2026" into "gis-analyst-2026"
    slug = serializers.CharField(max_length=120)

    class Meta:
        model = JobLink
        fields = (
            "id", "job", "job_title", "job_status", "kind", "name", "slug", "intro",
            "questions", "is_active", "url", "response_count", "created_at",
        )
        read_only_fields = ("created_at",)

    def get_response_count(self, obj):
        return obj.applications.exclude(status=Application.Status.AWAITING_FORM).count()

    def validate_slug(self, value):
        value = slugify(value or "")[:120]
        if not value or not SLUG_RE.match(value):
            raise serializers.ValidationError("Use lowercase letters, numbers and dashes, e.g. gis-analyst-2026.")
        if value in RESERVED_SLUGS:
            raise serializers.ValidationError("That address is reserved. Pick another.")
        clash = JobLink.objects.filter(slug=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("That link address is already taken.")
        return value

    def validate_questions(self, value):
        try:
            return validate_questions(value)
        except QuestionError as exc:
            raise serializers.ValidationError(str(exc))

    def validate(self, attrs):
        if self.instance and "job" in attrs and attrs["job"] != self.instance.job:
            raise serializers.ValidationError({"job": "A link can't be moved to another job."})
        if self.instance and "kind" in attrs and attrs["kind"] != self.instance.kind:
            raise serializers.ValidationError({"kind": "A link's type can't be changed."})
        return attrs


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
            "form_sent_at", "form_completed_at",
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
