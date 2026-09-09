from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from aria.artifacts.backups import BackupError, create_backup


class Command(BaseCommand):
    help = "Create an age-encrypted PostgreSQL backup and artifact inventory."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-directory",
            default=str(settings.BACKUP_ROOT),
            help="Container path for the encrypted bundle and manifest.",
        )
        parser.add_argument(
            "--upload-to-r2",
            action="store_true",
            default=settings.BACKUP_UPLOAD_TO_R2,
            help="Upload the verified-size bundle and manifest to configured R2 storage.",
        )

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    cursor.execute("SELECT pg_export_snapshot()")
                    database_snapshot = cursor.fetchone()[0]
                manifest_path, manifest, uploaded = create_backup(
                    output_directory=Path(options["output_directory"]),
                    age_recipient=settings.BACKUP_AGE_RECIPIENT,
                    database_snapshot=database_snapshot,
                    upload_to_r2=options["upload_to_r2"],
                )
        except BackupError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {manifest.backup_name}: {manifest.bundle_bytes} encrypted bytes, "
                f"{manifest.artifact_count} artifact records."
            )
        )
        self.stdout.write(f"Manifest: {manifest_path}")
        if uploaded:
            self.stdout.write(f"R2 bundle: {uploaded[0]}")
            self.stdout.write(f"R2 manifest: {uploaded[1]}")
