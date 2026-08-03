import hashlib
from datetime import timedelta
from urllib.parse import urldefrag, urlsplit

from django.db import transaction
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.artifacts.storage import ArtifactStorageError, get_artifact_store
from aria.discovery.models import DiscoveredCandidate
from aria.documents.models import (
    DocumentIdentity,
    DocumentVersion,
    NormalizedSection,
    VersionEvidence,
)
from aria.events.services import record_pipeline_event
from aria.extraction.extractors import (
    ExtractionError,
    configuration_hash,
    get_extractor,
    normalize_multiline_text,
)
from aria.extraction.models import ExtractedBlock, ExtractedDocument, ExtractionRun
from aria.fetching.client import hostname_is_allowed
from aria.knowledge.services import project_document_version


class RetryableExtractionError(RuntimeError):
    pass


def _canonical_url(observation: ArtifactObservation | None) -> str:
    if observation is None:
        return ""
    candidate = observation.candidate
    url = candidate.canonical_url or observation.final_url or observation.requested_url
    return urldefrag(url).url


def _document_identity_url(observation: ArtifactObservation) -> str:
    configured = observation.candidate.metadata_hints.get("document_identity_url", "")
    if not configured:
        return _canonical_url(observation)
    identity_url = urldefrag(configured).url
    parsed = urlsplit(identity_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not hostname_is_allowed(parsed.hostname, observation.candidate.endpoint.allowed_domains)
    ):
        raise ExtractionError("The configured document identity URL is not allowlisted HTTPS.")
    return identity_url


def _identity_key(collection_id, canonical_url: str) -> str:
    basis = f"{collection_id}\n{canonical_url}".encode()
    return hashlib.sha256(basis).hexdigest()


def _supersede_observed_url_identity(
    *,
    collection,
    observed_url: str,
    canonical_identity: DocumentIdentity,
    observation: ArtifactObservation,
) -> None:
    if observed_url == canonical_identity.canonical_url:
        return
    DocumentIdentity.objects.filter(
        collection=collection,
        canonical_url=observed_url,
        superseded_by__isnull=True,
    ).exclude(pk=canonical_identity.pk).update(
        superseded_by=canonical_identity,
        supersession_basis={
            "strategy": "linked_document_identity_v1",
            "document_identity_url": canonical_identity.canonical_url,
            "observed_url": observed_url,
            "candidate_id": str(observation.candidate_id),
        },
        updated_at=timezone.now(),
    )


def _artifact_observations(raw_artifact: RawArtifact):
    return raw_artifact.observations.select_related(
        "candidate",
        "candidate__endpoint",
        "candidate__endpoint__collection",
        "candidate__endpoint__collection__authority",
    ).order_by("-retrieved_at", "-id")


def _create_extracted_document(
    *, run: ExtractionRun, raw_artifact: RawArtifact, extracted
) -> ExtractedDocument:
    plain_text = extracted.plain_text
    document = ExtractedDocument.objects.create(
        extraction_run=run,
        raw_artifact=raw_artifact,
        title=extracted.title,
        language_hint=extracted.language_hint,
        plain_text=plain_text,
        plain_text_sha256=hashlib.sha256(plain_text.encode("utf-8")).hexdigest(),
        metadata=extracted.metadata,
        page_count=extracted.page_count,
        requires_ocr=extracted.requires_ocr,
    )
    offset = 0
    blocks: list[ExtractedBlock] = []
    for ordinal, block in enumerate(extracted.blocks):
        char_start = offset
        char_end = char_start + len(block.text)
        blocks.append(
            ExtractedBlock(
                extracted_document=document,
                ordinal=ordinal,
                block_type=block.block_type,
                heading_level=block.heading_level,
                heading=block.heading,
                text=block.text,
                text_sha256=hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
                page_number=block.page_number,
                char_start=char_start,
                char_end=char_end,
                source_locator=block.source_locator,
            )
        )
        offset = char_end + 2
    ExtractedBlock.objects.bulk_create(blocks)
    return document


def _create_version(
    *,
    run: ExtractionRun,
    raw_artifact: RawArtifact,
    extracted_document: ExtractedDocument,
    observation: ArtifactObservation,
) -> tuple[DocumentVersion, bool]:
    collection = observation.candidate.endpoint.collection
    observed_url = _canonical_url(observation)
    identity_url = _document_identity_url(observation)
    stable_key = _identity_key(collection.id, identity_url)
    identity, _ = DocumentIdentity.objects.get_or_create(
        stable_key=stable_key,
        defaults={
            "collection": collection,
            "canonical_title": extracted_document.title,
            "canonical_url": identity_url,
            "identity_basis": {
                "strategy": "collection_and_canonical_url_v1",
                "collection_id": str(collection.id),
                "canonical_url": identity_url,
                "observed_url": observed_url,
                "external_identifier": observation.candidate.external_identifier,
            },
        },
    )
    _supersede_observed_url_identity(
        collection=collection,
        observed_url=observed_url,
        canonical_identity=identity,
        observation=observation,
    )
    normalized_content = normalize_multiline_text(extracted_document.plain_text)
    content_sha256 = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
    version, version_created = DocumentVersion.objects.get_or_create(
        identity=identity,
        normalized_content_sha256=content_sha256,
        defaults={
            "title": extracted_document.title,
            "canonical_url": identity_url,
            "language_hint": extracted_document.language_hint,
            "plain_content": normalized_content,
            "normalized_metadata": {
                **extracted_document.metadata,
                "identity_strategy": "collection_and_canonical_url_v1",
            },
            "extractor_name": run.extractor_name,
            "extractor_version": run.extractor_version,
        },
    )
    _, evidence_created = VersionEvidence.objects.get_or_create(
        artifact_observation=observation,
        defaults={
            "document_version": version,
            "raw_artifact": raw_artifact,
            "extraction_run": run,
            "observed_url": observed_url,
        },
    )
    if version_created:
        sections = [
            NormalizedSection(
                document_version=version,
                source_artifact=raw_artifact,
                extraction_run=run,
                source_block=block,
                ordinal=block.ordinal,
                section_type=block.block_type,
                heading=block.heading,
                text=block.text,
                text_sha256=block.text_sha256,
                page_number=block.page_number,
                char_start=block.char_start,
                char_end=block.char_end,
                source_locator={
                    **block.source_locator,
                    "artifact_sha256": raw_artifact.sha256,
                    "observed_url": observed_url,
                    "document_identity_url": identity_url,
                },
            )
            for block in extracted_document.blocks.all()
        ]
        NormalizedSection.objects.bulk_create(sections)
    return version, evidence_created


def _project_observations(
    *,
    run: ExtractionRun,
    raw_artifact: RawArtifact,
    extracted_document: ExtractedDocument,
) -> int:
    projected = 0
    now = timezone.now()
    observations = list(_artifact_observations(raw_artifact))
    if not observations:
        raise ExtractionError(
            "Artifact has no observation and cannot be assigned a document identity."
        )
    for observation in observations:
        version, evidence_created = _create_version(
            run=run,
            raw_artifact=raw_artifact,
            extracted_document=extracted_document,
            observation=observation,
        )
        project_document_version(version)
        if evidence_created:
            projected += 1
            record_pipeline_event(
                event_type="document.versioned",
                aggregate_type="document_version",
                aggregate_id=version.id,
                payload={
                    "identity_id": str(version.identity_id),
                    "extraction_run_id": str(run.id),
                    "artifact_observation_id": str(observation.id),
                    "artifact_sha256": raw_artifact.sha256,
                    "section_count": version.sections.count(),
                },
            )
    DiscoveredCandidate.objects.filter(artifact_observations__raw_artifact=raw_artifact).update(
        pipeline_state=DiscoveredCandidate.PipelineState.VERSIONED,
        updated_at=now,
    )
    return projected


def _mark_failed(run: ExtractionRun, *, retryable: bool, error: Exception) -> None:
    status = (
        ExtractionRun.Status.RETRYABLE_FAILURE
        if retryable
        else ExtractionRun.Status.PERMANENT_FAILURE
    )
    ExtractionRun.objects.filter(pk=run.pk, status=ExtractionRun.Status.RUNNING).update(
        status=status,
        error_code=type(error).__name__,
        error_message=str(error)[:4000],
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _claim_run(run: ExtractionRun) -> tuple[ExtractionRun, bool]:
    now = timezone.now()
    with transaction.atomic():
        locked_run = ExtractionRun.objects.select_for_update().get(pk=run.pk)
        if locked_run.status == ExtractionRun.Status.OCR_REQUIRED:
            DiscoveredCandidate.objects.filter(
                artifact_observations__raw_artifact=locked_run.raw_artifact
            ).update(
                pipeline_state=DiscoveredCandidate.PipelineState.MANUAL_REVIEW,
                updated_at=now,
            )
            return locked_run, False
        if locked_run.status == ExtractionRun.Status.SUCCEEDED:
            try:
                _project_observations(
                    run=locked_run,
                    raw_artifact=locked_run.raw_artifact,
                    extracted_document=locked_run.extracted_document,
                )
            except Exception as error:
                raise RetryableExtractionError(str(error)) from error
            return locked_run, False
        stale_before = now - timedelta(minutes=30)
        if (
            locked_run.status == ExtractionRun.Status.RUNNING
            and locked_run.started_at
            and locked_run.started_at > stale_before
        ):
            raise RetryableExtractionError(
                "An extraction task is already processing this artifact."
            )
        locked_run.status = ExtractionRun.Status.RUNNING
        locked_run.started_at = now
        locked_run.finished_at = None
        locked_run.error_code = ""
        locked_run.error_message = ""
        locked_run.save(
            update_fields=(
                "status",
                "started_at",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
        return locked_run, True


def extract_artifact(raw_artifact: RawArtifact) -> ExtractionRun:
    extractor = get_extractor(raw_artifact.detected_content_type)
    config_hash = configuration_hash(extractor.configuration)
    run, _ = ExtractionRun.objects.get_or_create(
        raw_artifact=raw_artifact,
        extractor_name=extractor.name,
        extractor_version=extractor.version,
        configuration_hash=config_hash,
    )
    run, claimed = _claim_run(run)
    if not claimed:
        return run
    try:
        store = get_artifact_store(raw_artifact.storage_backend)
        content = store.read(raw_artifact.storage_key)
        if len(content) != raw_artifact.byte_size:
            raise ArtifactStorageError("Stored artifact byte size does not match its record.")
        if hashlib.sha256(content).hexdigest() != raw_artifact.sha256:
            raise ArtifactStorageError("Stored artifact failed SHA-256 verification.")
        observation = _artifact_observations(raw_artifact).first()
        extracted = extractor.extract(content, source_url=_canonical_url(observation))

        with transaction.atomic():
            locked_run = ExtractionRun.objects.select_for_update().get(pk=run.pk)
            if locked_run.status == ExtractionRun.Status.SUCCEEDED:
                return locked_run
            document = _create_extracted_document(
                run=locked_run,
                raw_artifact=raw_artifact,
                extracted=extracted,
            )
            now = timezone.now()
            if document.requires_ocr:
                locked_run.status = ExtractionRun.Status.OCR_REQUIRED
                DiscoveredCandidate.objects.filter(
                    artifact_observations__raw_artifact=raw_artifact
                ).update(
                    pipeline_state=DiscoveredCandidate.PipelineState.MANUAL_REVIEW,
                    updated_at=now,
                )
                record_pipeline_event(
                    event_type="artifact.ocr_required",
                    aggregate_type="raw_artifact",
                    aggregate_id=raw_artifact.id,
                    payload={
                        "extraction_run_id": str(locked_run.id),
                        "page_count": document.page_count,
                    },
                )
            else:
                _project_observations(
                    run=locked_run,
                    raw_artifact=raw_artifact,
                    extracted_document=document,
                )
                locked_run.status = ExtractionRun.Status.SUCCEEDED
            locked_run.finished_at = now
            locked_run.save(update_fields=("status", "finished_at", "updated_at"))
            run = locked_run
    except ArtifactStorageError as error:
        _mark_failed(run, retryable=True, error=error)
        raise RetryableExtractionError(str(error)) from error
    except ExtractionError as error:
        _mark_failed(run, retryable=False, error=error)
    except Exception as error:
        _mark_failed(run, retryable=True, error=error)
        raise RetryableExtractionError(str(error)) from error
    return run
