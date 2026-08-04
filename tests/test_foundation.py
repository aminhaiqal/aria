from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.models import CandidateObservation, DiscoveredCandidate, SourceRun
from aria.discovery.services import create_source_run, observe_candidate, schedule_due_source_runs
from aria.discovery.tasks import execute_source_run
from aria.events.models import OutboxEvent, PipelineEvent
from aria.events.services import record_pipeline_event
from aria.sources.models import SourceEndpoint


class FoundationTestCase(TestCase):
    def setUp(self) -> None:
        self.authority = Authority.objects.create(
            name="Personal Data Protection Commissioner",
            slug="pdp-commissioner",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            regulatory_domains=["personal-data-protection"],
            official_domains=["pdp.gov.my"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Guidelines",
            slug="guidelines",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Guideline listing",
            discovery_url="https://www.pdp.gov.my/guidelines/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["pdp.gov.my", "www.pdp.gov.my"],
            expected_content_types=["text/html", "application/pdf"],
            next_poll_at=timezone.now() - timedelta(minutes=1),
        )

    def test_registry_hierarchy_is_persisted(self) -> None:
        self.endpoint.full_clean()
        self.assertEqual(self.endpoint.collection.authority.country_code, "MY")
        self.assertTrue(self.endpoint.is_enabled)

    def test_scheduler_creates_one_idempotent_run_and_advances_poll_time(self) -> None:
        runs = schedule_due_source_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, SourceRun.Status.PENDING)

        self.endpoint.refresh_from_db()
        self.assertGreater(self.endpoint.next_poll_at, timezone.now())
        self.assertEqual(schedule_due_source_runs(), [])

    def test_candidate_is_reused_across_runs_but_observation_is_per_run(self) -> None:
        first_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        second_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        data = CandidateData(
            discovered_url="https://www.pdp.gov.my/files/guideline.pdf",
            canonical_url="https://www.pdp.gov.my/files/guideline.pdf",
            fingerprint="a" * 64,
        )

        first_candidate, first_created = observe_candidate(first_run, data)
        second_candidate, second_created = observe_candidate(second_run, data)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_candidate.id, second_candidate.id)
        self.assertEqual(DiscoveredCandidate.objects.count(), 1)
        self.assertEqual(CandidateObservation.objects.count(), 2)
        first_run.refresh_from_db()
        second_run.refresh_from_db()
        self.assertEqual(first_run.discovered_candidate_count, 1)
        self.assertEqual(second_run.discovered_candidate_count, 1)

    def test_pipeline_event_always_has_outbox_entry(self) -> None:
        event = record_pipeline_event(
            event_type="test.event",
            aggregate_type="source_endpoint",
            aggregate_id=self.endpoint.id,
            payload={"ok": True},
        )
        outbox = OutboxEvent.objects.get(pipeline_event=event)
        self.assertEqual(outbox.status, OutboxEvent.Status.PENDING)
        self.assertEqual(outbox.payload["data"], {"ok": True})

        event.payload = {"ok": False}
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_unimplemented_connector_fails_visibly(self) -> None:
        self.endpoint.connector_type = SourceEndpoint.ConnectorType.REST_API
        self.endpoint.save(update_fields=("connector_type", "updated_at"))
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)

        execute_source_run.run(str(source_run.id))

        source_run.refresh_from_db()
        self.endpoint.refresh_from_db()
        self.assertEqual(source_run.status, SourceRun.Status.FAILED)
        self.assertEqual(source_run.error_code, "connector_not_registered")
        self.assertEqual(self.endpoint.health_state, SourceEndpoint.HealthState.DEGRADED)
        self.assertEqual(self.endpoint.consecutive_failures, 1)
        self.assertTrue(
            PipelineEvent.objects.filter(
                aggregate_id=source_run.id,
                event_type="source.run.failed",
            ).exists()
        )


class HealthTestCase(TestCase):
    def test_liveness_does_not_depend_on_external_services(self) -> None:
        response = self.client.get(reverse("health:live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("aria.health.views.Redis.from_url")
    def test_readiness_checks_database_and_redis(self, redis_from_url: Mock) -> None:
        redis_from_url.return_value.ping.return_value = True
        response = self.client.get(reverse("health:ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"database": "ok", "redis": "ok"})
