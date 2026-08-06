import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, RawArtifact
from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.browser.connector import ConfiguredJavaScriptListingConnector
from aria.browser.contracts import BrowserRenderResult, CapturedNetworkExchange
from aria.browser.models import BrowserCapture, BrowserNetworkExchange
from aria.browser.network import BrowserNetworkPolicy, _ProxyBudget
from aria.browser.services import capture_source_run
from aria.collections.models import PublicationCollection
from aria.discovery.models import SourceRun
from aria.events.models import PipelineEvent
from aria.fetching.client import UnsafeTargetError
from aria.sources.models import ConnectorConfiguration, SourceEndpoint


class BrowserFixture(TestCase):
    def setUp(self) -> None:
        self.authority = Authority.objects.create(
            name="Browser test regulator",
            slug="browser-test-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Browser publications",
            slug="browser-publications",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official JavaScript listing",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING,
            requires_javascript=True,
            allowed_domains=["example.com"],
            expected_content_types=["text/html", "application/pdf"],
        )
        self.source_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            idempotency_key="browser-source-run",
            connector_configuration_version=1,
        )
        self.connector_configuration = ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "link_selector": "a.publication",
                "document_extensions": [".pdf"],
                "max_candidates": 10,
                "ready_selector": "main",
                "render_wait_milliseconds": 25,
            },
        )
        self.original = RawArtifact.objects.create(
            sha256="1" * 64,
            byte_size=10,
            detected_content_type="text/html",
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key="browser/original",
        )
        self.rendered = RawArtifact.objects.create(
            sha256="2" * 64,
            byte_size=20,
            detected_content_type="text/html",
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key="browser/rendered",
        )
        self.derivative = ArtifactDerivative.objects.create(
            source_artifact=self.original,
            derived_artifact=self.rendered,
            transformation_type=ArtifactDerivative.TransformationType.BROWSER_RENDERED_DOM,
            profile="bounded-chromium-v1",
            configuration_hash="3" * 64,
        )

    def create_capture(self) -> BrowserCapture:
        return BrowserCapture.objects.create(
            source_run=self.source_run,
            endpoint=self.endpoint,
            status=BrowserCapture.Status.COMPLETED,
            profile="bounded-chromium-v1",
            configuration={"max_requests": 80},
            configuration_hash="3" * 64,
            toolchain={"playwright": "1.61.0", "chromium": "test"},
            requested_url=self.endpoint.discovery_url,
            final_url=self.endpoint.discovery_url,
            response_status=200,
            original_artifact=self.original,
            rendered_artifact=self.rendered,
            rendered_derivative=self.derivative,
            attempt_count=1,
            request_count=2,
            response_bytes=30,
            finished_at=timezone.now(),
        )


class BrowserEvidenceModelTestCase(BrowserFixture):
    def test_capture_requires_explicit_javascript_endpoint_and_matching_lineage(self) -> None:
        capture = self.create_capture()
        capture.full_clean()

        self.endpoint.connector_type = SourceEndpoint.ConnectorType.HTML_LISTING
        self.endpoint.requires_javascript = False
        self.endpoint.save(
            update_fields=("connector_type", "requires_javascript", "updated_at")
        )
        with self.assertRaisesMessage(ValidationError, "JavaScript-enabled"):
            capture.full_clean()

    def test_network_exchange_is_append_only_and_body_hash_is_traceable(self) -> None:
        capture = self.create_capture()
        exchange = BrowserNetworkExchange(
            capture=capture,
            attempt_number=1,
            sequence=1,
            requested_url="https://example.com/api/publications.json",
            method="GET",
            resource_type="fetch",
            disposition=BrowserNetworkExchange.Disposition.ALLOWED,
            response_status=200,
            content_type="application/json",
            byte_size=self.rendered.byte_size,
            body_sha256=self.rendered.sha256,
            body_artifact=self.rendered,
            resolved_addresses=["93.184.216.34"],
        )
        exchange.full_clean()
        exchange.save()

        exchange.byte_size = 999
        with self.assertRaisesMessage(ValidationError, "append-only"):
            exchange.save()
        with self.assertRaisesMessage(ValidationError, "append-only"):
            exchange.delete()

    def test_blocked_exchange_requires_a_reason(self) -> None:
        exchange = BrowserNetworkExchange(
            capture=self.create_capture(),
            attempt_number=1,
            sequence=1,
            requested_url="https://example.net/tracker.js",
            method="GET",
            resource_type="script",
            disposition=BrowserNetworkExchange.Disposition.BLOCKED,
        )
        with self.assertRaisesMessage(ValidationError, "require a reason"):
            exchange.full_clean()


class BrowserEvidenceAPITestCase(BrowserFixture):
    def test_browser_evidence_api_is_staff_only_and_read_only(self) -> None:
        capture = self.create_capture()
        BrowserNetworkExchange.objects.create(
            capture=capture,
            attempt_number=1,
            sequence=1,
            requested_url=self.endpoint.discovery_url,
            method="GET",
            resource_type="document",
            disposition=BrowserNetworkExchange.Disposition.ALLOWED,
            response_status=200,
            content_type="text/html",
            byte_size=self.original.byte_size,
            body_sha256=self.original.sha256,
            body_artifact=self.original,
        )
        list_url = reverse("browsercapture-list")
        self.assertEqual(self.client.get(list_url).status_code, 403)

        staff = get_user_model().objects.create_user(
            username="browser-api-operator",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("browsercapture-detail", args=[capture.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["original_artifact"], str(self.original.id))
        self.assertEqual(len(response.json()["network_exchanges"]), 1)
        self.assertEqual(self.client.post(list_url, {}).status_code, 405)


class BrowserNetworkPolicyTestCase(TestCase):
    public_ip = "93.184.216.34"

    def policy(self, *, resolver=None, max_requests=4, max_redirects=2):
        return BrowserNetworkPolicy(
            allowed_domains=["example.com"],
            max_requests=max_requests,
            max_redirects=max_redirects,
            resolver=resolver or (lambda _hostname, _port: [self.public_ip]),
        )

    def test_policy_allows_bounded_read_only_official_requests(self) -> None:
        policy = self.policy()
        decision = policy.inspect_request(
            url="https://sub.example.com/publications.json",
            method="GET",
            resource_type="fetch",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.resolved_addresses, (self.public_ip,))

        policy.attach_response(
            url="https://sub.example.com/publications.json",
            method="GET",
            resource_type="fetch",
            status=200,
            content_type="application/json",
            body=b"[]",
        )
        self.assertEqual(policy.exchanges[0].response_status, 200)
        self.assertEqual(policy.exchanges[0].body, b"[]")

    def test_policy_blocks_private_dns_cross_domain_and_non_https(self) -> None:
        private = self.policy(resolver=lambda _hostname, _port: ["127.0.0.1"])
        self.assertFalse(
            private.inspect_request(
                url="https://example.com/private",
                method="GET",
                resource_type="document",
            ).allowed
        )
        self.assertIn("non-public", private.fatal_reason)
        policy = self.policy()
        for url in (
            "https://attacker.example.net/tracker.js",
            "http://example.com/insecure.js",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                self.assertFalse(
                    policy.inspect_request(
                        url=url,
                        method="GET",
                        resource_type="script",
                    ).allowed
                )
        self.assertEqual(policy.fatal_reason, "")

    def test_policy_blocks_side_effects_heavy_resources_and_bounds_execution(self) -> None:
        policy = self.policy(max_requests=3, max_redirects=1)
        unsafe_method = policy.inspect_request(
            url="https://example.com/api/mutate",
            method="POST",
            resource_type="fetch",
        )
        media = policy.inspect_request(
            url="https://example.com/video.mp4",
            method="GET",
            resource_type="media",
        )
        redirect = policy.inspect_request(
            url="https://example.com/final",
            method="GET",
            resource_type="document",
            redirect_count=2,
        )
        over_limit = policy.inspect_request(
            url="https://example.com/extra",
            method="GET",
            resource_type="script",
        )
        self.assertEqual(unsafe_method.reason, "unsafe_http_method")
        self.assertEqual(media.reason, "blocked_resource_type")
        self.assertEqual(redirect.reason, "redirect_limit_exceeded")
        self.assertEqual(over_limit.reason, "request_limit_exceeded")
        self.assertEqual(policy.blocked_request_count, 4)

    def test_pinned_proxy_revalidates_dns_and_enforces_shared_byte_budget(self) -> None:
        budget = _ProxyBudget(
            allowed_domains=("example.com",),
            max_response_bytes=5,
            resolver=lambda _hostname, _port: [self.public_ip],
        )
        self.assertEqual(budget.resolve("example.com", 443), [self.public_ip])
        self.assertTrue(budget.add_response_bytes(3))
        self.assertFalse(budget.add_response_bytes(3))
        self.assertTrue(budget.limit_exceeded)

        unsafe_budget = _ProxyBudget(
            allowed_domains=("example.com",),
            max_response_bytes=5,
            resolver=lambda _hostname, _port: ["10.0.0.8"],
        )
        with self.assertRaisesMessage(Exception, "non-public"):
            unsafe_budget.resolve("example.com", 443)
        self.assertIn("non-public", unsafe_budget.unsafe_reason)


class StubBrowserRunner:
    calls = 0

    def render(self, url, **_kwargs):
        self.calls += 1
        return BrowserRenderResult(
            requested_url=url,
            final_url=url,
            response_status=200,
            response_headers={"content-type": "text/html", "etag": '"browser-v1"'},
            redirect_chain=[],
            resolved_addresses=["93.184.216.34"],
            original_content=b"<html><main>Loading</main></html>",
            rendered_content=(
                b'<html><main><a class="publication" href="/gazette.pdf">'
                b"Gazette</a></main></html>"
            ),
            network_exchanges=(
                CapturedNetworkExchange(
                    sequence=1,
                    requested_url=url,
                    method="GET",
                    resource_type="document",
                    disposition="allowed",
                    response_status=200,
                    content_type="text/html",
                    body=b"<html><main>Loading</main></html>",
                    resolved_addresses=["93.184.216.34"],
                ),
            ),
            request_count=1,
            blocked_request_count=0,
            response_bytes=256,
            toolchain={"playwright": "1.61.0", "chromium": "test"},
        )


class BrowserCaptureServiceTestCase(BrowserFixture):
    def setUp(self) -> None:
        super().setUp()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = FilesystemArtifactStore(Path(self.temporary_directory.name))

    def test_rendered_listing_is_immutable_traceable_and_replayable(self) -> None:
        runner = StubBrowserRunner()
        connector = ConfiguredJavaScriptListingConnector(runner=runner, store=self.store)
        result = connector.discover(self.source_run)

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].canonical_url, "https://example.com/gazette.pdf")
        capture = BrowserCapture.objects.get(source_run=self.source_run)
        self.assertEqual(capture.status, BrowserCapture.Status.COMPLETED)
        self.assertNotEqual(capture.original_artifact_id, capture.rendered_artifact_id)
        self.assertEqual(
            capture.rendered_derivative.transformation_type,
            ArtifactDerivative.TransformationType.BROWSER_RENDERED_DOM,
        )
        exchange = capture.network_exchanges.get()
        self.assertEqual(exchange.body_sha256, exchange.body_artifact.sha256)
        self.assertEqual(runner.calls, 1)

        staff = get_user_model().objects.create_user(
            username="browser-console-operator",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff)
        console_response = self.client.get(
            reverse("console:source-detail", args=[self.endpoint.id])
        )
        self.assertContains(console_response, "Bounded browser evidence")
        self.assertContains(console_response, capture.rendered_artifact.sha256[:12])

        replay = ConfiguredJavaScriptListingConnector(runner=runner, store=self.store).discover(
            self.source_run
        )
        self.assertEqual(replay.candidates, result.candidates)
        self.assertEqual(runner.calls, 1)

    def test_static_task_hands_javascript_run_to_dedicated_queue(self) -> None:
        from aria.discovery.tasks import execute_source_run

        with patch("aria.browser.tasks.execute_browser_source_run.delay") as enqueue:
            execute_source_run.run(str(self.source_run.id))

        enqueue.assert_called_once_with(str(self.source_run.id))
        self.source_run.refresh_from_db()
        self.assertEqual(self.source_run.status, SourceRun.Status.PENDING)

    def test_unsafe_navigation_is_quarantined_with_auditable_failure(self) -> None:
        class UnsafeRunner:
            def render(self, *_args, **_kwargs):
                raise UnsafeTargetError("Target resolves to a non-public address.")

        with self.assertRaises(UnsafeTargetError):
            capture_source_run(self.source_run, runner=UnsafeRunner(), store=self.store)

        capture = BrowserCapture.objects.get(source_run=self.source_run)
        self.assertEqual(capture.status, BrowserCapture.Status.QUARANTINED)
        self.assertEqual(capture.error_code, "UnsafeTargetError")
        self.assertTrue(
            PipelineEvent.objects.filter(
                event_type="browser.capture.quarantined",
                aggregate_id=capture.id,
            ).exists()
        )
