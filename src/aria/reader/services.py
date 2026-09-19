from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, Subquery
from pgvector.django import CosineDistance

from aria.artifacts.models import ArtifactObservation
from aria.comparisons.models import ComparisonSummary, DocumentComparison
from aria.documents.models import (
    DocumentIdentity,
    DocumentVersion,
    NormalizedSection,
    VersionEvidence,
)
from aria.impacts.models import (
    BusinessProfile,
    ImpactReview,
    ProfileImpactMatch,
    RegulatoryImpact,
)
from aria.impacts.profiles import business_profile_snapshot
from aria.knowledge.embedding_services import current_sections_queryset, current_versions_queryset
from aria.knowledge.embeddings import (
    SUPPORTED_PROVIDERS,
    EmbeddingError,
    embed_text,
    embedding_configuration,
)
from aria.quality.models import DocumentQualityAssessment

SEARCH_MODES = frozenset({"hybrid", "full_text", "vector"})
TITLE_MATCH_WEIGHT = 0.025
TITLE_QUERY_STOP_WORDS = frozenset(
    {
        "and",
        "are",
        "does",
        "for",
        "from",
        "how",
        "into",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)
WORD_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


class ReaderQueryError(ValueError):
    pass


class ReaderSearchUnavailable(RuntimeError):
    pass


def _document_title(version: DocumentVersion, identity: DocumentIdentity) -> str:
    title = (version.title or identity.canonical_title or "").strip()
    if not title or title.startswith(("http://", "https://")):
        source_url = version.canonical_url or identity.canonical_url or title
        filename = unquote(PurePosixPath(urlsplit(source_url).path).name).strip()
        return filename or source_url
    return title


def _title_overlap_score(query_text: str, title: str) -> float:
    query_tokens = {
        token
        for token in WORD_PATTERN.findall(query_text.casefold())
        if len(token) > 2 and token not in TITLE_QUERY_STOP_WORDS
    }
    if not query_tokens:
        return 0.0
    title_tokens = set(WORD_PATTERN.findall(title.casefold()))
    return len(query_tokens & title_tokens) / len(query_tokens)


def _bounded_excerpt(text: str, query_text: str, *, maximum: int = 620) -> tuple[str, bool]:
    compact = " ".join(text.split())
    if len(compact) <= maximum:
        return compact, False
    tokens = [token.casefold() for token in query_text.split() if len(token) > 2]
    lowered = compact.casefold()
    positions = [lowered.find(token) for token in tokens]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - maximum // 4)
    end = min(len(compact), start + maximum)
    excerpt = compact[start:end]
    if start:
        excerpt = f"…{excerpt.lstrip()}"
    if end < len(compact):
        excerpt = f"{excerpt.rstrip()}…"
    return excerpt, True


def _latest_artifact_observations(artifact_ids: set[UUID]) -> dict[UUID, ArtifactObservation]:
    if not artifact_ids:
        return {}
    observations = (
        ArtifactObservation.objects.filter(raw_artifact_id__in=artifact_ids)
        .select_related("candidate", "candidate__endpoint")
        .order_by("raw_artifact_id", "-retrieved_at", "-id")
        .distinct("raw_artifact_id")
    )
    return {observation.raw_artifact_id: observation for observation in observations}


def _latest_version_evidence(
    version_ids: set[UUID],
    artifact_ids: set[UUID],
) -> dict[tuple[UUID, UUID], VersionEvidence]:
    if not version_ids or not artifact_ids:
        return {}
    records = (
        VersionEvidence.objects.filter(
            document_version_id__in=version_ids,
            raw_artifact_id__in=artifact_ids,
            artifact_observation__isnull=False,
        )
        .select_related(
            "artifact_observation",
            "artifact_observation__candidate",
            "artifact_observation__candidate__endpoint",
        )
        .order_by("document_version_id", "raw_artifact_id", "-created_at", "-id")
        .distinct("document_version_id", "raw_artifact_id")
    )
    return {
        (record.document_version_id, record.raw_artifact_id): record for record in records
    }


def _reader_impact_payloads(
    version_ids: set[UUID],
    *,
    business_profile: BusinessProfile | None = None,
    include_evidence: bool = True,
) -> dict[UUID, list[dict]]:
    if not version_ids:
        return {}
    impacts = list(
        RegulatoryImpact.objects.filter(
            comparison_item__comparison__after_version_id__in=version_ids,
        )
        .select_related(
            "comparison_item__comparison",
            "confirmation_review",
        )
        .prefetch_related(
            "comparison_item__reviews",
            "reviews__reviewer",
            "reviews__reviewed_targets__term",
            "evidence_records",
        )
        .order_by("created_at", "id")
    )
    eligible: list[tuple[RegulatoryImpact, ImpactReview]] = []
    for impact in impacts:
        review = impact.reviews.first()
        source_review = impact.comparison_item.reviews.first()
        if (
            review is None
            or review.decision
            not in (ImpactReview.Decision.APPROVED, ImpactReview.Decision.AMENDED)
            or source_review is None
            or source_review.id != impact.confirmation_review_id
            or source_review.decision != "confirmed"
        ):
            continue
        eligible.append((impact, review))

    matches: dict[UUID, ProfileImpactMatch] = {}
    selected_profile_snapshot = None
    if business_profile is not None:
        selected_profile_snapshot = business_profile_snapshot(business_profile)
        for match in ProfileImpactMatch.objects.filter(
            profile=business_profile,
            impact_review_id__in=[review.id for _, review in eligible],
        ).order_by("-created_at", "-id"):
            if (
                match.impact_review_id not in matches
                and match.profile_snapshot == selected_profile_snapshot
            ):
                matches[match.impact_review_id] = match

    grouped: dict[UUID, list[dict]] = {}
    for impact, review in eligible:
        match = matches.get(review.id)
        if business_profile is not None and (
            match is None or match.outcome != ProfileImpactMatch.Outcome.MATCHED
        ):
            continue
        targets = [
            {
                "id": str(target.term_id),
                "dimension": target.term.dimension,
                "code": target.term.code,
                "label": target.term.label,
                "disposition": target.disposition,
                "rationale": target.rationale,
            }
            for target in review.reviewed_targets.all()
        ]
        evidence = []
        if include_evidence:
            evidence = [
                {
                    "side": record.side,
                    "artifact_id": str(record.source_artifact_id),
                    "artifact_sha256": record.artifact_sha256,
                    "anchor_text": record.anchor_text,
                    "anchor_text_sha256": record.anchor_text_sha256,
                    "section_text_sha256": record.section_text_sha256,
                    "source_locator": record.source_locator,
                }
                for record in impact.evidence_records.all()
            ]
        payload = {
            "id": str(impact.id),
            "impact_type": impact.impact_type,
            "review_id": str(review.id),
            "review_decision": review.decision,
            "reviewed_title": review.reviewed_title,
            "reviewed_statement": review.reviewed_statement,
            "reviewed_effective_date_text": review.reviewed_effective_date_text,
            "reviewed_at": review.created_at,
            "comparison_item_id": str(impact.comparison_item_id),
            "change_type": impact.comparison_item.change_type,
            "legal_effect_assessed": False,
            "targets": targets,
            "evidence": evidence,
            "relevance": (
                {
                    "outcome": match.outcome,
                    "explanation": match.explanation,
                    "matched_terms": match.matched_terms,
                    "ruleset": match.ruleset,
                    "evaluated_at": match.created_at,
                }
                if match
                else None
            ),
        }
        identity_id = impact.comparison_item.comparison.identity_id
        grouped.setdefault(identity_id, []).append(payload)
    return grouped


def _validate_search_inputs(
    query_text: str,
    mode: str,
    provider_name: str,
    page: int,
    page_size: int,
) -> str:
    query_text = query_text.strip()
    if not query_text:
        raise ReaderQueryError("Enter a search query.")
    if len(query_text) > settings.READER_MAX_QUERY_CHARACTERS:
        raise ReaderQueryError(
            f"Search queries are limited to {settings.READER_MAX_QUERY_CHARACTERS} characters."
        )
    if mode not in SEARCH_MODES:
        raise ReaderQueryError("Search mode must be hybrid, full_text, or vector.")
    if provider_name and provider_name not in SUPPORTED_PROVIDERS:
        raise ReaderQueryError("Embedding provider must be local_hash or openrouter.")
    if page < 1 or page > settings.READER_MAX_SEARCH_PAGES:
        raise ReaderQueryError(
            f"Page must be between 1 and {settings.READER_MAX_SEARCH_PAGES}."
        )
    if page_size < 1 or page_size > settings.READER_MAX_PAGE_SIZE:
        raise ReaderQueryError(
            f"Page size must be between 1 and {settings.READER_MAX_PAGE_SIZE}."
        )
    return query_text


def search_reader_documents(
    query_text: str,
    *,
    mode: str = "hybrid",
    provider_name: str = "",
    authority_slug: str = "",
    collection_id: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 10,
    business_profile: BusinessProfile | None = None,
) -> dict:
    query_text = _validate_search_inputs(
        query_text,
        mode,
        provider_name,
        page,
        page_size,
    )
    base = current_sections_queryset().filter(
        document_version__identity__collection__is_enabled=True,
        document_version__identity__collection__is_evidence_eligible=True,
        document_version__identity__collection__authority__is_enabled=True,
    ).select_related(
        "document_version",
        "document_version__identity",
        "document_version__identity__collection",
        "document_version__identity__collection__authority",
        "source_artifact",
    )
    if authority_slug:
        base = base.filter(document_version__identity__collection__authority__slug=authority_slug)
    if collection_id:
        try:
            selected_collection_id = UUID(str(collection_id))
        except ValueError as error:
            raise ReaderQueryError("Collection must be a valid identifier.") from error
        base = base.filter(document_version__identity__collection_id=selected_collection_id)
    if date_from:
        base = base.filter(document_version__created_at__date__gte=date_from)
    if date_to:
        base = base.filter(document_version__created_at__date__lte=date_to)

    candidate_limit = settings.READER_SEARCH_CANDIDATE_LIMIT
    scores: dict[UUID, dict] = {}
    warnings: list[str] = []
    vector_configuration = None

    if mode in {"hybrid", "full_text"}:
        search_vector = (
            SearchVector("document_version__title", weight="A", config="simple")
            + SearchVector(
                "document_version__identity__canonical_title",
                weight="A",
                config="simple",
            )
            + SearchVector("heading", weight="B", config="simple")
            + SearchVector("text", weight="C", config="simple")
        )
        search_query = SearchQuery(query_text, search_type="websearch", config="simple")
        text_results = (
            base.annotate(text_rank=SearchRank(search_vector, search_query, normalization=2))
            .filter(text_rank__gt=0)
            .order_by("-text_rank", "id")[:candidate_limit]
        )
        for rank, section in enumerate(text_results, start=1):
            title_overlap_score = _title_overlap_score(
                query_text,
                _document_title(section.document_version, section.document_version.identity),
            )
            scores[section.id] = {
                "section": section,
                "score": (1 / (60 + rank)) + (TITLE_MATCH_WEIGHT * title_overlap_score),
                "text_rank": float(section.text_rank),
                "vector_distance": None,
            }

    if mode in {"hybrid", "vector"}:
        selected_provider = provider_name or settings.READER_EMBEDDING_PROVIDER
        try:
            provider, model, dimensions = embedding_configuration(selected_provider)
            query_vector = embed_text(query_text, provider_name=provider)
            vector_configuration = {
                "provider": provider,
                "model": model,
                "dimensions": dimensions,
                "query_sent_to_provider": provider == "openrouter",
            }
            if any(query_vector):
                vector_results = (
                    base.filter(
                        embeddings__provider=provider,
                        embeddings__model=model,
                        embeddings__source_text_sha256=F("text_sha256"),
                    )
                    .annotate(
                        vector_distance=CosineDistance("embeddings__embedding", query_vector)
                    )
                    .order_by("vector_distance", "id")[:candidate_limit]
                )
                for rank, section in enumerate(vector_results, start=1):
                    entry = scores.setdefault(
                        section.id,
                        {
                            "section": section,
                            "score": 0.0,
                            "text_rank": None,
                            "vector_distance": None,
                        },
                    )
                    entry["score"] += 1 / (60 + rank)
                    entry["vector_distance"] = float(section.vector_distance)
        except (EmbeddingError, ImproperlyConfigured) as error:
            if mode == "vector":
                raise ReaderSearchUnavailable(
                    "Semantic search is temporarily unavailable. Use hybrid or exact text search."
                ) from error
            warnings.append(
                "Semantic ranking was unavailable, so these results use exact text ranking only."
            )

    ranked_sections = sorted(
        scores.values(),
        key=lambda item: (-item["score"], str(item["section"].id)),
    )
    grouped: OrderedDict[UUID, dict] = OrderedDict()
    for entry in ranked_sections:
        section = entry["section"]
        version = section.document_version
        identity = version.identity
        collection = identity.collection
        document = grouped.setdefault(
            identity.id,
            {
                "identity_id": str(identity.id),
                "version_id": str(version.id),
                "title": _document_title(version, identity),
                "canonical_url": version.canonical_url or identity.canonical_url,
                "normalized_content_sha256": version.normalized_content_sha256,
                "language_hint": version.language_hint,
                "version_created_at": version.created_at,
                "authority": {
                    "id": str(collection.authority_id),
                    "name": collection.authority.name,
                    "slug": collection.authority.slug,
                    "trust_classification": collection.authority.trust_classification,
                },
                "collection": {
                    "id": str(collection.id),
                    "name": collection.name,
                    "document_family": collection.document_family,
                },
                "score": entry["score"],
                "passages": [],
            },
        )
        if len(document["passages"]) >= settings.READER_MAX_PASSAGES_PER_DOCUMENT:
            continue
        excerpt, truncated = _bounded_excerpt(section.text, query_text)
        document["passages"].append(
            {
                "section_id": str(section.id),
                "ordinal": section.ordinal,
                "heading": section.heading,
                "excerpt": excerpt,
                "excerpt_truncated": truncated,
                "page_number": section.page_number,
                "source_locator": section.source_locator,
                "score": entry["score"],
                "text_rank": entry["text_rank"],
                "vector_distance": entry["vector_distance"],
                "artifact": {
                    "id": str(section.source_artifact_id),
                    "sha256": section.source_artifact.sha256,
                    "content_type": section.source_artifact.detected_content_type,
                },
            }
        )

    documents = list(grouped.values())[: settings.READER_MAX_SEARCH_RESULTS]
    if business_profile is not None:
        impact_payloads = _reader_impact_payloads(
            {UUID(document["version_id"]) for document in documents},
            business_profile=business_profile,
            include_evidence=False,
        )
        relevant_documents = []
        for document in documents:
            impacts = impact_payloads.get(UUID(document["identity_id"]), [])
            if not impacts:
                continue
            document["relevance"] = {
                "profile_id": str(business_profile.id),
                "profile_name": business_profile.name,
                "impact_count": len(impacts),
                "impacts": impacts,
            }
            relevant_documents.append(document)
        documents = relevant_documents
    artifact_ids = {
        UUID(passage["artifact"]["id"])
        for document in documents
        for passage in document["passages"]
    }
    version_ids = {UUID(document["version_id"]) for document in documents}
    evidence_records = _latest_version_evidence(version_ids, artifact_ids)
    for document in documents:
        document["score"] = round(document["score"], 8)
        for passage in document["passages"]:
            artifact_id = UUID(passage["artifact"]["id"])
            evidence_record = evidence_records.get((UUID(document["version_id"]), artifact_id))
            observation = evidence_record.artifact_observation if evidence_record else None
            passage["score"] = round(passage["score"], 8)
            if observation:
                passage["artifact"].update(
                    {
                        "official_url": observation.final_url,
                        "retrieved_at": observation.retrieved_at,
                        "source_endpoint": observation.candidate.endpoint.name,
                    }
                )

    start = (page - 1) * page_size
    end = start + page_size
    selected_documents = documents[start:end]
    return {
        "query": query_text,
        "mode": mode,
        "embedding": vector_configuration,
        "filters": {
            "authority": authority_slug,
            "collection": collection_id,
            "date_from": date_from,
            "date_to": date_to,
            "profile": str(business_profile.id) if business_profile else "",
        },
        "page": page,
        "page_size": page_size,
        "bounded_result_count": len(documents),
        "has_previous": page > 1,
        "has_next": end < len(documents),
        "warnings": warnings,
        "results": selected_documents,
    }


def browse_reader_documents(
    *,
    authority_slug: str = "",
    collection_id: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 10,
    business_profile: BusinessProfile | None = None,
) -> dict:
    if not authority_slug and not collection_id:
        raise ReaderQueryError("Choose an authority or collection to browse.")
    if page < 1 or page > settings.READER_MAX_SEARCH_PAGES:
        raise ReaderQueryError(f"Page must be between 1 and {settings.READER_MAX_SEARCH_PAGES}.")
    if page_size < 1 or page_size > settings.READER_MAX_PAGE_SIZE:
        raise ReaderQueryError(f"Page size must be between 1 and {settings.READER_MAX_PAGE_SIZE}.")

    current_version_ids = current_versions_queryset().values("id")
    versions = DocumentVersion.objects.filter(
        id__in=Subquery(current_version_ids),
        identity__collection__is_enabled=True,
        identity__collection__is_evidence_eligible=True,
        identity__collection__authority__is_enabled=True,
    ).select_related("identity", "identity__collection", "identity__collection__authority")
    if authority_slug:
        versions = versions.filter(identity__collection__authority__slug=authority_slug)
    if collection_id:
        try:
            selected_collection_id = UUID(str(collection_id))
        except ValueError as error:
            raise ReaderQueryError("Collection must be a valid identifier.") from error
        versions = versions.filter(identity__collection_id=selected_collection_id)
    if date_from:
        versions = versions.filter(created_at__date__gte=date_from)
    if date_to:
        versions = versions.filter(created_at__date__lte=date_to)

    bounded_versions = list(
        versions.order_by("-created_at", "identity__canonical_title", "id")[
            : settings.READER_MAX_SEARCH_RESULTS
        ]
    )
    version_ids = {version.id for version in bounded_versions}
    first_sections = {
        section.document_version_id: section
        for section in NormalizedSection.objects.filter(document_version_id__in=version_ids)
        .select_related("source_artifact")
        .order_by("document_version_id", "ordinal", "id")
        .distinct("document_version_id")
    }
    evidence_records = _latest_version_evidence(
        version_ids,
        {section.source_artifact_id for section in first_sections.values()},
    )

    documents = []
    for version in bounded_versions:
        identity = version.identity
        collection = identity.collection
        section = first_sections.get(version.id)
        passages = []
        if section is not None:
            excerpt, truncated = _bounded_excerpt(section.text, "")
            artifact = {
                "id": str(section.source_artifact_id),
                "sha256": section.source_artifact.sha256,
                "content_type": section.source_artifact.detected_content_type,
            }
            evidence_record = evidence_records.get((version.id, section.source_artifact_id))
            observation = evidence_record.artifact_observation if evidence_record else None
            if observation:
                artifact.update(
                    {
                        "official_url": observation.final_url,
                        "retrieved_at": observation.retrieved_at,
                        "source_endpoint": observation.candidate.endpoint.name,
                    }
                )
            passages.append(
                {
                    "section_id": str(section.id),
                    "ordinal": section.ordinal,
                    "heading": section.heading,
                    "excerpt": excerpt,
                    "excerpt_truncated": truncated,
                    "page_number": section.page_number,
                    "source_locator": section.source_locator,
                    "score": 0.0,
                    "text_rank": None,
                    "vector_distance": None,
                    "artifact": artifact,
                }
            )
        documents.append(
            {
                "identity_id": str(identity.id),
                "version_id": str(version.id),
                "title": _document_title(version, identity),
                "canonical_url": version.canonical_url or identity.canonical_url,
                "normalized_content_sha256": version.normalized_content_sha256,
                "language_hint": version.language_hint,
                "version_created_at": version.created_at,
                "authority": {
                    "id": str(collection.authority_id),
                    "name": collection.authority.name,
                    "slug": collection.authority.slug,
                    "trust_classification": collection.authority.trust_classification,
                },
                "collection": {
                    "id": str(collection.id),
                    "name": collection.name,
                    "document_family": collection.document_family,
                },
                "score": 0.0,
                "passages": passages,
            }
        )

    if business_profile is not None:
        impact_payloads = _reader_impact_payloads(
            version_ids,
            business_profile=business_profile,
            include_evidence=False,
        )
        relevant_documents = []
        for document in documents:
            impacts = impact_payloads.get(UUID(document["identity_id"]), [])
            if not impacts:
                continue
            document["relevance"] = {
                "profile_id": str(business_profile.id),
                "profile_name": business_profile.name,
                "impact_count": len(impacts),
                "impacts": impacts,
            }
            relevant_documents.append(document)
        documents = relevant_documents

    start = (page - 1) * page_size
    end = start + page_size
    return {
        "query": "",
        "mode": "browse",
        "embedding": None,
        "filters": {
            "authority": authority_slug,
            "collection": collection_id,
            "date_from": date_from,
            "date_to": date_to,
            "profile": str(business_profile.id) if business_profile else "",
        },
        "page": page,
        "page_size": page_size,
        "bounded_result_count": len(documents),
        "has_previous": page > 1,
        "has_next": end < len(documents),
        "warnings": [],
        "results": documents[start:end],
    }


def reader_document_payload(
    identity_id: UUID,
    *,
    business_profile: BusinessProfile | None = None,
) -> dict:
    identity = DocumentIdentity.objects.select_related("collection", "collection__authority").get(
        pk=identity_id,
        collection__is_enabled=True,
        collection__is_evidence_eligible=True,
        collection__authority__is_enabled=True,
    )
    try:
        current_version = current_versions_queryset(identity.collection).get(identity=identity)
    except DocumentVersion.DoesNotExist as error:
        raise DocumentIdentity.DoesNotExist from error

    section_queryset = current_version.sections.select_related(
        "source_artifact",
        "extraction_run",
    ).order_by("ordinal")
    section_count = section_queryset.count()
    sections = list(section_queryset[: settings.READER_MAX_DOCUMENT_SECTIONS])
    artifact_ids = {section.source_artifact_id for section in sections}
    observations = _latest_artifact_observations(artifact_ids)

    evidence_records = list(
        current_version.evidence_records.select_related(
            "raw_artifact",
            "extraction_run",
            "artifact_observation",
            "artifact_observation__candidate",
            "artifact_observation__candidate__endpoint",
        ).order_by("-created_at")
    )
    evidence = []
    seen_artifacts = set()
    for record in evidence_records:
        seen_artifacts.add(record.raw_artifact_id)
        observation = record.artifact_observation or observations.get(record.raw_artifact_id)
        evidence.append(
            {
                "artifact_id": str(record.raw_artifact_id),
                "sha256": record.raw_artifact.sha256,
                "byte_size": record.raw_artifact.byte_size,
                "content_type": record.raw_artifact.detected_content_type,
                "stored_at": record.raw_artifact.created_at,
                "observed_url": record.observed_url
                or (observation.final_url if observation else ""),
                "retrieved_at": observation.retrieved_at if observation else None,
                "source_endpoint": (
                    observation.candidate.endpoint.name if observation is not None else ""
                ),
                "extraction": {
                    "name": record.extraction_run.extractor_name,
                    "version": record.extraction_run.extractor_version,
                    "status": record.extraction_run.status,
                },
            }
        )
    for artifact_id in sorted(artifact_ids - seen_artifacts, key=str):
        section = next(item for item in sections if item.source_artifact_id == artifact_id)
        observation = observations.get(artifact_id)
        evidence.append(
            {
                "artifact_id": str(artifact_id),
                "sha256": section.source_artifact.sha256,
                "byte_size": section.source_artifact.byte_size,
                "content_type": section.source_artifact.detected_content_type,
                "stored_at": section.source_artifact.created_at,
                "observed_url": observation.final_url if observation else "",
                "retrieved_at": observation.retrieved_at if observation else None,
                "source_endpoint": (
                    observation.candidate.endpoint.name if observation is not None else ""
                ),
                "extraction": {
                    "name": section.extraction_run.extractor_name,
                    "version": section.extraction_run.extractor_version,
                    "status": section.extraction_run.status,
                },
            }
        )

    quality = (
        DocumentQualityAssessment.objects.filter(document_version=current_version)
        .prefetch_related("findings")
        .order_by("-created_at", "-id")
        .first()
    )
    comparison = (
        DocumentComparison.objects.filter(
            identity=identity,
            after_version=current_version,
            status=DocumentComparison.Status.COMPLETED,
        )
        .select_related("before_version", "after_version")
        .order_by("-finished_at", "-created_at")
        .first()
    )
    summary = None
    if comparison:
        summary = (
            ComparisonSummary.objects.filter(
                comparison=comparison,
                status=ComparisonSummary.Status.COMPLETED,
                output__legal_effect_not_assessed=True,
            )
            .order_by("-finished_at", "-created_at")
            .first()
        )
    impact_payloads = _reader_impact_payloads(
        {current_version.id},
        business_profile=business_profile,
    )

    return {
        "identity": {
            "id": str(identity.id),
            "title": _document_title(current_version, identity),
            "canonical_url": current_version.canonical_url or identity.canonical_url,
            "identity_basis": identity.identity_basis,
        },
        "authority": {
            "id": str(identity.collection.authority_id),
            "name": identity.collection.authority.name,
            "slug": identity.collection.authority.slug,
            "trust_classification": identity.collection.authority.trust_classification,
        },
        "collection": {
            "id": str(identity.collection_id),
            "name": identity.collection.name,
            "document_family": identity.collection.document_family,
            "default_legal_status": identity.collection.default_legal_status,
        },
        "version": {
            "id": str(current_version.id),
            "normalized_content_sha256": current_version.normalized_content_sha256,
            "language_hint": current_version.language_hint,
            "extractor_name": current_version.extractor_name,
            "extractor_version": current_version.extractor_version,
            "created_at": current_version.created_at,
        },
        "versions": [
            {
                "id": str(version.id),
                "normalized_content_sha256": version.normalized_content_sha256,
                "created_at": version.created_at,
                "is_current": version.id == current_version.id,
            }
            for version in identity.versions.order_by("-created_at", "-id")[:50]
        ],
        "section_count": section_count,
        "sections_truncated": section_count > len(sections),
        "sections": [
            {
                "id": str(section.id),
                "ordinal": section.ordinal,
                "section_type": section.section_type,
                "heading": section.heading,
                "text": section.text,
                "text_sha256": section.text_sha256,
                "page_number": section.page_number,
                "source_locator": section.source_locator,
                "artifact_id": str(section.source_artifact_id),
            }
            for section in sections
        ],
        "evidence": evidence,
        "quality": (
            {
                "outcome": quality.outcome,
                "score": quality.score,
                "metrics": quality.metrics,
                "assessed_at": quality.created_at,
                "findings": [
                    {
                        "code": finding.code,
                        "severity": finding.severity,
                        "message": finding.message,
                    }
                    for finding in quality.findings.all()
                ],
            }
            if quality
            else None
        ),
        "comparison": (
            {
                "id": str(comparison.id),
                "before_version_id": str(comparison.before_version_id),
                "after_version_id": str(comparison.after_version_id),
                "finished_at": comparison.finished_at,
                "counts": {
                    "added": comparison.added_count,
                    "removed": comparison.removed_count,
                    "modified": comparison.modified_count,
                    "moved": comparison.moved_count,
                    "format_only": comparison.format_only_count,
                    "ambiguous": comparison.ambiguous_count,
                },
            }
            if comparison
            else None
        ),
        "gpt_summary": (
            {
                "id": str(summary.id),
                "provider": summary.provider,
                "model": summary.model,
                "prompt_version": summary.prompt_version,
                "output": summary.output,
                "finished_at": summary.finished_at,
            }
            if summary
            else None
        ),
        "selected_profile": (
            {
                "id": str(business_profile.id),
                "name": business_profile.name,
                "taxonomy_id": str(business_profile.taxonomy_id),
            }
            if business_profile
            else None
        ),
        "reviewed_impacts": impact_payloads.get(identity.id, []),
    }
