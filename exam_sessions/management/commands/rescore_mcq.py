"""
Re-mark every multiple-choice answer against the CURRENT answer key.
Use after answer keys were edited before automatic re-marking existed:

    python manage.py rescore_mcq
"""
from django.core.management.base import BaseCommand

from assessments.models import Question
from exam_sessions.services import rescore_question


class Command(BaseCommand):
    help = "Re-mark all MCQ answers against the current answer keys."

    def handle(self, *args, **opts):
        total = 0
        for question in Question.objects.filter(type=Question.Type.MCQ, answer__isnull=False).distinct():
            total += rescore_question(question)
        self.stdout.write(self.style.SUCCESS(f"Re-marked {total} answers."))
