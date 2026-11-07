"""
Delete one candidate from everywhere (e.g. to re-test, or a data deletion request).

  python manage.py delete_candidate --name "Naomi Cole"            # dry run: shows what matches
  python manage.py delete_candidate --email naomi@example.com      # dry run
  python manage.py delete_candidate --name "Naomi Cole" --yes      # delete

Matches applications and test invites by name (first + last) and/or email,
case-insensitive, and also anything else using an email found on a match.
Deletes: applications (+ CV / cover letter files), invites, exam sessions,
answers, violations, recordings and snapshots.
Blacklist entries are only removed with --include-blacklist.
Staff logins are never touched.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Value
from django.db.models.functions import Concat, Lower

from exam_sessions.models import Session
from exam_sessions.storage import delete_session_files
from invites.models import Invite
from jobs.models import Application, Blacklist


class Command(BaseCommand):
    help = "Delete a candidate's applications, tests and results everywhere."

    def add_arguments(self, parser):
        parser.add_argument("--name", help='Full name, e.g. "Naomi Cole"')
        parser.add_argument("--email", help="Email address")
        parser.add_argument("--include-blacklist", action="store_true", help="Also remove matching blacklist entries")
        parser.add_argument("--yes", action="store_true", help="Really delete (otherwise dry run)")

    def handle(self, *args, name, email, include_blacklist, yes, **options):
        if not name and not email:
            raise CommandError('Give --name "First Last" and/or --email address.')
        name = " ".join((name or "").split()).lower()
        email = (email or "").strip().lower()

        apps = Application.objects.annotate(
            full=Lower(Concat("first_name", Value(" "), "last_name"))
        )
        app_q, inv_q = Q(pk__in=[]), Q(pk__in=[])
        if name:
            app_q |= Q(full=name)
            inv_q |= Q(candidate_name__iexact=name)
        if email:
            app_q |= Q(email__iexact=email)
            inv_q |= Q(candidate_email__iexact=email)

        # Widen to every email found on a name match (same person, other records)
        emails = {e.lower() for e in apps.filter(app_q).values_list("email", flat=True)}
        emails |= {e.lower() for e in Invite.objects.filter(inv_q).values_list("candidate_email", flat=True)}
        if email:
            emails.add(email)
        for e in emails:
            app_q |= Q(email__iexact=e)
            inv_q |= Q(candidate_email__iexact=e)

        matched_apps = list(apps.filter(app_q).select_related("job"))
        matched_invites = list(Invite.objects.filter(inv_q).select_related("test"))
        session_ids = list(Session.objects.filter(invite__in=matched_invites).values_list("id", flat=True))
        bl_q = Q(pk__in=[])
        if name:
            bl_q |= Q(name__iexact=name)
        for e in emails:
            bl_q |= Q(email__iexact=e)
        matched_bl = list(Blacklist.objects.filter(bl_q)) if include_blacklist else []

        self.stdout.write(f"Emails: {', '.join(sorted(emails)) or '(none found)'}")
        self.stdout.write(f"Applications ({len(matched_apps)}):")
        for a in matched_apps:
            self.stdout.write(f"  - {a.first_name} {a.last_name} <{a.email}> -> {a.job.title} [{a.status}]")
        self.stdout.write(f"Test invites ({len(matched_invites)}), with {len(session_ids)} exam session(s):")
        for i in matched_invites:
            self.stdout.write(f"  - {i.candidate_name} <{i.candidate_email}> -> {i.test.job_role or i.test.title} [{i.status}]")
        if include_blacklist:
            self.stdout.write(f"Blacklist entries ({len(matched_bl)}): {', '.join(b.name for b in matched_bl) or '-'}")

        if not (matched_apps or matched_invites or matched_bl):
            self.stdout.write(self.style.WARNING("Nothing matched."))
            return
        if not yes:
            self.stdout.write(self.style.NOTICE("\nDry run only. Run again with --yes to delete."))
            return

        for a in matched_apps:
            for f in (a.cv, a.cover_letter):
                if f:
                    f.delete(save=False)
        with transaction.atomic():
            Application.objects.filter(pk__in=[a.pk for a in matched_apps]).delete()
            Invite.objects.filter(pk__in=[i.pk for i in matched_invites]).delete()  # cascades sessions/answers/violations
            if matched_bl:
                Blacklist.objects.filter(pk__in=[b.pk for b in matched_bl]).delete()
        for sid in session_ids:
            delete_session_files(sid)

        self.stdout.write(self.style.SUCCESS(
            f"\nDeleted {len(matched_apps)} application(s), {len(matched_invites)} invite(s), "
            f"{len(session_ids)} session(s)" + (f", {len(matched_bl)} blacklist entr(y/ies)" if matched_bl else "") + "."
        ))
