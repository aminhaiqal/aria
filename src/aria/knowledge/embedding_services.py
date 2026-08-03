from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

from aria.collections.models import PublicationCollection
from aria.documents.models import DocumentVersion, NormalizedSection
from aria.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from aria.knowledge.models import SectionEmbedding


@dataclass(frozen=True)
class EmbeddingProjectionSummary:
    provider: str
    model: str
    dimensions: int
    candidate_count: int
    created_count: int
    skipped_count: int
    prompt_tokens: int


def current_versions_queryset(
    collection: PublicationCollection | None = None,
) -> QuerySet[DocumentVersion]:
    queryset = DocumentVersion.objects.filter(identity__superseded_by__isnull=True)
    if collection is not None:
        queryset = queryset.filter(identity__collection=collection)
    return queryset.order_by("identity_id", "-created_at", "-id").distinct("identity_id")


def current_sections_queryset(
    collection: PublicationCollection | None = None,
) -> QuerySet[NormalizedSection]:
    version_ids = current_versions_queryset(collection).values_list("id", flat=True)
    return NormalizedSection.objects.filter(document_version_id__in=version_ids).select_related(
        "document_version",
        "document_version__identity",
        "source_artifact",
    )


def section_embedding_input(section: NormalizedSection) -> str:
    return f"{section.heading}\n{section.text}"


def project_section_embeddings(
    sections,
    *,
    provider_name: str | None = None,
    provider: EmbeddingProvider | None = None,
    batch_size: int | None = None,
) -> EmbeddingProjectionSummary:
    embedding_provider = provider or get_embedding_provider(provider_name)
    selected_provider = embedding_provider.provider_name
    model = embedding_provider.model
    dimensions = embedding_provider.dimensions
    section_list = list(sections)
    candidate_count = len(section_list)

    existing = set(
        SectionEmbedding.objects.filter(
            normalized_section_id__in=[section.id for section in section_list],
            provider=selected_provider,
            model=model,
        ).values_list("normalized_section_id", "source_text_sha256")
    )
    pending = [
        section for section in section_list if (section.id, section.text_sha256) not in existing
    ]
    skipped_count = candidate_count - len(pending)
    if not pending:
        return EmbeddingProjectionSummary(
            provider=selected_provider,
            model=model,
            dimensions=dimensions,
            candidate_count=candidate_count,
            created_count=0,
            skipped_count=skipped_count,
            prompt_tokens=0,
        )

    selected_batch_size = batch_size or (
        settings.OPENAI_EMBEDDING_BATCH_SIZE if selected_provider == "openai" else 256
    )
    if selected_batch_size < 1:
        raise ValueError("Embedding batch size must be positive.")

    prompt_tokens = 0
    before_count = SectionEmbedding.objects.filter(
        normalized_section_id__in=[section.id for section in pending],
        provider=selected_provider,
        model=model,
    ).count()
    for offset in range(0, len(pending), selected_batch_size):
        batch_sections = pending[offset : offset + selected_batch_size]
        result = embedding_provider.embed_texts(
            [section_embedding_input(section) for section in batch_sections]
        )
        prompt_tokens += result.prompt_tokens
        with transaction.atomic():
            SectionEmbedding.objects.bulk_create(
                [
                    SectionEmbedding(
                        normalized_section=section,
                        provider=selected_provider,
                        model=model,
                        dimensions=dimensions,
                        source_text_sha256=section.text_sha256,
                        embedding=list(vector),
                    )
                    for section, vector in zip(
                        batch_sections,
                        result.vectors,
                        strict=True,
                    )
                ],
                ignore_conflicts=True,
            )
    after_count = SectionEmbedding.objects.filter(
        normalized_section_id__in=[section.id for section in pending],
        provider=selected_provider,
        model=model,
    ).count()
    return EmbeddingProjectionSummary(
        provider=selected_provider,
        model=model,
        dimensions=dimensions,
        candidate_count=candidate_count,
        created_count=after_count - before_count,
        skipped_count=skipped_count,
        prompt_tokens=prompt_tokens,
    )
