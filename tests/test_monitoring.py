import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import EndpointObservation, SourceRun
from aria.discovery.services import (
    conditional_headers_from_cursor,
    create_source_run,
    mark_source_run_completed,
    mark_source_run_failed,
    mark_source_run_started,
    record_endpoint_observation,
)
from aria.fetching.client import FetchResponse
from aria.sources.models import SourceEndpoint


class EndpointMonitoringContractTestCase(TestCase):
    def setUp(self) -> None:
        authority = Authority.objects.create(
            name="Test regulator",
            slug="test-regulator-monitor",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        collection = PublicationCollection.objects.create(
            authority=authority,
            name="Monitored publications",
            slug="monitored-publications",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=collection,
            name="Official monitor",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html", "application/pdf"],
        )

    def response(
        self,
        *,
        status: int = 200,
        content: bytes = b"<html>official listing</html>",
        headers: dict[str, str] | None = None,
    ) -> FetchResponse:
        return FetchResponse(
            requested_url=self.endpoint.discovery_url,
            final_url=self.endpoint.discovery_url,
            status_code=status,
            headers=headers
            or {
                "Content-Type": "text/html",
                "ETag": '"listing-v1"',
                "Last-Modified": "Tue, 04 Aug 2026 01:00:00 GMT",
                "Set-Cookie": "must-not-be-persisted=1",
            },
            redirect_chain=[],
            resolved_addresses=["93.184.216.34"],
            content=content,
        )

    def test_changed_observation_is_append_only_and_becomes_next_run_cursor(self) -> None:
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        mark_source_run_started(source_run)
        observation = record_endpoint_observation(source_run, self.response())
        mark_source_run_completed(source_run)

        self.assertEqual(observation.outcome, EndpointObservation.Outcome.CHANGED)
        self.assertEqual(
            observation.content_sha256,
            hashlib.sha256(b"<html>official listing</html>").hexdigest(),
        )
        self.assertNotIn("set-cookie", observation.response_headers)
        self.endpoint.refresh_from_db()
        source_run.refresh_from_db()
        self.assertEqual(self.endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertEqual(self.endpoint.consecutive_failures, 0)
        self.assertEqual(self.endpoint.last_changed_at, observation.checked_at)
        self.assertEqual(source_run.cursor_after["endpoint_observation_id"], str(observation.id))

        next_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.SCHEDULED)
        self.assertEqual(next_run.cursor_before, source_run.cursor_after)
        self.assertEqual(
            conditional_headers_from_cursor(next_run.cursor_before),
            {
                "If-None-Match": '"listing-v1"',
                "If-Modified-Since": "Tue, 04 Aug 2026 01:00:00 GMT",
            },
        )

        observation.outcome = EndpointObservation.Outcome.UNCHANGED
        with self.assertRaises(ValidationError):
            observation.save()
        with self.assertRaises(ValidationError):
            observation.delete()

    def test_not_modified_observation_preserves_previous_content_evidence(self) -> None:
        first_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        first = record_endpoint_observation(first_run, self.response())
        mark_source_run_completed(first_run)
        second_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        second = record_endpoint_observation(
            second_run,
            self.response(
                status=304,
                content=b"",
                headers={"ETag": '"listing-v1"'},
            ),
            request_headers=conditional_headers_from_cursor(second_run.cursor_before),
        )
        mark_source_run_completed(second_run)

        self.assertEqual(second.outcome, EndpointObservation.Outcome.NOT_MODIFIED)
        self.assertEqual(second.previous_observation_id, first.id)
        self.assertEqual(second.content_sha256, first.content_sha256)
        self.assertEqual(second.byte_size, first.byte_size)
        self.endpoint.refresh_from_db()
        self.assertEqual(self.endpoint.last_changed_at, first.checked_at)

    @patch("aria.discovery.services.settings.MONITOR_UNHEALTHY_AFTER_FAILURES", 3)
    def test_failure_threshold_degrades_then_marks_unhealthy_and_success_recovers(self) -> None:
        for attempt_number in range(1, 4):
            source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
            mark_source_run_failed(
                source_run,
                code="RetryableFetchError",
                message="Temporary upstream failure",
            )
            self.endpoint.refresh_from_db()
            self.assertEqual(self.endpoint.consecutive_failures, attempt_number)
            expected = (
                SourceEndpoint.HealthState.UNHEALTHY
                if attempt_number == 3
                else SourceEndpoint.HealthState.DEGRADED
            )
            self.assertEqual(self.endpoint.health_state, expected)

        recovery_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        recovery_observation = record_endpoint_observation(recovery_run, self.response())
        mark_source_run_completed(recovery_run)
        self.endpoint.refresh_from_db()
        self.assertEqual(self.endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertEqual(self.endpoint.consecutive_failures, 0)
        self.assertEqual(self.endpoint.last_checked_at, recovery_observation.checked_at)

    def test_observation_rejects_a_different_endpoint_than_its_run(self) -> None:
        other_endpoint = SourceEndpoint.objects.create(
            collection=self.endpoint.collection,
            name="Other official monitor",
            discovery_url="https://example.com/other/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
        )
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        observation = EndpointObservation(
            source_run=source_run,
            endpoint=other_endpoint,
            outcome=EndpointObservation.Outcome.CHANGED,
            requested_url=other_endpoint.discovery_url,
            final_url=other_endpoint.discovery_url,
            response_status=200,
            content_sha256="a" * 64,
            connector_configuration_version=1,
        )
        with self.assertRaisesMessage(
            ValidationError,
            "Endpoint observation must match its source run endpoint.",
        ):
            observation.full_clean()

    def test_observation_api_is_administrator_only_and_read_only(self) -> None:
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        observation = record_endpoint_observation(source_run, self.response())
        self.assertEqual(
            self.client.get(
                reverse("endpointobservation-detail", args=[observation.id])
            ).status_code,
            403,
        )
        admin = get_user_model().objects.create_superuser(
            username="monitor-admin",
            password="test-password",
        )
        self.client.force_login(admin)
        detail_url = reverse("endpointobservation-detail", args=[observation.id])
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("endpointobservation-list"), {}).status_code,
            405,
        )
