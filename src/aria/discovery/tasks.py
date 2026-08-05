import logging

from celery import shared_task
from django.db import transaction

from aria.discovery.connectors import ConnectorNotRegistered, get_connector
from aria.discovery.models import MonitoredResource, ResourceRun, SourceRun
from aria.discovery.resource_connectors import parse_detail_page, parse_feed
from aria.discovery.services import (
    mark_source_run_completed,
    mark_source_run_failed,
    mark_source_run_started,
    mark_resource_run_completed,
    mark_resource_run_failed,
    mark_resource_run_started,
    observe_candidate,
    record_endpoint_observation,
    record_resource_observation,
    reconcile_listing_candidates,
    reconcile_resource_links,
    resource_conditional_headers,
    schedule_due_resource_runs,
    schedule_due_source_runs,
)
from aria.events.services import record_pipeline_event
from aria.fetching.client import get_default_http_client
from aria.sources.models import ConnectorConfiguration

logger = logging.getLogger(__name__)


@shared_task(name="aria.discovery.tasks.schedule_due_endpoints")
def schedule_due_endpoints() -> int:
    from aria.discovery.tasks import execute_source_run

    source_runs = schedule_due_source_runs()
    for source_run in source_runs:
        execute_source_run.delay(str(source_run.id))
    return len(source_runs)


@shared_task(name="aria.discovery.tasks.schedule_due_resources")
def schedule_due_resources() -> int:
    from aria.discovery.tasks import execute_resource_run

    resource_runs = schedule_due_resource_runs()
    for resource_run in resource_runs:
        execute_resource_run.delay(str(resource_run.id))
    return len(resource_runs)


@shared_task(
    bind=True,
    name="aria.discovery.tasks.execute_source_run",
    acks_late=True,
    max_retries=5,
)
def execute_source_run(self, source_run_id: str) -> None:
    source_run = SourceRun.objects.select_related("endpoint").get(pk=source_run_id)
    if source_run.status == SourceRun.Status.PENDING:
        mark_source_run_started(source_run)
    elif source_run.status != SourceRun.Status.RUNNING:
        logger.info("Skipping source run %s in state %s", source_run.id, source_run.status)
        return

    try:
        connector = get_connector(source_run.endpoint.connector_type)
        result = connector.discover(source_run.endpoint, source_run.cursor_before)
        from aria.fetching.tasks import fetch_candidate

        with transaction.atomic():
            configuration = (
                ConnectorConfiguration.objects.filter(
                    endpoint=source_run.endpoint,
                    version=source_run.connector_configuration_version,
                    is_active=True,
                )
                .order_by("-created_at")
                .values_list("configuration", flat=True)
                .first()
                or {}
            )
            record_endpoint_observation(
                source_run,
                result.response,
                request_headers=result.request_headers,
            )
            fetchable_candidates = reconcile_listing_candidates(
                source_run.endpoint,
                result.candidates,
                document_extensions=tuple(
                    configuration.get(
                        "document_extensions",
                        [".pdf", ".doc", ".docx", ".csv", ".json", ".xml"],
                    )
                ),
            )
            for candidate_data in fetchable_candidates:
                candidate, _ = observe_candidate(source_run, candidate_data)
                transaction.on_commit(
                    lambda candidate_id=str(candidate.id): fetch_candidate.delay(
                        candidate_id,
                        str(source_run.id),
                    )
                )
            mark_source_run_completed(source_run)
    except ConnectorNotRegistered as error:
        mark_source_run_failed(source_run, code="connector_not_registered", message=str(error))
    except Exception as error:
        logger.exception("Source run %s failed", source_run.id)
        if self.request.retries >= self.max_retries:
            mark_source_run_failed(source_run, code=type(error).__name__, message=str(error))
            raise
        record_pipeline_event(
            event_type="source.run.retry_scheduled",
            aggregate_type="source_run",
            aggregate_id=source_run.id,
            payload={
                "attempt": self.request.retries + 1,
                "error_code": type(error).__name__,
                "error_message": str(error),
            },
        )
        raise self.retry(
            exc=error,
            countdown=min(300, 2 ** (self.request.retries + 1)),
        ) from error


@shared_task(
    bind=True,
    name="aria.discovery.tasks.execute_resource_run",
    acks_late=True,
    max_retries=5,
)
def execute_resource_run(self, resource_run_id: str) -> None:
    resource_run = ResourceRun.objects.select_related(
        "resource", "resource__endpoint", "source_run"
    ).get(pk=resource_run_id)
    if resource_run.status == ResourceRun.Status.PENDING:
        mark_resource_run_started(resource_run)
    elif resource_run.status != ResourceRun.Status.RUNNING:
        logger.info("Skipping resource run %s in state %s", resource_run.id, resource_run.status)
        return

    resource = resource_run.resource
    client = None
    try:
        if not resource.is_enabled or not resource.is_approved:
            raise ValueError("Monitored resource is not enabled and explicitly approved.")
        configuration = (
            ConnectorConfiguration.objects.filter(
                endpoint=resource.endpoint,
                version=resource.endpoint.connector_configuration_version,
                is_active=True,
            )
            .order_by("-created_at")
            .values_list("configuration", flat=True)
            .first()
            or {}
        )
        headers = resource_conditional_headers(resource_run.cursor_before)
        client = get_default_http_client()
        response = client.fetch(
            resource.url,
            allowed_domains=resource.endpoint.allowed_domains,
            headers=headers,
        )
        links = None
        if response.status_code != 304:
            normalized_headers = {key.lower(): value for key, value in response.headers.items()}
            content_type = normalized_headers.get("content-type", "").lower()
            if resource.resource_type == MonitoredResource.ResourceType.DETAIL_PAGE:
                if "html" not in content_type:
                    raise ValueError(
                        f"Expected HTML detail resource, received '{content_type or 'unknown'}'."
                    )
                links = parse_detail_page(
                    response.content,
                    resource_url=resource.url,
                    allowed_domains=resource.endpoint.allowed_domains,
                    content_selector=configuration.get(
                        "detail_content_selector", ".betterdocs-entry-content"
                    ),
                    document_extensions=tuple(
                        configuration.get(
                            "document_extensions",
                            [".pdf", ".doc", ".docx", ".csv", ".json", ".xml"],
                        )
                    ),
                )
            elif resource.resource_type in (
                MonitoredResource.ResourceType.RSS,
                MonitoredResource.ResourceType.ATOM,
            ):
                accepted_feed_types = ("xml", "rss", "atom")
                if not any(token in content_type for token in accepted_feed_types):
                    raise ValueError(
                        f"Expected XML feed resource, received '{content_type or 'unknown'}'."
                    )
                parsed_feed = parse_feed(
                    response.content,
                    feed_url=resource.url,
                    allowed_domains=resource.endpoint.allowed_domains,
                    max_entries=int(configuration.get("max_feed_entries", 50)),
                )
                if parsed_feed.feed_type != resource.resource_type:
                    raise ValueError(
                        f"Configured {resource.resource_type} resource returned "
                        f"{parsed_feed.feed_type}."
                    )
                links = parsed_feed.links
            else:
                raise ValueError(f"Unsupported monitored resource type: {resource.resource_type}")
        from aria.fetching.tasks import fetch_candidate

        with transaction.atomic():
            record_resource_observation(
                resource_run,
                response,
                links=links,
                request_headers=headers,
            )
            candidates = reconcile_resource_links(
                resource_run,
                detail_path_prefixes=tuple(configuration.get("detail_resource_path_prefixes", [])),
            )
            for candidate in candidates:
                transaction.on_commit(
                    lambda candidate_id=str(candidate.id): fetch_candidate.delay(
                        candidate_id,
                        str(resource_run.source_run_id),
                    )
                )
            mark_resource_run_completed(resource_run)
    except Exception as error:
        logger.exception("Resource run %s failed", resource_run.id)
        if self.request.retries >= self.max_retries:
            mark_resource_run_failed(resource_run, code=type(error).__name__, message=str(error))
            raise
        record_pipeline_event(
            event_type="source.resource_run.retry_scheduled",
            aggregate_type="resource_run",
            aggregate_id=resource_run.id,
            payload={
                "attempt": self.request.retries + 1,
                "error_code": type(error).__name__,
                "error_message": str(error),
            },
        )
        raise self.retry(
            exc=error,
            countdown=min(300, 2 ** (self.request.retries + 1)),
        ) from error
    finally:
        if client is not None:
            client.close()
