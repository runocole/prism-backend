"""
jobs/models.py
───────────────
Two models:

  JobPost     — a job opening created by HR. Has a unique slug used in the
                public application URL: /apply/<slug>

  Application — a candidate's application to a JobPost. Stores their
                personal info, CV and cover letter PDFs, social handles,
                and AI screening result.
"""

import uuid
from django.db import models
from django.conf import settings
from django.utils.text import slugify


class JobPost(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=255)
    department = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    # Old fixed-question fields — replaced by JobLink.questions, kept so
    # older data stays readable. Not used by new code.
    screening_questions = models.JSONField(default=list, blank=True)
    preferred_answers = models.JSONField(default=dict, blank=True)

    class CvAiMode(models.TextChoices):
        OFF = "off", "Off"
        RANK = "rank", "Rank only"
        SCREEN = "screen", "Rank and screen out"

    # Whether the AI reads CVs during screening (see jobs/screening.py)
    cv_ai_mode = models.CharField(
        max_length=10, choices=CvAiMode.choices, default=CvAiMode.OFF
    )

    # Public URL: FRONTEND_URL/apply/<slug> — HR chooses it when posting the job
    slug = models.SlugField(max_length=255, unique=True, blank=True)

    # Questions applicants answer on the job's public page (set by "Post job")
    apply_link = models.ForeignKey(
        "JobLink", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    # HR's preferred answers for screening, per question id:
    #   {"<question id>": {"preferred": ["Yes"], "min": null, "max": 300000}}
    preferences = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="job_posts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "job_posts"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Auto-generate slug from title + short uuid if not set
        if not self.slug:
            base = slugify(self.title)
            short = str(uuid.uuid4())[:8]
            self.slug = f"{base}-{short}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} [{self.status}]"

class JobLink(models.Model):
    """
    A "Link": a reusable, named set of screening questions (e.g. "Entry level",
    "Experienced"). Not tied to a job — a job uses one when it's posted
    (JobPost.apply_link), and imported candidates are sent one (Application.link).

    questions is a list of:
      {
        "id": "q_ab12cd",                       # stable key used in answers
        "text": "Are you willing to relocate?",
        "type": "choice" | "yes_no" | "number" | "text",
        "options": ["...", ...],                # choice only
        "required": true,
        "show_if": {"question": "<id>", "op": "is" | "is_not", "value": "..."} | null
      }
    Preferred answers are NOT stored here — they're per job (JobPost.preferences).
    """

    name = models.CharField(max_length=255, help_text="e.g. 'Entry level' or 'Experienced'")
    intro = models.TextField(blank=True, help_text="Message shown at the top of the form")
    questions = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="job_links",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_links"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class CvImport(models.Model):
    """One "Import CVs" batch. Processed in the background; HR polls progress."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(JobPost, on_delete=models.CASCADE, related_name="cv_imports")
    link = models.ForeignKey(JobLink, on_delete=models.SET_NULL, null=True, related_name="cv_imports")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    emailed_count = models.PositiveIntegerField(default=0)
    # [{"file": "cv.pdf", "reason": "No email address found"}]
    problems = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cv_imports"
        ordering = ["-created_at"]


class Blacklist(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    role = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "blacklist"

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Application(models.Model):

    class Status(models.TextChoices):
        AWAITING_FORM = "awaiting_form", "Awaiting form"  # imported, hasn't answered the screening link yet
        PENDING = "pending", "Pending"           # just submitted, not yet screened
        SCREENING = "screening", "Screening"     # AI is processing
        SCREENED_IN = "screened_in", "Screened In"   # passed AI screen
        SCREENED_OUT = "screened_out", "Screened Out" # failed AI screen
        INVITED = "invited", "Invited"           # sent test invite
        BLACKLISTED = "blacklisted", "Blacklisted"

    job = models.ForeignKey(
        JobPost,
        on_delete=models.CASCADE,
        related_name="applications",
    )

    class Source(models.TextChoices):
        APPLY = "apply", "Applied via link"
        IMPORT = "import", "Imported CV"

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.APPLY)
    # The link whose questions this person answers / answered
    link = models.ForeignKey(
        JobLink, on_delete=models.SET_NULL, null=True, blank=True, related_name="applications",
    )
    # Imported candidates: personal token for their screening link
    form_token = models.UUIDField(null=True, blank=True, unique=True)
    form_sent_at = models.DateTimeField(null=True, blank=True)
    form_completed_at = models.DateTimeField(null=True, blank=True)

    # Personal info
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True)
    phone2 = models.CharField(max_length=50, blank=True)
    dob = models.DateField(null=True, blank=True)        

    # Documents — stored as file paths
    cv = models.FileField(upload_to="applications/cvs/")
    cover_letter = models.FileField(upload_to="applications/cover_letters/", blank=True)

    # Social media handles
    linkedin = models.URLField(blank=True)
    twitter = models.URLField(blank=True)
    github = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    tiktok = models.URLField(blank=True)
    other_social = models.CharField(max_length=500, blank=True)
    screening_answers = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # AI screening result
    ai_score = models.FloatField(null=True, blank=True)
    ai_summary = models.TextField(blank=True)
    ai_screened_at = models.DateTimeField(null=True, blank=True)
    # Requirement checklist + years of relevant experience from the CV check
    ai_details = models.JSONField(default=dict, blank=True)
    # When background screening picked this up — used to retry stuck jobs
    screening_started_at = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "applications"
        ordering = ["-submitted_at"]
        # One application per candidate per job
        unique_together = ("job", "email")

    def __str__(self):
        return f"{self.first_name} {self.last_name} → {self.job.title}"