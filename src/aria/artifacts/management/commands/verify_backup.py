from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aria.artifacts.backups import (
    BackupError,
    verified_backup_contents,
    verify_encrypted_bundle,
)


class Command(BaseCommand):
    help = "Verify an encrypted backup manifest, optionally decrypting and inspecting its dump."

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path)
        parser.add_argument(
            "--identity-file",
            type=Path,
            help="Age identity file for full decryption and pg_restore validation.",
        )

    def handle(self, *args, **options):
        try:
            if options["identity_file"]:
                with verified_backup_contents(
                    options["manifest"], identity_file=options["identity_file"]
                ) as (manifest, _database_dump, _inventory):
                    pass
                mode = "full decrypt-and-restore-format"
            else:
                manifest, _bundle = verify_encrypted_bundle(options["manifest"])
                mode = "encrypted envelope"
        except BackupError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Verified {manifest.backup_name} ({mode}); "
                f"{manifest.artifact_count} artifact records inventoried."
            )
        )
