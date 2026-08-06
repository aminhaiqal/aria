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
def extract_raw_artifact(
    self,
    raw_artifact_id: str,
    *,
    project_knowledge: bool = True,
) -> str:
    artifact = RawArtifact.objects.get(pk=raw_artifact_id)
    run = extract_artifact(artifact, project_knowledge=project_knowledge)
    if run.status == run.Status.SUCCEEDED:
        from aria.orchestration.services import resume_waiting_orchestrations_for_artifact

        resume_waiting_orchestrations_for_artifact(artifact)
    return str(run.id)
