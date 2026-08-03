from celery import shared_task

from aria.collections.models import PublicationCollection
from aria.documents.models import DocumentVersion
from aria.knowledge.embedding_services import (
    current_sections_queryset,
    project_section_embeddings,
)
from aria.knowledge.embeddings import RetryableEmbeddingError


@shared_task(
    bind=True,
    autoretry_for=(RetryableEmbeddingError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def embed_collection_sections(self, collection_id: str, provider_name: str) -> dict:
    collection = PublicationCollection.objects.get(pk=collection_id)
    summary = project_section_embeddings(
        current_sections_queryset(collection).order_by("id"),
        provider_name=provider_name,
    )
    return summary.__dict__


@shared_task(
    bind=True,
    autoretry_for=(RetryableEmbeddingError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def embed_document_version_sections(self, version_id: str, provider_name: str) -> dict:
    version = DocumentVersion.objects.get(pk=version_id)
    summary = project_section_embeddings(
        version.sections.order_by("id"),
        provider_name=provider_name,
    )
    return summary.__dict__
