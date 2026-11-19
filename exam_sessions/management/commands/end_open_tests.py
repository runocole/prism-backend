"""
List tests that are still open (waiting room / in progress), and end them so
they go to Results with whatever the candidate saved.

    python manage.py end_open_tests                  # just list them
    python manage.py end_open_tests --email a@b.com  # end that candidate's open test
    python manage.py end_open_tests --all            # end every open test
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from exam_sessions.models import Session
from exam_sessions.services import end_session


class Command(BaseCommand):
    help = "List or end tests that are still open, sending them to Results."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="End the open test of this candidate")
        parser.add_argument("--all", action="store_true", help="End every open test")

    def handle(self, *args, **opts):
        open_tests = Session.objects.filter(
            status__in=[Session.Status.WAITING, Session.Status.IN_PROGRESS],
        ).select_related("invite__test")
        if opts["email"]:
            open_tests = open_tests.filter(invite__candidate_email__iexact=opts["email"].strip())
        if not open_tests:
            self.stdout.write("No open tests found.")
            return
        for s in open_tests:
            seen = timezone.localtime(s.last_seen_at).strftime("%d %b %H:%M") if s.last_seen_at else "never"
            self.stdout.write(
                f"#{s.id}  {s.invite.candidate_name} <{s.invite.candidate_email}>  {s.invite.test.title}  "
                f"status={s.status}  answers={s.answers.count()}  last seen {seen}"
            )
        if not (opts["email"] or opts["all"]):
            self.stdout.write("\nNothing changed. Add --email <address> or --all to end them.")
            return
        ended = sum(1 for s in open_tests if end_session(s))
        self.stdout.write(self.style.SUCCESS(f"Ended {ended} test(s). They're now in Results."))
