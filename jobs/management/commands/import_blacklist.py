"""
Import blacklist from an Excel (.xlsx) or CSV file.
Usage: python manage.py import_blacklist <path>
Same rules as the Upload button on the Blacklist page (see jobs/blacklist_import.py).
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from jobs.blacklist_import import BlacklistFileError, import_blacklist


class Command(BaseCommand):
    help = "Import blacklist entries from an Excel or CSV file"

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Path to .xlsx or .csv file")

    def handle(self, *args, **options):
        path = Path(options["file"])
        try:
            result = import_blacklist(path.read_bytes(), path.name)
        except (OSError, BlacklistFileError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Done — {result['created']} imported, {result['skipped']} skipped (duplicates)."
        ))
