import re

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import SourceRun
from aria.health.metrics import pipeline_heartbeat
from aria.health.tasks import heartbeat_scheduler_worker
from aria.sources.models import SourceEndpoint

OBSERVABILITY_SETTINGS = override_settings(
    METRICS_TOKEN="test-metrics-token-with-enough-entropy",
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
        SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            status=SourceRun.Status.FAILED,
            idempotency_key="metrics-failed-run",
            connector_configuration_version=1,
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

    def test_metrics_expose_bounded_states_without_source_identifiers(self) -> None:
        response = self.client.get(
            reverse("health:metrics"),
            HTTP_AUTHORIZATION="Bearer test-metrics-token-with-enough-entropy",
        )
        payload = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertIn('aria_source_runs{status="failed"} 1', payload)
        self.assertIn('aria_outbox_events{status="failed"} 0', payload)
        self.assertIn("aria_scheduler_worker_heartbeat_age_seconds +Inf", payload)
        self.assertNotIn("Secret metrics source title", payload)
        self.assertNotIn("secret.example.com", payload)

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
