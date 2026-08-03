from celery import shared_task

from aria.comparisons.services import compare_all_eligible_versions, compare_document_versions
from aria.documents.models import DocumentVersion


@shared_task
def compare_version_pair(before_version_id: str, after_version_id: str) -> dict:
    before = DocumentVersion.objects.get(pk=before_version_id)
    after = DocumentVersion.objects.get(pk=after_version_id)
    comparison, created = compare_document_versions(before, after)
    return {"comparison_id": str(comparison.id), "created": created}


@shared_task
def compare_eligible_versions() -> dict:
    return compare_all_eligible_versions().__dict__
