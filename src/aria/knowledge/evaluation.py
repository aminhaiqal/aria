from dataclasses import dataclass

from pgvector.django import CosineDistance

from aria.collections.models import PublicationCollection
from aria.knowledge.embedding_services import current_sections_queryset
from aria.knowledge.embeddings import EmbeddingError, EmbeddingProvider, get_embedding_provider


@dataclass(frozen=True)
class RetrievalCase:
    name: str
    query: str
    expected_url_suffix: str


JPDP_RETRIEVAL_CASES = (
    RetrievalCase(
        name="breach_notification",
        query="What must an organization do after discovering a personal data breach?",
        expected_url_suffix="circular-of-personal-data-protection-commissioner-no-1-2025-data-breach-notification/",
    ),
    RetrievalCase(
        name="data_protection_officer",
        query="When is an organization required to appoint a data protection officer?",
        expected_url_suffix="circular-of-personal-data-protection-commissioner-no-2-2025-appointment-of-data-protection-officer/",
    ),
    RetrievalCase(
        name="impact_assessment",
        query="How should privacy risks be assessed before high-risk personal data processing?",
        expected_url_suffix="data-protection-impact-assessment-guideline-dpia/",
    ),
    RetrievalCase(
        name="registration_fees",
        query="Which publication specifies the fees payable for personal data registration?",
        expected_url_suffix="personal-data-protection-regulations-fees/",
    ),
    RetrievalCase(
        name="registration_rules",
        query="What rules govern an application to register as a data user?",
        expected_url_suffix="personal-data-protection-regulations-registration-of-data-users/",
    ),
    RetrievalCase(
        name="data_user_classes",
        query="Which categories of organizations are classes of data users?",
        expected_url_suffix="personal-data-protection-order-class-of-data-users/",
    ),
    RetrievalCase(
        name="amendment_commencement",
        query="When did the Personal Data Protection Amendment Act 2024 come into operation?",
        expected_url_suffix="personal-data-protection-amendment-act-2024-appointment-of-date-of-coming-into-operation/",
    ),
    RetrievalCase(
        name="compoundable_offences",
        query="Which offences may be compounded instead of prosecuted?",
        expected_url_suffix="compounding-of-offences-regulations/",
    ),
)


def evaluate_embedding_provider(
    collection: PublicationCollection,
    *,
    provider_name: str,
    provider: EmbeddingProvider | None = None,
    cases: tuple[RetrievalCase, ...] = JPDP_RETRIEVAL_CASES,
    result_limit: int = 5,
) -> dict:
    embedding_provider = provider or get_embedding_provider(provider_name)
    sections = current_sections_queryset(collection)
    section_count = sections.count()
    embedded_count = (
        sections.filter(
            embeddings__provider=embedding_provider.provider_name,
            embeddings__model=embedding_provider.model,
        )
        .distinct()
        .count()
    )
    if embedded_count != section_count:
        raise EmbeddingError(
            f"Provider {embedding_provider.provider_name}/{embedding_provider.model} covers "
            f"{embedded_count} of {section_count} current sections."
        )

    query_batch = embedding_provider.embed_texts([case.query for case in cases])
    results: list[dict] = []
    reciprocal_rank_total = 0.0
    hit_at_1 = hit_at_3 = hit_at_5 = 0
    for case, query_vector in zip(cases, query_batch.vectors, strict=True):
        ranked_sections = (
            sections.filter(
                embeddings__provider=embedding_provider.provider_name,
                embeddings__model=embedding_provider.model,
            )
            .annotate(vector_distance=CosineDistance("embeddings__embedding", list(query_vector)))
            .order_by("vector_distance")[:250]
        )
        documents: list[dict] = []
        seen_identities: set[str] = set()
        for section in ranked_sections:
            identity = section.document_version.identity
            identity_id = str(identity.id)
            if identity_id in seen_identities:
                continue
            seen_identities.add(identity_id)
            documents.append(
                {
                    "identity_id": identity_id,
                    "title": section.document_version.title or identity.canonical_title,
                    "canonical_url": identity.canonical_url,
                    "distance": float(section.vector_distance),
                    "section_id": str(section.id),
                    "page_number": section.page_number,
                }
            )
            if len(documents) >= result_limit:
                break

        rank = next(
            (
                index
                for index, document in enumerate(documents, start=1)
                if document["canonical_url"].endswith(case.expected_url_suffix)
            ),
            None,
        )
        if rank is not None:
            reciprocal_rank_total += 1 / rank
            hit_at_1 += rank <= 1
            hit_at_3 += rank <= 3
            hit_at_5 += rank <= 5
        results.append(
            {
                "name": case.name,
                "query": case.query,
                "expected_url_suffix": case.expected_url_suffix,
                "rank": rank,
                "top_documents": documents,
            }
        )

    case_count = len(cases)
    return {
        "provider": embedding_provider.provider_name,
        "model": embedding_provider.model,
        "dimensions": embedding_provider.dimensions,
        "section_count": section_count,
        "case_count": case_count,
        "prompt_tokens": query_batch.prompt_tokens,
        "mean_reciprocal_rank": reciprocal_rank_total / case_count if case_count else 0.0,
        "hit_at_1": hit_at_1 / case_count if case_count else 0.0,
        "hit_at_3": hit_at_3 / case_count if case_count else 0.0,
        "hit_at_5": hit_at_5 / case_count if case_count else 0.0,
        "cases": results,
    }
