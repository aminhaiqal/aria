import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, RawArtifact
from aria.artifacts.storage import ArtifactStorageError, get_artifact_store, persist_artifact
from aria.collections.models import PublicationCollection
from aria.events.services import record_pipeline_event
from aria.extraction.models import ExtractionRun
from aria.ocr.executor import (
    OCRExecutionError,
    OCRExecutor,
    RetryableOCRExecutionError,
    SubprocessOCRExecutor,
)
from aria.ocr.models import OCRRun


class RetryableOCRError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRPlanItem:
    source_artifact: RawArtifact
    extraction_run: ExtractionRun
    ocr_run: OCRRun


def ocr_configuration() -> dict:
    return {
        "binary": settings.OCR_BINARY,
        "tesseract_binary": settings.OCR_TESSERACT_BINARY,
        "languages": settings.OCR_LANGUAGES,
        "declared_toolchain": settings.OCR_DECLARED_TOOLCHAIN,
        "process_timeout_seconds": settings.OCR_PROCESS_TIMEOUT_SECONDS,
        "tesseract_timeout_seconds": settings.OCR_TESSERACT_TIMEOUT_SECONDS,
        "maximum_input_bytes": settings.OCR_MAX_INPUT_BYTES,
        "options": [
            "rotate_pages",
            "deskew",
            "skip_text",
            "optimize_0",
            "jobs_1",
            "output_pdf",
            "text_sidecar",
        ],
    }


def ocr_configuration_hash(configuration: dict | None = None) -> str:
    serialized = json.dumps(
        configuration or ocr_configuration(), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def ensure_ocr_run(source_artifact: RawArtifact) -> OCRRun:
    configuration = ocr_configuration()
    run, _ = OCRRun.objects.get_or_create(
        source_artifact=source_artifact,
        profile_name=settings.OCR_PROFILE_NAME,
        profile_version=settings.OCR_PROFILE_VERSION,
        configuration_hash=ocr_configuration_hash(configuration),
        defaults={"configuration": configuration},
    )
    return run


def ocr_source_artifacts(collection: PublicationCollection) -> list[RawArtifact]:
    return list(
        RawArtifact.objects.filter(
            observations__candidate__endpoint__collection=collection,
            extraction_runs__status=ExtractionRun.Status.OCR_REQUIRED,
        )
        .distinct()
        .order_by("sha256")
    )


def plan_collection_ocr(collection: PublicationCollection) -> list[OCRPlanItem]:
    items: list[OCRPlanItem] = []
    for artifact in ocr_source_artifacts(collection):
        extraction_run = artifact.extraction_runs.filter(
            status=ExtractionRun.Status.OCR_REQUIRED
        ).latest("created_at")
        items.append(
            OCRPlanItem(
                source_artifact=artifact,
                extraction_run=extraction_run,
                ocr_run=ensure_ocr_run(artifact),
            )
        )
    return items


def _claim_run(run: OCRRun) -> tuple[OCRRun, bool]:
    now = timezone.now()
    with transaction.atomic():
        locked = OCRRun.objects.select_for_update().get(pk=run.pk)
        if locked.status in (OCRRun.Status.SUCCEEDED, OCRRun.Status.PERMANENT_FAILURE):
            return locked, False
        stale_before = now - timedelta(minutes=settings.OCR_STALE_AFTER_MINUTES)
        if (
            locked.status == OCRRun.Status.RUNNING
            and locked.started_at
            and locked.started_at > stale_before
        ):
            raise RetryableOCRError("An OCR task is already processing this artifact.")
        locked.status = OCRRun.Status.RUNNING
        locked.started_at = now
        locked.finished_at = None
        locked.error_code = ""
        locked.error_message = ""
        locked.save(
            update_fields=(
                "status",
                "started_at",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
        return locked, True


def _mark_failed(run: OCRRun, error: Exception, *, retryable: bool) -> None:
    OCRRun.objects.filter(pk=run.pk, status=OCRRun.Status.RUNNING).update(
        status=(OCRRun.Status.RETRYABLE_FAILURE if retryable else OCRRun.Status.PERMANENT_FAILURE),
        error_code=type(error).__name__,
        error_message=str(error)[:4000],
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )


def process_ocr_run(
    run: OCRRun,
    *,
    executor: OCRExecutor | None = None,
    dispatch_extraction: bool = True,
    project_knowledge: bool = True,
) -> OCRRun:
    run, claimed = _claim_run(run)
    if not claimed:
        if run.status == OCRRun.Status.SUCCEEDED and dispatch_extraction:
            from aria.extraction.tasks import extract_raw_artifact

            artifact_id = str(run.searchable_pdf_derivative.derived_artifact_id)
            if project_knowledge:
                extract_raw_artifact.delay(artifact_id)
            else:
                extract_raw_artifact.delay(artifact_id, project_knowledge=False)
        return run

    try:
        source = run.source_artifact
        store = get_artifact_store(source.storage_backend)
        content = store.read(source.storage_key)
        if len(content) != source.byte_size:
            raise ArtifactStorageError("Stored OCR source byte size does not match its record.")
        if hashlib.sha256(content).hexdigest() != source.sha256:
            raise ArtifactStorageError("Stored OCR source failed SHA-256 verification.")

        result = (executor or SubprocessOCRExecutor()).execute(content, run.configuration)
        searchable_pdf = persist_artifact(
            result.searchable_pdf,
            "application/pdf",
            backend=source.storage_backend,
            store=store,
            namespace="derived/ocr",
        )
        text_sidecar = persist_artifact(
            result.text_sidecar,
            "text/plain",
            backend=source.storage_backend,
            store=store,
            namespace="derived/ocr",
        )

        with transaction.atomic():
            locked = OCRRun.objects.select_for_update().get(pk=run.pk)
            pdf_derivative, _ = ArtifactDerivative.objects.get_or_create(
                source_artifact=source,
                derived_artifact=searchable_pdf,
                transformation_type=ArtifactDerivative.TransformationType.OCR_SEARCHABLE_PDF,
                configuration_hash=locked.configuration_hash,
                defaults={
                    "profile": f"{locked.profile_name}:{locked.profile_version}",
                    "metadata": {
                        "ocr_run_id": str(locked.id),
                        "page_count": result.page_count,
                        "non_whitespace_characters": result.non_whitespace_characters,
                        "toolchain": result.toolchain,
                    },
                },
            )
            sidecar_derivative, _ = ArtifactDerivative.objects.get_or_create(
                source_artifact=source,
                derived_artifact=text_sidecar,
                transformation_type=ArtifactDerivative.TransformationType.OCR_TEXT_SIDECAR,
                configuration_hash=locked.configuration_hash,
                defaults={
                    "profile": f"{locked.profile_name}:{locked.profile_version}",
                    "metadata": {
                        "ocr_run_id": str(locked.id),
                        "page_count": result.page_count,
                        "non_whitespace_characters": result.non_whitespace_characters,
                        "toolchain": result.toolchain,
                    },
                },
            )
            now = timezone.now()
            locked.searchable_pdf_derivative = pdf_derivative
            locked.text_sidecar_derivative = sidecar_derivative
            locked.toolchain = result.toolchain
            locked.page_count = result.page_count
            locked.non_whitespace_characters = result.non_whitespace_characters
            locked.status = OCRRun.Status.SUCCEEDED
            locked.finished_at = now
            locked.save(
                update_fields=(
                    "searchable_pdf_derivative",
                    "text_sidecar_derivative",
                    "toolchain",
                    "page_count",
                    "non_whitespace_characters",
                    "status",
                    "finished_at",
                    "updated_at",
                )
            )
            record_pipeline_event(
                event_type="artifact.ocr_completed",
                aggregate_type="ocr_run",
                aggregate_id=locked.id,
                payload={
                    "source_artifact_id": str(source.id),
                    "source_sha256": source.sha256,
                    "searchable_pdf_artifact_id": str(searchable_pdf.id),
                    "searchable_pdf_sha256": searchable_pdf.sha256,
                    "text_sidecar_artifact_id": str(text_sidecar.id),
                    "text_sidecar_sha256": text_sidecar.sha256,
                    "profile": f"{locked.profile_name}:{locked.profile_version}",
                    "configuration_hash": locked.configuration_hash,
                    "page_count": result.page_count,
                    "non_whitespace_characters": result.non_whitespace_characters,
                },
            )
            run = locked

        if dispatch_extraction:
            from aria.extraction.tasks import extract_raw_artifact

            artifact_id = str(searchable_pdf.id)
            if project_knowledge:
                extract_raw_artifact.delay(artifact_id)
            else:
                extract_raw_artifact.delay(artifact_id, project_knowledge=False)
    except (ArtifactStorageError, RetryableOCRExecutionError) as error:
        _mark_failed(run, error, retryable=True)
        raise RetryableOCRError(str(error)) from error
    except OCRExecutionError as error:
        _mark_failed(run, error, retryable=False)
        run.refresh_from_db()
    except Exception as error:
        _mark_failed(run, error, retryable=True)
        raise RetryableOCRError(str(error)) from error
    return run
