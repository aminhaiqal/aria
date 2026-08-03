import hashlib
from email.message import Message

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.artifacts.storage import ArtifactStore, get_artifact_store
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.events.services import record_pipeline_event
from aria.fetching.client import FetchResponse
from aria.fetching.models import FetchAttempt


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
    digest = hashlib.sha256(response.content).hexdigest()
    existing_artifact = RawArtifact.objects.filter(sha256=digest).first()
    if existing_artifact:
        if existing_artifact.byte_size != len(response.content):
            raise ValueError("Existing artifact size does not match retrieved content.")
        raw_artifact = existing_artifact
    else:
        artifact_store = store or get_artifact_store()
        storage_key = artifact_store.put_if_absent(
            digest,
            response.content,
            detected_content_type,
        )
        raw_artifact, _ = RawArtifact.objects.get_or_create(
            sha256=digest,
            defaults={
                "byte_size": len(response.content),
                "detected_content_type": detected_content_type,
                "storage_backend": artifact_store.backend_name,
                "storage_key": storage_key,
            },
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
    DiscoveredCandidate.objects.filter(pk=attempt.candidate_id).update(
        pipeline_state=DiscoveredCandidate.PipelineState.RAW_STORED,
        updated_at=now,
    )
    record_pipeline_event(
        event_type="artifact.fetched",
        aggregate_type="raw_artifact",
        aggregate_id=raw_artifact.id,
        payload={
            "candidate_id": str(attempt.candidate_id),
            "fetch_attempt_id": str(attempt.id),
            "sha256": raw_artifact.sha256,
            "byte_size": raw_artifact.byte_size,
            "content_type": raw_artifact.detected_content_type,
        },
    )
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
    DiscoveredCandidate.objects.filter(pk=attempt.candidate_id).update(
        pipeline_state=DiscoveredCandidate.PipelineState.RAW_STORED,
        updated_at=now,
    )
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
