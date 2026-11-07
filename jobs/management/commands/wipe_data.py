"""
Delete all recruitment data, keeping staff logins and the blacklist.

  python manage.py wipe_data                         # dry run: shows what would be deleted
  python manage.py wipe_data --yes                   # delete everything below
  python manage.py wipe_data --keep-tests --keep-links --yes
                                                     # ...but keep your tests and/or question sets

Deleted: jobs, applications (+ CV / cover letter files), CV imports, links
(question sets), tests (+ questions, question images), question imports,
invites, exam sessions, answers, violations, recordings and webcam snapshots.
With --keep-tests, tests and their questions stay (their invites and results
are still deleted). With --keep-links, the question sets stay.
Kept always: user accounts (logins/passwords) and the blacklist.

Back up db.sqlite3, media/ and private_media/ first — this cannot be undone.
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

CANDIDATE_DIRS = ["applications", "recordings", "imports", "snapshots"]


class Command(BaseCommand):
    help = "Delete all recruitment data except staff logins and the blacklist."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Really delete (otherwise dry run).")
        parser.add_argument("--keep-tests", action="store_true", help="Keep tests and their questions.")
        parser.add_argument("--keep-links", action="store_true", help="Keep links (question sets).")

    def handle(self, *args, yes, keep_tests, keep_links, **options):
        counts = {
            "Jobs": JobPost.objects.count(),
            "Applications": Application.objects.count(),
            "CV imports": CvImport.objects.count(),
            "Invites": Invite.objects.count(),
            "Exam sessions": Session.objects.count(),
            "Answers": Answer.objects.count(),
            "Violations": Violation.objects.count(),
            "Question imports": QuestionImport.objects.count(),
        }
        if not keep_links:
            counts["Links"] = JobLink.objects.count()
        if not keep_tests:
            counts["Tests"] = Test.objects.count()
            counts["Questions"] = Question.objects.count()

        roots = [Path(settings.MEDIA_ROOT), Path(settings.PRIVATE_MEDIA_ROOT)]
        folders = CANDIDATE_DIRS + ([] if keep_tests else ["question_images"])
        dirs = [root / d for root in roots for d in folders if (root / d).exists()]

        self.stdout.write("Will DELETE:")
        for name, n in counts.items():
            self.stdout.write(f"  {name:<18} {n}")
        for d in dirs:
            files = sum(1 for p in d.rglob("*") if p.is_file())
            self.stdout.write(f"  files in {d}  ({files})")
        self.stdout.write("Will KEEP:")
        self.stdout.write(f"  Staff logins       {User.objects.count()}")
        self.stdout.write(f"  Blacklist entries  {Blacklist.objects.count()}")
        if keep_tests:
            self.stdout.write(f"  Tests              {Test.objects.count()} (with {Question.objects.count()} questions)")
        if keep_links:
            self.stdout.write(f"  Links              {JobLink.objects.count()}")
        if settings.STORAGE_ENDPOINT_URL:
            self.stdout.write(self.style.WARNING(
                "  Note: files in external object storage are NOT deleted by this command."
            ))

        if not yes:
            self.stdout.write(self.style.NOTICE("\nDry run only. Run again with --yes to delete."))
            return

        with transaction.atomic():
            # Invites cascade to sessions, answers and violations; jobs cascade to
            # applications and CV imports; tests cascade to questions.
            Invite.objects.all().delete()
            JobPost.objects.all().delete()
            QuestionImport.objects.all().delete()
            if not keep_links:
                JobLink.objects.all().delete()
            if not keep_tests:
                Test.objects.all().delete()

        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Kept {User.objects.count()} logins and {Blacklist.objects.count()} blacklist entries."
        ))
