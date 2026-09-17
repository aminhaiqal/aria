from celery import shared_task

from aria.collections.models import PublicationCollection
from aria.documents.models import DocumentVersion
from aria.knowledge.embedding_services import (
    current_sections_queryset,
    project_section_embeddings,
)
from aria.knowledge.embeddings import RetryableEmbeddingError
from aria.knowledge.services import project_document_version


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
        version.sections.select_related("document_version__identity").order_by("id"),
        provider_name=provider_name,
    )
    return summary.__dict__


@shared_task(name="aria.knowledge.tasks.project_document_version_knowledge")
def project_document_version_knowledge(version_id: str) -> dict:
    version = DocumentVersion.objects.get(pk=version_id)
    project_document_version(version)
    return {
        "document_version_id": str(version.id),
        "section_count": version.sections.count(),
        "graph_edge_count": version.graph_edges.count(),
    }
