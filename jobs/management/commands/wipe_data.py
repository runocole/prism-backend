"""
Delete all recruitment data, keeping only staff logins and the blacklist.

  python manage.py wipe_data          # dry run: shows what would be deleted
  python manage.py wipe_data --yes    # actually deletes

Deleted: jobs, links, applications (+ CV / cover letter files), CV imports,
tests, questions, question imports, invites, exam sessions, answers,
violations, and recordings stored on this server (media/recordings).
Kept: user accounts and the blacklist.

Back up db.sqlite3 and media/ first — this cannot be undone.
"""

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from assessments.models import Question, QuestionImport, Test
from exam_sessions.models import Answer, Session, Violation
from invites.models import Invite
from jobs.models import Application, Blacklist, CvImport, JobLink, JobPost
from users.models import User

MEDIA_DIRS = ["applications", "recordings", "imports", "snapshots", "question_images"]


class Command(BaseCommand):
    help = "Delete all recruitment data except staff logins and the blacklist."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Really delete (otherwise dry run).")

    def handle(self, *args, yes, **options):
        counts = {
            "Jobs": JobPost.objects.count(),
            "Links": JobLink.objects.count(),
            "Applications": Application.objects.count(),
            "CV imports": CvImport.objects.count(),
            "Tests": Test.objects.count(),
            "Questions": Question.objects.count(),
            "Question imports": QuestionImport.objects.count(),
            "Invites": Invite.objects.count(),
            "Exam sessions": Session.objects.count(),
            "Answers": Answer.objects.count(),
            "Violations": Violation.objects.count(),
        }
        media_root = Path(settings.MEDIA_ROOT)
        roots = [media_root, Path(settings.PRIVATE_MEDIA_ROOT)]
        dirs = [root / d for root in roots for d in MEDIA_DIRS if (root / d).exists()]

        self.stdout.write("Will DELETE:")
        for name, n in counts.items():
            self.stdout.write(f"  {name:<18} {n}")
        for d in dirs:
            files = sum(1 for p in d.rglob("*") if p.is_file())
            self.stdout.write(f"  files in {d}  ({files})")
        self.stdout.write("Will KEEP:")
        self.stdout.write(f"  Staff logins       {User.objects.count()}")
        self.stdout.write(f"  Blacklist entries  {Blacklist.objects.count()}")
        if settings.STORAGE_ENDPOINT_URL:
            self.stdout.write(self.style.WARNING(
                "  Note: recordings in external object storage are NOT deleted by this command."
            ))

        if not yes:
            self.stdout.write(self.style.NOTICE("\nDry run only. Run again with --yes to delete."))
            return

        with transaction.atomic():
            # Deleting tests cascades to questions, invites, sessions, answers, violations;
            # deleting jobs cascades to links, applications and CV imports.
            Test.objects.all().delete()
            JobPost.objects.all().delete()
            QuestionImport.objects.all().delete()

        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Kept {User.objects.count()} logins and {Blacklist.objects.count()} blacklist entries."
        ))
