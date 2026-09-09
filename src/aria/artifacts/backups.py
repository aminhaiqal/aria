import hashlib
import json
import os
import re
import subprocess
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from botocore.exceptions import ClientError
from django.conf import settings

from aria.artifacts.models import RawArtifact

BACKUP_SCHEMA_VERSION = 1
BACKUP_NAME_PATTERN = re.compile(r"^aria-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
BACKUP_MEMBER_NAMES = frozenset(
    {"database.dump", "artifact-inventory.jsonl", "filesystem-artifacts.tar"}
)


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupManifest:
    schema_version: int
    created_at: str
    backup_name: str
    database_name: str
    bundle_filename: str
    bundle_sha256: str
    bundle_bytes: int
    database_dump_sha256: str
    database_dump_bytes: int
    artifact_inventory_sha256: str
    artifact_inventory_bytes: int
    artifact_count: int
    artifact_bytes: int
    filesystem_artifact_archive_sha256: str
    filesystem_artifact_archive_bytes: int
    filesystem_artifact_count: int
    filesystem_artifact_bytes: int
    encryption: str = "age-x25519"
    database_format: str = "postgresql-custom"

    def validate(self) -> None:
        if self.schema_version != BACKUP_SCHEMA_VERSION:
            raise BackupError("Unsupported backup manifest schema version.")
        if not BACKUP_NAME_PATTERN.fullmatch(self.backup_name):
            raise BackupError("Backup manifest has an unsafe backup name.")
        if self.bundle_filename != f"{self.backup_name}.tar.age":
            raise BackupError("Backup bundle name does not match its manifest.")
        if not self.database_name or len(self.database_name) > 63:
            raise BackupError("Backup manifest has an invalid database name.")
        for field_name in (
            "bundle_sha256",
            "database_dump_sha256",
            "artifact_inventory_sha256",
            "filesystem_artifact_archive_sha256",
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, field_name)):
                raise BackupError(f"Backup manifest has an invalid {field_name}.")
        if min(
            self.bundle_bytes,
            self.database_dump_bytes,
            self.artifact_inventory_bytes,
            self.artifact_count,
            self.artifact_bytes,
            self.filesystem_artifact_archive_bytes,
            self.filesystem_artifact_count,
            self.filesystem_artifact_bytes,
        ) < 0:
            raise BackupError("Backup manifest contains a negative count or size.")
        if self.encryption != "age-x25519" or self.database_format != "postgresql-custom":
            raise BackupError("Backup manifest declares an unsupported format.")
        try:
            datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise BackupError("Backup manifest has an invalid creation timestamp.") from error

    def as_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_path(cls, path: Path) -> "BackupManifest":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest = cls(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise BackupError("Backup manifest is unreadable or malformed.") from error
        manifest.validate()
        return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_r2_prefix(prefix: str) -> str:
    normalized = prefix.strip("/")
    if (
        not normalized
        or len(normalized) > 200
        or normalized.startswith(".")
        or ".." in normalized.split("/")
        or "//" in normalized
    ):
        raise BackupError("Backup R2 prefix is unsafe.")
    return normalized


def command_environment() -> dict[str, str]:
    allowed = ("HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "PATH", "TMPDIR", "TZ")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def database_environment() -> dict[str, str]:
    database = settings.DATABASES["default"]
    environment = command_environment()
    environment["PGPASSWORD"] = str(database.get("PASSWORD", ""))
    for key in ("PGSSLCA", "PGSSLCERT", "PGSSLKEY", "PGSSLMODE"):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def database_cli_arguments() -> tuple[str, str, str, str]:
    database = settings.DATABASES["default"]
    return (
        str(database.get("HOST") or "localhost"),
        str(database.get("PORT") or "5432"),
        str(database.get("USER") or ""),
        str(database.get("NAME") or ""),
    )


def run_checked(arguments: list[str], *, environment: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            env=environment or command_environment(),
            text=True,
        )
    except FileNotFoundError as error:
        raise BackupError(f"Required backup executable {arguments[0]!r} is unavailable.") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "backup command failed").strip()[-1000:]
        raise BackupError(f"{arguments[0]} failed: {detail}") from error
    return result.stdout


def write_artifact_inventory(path: Path) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    with path.open("w", encoding="utf-8") as destination:
        rows = (
            RawArtifact.objects.order_by("sha256")
            .values(
                "id",
                "sha256",
                "byte_size",
                "detected_content_type",
                "storage_backend",
                "storage_key",
            )
            .iterator(chunk_size=1000)
        )
        for row in rows:
            row["id"] = str(row["id"])
            destination.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            count += 1
            total_bytes += row["byte_size"]
    return count, total_bytes


def _safe_artifact_path(root: Path, storage_key: str) -> Path:
    if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
        raise BackupError(f"Filesystem artifact key {storage_key!r} is unsafe.")
    resolved_root = root.resolve()
    candidate = root / storage_key
    resolved_path = candidate.resolve()
    if resolved_root not in resolved_path.parents:
        raise BackupError(f"Filesystem artifact key {storage_key!r} escapes its storage root.")
    if not resolved_path.is_file() or candidate.is_symlink():
        raise BackupError(f"Filesystem artifact {storage_key!r} is missing or not a regular file.")
    return resolved_path


def write_filesystem_artifact_archive(path: Path) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    with tarfile.open(path, "w") as archive:
        rows = (
            RawArtifact.objects.filter(storage_backend=RawArtifact.StorageBackend.FILESYSTEM)
            .order_by("storage_key")
            .values("sha256", "byte_size", "storage_key")
            .iterator(chunk_size=1000)
        )
        for row in rows:
            source = _safe_artifact_path(settings.OBJECT_STORAGE_ROOT, row["storage_key"])
            if source.stat().st_size != row["byte_size"] or sha256_file(source) != row["sha256"]:
                raise BackupError(
                    f"Filesystem artifact {row['storage_key']!r} failed integrity verification."
                )
            archive.add(source, arcname=row["storage_key"], recursive=False)
            count += 1
            total_bytes += row["byte_size"]
    return count, total_bytes


def _upload_backup_file(client, *, bucket: str, key: str, path: Path, content_type: str) -> None:
    digest = sha256_file(path)
    try:
        client.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type, "Metadata": {"sha256": digest}},
        )
        observed = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", "unknown"))
        raise BackupError(f"R2 backup upload failed with provider code {code}.") from error
    if observed.get("ContentLength") != path.stat().st_size:
        raise BackupError(f"R2 object {key!r} has an unexpected byte size after upload.")
    if observed.get("Metadata", {}).get("sha256") != digest:
        raise BackupError(f"R2 object {key!r} has an unexpected digest after upload.")


def upload_backup_to_r2(manifest_path: Path, manifest: BackupManifest) -> tuple[str, str]:
    from aria.artifacts.storage import S3ArtifactStore, get_artifact_store

    store = get_artifact_store()
    if not isinstance(store, S3ArtifactStore):
        raise BackupError("R2 upload requires ARIA_OBJECT_STORAGE_BACKEND=s3.")
    prefix = validate_r2_prefix(settings.BACKUP_R2_PREFIX)
    created = datetime.fromisoformat(manifest.created_at.replace("Z", "+00:00"))
    object_prefix = f"{prefix}/{created:%Y/%m/%d}/{manifest.backup_name}"
    bundle_path = manifest_path.parent / manifest.bundle_filename
    bundle_key = f"{object_prefix}/{manifest.bundle_filename}"
    manifest_key = f"{object_prefix}/{manifest_path.name}"
    _upload_backup_file(
        store.client,
        bucket=store.bucket,
        key=bundle_key,
        path=bundle_path,
        content_type="application/octet-stream",
    )
    _upload_backup_file(
        store.client,
        bucket=store.bucket,
        key=manifest_key,
        path=manifest_path,
        content_type="application/json",
    )
    return bundle_key, manifest_key


def create_backup(
    *,
    output_directory: Path,
    age_recipient: str,
    database_snapshot: str,
    upload_to_r2: bool,
) -> tuple[Path, BackupManifest, tuple[str, str] | None]:
    if not age_recipient.startswith("age1") or len(age_recipient) > 200:
        raise BackupError("ARIA_BACKUP_AGE_RECIPIENT must be a valid public age recipient.")
    if not database_snapshot.strip() or any(char.isspace() for char in database_snapshot):
        raise BackupError("A valid exported PostgreSQL snapshot is required.")
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC)
    backup_name = f"aria-{timestamp:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
    bundle_path = output_directory / f"{backup_name}.tar.age"
    manifest_path = output_directory / f"{backup_name}.manifest.json"
    host, port, user, database = database_cli_arguments()

    with TemporaryDirectory(prefix=".aria-backup-", dir=output_directory) as temporary:
        working = Path(temporary)
        database_dump = working / "database.dump"
        inventory = working / "artifact-inventory.jsonl"
        filesystem_artifacts = working / "filesystem-artifacts.tar"
        archive = working / "backup.tar"
        encrypted_bundle = working / bundle_path.name
        run_checked(
            [
                "pg_dump",
                "--host",
                host,
                "--port",
                port,
                "--username",
                user,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--snapshot",
                database_snapshot,
                "--file",
                str(database_dump),
                database,
            ],
            environment=database_environment(),
        )
        artifact_count, artifact_bytes = write_artifact_inventory(inventory)
        filesystem_artifact_count, filesystem_artifact_bytes = (
            write_filesystem_artifact_archive(filesystem_artifacts)
        )
        with tarfile.open(archive, "w") as bundle:
            bundle.add(database_dump, arcname="database.dump", recursive=False)
            bundle.add(inventory, arcname="artifact-inventory.jsonl", recursive=False)
            bundle.add(
                filesystem_artifacts,
                arcname="filesystem-artifacts.tar",
                recursive=False,
            )
        run_checked(
            [
                "age",
                "--encrypt",
                "--recipient",
                age_recipient,
                "--output",
                str(encrypted_bundle),
                str(archive),
            ]
        )
        encrypted_bundle.replace(bundle_path)
        manifest = BackupManifest(
            schema_version=BACKUP_SCHEMA_VERSION,
            created_at=timestamp.isoformat().replace("+00:00", "Z"),
            backup_name=backup_name,
            database_name=database,
            bundle_filename=bundle_path.name,
            bundle_sha256=sha256_file(bundle_path),
            bundle_bytes=bundle_path.stat().st_size,
            database_dump_sha256=sha256_file(database_dump),
            database_dump_bytes=database_dump.stat().st_size,
            artifact_inventory_sha256=sha256_file(inventory),
            artifact_inventory_bytes=inventory.stat().st_size,
            artifact_count=artifact_count,
            artifact_bytes=artifact_bytes,
            filesystem_artifact_archive_sha256=sha256_file(filesystem_artifacts),
            filesystem_artifact_archive_bytes=filesystem_artifacts.stat().st_size,
            filesystem_artifact_count=filesystem_artifact_count,
            filesystem_artifact_bytes=filesystem_artifact_bytes,
        )
        temporary_manifest = working / manifest_path.name
        temporary_manifest.write_text(manifest.as_json(), encoding="utf-8")
        temporary_manifest.replace(manifest_path)

    uploaded = upload_backup_to_r2(manifest_path, manifest) if upload_to_r2 else None
    return manifest_path, manifest, uploaded


def verify_encrypted_bundle(manifest_path: Path) -> tuple[BackupManifest, Path]:
    manifest = BackupManifest.from_path(manifest_path)
    bundle_path = manifest_path.parent / manifest.bundle_filename
    if not bundle_path.is_file():
        raise BackupError(f"Encrypted backup bundle {manifest.bundle_filename!r} is missing.")
    if bundle_path.stat().st_size != manifest.bundle_bytes:
        raise BackupError("Encrypted backup bundle byte size does not match its manifest.")
    if sha256_file(bundle_path) != manifest.bundle_sha256:
        raise BackupError("Encrypted backup bundle digest does not match its manifest.")
    return manifest, bundle_path


def _verify_filesystem_artifact_archive(
    archive_path: Path,
    inventory_path: Path,
    manifest: BackupManifest,
) -> None:
    expected: dict[str, tuple[str, int]] = {}
    with inventory_path.open("r", encoding="utf-8") as inventory:
        for line in inventory:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise BackupError("Artifact inventory contains malformed JSON.") from error
            if row.get("storage_backend") == RawArtifact.StorageBackend.FILESYSTEM:
                storage_key = row.get("storage_key", "")
                if (
                    not isinstance(storage_key, str)
                    or not storage_key
                    or storage_key.startswith("/")
                    or ".." in storage_key.split("/")
                ):
                    raise BackupError("Artifact inventory contains an unsafe filesystem key.")
                expected[storage_key] = (row.get("sha256", ""), row.get("byte_size", -1))
    if len(expected) != manifest.filesystem_artifact_count:
        raise BackupError("Filesystem artifact count does not match its manifest.")
    if sum(size for _digest, size in expected.values()) != manifest.filesystem_artifact_bytes:
        raise BackupError("Filesystem artifact bytes do not match the backup manifest.")
    with tarfile.open(archive_path, "r") as archive:
        members = archive.getmembers()
        if (
            len(members) != len(expected)
            or {member.name for member in members} != set(expected)
            or any(not member.isfile() for member in members)
        ):
            raise BackupError("Filesystem artifact archive has unexpected members.")
        for member in members:
            expected_digest, expected_size = expected[member.name]
            if member.size != expected_size:
                raise BackupError(f"Filesystem artifact {member.name!r} has an unexpected size.")
            source = archive.extractfile(member)
            if source is None:
                raise BackupError(f"Unable to read filesystem artifact {member.name!r}.")
            digest = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                raise BackupError(f"Filesystem artifact {member.name!r} failed its digest check.")


@contextmanager
def verified_backup_contents(
    manifest_path: Path,
    *,
    identity_file: Path,
) -> Iterator[tuple[BackupManifest, Path, Path]]:
    manifest, bundle_path = verify_encrypted_bundle(manifest_path)
    if not identity_file.is_file():
        raise BackupError("The age identity file is missing.")
    with TemporaryDirectory(prefix="aria-restore-") as temporary:
        working = Path(temporary)
        archive = working / "backup.tar"
        run_checked(
            [
                "age",
                "--decrypt",
                "--identity",
                str(identity_file),
                "--output",
                str(archive),
                str(bundle_path),
            ]
        )
        with tarfile.open(archive, "r") as bundle:
            members = bundle.getmembers()
            expected_sizes = {
                "database.dump": manifest.database_dump_bytes,
                "artifact-inventory.jsonl": manifest.artifact_inventory_bytes,
                "filesystem-artifacts.tar": manifest.filesystem_artifact_archive_bytes,
            }
            if (
                len(members) != len(BACKUP_MEMBER_NAMES)
                or {member.name for member in members} != BACKUP_MEMBER_NAMES
                or any(
                    not member.isfile() or member.size != expected_sizes[member.name]
                    for member in members
                )
            ):
                raise BackupError("Decrypted backup contains unexpected archive members.")
            extracted: dict[str, Path] = {}
            for member in members:
                source = bundle.extractfile(member)
                if source is None:
                    raise BackupError(f"Unable to read backup member {member.name!r}.")
                destination = working / member.name
                with destination.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                extracted[member.name] = destination
        database_dump = extracted["database.dump"]
        inventory = extracted["artifact-inventory.jsonl"]
        filesystem_artifacts = extracted["filesystem-artifacts.tar"]
        if sha256_file(database_dump) != manifest.database_dump_sha256:
            raise BackupError("Database dump digest does not match its manifest.")
        if sha256_file(inventory) != manifest.artifact_inventory_sha256:
            raise BackupError("Artifact inventory digest does not match its manifest.")
        if sha256_file(filesystem_artifacts) != manifest.filesystem_artifact_archive_sha256:
            raise BackupError("Filesystem artifact archive digest does not match its manifest.")
        with inventory.open("rb") as inventory_file:
            inventory_count = sum(1 for line in inventory_file if line.strip())
        if inventory_count != manifest.artifact_count:
            raise BackupError("Artifact inventory count does not match its manifest.")
        _verify_filesystem_artifact_archive(filesystem_artifacts, inventory, manifest)
        run_checked(["pg_restore", "--list", str(database_dump)])
        yield manifest, database_dump, inventory


def run_restore_drill(manifest_path: Path, *, identity_file: Path) -> tuple[str, int]:
    host, port, user, _database = database_cli_arguments()
    drill_database = f"aria_restore_drill_{uuid4().hex[:12]}"
    if not drill_database.startswith("aria_restore_drill_"):
        raise BackupError("Refusing an unsafe restore-drill database name.")
    environment = database_environment()
    with verified_backup_contents(manifest_path, identity_file=identity_file) as (
        _manifest,
        database_dump,
        _inventory,
    ):
        created = False
        try:
            run_checked(
                [
                    "createdb",
                    "--host",
                    host,
                    "--port",
                    port,
                    "--username",
                    user,
                    drill_database,
                ],
                environment=environment,
            )
            created = True
            run_checked(
                [
                    "pg_restore",
                    "--host",
                    host,
                    "--port",
                    port,
                    "--username",
                    user,
                    "--dbname",
                    drill_database,
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    str(database_dump),
                ],
                environment=environment,
            )
            table_count_output = run_checked(
                [
                    "psql",
                    "--host",
                    host,
                    "--port",
                    port,
                    "--username",
                    user,
                    "--dbname",
                    drill_database,
                    "--tuples-only",
                    "--no-align",
                    "--command",
                    "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';",
                ],
                environment=environment,
            )
            table_count = int(table_count_output.strip())
            if table_count < 1:
                raise BackupError("Restore drill completed without any public tables.")
            return drill_database, table_count
        finally:
            if created:
                run_checked(
                    [
                        "dropdb",
                        "--host",
                        host,
                        "--port",
                        port,
                        "--username",
                        user,
                        "--force",
                        drill_database,
                    ],
                    environment=environment,
                )
