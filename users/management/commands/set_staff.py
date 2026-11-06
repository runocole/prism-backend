"""
Create or update a staff login.

  python manage.py set_staff otichr@oticgs.com --name "OTIC HR" --role hr
  python manage.py set_staff oticadmin@oticgs.com --name "OTIC Admin" --role reviewer

Asks for a password when the account is new (or with --reset-password).
All staff accounts see the same jobs, tests and results; the role is only a
label for their main job (hr = recruits, reviewer = grades).
"""

from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from users.models import User


class Command(BaseCommand):
    help = "Create or update a staff login (email, display name, role, password)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", help='Display name, e.g. "OTIC Admin" (shown as "Graded by")')
        parser.add_argument(
            "--role",
            choices=[r.value for r in User.Role],
            help="hr or reviewer. New accounts default to hr; existing accounts keep their role if omitted.",
        )
        parser.add_argument("--reset-password", action="store_true")

    def handle(self, *args, email, name, role, reset_password, **options):
        email = User.objects.normalize_email(email.strip())
        user = User.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            user = User(email=email)

        if name:
            first, _, last = name.strip().partition(" ")
            user.first_name, user.last_name = first, last
        if role:
            user.role = role
        elif created:
            user.role = User.Role.HR
        user.is_active = True
        user.is_staff = True  # Django admin access

        if created or reset_password:
            password = getpass(f"Password for {email}: ")
            if len(password) < 8:
                raise CommandError("Use at least 8 characters.")
            if getpass("Repeat password: ") != password:
                raise CommandError("Passwords don't match.")
            user.set_password(password)

        user.save()
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} {user.email} — shown as \"{user.full_name}\", role {user.role}"
        ))
