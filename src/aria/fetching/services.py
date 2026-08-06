import hashlib
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation
from aria.artifacts.storage import ArtifactStore, persist_artifact
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.events.services import record_pipeline_event
from aria.fetching.client import FetchResponse, UnexpectedContentTypeError
from aria.fetching.models import FetchAttempt

DOCUMENT_CONTENT_TYPES = {
    ".csv": {"application/csv", "text/csv"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".json": {"application/json"},
    ".pdf": {"application/pdf"},
    ".xml": {"application/xml", "text/xml"},
}

STABLE_CANDIDATE_STATES = {
    DiscoveredCandidate.PipelineState.RAW_STORED,
    DiscoveredCandidate.PipelineState.EXTRACTED,
    DiscoveredCandidate.PipelineState.NORMALIZED,
    DiscoveredCandidate.PipelineState.IDENTITY_RESOLVED,
    DiscoveredCandidate.PipelineState.VERSIONED,
    DiscoveredCandidate.PipelineState.DIFFED,
    DiscoveredCandidate.PipelineState.PUBLISHED,
    DiscoveredCandidate.PipelineState.MANUAL_REVIEW,
}


def detect_content_type(content: bytes, response_headers: dict[str, str]) -> str:
    sample = content[:512].lstrip().lower()
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if sample.startswith((b"<!doctype html", b"<html", b"<?xml")):
        if sample.startswith(b"<?xml"):
            return "application/xml"
        return "text/html"
    if sample.startswith((b"{", b"[")):
        return "application/json"
    header_value = response_headers.get("content-type", "application/octet-stream")
    message = Message()
    message["content-type"] = header_value
    return message.get_content_type()


def latest_conditional_headers(candidate: DiscoveredCandidate) -> dict[str, str]:
    observation = candidate.artifact_observations.order_by("-retrieved_at").first()
    if not observation:
        return {}
    headers: dict[str, str] = {}
    if observation.etag:
        headers["If-None-Match"] = observation.etag
    if observation.last_modified:
        headers["If-Modified-Since"] = observation.last_modified
    return headers


def expected_content_types(candidate: DiscoveredCandidate) -> set[str]:
    url = candidate.canonical_url or candidate.discovered_url
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in DOCUMENT_CONTENT_TYPES:
        return DOCUMENT_CONTENT_TYPES[suffix]
    return {
        str(content_type).split(";", 1)[0].strip().lower()
        for content_type in candidate.endpoint.expected_content_types
        if str(content_type).strip()
    }


def artifact_namespace(candidate: DiscoveredCandidate) -> str:
    collection = candidate.endpoint.collection
    return f"sources/{collection.authority.slug}/{collection.slug}"


def restore_unchanged_candidate_state(candidate_id, *, now) -> None:
    candidate = DiscoveredCandidate.objects.get(pk=candidate_id)
    if candidate.pipeline_state in STABLE_CANDIDATE_STATES:
        return
    DiscoveredCandidate.objects.filter(pk=candidate_id).update(
        pipeline_state=DiscoveredCandidate.PipelineState.RAW_STORED,
        updated_at=now,
    )


@transaction.atomic
def begin_fetch_attempt(
    candidate: DiscoveredCandidate,
    source_run: SourceRun,
    *,
    request_headers: dict[str, str],
) -> FetchAttempt:
    DiscoveredCandidate.objects.select_for_update().get(pk=candidate.pk)
    latest_number = (
        FetchAttempt.objects.filter(candidate=candidate, source_run=source_run).aggregate(
            maximum=Max("attempt_number")
        )["maximum"]
        or 0
    )
    return FetchAttempt.objects.create(
        candidate=candidate,
        source_run=source_run,
        attempt_number=latest_number + 1,
        requested_url=candidate.canonical_url or candidate.discovered_url,
        request_headers=request_headers,
        started_at=timezone.now(),
    )


@transaction.atomic
def complete_fetch(
    attempt: FetchAttempt,
    response: FetchResponse,
    *,
    store: ArtifactStore | None = None,
) -> ArtifactObservation:
    detected_content_type = detect_content_type(response.content, response.headers)
    allowed_content_types = expected_content_types(attempt.candidate)
    if allowed_content_types and detected_content_type not in allowed_content_types:
        raise UnexpectedContentTypeError(
            f"Detected content type '{detected_content_type}' is not allowed for "
            f"{attempt.requested_url}."
        )
    digest = hashlib.sha256(response.content).hexdigest()
    previous = (
        attempt.candidate.artifact_observations.select_related("raw_artifact")
        .order_by("-retrieved_at", "-id")
        .first()
    )
    content_changed = previous is None or previous.raw_artifact.sha256 != digest
    raw_artifact = persist_artifact(
        response.content,
        detected_content_type,
        store=store,
        namespace=artifact_namespace(attempt.candidate),
    )

    now = timezone.now()
    observation = ArtifactObservation.objects.create(
        raw_artifact=raw_artifact,
        fetch_attempt=attempt,
        candidate=attempt.candidate,
        source_run=attempt.source_run,
        requested_url=response.requested_url,
        final_url=response.final_url,
        response_status=response.status_code,
        response_headers=response.headers,
        redirect_chain=response.redirect_chain,
        content_changed=content_changed,
        retrieved_at=now,
        connector_configuration_version=attempt.source_run.connector_configuration_version,
        etag=response.headers.get("etag", ""),
        last_modified=response.headers.get("last-modified", ""),
    )
    attempt.status = FetchAttempt.Status.SUCCEEDED
    attempt.final_url = response.final_url
    attempt.response_status = response.status_code
    attempt.response_headers = response.headers
    attempt.redirect_chain = response.redirect_chain
    attempt.resolved_addresses = response.resolved_addresses
    attempt.bytes_received = len(response.content)
    attempt.finished_at = now
    attempt.save(
        update_fields=(
            "status",
            "final_url",
            "response_status",
            "response_headers",
            "redirect_chain",
            "resolved_addresses",
            "bytes_received",
            "finished_at",
            "updated_at",
        )
    )
    if content_changed:
        DiscoveredCandidate.objects.filter(pk=attempt.candidate_id).update(
            pipeline_state=DiscoveredCandidate.PipelineState.RAW_STORED,
            updated_at=now,
        )
    else:
        restore_unchanged_candidate_state(attempt.candidate_id, now=now)
    record_pipeline_event(
        event_type="artifact.fetched" if content_changed else "artifact.unchanged",
        aggregate_type="raw_artifact",
        aggregate_id=raw_artifact.id,
        payload={
            "candidate_id": str(attempt.candidate_id),
            "fetch_attempt_id": str(attempt.id),
            "sha256": raw_artifact.sha256,
            "byte_size": raw_artifact.byte_size,
            "content_type": raw_artifact.detected_content_type,
            "content_changed": content_changed,
        },
    )
    if content_changed:
        from aria.orchestration.services import ensure_change_orchestration
        from aria.orchestration.tasks import process_change_orchestration

        orchestration, _ = ensure_change_orchestration(observation)
        transaction.on_commit(lambda: process_change_orchestration.delay(str(orchestration.id)))
    return observation


@transaction.atomic
def complete_not_modified(attempt: FetchAttempt, response: FetchResponse) -> ArtifactObservation:
    previous = attempt.candidate.artifact_observations.order_by("-retrieved_at").first()
    if not previous:
        raise ValueError("Received HTTP 304 without a previous artifact observation.")
    now = timezone.now()
    observation = ArtifactObservation.objects.create(
        raw_artifact=previous.raw_artifact,
        fetch_attempt=attempt,
        candidate=attempt.candidate,
        source_run=attempt.source_run,
        requested_url=response.requested_url,
        final_url=response.final_url,
        response_status=response.status_code,
        response_headers=response.headers,
        redirect_chain=response.redirect_chain,
        content_changed=False,
        retrieved_at=now,
        connector_configuration_version=attempt.source_run.connector_configuration_version,
        etag=response.headers.get("etag", previous.etag),
        last_modified=response.headers.get("last-modified", previous.last_modified),
    )
    attempt.status = FetchAttempt.Status.NOT_MODIFIED
    attempt.final_url = response.final_url
    attempt.response_status = response.status_code
    attempt.response_headers = response.headers
    attempt.redirect_chain = response.redirect_chain
    attempt.resolved_addresses = response.resolved_addresses
    attempt.finished_at = now
    attempt.save(
        update_fields=(
            "status",
            "final_url",
            "response_status",
            "response_headers",
            "redirect_chain",
            "resolved_addresses",
            "finished_at",
            "updated_at",
        )
    )
    restore_unchanged_candidate_state(attempt.candidate_id, now=now)
    record_pipeline_event(
        event_type="artifact.not_modified",
        aggregate_type="raw_artifact",
        aggregate_id=previous.raw_artifact_id,
        payload={
            "candidate_id": str(attempt.candidate_id),
            "fetch_attempt_id": str(attempt.id),
        },
    )
    return observation


@transaction.atomic
def fail_fetch_attempt(
    attempt: FetchAttempt,
    *,
    status: str,
    pipeline_state: str,
    error_code: str,
    error_message: str,
) -> None:
    now = timezone.now()
    attempt.status = status
    attempt.error_code = error_code
    attempt.error_message = error_message
    attempt.finished_at = now
    attempt.save(
        update_fields=(
            "status",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        )
    )
    DiscoveredCandidate.objects.filter(pk=attempt.candidate_id).update(
        pipeline_state=pipeline_state,
        updated_at=now,
    )
    event_type = (
        "artifact.quarantined"
        if status == FetchAttempt.Status.QUARANTINED
        else "artifact.fetch_failed"
    )
    record_pipeline_event(
        event_type=event_type,
        aggregate_type="candidate",
        aggregate_id=attempt.candidate_id,
        payload={
            "fetch_attempt_id": str(attempt.id),
            "error_code": error_code,
            "error_message": error_message,
            "retryable": status == FetchAttempt.Status.RETRYABLE_FAILURE,
        },
    )


def storage_backend_name() -> str:
    return settings.OBJECT_STORAGE_BACKEND
