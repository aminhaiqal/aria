from celery import shared_task

from aria.artifacts.models import RawArtifact
from aria.extraction.services import RetryableExtractionError, extract_artifact


@shared_task(
    bind=True,
    autoretry_for=(RetryableExtractionError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def extract_raw_artifact(self, raw_artifact_id: str) -> str:
    artifact = RawArtifact.objects.get(pk=raw_artifact_id)
    return str(extract_artifact(artifact).id)
