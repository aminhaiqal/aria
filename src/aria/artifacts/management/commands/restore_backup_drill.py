from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aria.artifacts.backups import BackupError, run_restore_drill


class Command(BaseCommand):
    help = "Restore a backup into a randomly named disposable database, verify it, then remove it."

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path)
        parser.add_argument("--identity-file", type=Path, required=True)
        parser.add_argument(
            "--confirm",
            required=True,
            help="Required safety acknowledgement; must be RESTORE-DRILL.",
        )

    def handle(self, *args, **options):
        if options["confirm"] != "RESTORE-DRILL":
            raise CommandError("Restore drill requires --confirm RESTORE-DRILL.")
        try:
            database_name, table_count = run_restore_drill(
                options["manifest"], identity_file=options["identity_file"]
            )
        except BackupError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Restore drill passed with {table_count} public tables; "
                f"disposable database {database_name} was removed."
            )
        )
