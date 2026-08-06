import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative
from aria.artifacts.storage import ArtifactStore, persist_artifact
from aria.browser.contracts import BrowserRenderResult
from aria.browser.models import BrowserCapture, BrowserNetworkExchange
from aria.browser.runtime import PlaywrightBrowserRunner
from aria.events.services import record_pipeline_event
from aria.fetching.client import FetchResponse, UnsafeTargetError
from aria.fetching.services import detect_content_type
from aria.sources.models import ConnectorConfiguration, SourceEndpoint


def active_connector_configuration(endpoint: SourceEndpoint) -> dict:
    configuration = (
        ConnectorConfiguration.objects.filter(
            endpoint=endpoint,
            version=endpoint.connector_configuration_version,
            is_active=True,
        )
        .order_by("-created_at")
        .values_list("configuration", flat=True)
        .first()
    )
    if configuration is None:
        raise ValueError(
            f"Endpoint {endpoint.id} has no active connector configuration version "
            f"{endpoint.connector_configuration_version}."
        )
    return configuration


def browser_configuration(endpoint: SourceEndpoint, connector_configuration: dict) -> dict:
    return {
        "profile": settings.BROWSER_CAPTURE_PROFILE,
        "profile_version": settings.BROWSER_PROFILE_VERSION,
        "allowed_domains": sorted(endpoint.allowed_domains),
        "navigation_timeout_seconds": settings.BROWSER_NAVIGATION_TIMEOUT_SECONDS,
        "render_wait_milliseconds": min(
            settings.BROWSER_MAX_RENDER_WAIT_MILLISECONDS,
            max(
                0,
                int(
                    connector_configuration.get(
                        "render_wait_milliseconds",
                        settings.BROWSER_RENDER_WAIT_MILLISECONDS,
                    )
                ),
            ),
        ),
        "ready_selector": str(connector_configuration.get("ready_selector", ""))[:512],
        "max_requests": settings.BROWSER_MAX_REQUESTS,
        "max_redirects": settings.BROWSER_MAX_REDIRECTS,
        "max_response_bytes": settings.BROWSER_MAX_RESPONSE_BYTES,
        "max_dom_bytes": settings.BROWSER_MAX_DOM_BYTES,
        "max_capture_body_bytes": settings.BROWSER_MAX_CAPTURE_BODY_BYTES,
        "playwright_version": settings.BROWSER_PLAYWRIGHT_VERSION,
    }


def configuration_hash(configuration: dict) -> str:
    serialized = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


def artifact_namespace(endpoint: SourceEndpoint, category: str) -> str:
    collection = endpoint.collection
    return f"sources/{collection.authority.slug}/{collection.slug}/browser/{category}"


@transaction.atomic
def begin_browser_capture(source_run, configuration: dict) -> BrowserCapture:
    capture, _ = BrowserCapture.objects.select_for_update().get_or_create(
        source_run=source_run,
        defaults={
            "endpoint": source_run.endpoint,
            "profile": settings.BROWSER_CAPTURE_PROFILE,
            "configuration": configuration,
            "configuration_hash": configuration_hash(configuration),
            "requested_url": source_run.endpoint.discovery_url,
        },
    )
    if capture.status == BrowserCapture.Status.COMPLETED:
        return capture
    now = timezone.now()
    capture.status = BrowserCapture.Status.RUNNING
    capture.profile = settings.BROWSER_CAPTURE_PROFILE
    capture.configuration = configuration
    capture.configuration_hash = configuration_hash(configuration)
    capture.attempt_count += 1
    capture.started_at = now
    capture.finished_at = None
    capture.error_code = ""
    capture.error_message = ""
    capture.full_clean()
    capture.save()
    return capture


def _network_content_type(exchange) -> str:
    if exchange.body is None:
        return "application/octet-stream"
    return detect_content_type(exchange.body, {"content-type": exchange.content_type})


@transaction.atomic
def complete_browser_capture(
    capture: BrowserCapture,
    result: BrowserRenderResult,
    *,
    store: ArtifactStore | None = None,
) -> BrowserCapture:
    endpoint = capture.endpoint
    original_type = detect_content_type(result.original_content, result.response_headers)
    original = persist_artifact(
        result.original_content,
        original_type,
        store=store,
        namespace=artifact_namespace(endpoint, "original"),
    )
    rendered = persist_artifact(
        result.rendered_content,
        "text/html",
        store=store,
        namespace=artifact_namespace(endpoint, "rendered"),
    )
    derivative = None
    if original.pk != rendered.pk:
        derivative, _ = ArtifactDerivative.objects.get_or_create(
            source_artifact=original,
            derived_artifact=rendered,
            transformation_type=ArtifactDerivative.TransformationType.BROWSER_RENDERED_DOM,
            configuration_hash=capture.configuration_hash,
            defaults={
                "profile": capture.profile,
                "metadata": {
                    "source_run_id": str(capture.source_run_id),
                    "toolchain": result.toolchain,
                },
            },
        )

    for exchange in result.network_exchanges:
        body_artifact = None
        body_sha256 = ""
        byte_size = 0
        if exchange.body is not None:
            body_sha256 = hashlib.sha256(exchange.body).hexdigest()
            byte_size = len(exchange.body)
            body_artifact = persist_artifact(
                exchange.body,
                _network_content_type(exchange),
                store=store,
                namespace=artifact_namespace(endpoint, "network"),
            )
        network_exchange = BrowserNetworkExchange(
            capture=capture,
            attempt_number=capture.attempt_count,
            sequence=exchange.sequence,
            requested_url=exchange.requested_url,
            method=exchange.method,
            resource_type=exchange.resource_type,
            disposition=exchange.disposition,
            block_reason=exchange.block_reason,
            response_status=exchange.response_status,
            content_type=exchange.content_type,
            byte_size=byte_size,
            body_sha256=body_sha256,
            body_artifact=body_artifact,
            resolved_addresses=exchange.resolved_addresses,
        )
        network_exchange.full_clean()
        network_exchange.save()

    capture.status = BrowserCapture.Status.COMPLETED
    capture.final_url = result.final_url
    capture.response_status = result.response_status
    capture.response_headers = result.response_headers
    capture.redirect_chain = result.redirect_chain
    capture.resolved_addresses = result.resolved_addresses
    capture.original_artifact = original
    capture.rendered_artifact = rendered
    capture.rendered_derivative = derivative
    capture.toolchain = result.toolchain
    capture.request_count = result.request_count
    capture.blocked_request_count = result.blocked_request_count
    capture.response_bytes = result.response_bytes
    capture.finished_at = timezone.now()
    capture.full_clean()
    capture.save()
    record_pipeline_event(
        event_type="browser.capture.completed",
        aggregate_type="browser_capture",
        aggregate_id=capture.id,
        payload={
            "source_run_id": str(capture.source_run_id),
            "original_sha256": original.sha256,
            "rendered_sha256": rendered.sha256,
            "request_count": capture.request_count,
            "blocked_request_count": capture.blocked_request_count,
        },
    )
    return capture


@transaction.atomic
def fail_browser_capture(capture: BrowserCapture, error: Exception) -> None:
    capture.status = (
        BrowserCapture.Status.QUARANTINED
        if isinstance(error, UnsafeTargetError)
        else BrowserCapture.Status.FAILED
    )
    capture.error_code = type(error).__name__[:128]
    capture.error_message = str(error)
    capture.finished_at = timezone.now()
    capture.save(
        update_fields=(
            "status",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        )
    )
    record_pipeline_event(
        event_type=(
            "browser.capture.quarantined"
            if capture.status == BrowserCapture.Status.QUARANTINED
            else "browser.capture.failed"
        ),
        aggregate_type="browser_capture",
        aggregate_id=capture.id,
        payload={"error_code": capture.error_code, "error_message": capture.error_message},
    )


def capture_source_run(
    source_run,
    *,
    runner=None,
    store=None,
) -> tuple[BrowserCapture, FetchResponse]:
    endpoint = source_run.endpoint
    if (
        endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING
        or not endpoint.requires_javascript
    ):
        raise ValueError("Browser capture requires an explicit JavaScript listing endpoint.")
    connector_configuration = active_connector_configuration(endpoint)
    configuration = browser_configuration(endpoint, connector_configuration)
    capture = begin_browser_capture(source_run, configuration)
    if capture.status == BrowserCapture.Status.COMPLETED:
        assert capture.rendered_artifact_id
        from aria.artifacts.storage import get_artifact_store

        rendered = capture.rendered_artifact
        artifact_store = store or get_artifact_store(rendered.storage_backend)
        return capture, FetchResponse(
            requested_url=capture.requested_url,
            final_url=capture.final_url,
            status_code=capture.response_status or 200,
            headers={**capture.response_headers, "content-type": "text/html; charset=utf-8"},
            redirect_chain=capture.redirect_chain,
            resolved_addresses=capture.resolved_addresses,
            content=artifact_store.read(rendered.storage_key),
        )
    browser_runner = runner or PlaywrightBrowserRunner()
    try:
        result = browser_runner.render(
            endpoint.discovery_url,
            allowed_domains=endpoint.allowed_domains,
            ready_selector=configuration["ready_selector"],
            render_wait_milliseconds=configuration["render_wait_milliseconds"],
        )
        complete_browser_capture(capture, result, store=store)
    except Exception as error:
        fail_browser_capture(capture, error)
        raise
    return capture, FetchResponse(
        requested_url=result.requested_url,
        final_url=result.final_url,
        status_code=result.response_status,
        headers={**result.response_headers, "content-type": "text/html; charset=utf-8"},
        redirect_chain=result.redirect_chain,
        resolved_addresses=result.resolved_addresses,
        content=result.rendered_content,
    )
