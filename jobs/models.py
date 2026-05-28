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
    screening_questions = models.JSONField(default=list, blank=True)
    preferred_answers = models.JSONField(default=dict, blank=True)
    
    # Unique slug used in the public application URL
    slug = models.SlugField(max_length=255, unique=True, blank=True)

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

    # Personal info
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=50) 
    phone2 = models.CharField(max_length=50, blank=True)
    dob = models.DateField(null=True, blank=True)        

    # Documents — stored as file paths
    cv = models.FileField(upload_to="applications/cvs/")
    cover_letter = models.FileField(upload_to="applications/cover_letters/")

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

    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "applications"
        ordering = ["-submitted_at"]
        # One application per candidate per job
        unique_together = ("job", "email")

    def __str__(self):
        return f"{self.first_name} {self.last_name} → {self.job.title}"