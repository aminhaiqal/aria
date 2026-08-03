import hashlib
from uuid import uuid4

from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand, CommandError

from aria.artifacts.storage import S3ArtifactStore, get_artifact_store


class Command(BaseCommand):
    help = "Write, verify, and remove one temporary object in configured S3-compatible storage."

    def handle(self, *args, **options):
        store = get_artifact_store()
        if not isinstance(store, S3ArtifactStore):
            raise CommandError("The configured object storage backend is not S3-compatible.")

        key = f".aria-probe/{uuid4()}"
        payload = f"ARIA object storage probe {uuid4()}".encode()
        expected_digest = hashlib.sha256(payload).hexdigest()
        created = False
        try:
            store.client.put_object(
                Bucket=store.bucket,
                Key=key,
                Body=payload,
                ContentType="text/plain",
                Metadata={"sha256": expected_digest, "temporary": "true"},
            )
            created = True
            response = store.client.get_object(Bucket=store.bucket, Key=key)
            retrieved = response["Body"].read()
            if hashlib.sha256(retrieved).hexdigest() != expected_digest:
                raise CommandError("The object storage probe failed its integrity check.")
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", "unknown"))
            raise CommandError(
                f"The object storage probe failed with provider code {code}."
            ) from error
        finally:
            if created:
                try:
                    store.client.delete_object(Bucket=store.bucket, Key=key)
                except ClientError as error:
                    code = str(error.response.get("Error", {}).get("Code", "unknown"))
                    raise CommandError(
                        f"The temporary probe could not be removed (provider code {code})."
                    ) from error

        try:
            store.client.head_object(Bucket=store.bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise CommandError(
                    f"Probe cleanup could not be confirmed (provider code {code})."
                ) from error
        else:
            raise CommandError("The temporary object still exists after cleanup.")
        self.stdout.write(self.style.SUCCESS("Object storage write/read/delete probe passed."))
