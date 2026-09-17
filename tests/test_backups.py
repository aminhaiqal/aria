import hashlib
import json
import os
import tarfile
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from aria.artifacts.backups import (
    BackupError,
    BackupManifest,
    _upload_backup_file,
    _verify_filesystem_artifact_archive,
    command_environment,
    create_backup,
    run_restore_drill,
    sha256_file,
    validate_r2_prefix,
    verify_encrypted_bundle,
    write_artifact_inventory,
    write_filesystem_artifact_archive,
)
from aria.artifacts.models import RawArtifact


def manifest_for(bundle: Path, **overrides) -> BackupManifest:
    values = {
        "schema_version": 1,
        "created_at": "2026-09-09T12:00:00Z",
        "backup_name": "aria-20260909T120000Z-abcdef123456",
        "database_name": "aria",
        "bundle_filename": "aria-20260909T120000Z-abcdef123456.tar.age",
        "bundle_sha256": sha256_file(bundle),
        "bundle_bytes": bundle.stat().st_size,
        "database_dump_sha256": "a" * 64,
        "database_dump_bytes": 10,
        "artifact_inventory_sha256": "b" * 64,
        "artifact_inventory_bytes": 20,
        "artifact_count": 2,
        "artifact_bytes": 30,
        "filesystem_artifact_archive_sha256": "c" * 64,
        "filesystem_artifact_archive_bytes": 10,
        "filesystem_artifact_count": 0,
        "filesystem_artifact_bytes": 0,
    }
    values.update(overrides)
    return BackupManifest(**values)


class BackupEnvelopeTests(SimpleTestCase):
    def test_backup_subprocess_environment_excludes_application_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "OPENROUTER_API_KEY": "secret",
                "ARIA_OBJECT_STORAGE_SECRET_KEY": "secret",
            },
            clear=True,
        ):
            environment = command_environment()

        self.assertEqual(environment, {"PATH": "/usr/bin"})

    def test_manifest_is_strict_and_does_not_serialize_credentials(self) -> None:
        with TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.write_bytes(b"encrypted")
            payload = manifest_for(bundle).as_json()

        self.assertNotIn("password", payload.lower())
        self.assertNotIn("secret", payload.lower())
        self.assertEqual(json.loads(payload)["encryption"], "age-x25519")

    def test_manifest_rejects_path_like_bundle_names(self) -> None:
        with TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.write_bytes(b"encrypted")
            manifest = manifest_for(bundle, bundle_filename="../backup.tar.age")

            with self.assertRaisesMessage(BackupError, "does not match"):
                manifest.validate()

    def test_r2_prefix_validation_rejects_traversal(self) -> None:
        self.assertEqual(validate_r2_prefix("/backups/database/"), "backups/database")
        for value in ("", ".private", "backups/../private", "backups//database"):
            with self.subTest(value=value), self.assertRaises(BackupError):
                validate_r2_prefix(value)

    def test_encrypted_envelope_detects_tampering_before_decryption(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = "aria-20260909T120000Z-abcdef123456"
            bundle = root / f"{name}.tar.age"
            bundle.write_bytes(b"encrypted")
            manifest = manifest_for(bundle)
            manifest_path = root / f"{name}.manifest.json"
            manifest_path.write_text(manifest.as_json(), encoding="utf-8")

            observed, observed_bundle = verify_encrypted_bundle(manifest_path)
            self.assertEqual(observed, manifest)
            self.assertEqual(observed_bundle, bundle)

            bundle.write_bytes(b"tampered!")
            with self.assertRaisesMessage(BackupError, "digest"):
                verify_encrypted_bundle(manifest_path)

    def test_r2_upload_is_followed_by_size_and_digest_verification(self) -> None:
        class Client:
            def __init__(self):
                self.extra_args = None

            def upload_file(self, filename, bucket, key, ExtraArgs):
                self.extra_args = ExtraArgs

            def head_object(self, **kwargs):
                return {
                    "ContentLength": len(b"encrypted"),
                    "Metadata": {"sha256": hashlib.sha256(b"encrypted").hexdigest()},
                }

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "backup.age"
            path.write_bytes(b"encrypted")
            client = Client()
            _upload_backup_file(
                client,
                bucket="aria-artifacts",
                key="backups/database/backup.age",
                path=path,
                content_type="application/octet-stream",
            )

        self.assertEqual(
            client.extra_args["Metadata"]["sha256"],
            hashlib.sha256(b"encrypted").hexdigest(),
        )

    def test_restore_drill_requires_explicit_confirmation(self) -> None:
        with self.assertRaisesMessage(CommandError, "RESTORE-DRILL"):
            call_command(
                "restore_backup_drill",
                "/backups/example.manifest.json",
                identity_file="/offline/key.txt",
                confirm="no",
            )


class BackupDatabaseTests(TestCase):
    def test_artifact_inventory_is_ordered_complete_and_hashable(self) -> None:
        RawArtifact.objects.create(
            sha256="b" * 64,
            byte_size=20,
            detected_content_type="application/pdf",
            storage_backend=RawArtifact.StorageBackend.S3,
            storage_key="inst/jpdp/b",
        )
        RawArtifact.objects.create(
            sha256="a" * 64,
            byte_size=10,
            detected_content_type="text/html",
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key="inst/jpdp/a",
        )
        with TemporaryDirectory() as temporary:
            inventory = Path(temporary) / "inventory.jsonl"
            count, total_bytes = write_artifact_inventory(inventory)
            rows = [json.loads(line) for line in inventory.read_text().splitlines()]

        self.assertEqual(count, 2)
        self.assertEqual(total_bytes, 30)
        self.assertEqual([row["sha256"] for row in rows], ["a" * 64, "b" * 64])
        self.assertEqual(rows[0]["storage_key"], "inst/jpdp/a")

    def test_filesystem_artifact_archive_contains_only_verified_evidence(self) -> None:
        content = b"official evidence"
        digest = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_path = root / "inst/jpdp/evidence.pdf"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(content)
            RawArtifact.objects.create(
                sha256=digest,
                byte_size=len(content),
                detected_content_type="application/pdf",
                storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
                storage_key="inst/jpdp/evidence.pdf",
            )
            archive_path = root / "filesystem-artifacts.tar"
            inventory_path = root / "artifact-inventory.jsonl"
            with override_settings(OBJECT_STORAGE_ROOT=root):
                count, total_bytes = write_filesystem_artifact_archive(archive_path)
                write_artifact_inventory(inventory_path)
            with tarfile.open(archive_path, "r") as archive:
                member = archive.getmember("inst/jpdp/evidence.pdf")
                archived = archive.extractfile(member)
                self.assertIsNotNone(archived)
                self.assertEqual(archived.read(), content)
            bundle = root / "bundle"
            bundle.write_bytes(b"encrypted")
            manifest = manifest_for(
                bundle,
                filesystem_artifact_archive_sha256=sha256_file(archive_path),
                filesystem_artifact_archive_bytes=archive_path.stat().st_size,
                filesystem_artifact_count=1,
                filesystem_artifact_bytes=len(content),
            )
            _verify_filesystem_artifact_archive(archive_path, inventory_path, manifest)

            with tarfile.open(archive_path, "w") as archive:
                altered = root / "altered.pdf"
                altered.write_bytes(b"altered evidence")
                archive.add(altered, arcname="inst/jpdp/evidence.pdf", recursive=False)
            with self.assertRaises(BackupError):
                _verify_filesystem_artifact_archive(archive_path, inventory_path, manifest)

        self.assertEqual(count, 1)
        self.assertEqual(total_bytes, len(content))

    def test_filesystem_artifact_archive_fails_closed_on_tampered_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_path = root / "inst/jpdp/evidence.pdf"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(b"tampered")
            RawArtifact.objects.create(
                sha256=hashlib.sha256(b"original").hexdigest(),
                byte_size=len(b"original"),
                detected_content_type="application/pdf",
                storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
                storage_key="inst/jpdp/evidence.pdf",
            )
            with (
                override_settings(OBJECT_STORAGE_ROOT=root),
                self.assertRaisesMessage(BackupError, "integrity verification"),
            ):
                write_filesystem_artifact_archive(root / "filesystem-artifacts.tar")

    @patch("aria.artifacts.backups.database_cli_arguments")
    @patch("aria.artifacts.backups.run_checked")
    def test_create_backup_builds_an_encrypted_manifest_from_one_snapshot(
        self,
        run_checked_mock,
        cli_arguments_mock,
    ) -> None:
        cli_arguments_mock.return_value = ("postgres", "5432", "aria", "aria")
        RawArtifact.objects.create(
            sha256="c" * 64,
            byte_size=42,
            detected_content_type="application/pdf",
            storage_backend=RawArtifact.StorageBackend.S3,
            storage_key="inst/jpdp/c",
        )

        def fake_command(arguments, *, environment=None):
            if arguments[0] == "pg_dump":
                Path(arguments[arguments.index("--file") + 1]).write_bytes(b"PGDUMP")
            elif arguments[0] == "age":
                Path(arguments[arguments.index("--output") + 1]).write_bytes(b"AGE-BUNDLE")
            return ""

        run_checked_mock.side_effect = fake_command
        with TemporaryDirectory() as temporary:
            manifest_path, manifest, uploaded = create_backup(
                output_directory=Path(temporary),
                age_recipient="age1abcdefghijklmnopqrstuvwxyz0123456789",
                database_snapshot="00000003-0000001B-1",
                upload_to_r2=False,
            )

            self.assertTrue(manifest_path.is_file())
            self.assertTrue((manifest_path.parent / manifest.bundle_filename).is_file())
            self.assertEqual(manifest.artifact_count, 1)
            self.assertEqual(manifest.artifact_bytes, 42)
            self.assertEqual(manifest.database_dump_sha256, hashlib.sha256(b"PGDUMP").hexdigest())
            self.assertIsNone(uploaded)
            pg_dump_call = run_checked_mock.call_args_list[0].args[0]
            self.assertIn("00000003-0000001B-1", pg_dump_call)

    @patch("aria.artifacts.backups.database_cli_arguments")
    @patch("aria.artifacts.backups.run_checked")
    def test_restore_drill_always_removes_its_disposable_database(
        self,
        run_checked_mock,
        cli_arguments_mock,
    ) -> None:
        cli_arguments_mock.return_value = ("postgres", "5432", "aria", "aria")

        @contextmanager
        def fake_verified(*args, **kwargs):
            with TemporaryDirectory() as temporary:
                dump = Path(temporary) / "database.dump"
                inventory = Path(temporary) / "artifact-inventory.jsonl"
                dump.write_bytes(b"dump")
                inventory.write_bytes(b"")
                yield object(), dump, inventory

        run_checked_mock.side_effect = ["", "", "3\n", ""]
        with patch("aria.artifacts.backups.verified_backup_contents", fake_verified):
            database_name, table_count = run_restore_drill(
                Path("manifest.json"), identity_file=Path("identity.txt")
            )

        self.assertTrue(database_name.startswith("aria_restore_drill_"))
        self.assertEqual(table_count, 3)
        self.assertEqual(run_checked_mock.call_args_list[-1].args[0][0], "dropdb")
        self.assertIn("--force", run_checked_mock.call_args_list[-1].args[0])
