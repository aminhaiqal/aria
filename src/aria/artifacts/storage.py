import hashlib
import os
from pathlib import Path
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class ArtifactStorageError(RuntimeError):
    pass


class ArtifactStorageIntegrityError(ArtifactStorageError):
    pass


class ArtifactStore(Protocol):
    backend_name: str

    def put_if_absent(
        self,
        sha256: str,
        content: bytes,
        content_type: str,
        *,
        namespace: str = "",
    ) -> str: ...

    def read(self, key: str) -> bytes: ...


def build_artifact_key(sha256: str, *, namespace: str = "") -> str:
    normalized_namespace = namespace.strip("/")
    if normalized_namespace and (
        normalized_namespace.startswith(".")
        or ".." in normalized_namespace.split("/")
        or "//" in normalized_namespace
    ):
        raise ArtifactStorageError("Artifact namespace is not safe.")
    prefix = f"{normalized_namespace}/" if normalized_namespace else ""
    return f"{prefix}sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"


class FilesystemArtifactStore:
    backend_name = "filesystem"

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def put_if_absent(
        self,
        sha256: str,
        content: bytes,
        content_type: str,
        *,
        namespace: str = "",
    ) -> str:
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ArtifactStorageIntegrityError("Content does not match its SHA-256 digest.")
        key = build_artifact_key(sha256, namespace=namespace)
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        except FileExistsError:
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != sha256:
                raise ArtifactStorageIntegrityError(
                    f"Existing artifact at {key} failed integrity verification."
                ) from None
            return key
        with os.fdopen(descriptor, "wb") as artifact_file:
            artifact_file.write(content)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())
        return key

    def read(self, key: str) -> bytes:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            raise ArtifactStorageError("Artifact key escapes the configured storage root.")
        return path.read_bytes()


class S3ArtifactStore:
    backend_name = "s3"

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
    ):
        if not all((endpoint_url, bucket, access_key, secret_key)):
            raise ImproperlyConfigured(
                "Cloudflare R2 requires endpoint, bucket, access key, and secret key."
            )
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def put_if_absent(
        self,
        sha256: str,
        content: bytes,
        content_type: str,
        *,
        namespace: str = "",
    ) -> str:
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ArtifactStorageIntegrityError("Content does not match its SHA-256 digest.")
        key = build_artifact_key(sha256, namespace=namespace)
        try:
            existing = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise ArtifactStorageError(f"Unable to inspect artifact {key}.") from error
        else:
            metadata_digest = existing.get("Metadata", {}).get("sha256")
            if metadata_digest != sha256 or existing.get("ContentLength") != len(content):
                raise ArtifactStorageIntegrityError(
                    f"Existing object at {key} failed integrity verification."
                )
            return key

        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                Metadata={"sha256": sha256},
            )
        except ClientError as error:
            raise ArtifactStorageError(f"Unable to store artifact {key}.") from error
        return key

    def read(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except ClientError as error:
            raise ArtifactStorageError(f"Unable to read artifact {key}.") from error


def get_artifact_store(backend: str | None = None) -> ArtifactStore:
    selected_backend = backend or settings.OBJECT_STORAGE_BACKEND
    if selected_backend == "filesystem":
        return FilesystemArtifactStore(settings.OBJECT_STORAGE_ROOT)
    if selected_backend == "s3":
        return S3ArtifactStore(
            endpoint_url=settings.OBJECT_STORAGE_ENDPOINT,
            bucket=settings.OBJECT_STORAGE_BUCKET,
            access_key=settings.OBJECT_STORAGE_ACCESS_KEY,
            secret_key=settings.OBJECT_STORAGE_SECRET_KEY,
            region=settings.OBJECT_STORAGE_REGION,
        )
    raise ImproperlyConfigured(f"Unsupported artifact storage backend: {selected_backend}")


def persist_artifact(
    content: bytes,
    content_type: str,
    *,
    backend: str | None = None,
    store: ArtifactStore | None = None,
    namespace: str = "",
):
    from aria.artifacts.models import RawArtifact

    digest = hashlib.sha256(content).hexdigest()
    existing = RawArtifact.objects.filter(sha256=digest).first()
    if existing is not None:
        if existing.byte_size != len(content):
            raise ArtifactStorageIntegrityError(
                "Existing artifact byte size does not match the derived content."
            )
        return existing

    artifact_store = store or get_artifact_store(backend)
    storage_key = artifact_store.put_if_absent(
        digest,
        content,
        content_type,
        namespace=namespace,
    )
    artifact, created = RawArtifact.objects.get_or_create(
        sha256=digest,
        defaults={
            "byte_size": len(content),
            "detected_content_type": content_type,
            "storage_backend": artifact_store.backend_name,
            "storage_key": storage_key,
        },
    )
    if not created and artifact.byte_size != len(content):
        raise ArtifactStorageIntegrityError(
            "Concurrent artifact record does not match the derived content."
        )
    return artifact
