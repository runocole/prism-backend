"""
users/models.py
────────────────
Custom User model — email login, no username field.

Roles:
  hr        — creates tests, sends invites, views results
  reviewer  — grades submissions (can overlap with hr in small teams)

HR accounts are created manually via seed.py or the Django admin.
Candidates do NOT have accounts — they authenticate via invite tokens.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):

    def create_user(self, email, password, role="hr", **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, role=role, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, role="hr", **extra)


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        HR = "hr", "HR / Test Creator"
        REVIEWER = "reviewer", "Reviewer"

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.HR)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # required for Django admin access
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # only email + password needed

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.email} ({self.role})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email