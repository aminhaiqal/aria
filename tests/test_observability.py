import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.comparisons.models import ComparisonItem, ComparisonReview
from aria.comparisons.publications import publish_confirmed_comparison_changes
from aria.comparisons.reviews import record_comparison_review
from aria.comparisons.services import compare_document_versions
from aria.discovery.models import SourceRun
from aria.health.metrics import pipeline_heartbeat
from aria.health.tasks import heartbeat_scheduler_worker
from aria.reliability.models import SourceReliabilityAssessment
from aria.sources.models import SourceEndpoint
from tests.test_phase3c import Phase3CFixture

OBSERVABILITY_SETTINGS = override_settings(
    METRICS_TOKEN="test-metrics-token-with-enough-entropy",
    IMPACT_WEBHOOK_URL="",
    IMPACT_WEBHOOK_ALLOWED_DOMAINS=[],
    IMPACT_WEBHOOK_SECRET="",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "aria-observability-tests",
        }
    },
)


@OBSERVABILITY_SETTINGS
class OperationalMetricsTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        authority = Authority.objects.create(
            name="Metrics regulator secret title",
            slug="metrics-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["secret.example.com"],
        )
        collection = PublicationCollection.objects.create(
            authority=authority,
            name="Metrics publications",
            slug="metrics-publications",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=collection,
            name="Secret metrics source title",
            discovery_url="https://secret.example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["secret.example.com"],
            expected_content_types=["text/html"],
        )
        now = timezone.now()
        self.endpoint.last_successful_run_at = now - timedelta(minutes=10)
        self.endpoint.next_poll_at = now + timedelta(minutes=20)
        self.endpoint.consecutive_failures = 1
        self.endpoint.health_state = SourceEndpoint.HealthState.DEGRADED
        self.endpoint.save(
            update_fields=(
                "last_successful_run_at",
                "next_poll_at",
                "consecutive_failures",
                "health_state",
                "updated_at",
            )
        )
        SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            status=SourceRun.Status.FAILED,
            idempotency_key="metrics-failed-run",
            connector_configuration_version=1,
        )
        SourceReliabilityAssessment.objects.create(
            endpoint=self.endpoint,
            status=SourceReliabilityAssessment.Status.WARNING,
            assessment_signature="a" * 64,
            freshness_deadline=now + timedelta(minutes=30),
            candidate_count=4,
            artifact_ready_count=4,
            extraction_ready_count=3,
            graph_ready_count=2,
            section_count=10,
            local_embedding_count=10,
            configured_embedding_count=8,
            findings=[
                {
                    "code": "extraction_coverage",
                    "severity": "warning",
                    "detail": "Sensitive finding detail that metrics must omit.",
                }
            ],
            assessed_at=now,
        )

    def test_metrics_are_disabled_when_no_token_is_configured(self) -> None:
        with override_settings(METRICS_TOKEN=""):
            response = self.client.get(reverse("health:metrics"))

        self.assertEqual(response.status_code, 404)

    def test_metrics_require_an_exact_bearer_token(self) -> None:
        route = reverse("health:metrics")
        self.assertEqual(self.client.get(route).status_code, 403)
        self.assertEqual(
            self.client.get(route, HTTP_AUTHORIZATION="Bearer wrong-token").status_code,
            403,
        )

    def test_metrics_expose_bounded_source_dimensions_without_sensitive_content(self) -> None:
        response = self.client.get(
            reverse("health:metrics"),
            HTTP_AUTHORIZATION="Bearer test-metrics-token-with-enough-entropy",
        )
        payload = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertIn('aria_source_runs{status="failed"} 1', payload)
        self.assertIn('aria_source_runs_window{window="24h",status="failed"} 1', payload)
        self.assertIn('aria_source_run_success_ratio{window="24h"} 0', payload)
        self.assertIn("aria_source_run_duration_quantile_seconds", payload)
        self.assertIn("aria_orchestration_duration_quantile_seconds", payload)
        self.assertIn("aria_pipeline_oldest_active_age_seconds 0.000", payload)
        self.assertIn(
            'aria_artifact_observations_window{window="24h",content_changed="false"} 0',
            payload,
        )
        self.assertIn('aria_evidence_coverage_ratio{stage="artifact"} 1', payload)
        self.assertIn('aria_outbox_events{status="failed"} 0', payload)
        self.assertIn('aria_review_queue{kind="change"} 0', payload)
        self.assertIn('aria_review_queue{kind="impact"} 0', payload)
        self.assertIn('aria_publication_ready{kind="change"} 0', payload)
        self.assertIn('aria_publication_ready{kind="impact"} 0', payload)
        self.assertIn(
            'aria_publication_blocked{kind="impact",reason="stale_source_review"} 0',
            payload,
        )
        self.assertIn(
            'aria_impact_delivery_events_window{window="7d",status="failed"} 0',
            payload,
        )
        self.assertIn("aria_impact_delivery_configured 0", payload)
        self.assertIn("aria_impact_delivery_oldest_pending_age_seconds 0.000", payload)
        self.assertIn("aria_impact_delivery_last_success_age_seconds +Inf", payload)
        self.assertIn("aria_scheduler_worker_heartbeat_age_seconds +Inf", payload)
        source_labels = (
            'source="metrics-regulator/metrics-publications",'
            f'endpoint="{self.endpoint.id}"'
        )
        self.assertIn(
            f'aria_source_reliability_info{{{source_labels},status="warning"}} 1',
            payload,
        )
        self.assertIn(
            f'aria_source_http_health_info{{{source_labels},health_state="degraded"}} 1',
            payload,
        )
        self.assertIn(f"aria_source_consecutive_failures{{{source_labels}}} 1", payload)
        self.assertIn(
            f'aria_source_coverage_ratio{{{source_labels},stage="extraction"}} 0.75',
            payload,
        )
        self.assertIn(
            f'aria_source_scheduled_runs_window{{{source_labels},window="7d",'
            'status="failed"} 1',
            payload,
        )
        self.assertIn(
            f'aria_source_reliability_findings{{{source_labels},'
            'code="extraction_coverage",severity="warning"} 1',
            payload,
        )
        self.assertNotIn("Secret metrics source title", payload)
        self.assertNotIn("secret.example.com", payload)
        self.assertNotIn("Sensitive finding detail", payload)

    def test_scheduler_worker_task_records_a_fresh_shared_heartbeat(self) -> None:
        result = heartbeat_scheduler_worker.run()
        observed = pipeline_heartbeat()

        self.assertIsNotNone(observed)
        self.assertEqual(observed.isoformat(), result)
        response = self.client.get(
            reverse("health:metrics"),
            HTTP_AUTHORIZATION="Bearer test-metrics-token-with-enough-entropy",
        )
        self.assertNotIn(
            "aria_scheduler_worker_heartbeat_age_seconds +Inf",
            response.content.decode(),
        )


@OBSERVABILITY_SETTINGS
class ReviewPublicationMetricsTests(Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        before = self.create_version(
            "Section 1 Notice\nNotice must be given promptly.\nSection 2 Stable\nStable text."
        )
        after = self.create_version(
            "Section 1 Notice\nNotice must be given within 72 hours.\n"
            "Section 2 Stable\nStable text."
        )
        self.comparison, _ = compare_document_versions(before, after)
        self.item = self.comparison.items.get(
            change_type=ComparisonItem.ChangeType.MODIFIED
        )
        self.reviewer = get_user_model().objects.create_superuser(
            username="metrics-reviewer",
            password="test-password",
        )

    def metrics_payload(self) -> str:
        response = self.client.get(
            reverse("health:metrics"),
            HTTP_AUTHORIZATION="Bearer test-metrics-token-with-enough-entropy",
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_metrics_follow_review_readiness_publication_and_delivery(self) -> None:
        unreviewed = self.metrics_payload()
        self.assertIn('aria_review_queue{kind="change"} 1', unreviewed)
        self.assertIn('aria_publication_ready{kind="change"} 0', unreviewed)

        record_comparison_review(
            self.item,
            decision=ComparisonReview.Decision.CONFIRMED,
            reviewer=self.reviewer,
            rationale="Confirmed against the exact before and after anchors.",
        )
        reviewed = self.metrics_payload()
        self.assertIn('aria_review_queue{kind="change"} 0', reviewed)
        self.assertIn('aria_publication_ready{kind="change"} 1', reviewed)
        self.assertIn(
            'aria_review_decisions_window{kind="change",window="7d",'
            'decision="confirmed"} 1',
            reviewed,
        )

        publish_confirmed_comparison_changes(
            self.comparison,
            publisher=self.reviewer,
        )
        published = self.metrics_payload()
        self.assertIn('aria_publication_ready{kind="change"} 0', published)
        self.assertIn(
            'aria_publications_window{kind="change",window="7d"} 1', published
        )
        self.assertIn(
            'aria_impact_delivery_events_window{window="7d",status="pending"} 0',
            published,
        )
        self.assertNotIn('aria_last_publication_age_seconds{kind="change"} +Inf', published)


class CorrelationIdTests(TestCase):
    def test_valid_request_id_is_preserved_on_the_response(self) -> None:
        response = self.client.get(reverse("health:live"), HTTP_X_REQUEST_ID="campaign-check-42")

        self.assertEqual(response["X-Request-ID"], "campaign-check-42")

    def test_untrusted_request_id_characters_are_replaced(self) -> None:
        response = self.client.get(
            reverse("health:live"), HTTP_X_REQUEST_ID="bad\nforged-log-entry"
        )

        self.assertNotEqual(response["X-Request-ID"], "bad\nforged-log-entry")
        self.assertRegex(
            response["X-Request-ID"],
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )
