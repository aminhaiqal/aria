import hashlib
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation, RawArtifact
from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.browser.admission import evaluate_browser_admission
from aria.browser.connector import ConfiguredJavaScriptListingConnector
from aria.browser.contracts import BrowserRenderResult, CapturedNetworkExchange
from aria.browser.models import BrowserCapture, BrowserNetworkExchange
from aria.browser.network import BrowserNetworkPolicy, _ProxyBudget
from aria.browser.runtime import PlaywrightBrowserRunner
from aria.browser.services import browser_configuration, capture_source_run
from aria.collections.models import PublicationCollection
from aria.discovery.models import CandidateObservation, DiscoveredCandidate, SourceRun
from aria.events.models import PipelineEvent
from aria.extraction.models import ExtractionRun
from aria.fetching.client import UnsafeTargetError
from aria.fetching.models import FetchAttempt
from aria.knowledge.models import GraphNode
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
        self.endpoint.save(update_fields=("connector_type", "requires_javascript", "updated_at"))
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

    def test_request_body_evidence_is_hash_only_and_consistent(self) -> None:
        exchange = BrowserNetworkExchange(
            capture=self.create_capture(),
            attempt_number=1,
            sequence=1,
            requested_url="https://example.com/api/listing",
            method="POST",
            resource_type="xhr",
            disposition=BrowserNetworkExchange.Disposition.ALLOWED,
            request_body_bytes=6,
            request_body_sha256=hashlib.sha256(b"draw=1").hexdigest(),
            response_status=200,
        )
        exchange.full_clean()

        exchange.request_body_sha256 = ""
        with self.assertRaisesMessage(ValidationError, "recorded together"):
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

    def test_policy_allows_only_an_exact_bounded_official_read_only_post(self) -> None:
        policy = BrowserNetworkPolicy(
            allowed_domains=["example.com"],
            dependency_domains=["cdn.example.net"],
            read_only_post_paths=["/api/listing"],
            max_requests=8,
            max_redirects=2,
            max_request_body_bytes=16,
            resolver=lambda _hostname, _port: [self.public_ip],
        )
        body = b"draw=1"
        approved = policy.inspect_request(
            url="https://example.com/api/listing",
            method="POST",
            resource_type="xhr",
            request_body=body,
        )
        dependency = policy.inspect_request(
            url="https://cdn.example.net/table.js",
            method="GET",
            resource_type="script",
        )
        dependency_xhr = policy.inspect_request(
            url="https://cdn.example.net/api/listing",
            method="GET",
            resource_type="xhr",
        )
        tracking = policy.inspect_request(
            url="https://example.com/api/hit-counter",
            method="POST",
            resource_type="xhr",
            request_body=body,
        )
        dependency_post = policy.inspect_request(
            url="https://cdn.example.net/api/listing",
            method="POST",
            resource_type="xhr",
            request_body=body,
        )
        queried = policy.inspect_request(
            url="https://example.com/api/listing?mutate=1",
            method="POST",
            resource_type="xhr",
            request_body=body,
        )
        oversized = policy.inspect_request(
            url="https://example.com/api/listing",
            method="POST",
            resource_type="xhr",
            request_body=b"x" * 17,
        )

        self.assertTrue(approved.allowed)
        self.assertTrue(dependency.allowed)
        self.assertEqual(dependency_xhr.reason, "dependency_resource_type")
        self.assertEqual(tracking.reason, "unsafe_http_method")
        self.assertEqual(dependency_post.reason, "dependency_resource_type")
        self.assertEqual(queried.reason, "unsafe_http_method")
        self.assertEqual(oversized.reason, "request_body_limit_exceeded")
        self.assertEqual(policy.exchanges[0].request_body_bytes, len(body))
        self.assertEqual(
            policy.exchanges[0].request_body_sha256,
            hashlib.sha256(body).hexdigest(),
        )

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
    last_kwargs = None

    def render(self, url, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return BrowserRenderResult(
            requested_url=url,
            final_url=url,
            response_status=200,
            response_headers={"content-type": "text/html", "etag": '"browser-v1"'},
            redirect_chain=[],
            resolved_addresses=["93.184.216.34"],
            original_content=b"<html><main>Loading</main></html>",
            rendered_content=(
                b'<html><main><a class="publication" href="/gazette.pdf">Gazette</a></main></html>'
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
        self.assertEqual(runner.last_kwargs["dependency_domains"], [])
        self.assertEqual(runner.last_kwargs["read_only_post_paths"], [])
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

    def test_browser_source_network_configuration_is_strictly_validated(self) -> None:
        configuration = browser_configuration(
            self.endpoint,
            {
                "browser_dependency_domains": ["CDN.Example.NET."],
                "browser_read_only_post_paths": ["/api/listing"],
            },
        )
        self.assertEqual(configuration["dependency_domains"], ["cdn.example.net"])
        self.assertEqual(configuration["read_only_post_paths"], ["/api/listing"])

        for invalid in (
            {"browser_dependency_domains": ["https://cdn.example.net/script.js"]},
            {"browser_read_only_post_paths": ["https://example.com/api/listing"]},
            {"browser_read_only_post_paths": ["/api/listing?mutate=1"]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                browser_configuration(self.endpoint, invalid)


class BrowserRuntimeCleanupTestCase(TestCase):
    def test_cleanup_closes_context_and_browser_even_when_unroute_fails(self) -> None:
        calls = []

        class Context:
            def unroute_all(self, **kwargs):
                calls.append(("unroute", kwargs))
                raise RuntimeError("route callback already stopped")

            def close(self):
                calls.append(("context", {}))

        class Browser:
            def close(self):
                calls.append(("browser", {}))

        PlaywrightBrowserRunner._close_resources(Context(), Browser())

        self.assertEqual(
            calls,
            [
                ("unroute", {"behavior": "ignoreErrors"}),
                ("context", {}),
                ("browser", {}),
            ],
        )


class BrowserAdmissionGateTestCase(BrowserFixture):
    def test_two_repeatable_captures_require_safe_network_and_downstream_lineage(self) -> None:
        self.endpoint.is_enabled = False
        self.endpoint.next_poll_at = None
        self.endpoint.health_state = SourceEndpoint.HealthState.DISABLED
        self.endpoint.save(
            update_fields=("is_enabled", "next_poll_at", "health_state", "updated_at")
        )
        configuration = {
            **self.connector_configuration.configuration,
            "include_path_prefixes": ["/"],
        }
        self.connector_configuration.configuration = configuration
        self.connector_configuration.save(update_fields=("configuration", "updated_at"))

        self.source_run.status = SourceRun.Status.COMPLETED
        self.source_run.finished_at = timezone.now()
        self.source_run.save(update_fields=("status", "finished_at", "updated_at"))
        second_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="browser-source-run-second",
            connector_configuration_version=1,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        captures = [self.create_capture()]
        captures[0].configuration = {
            "read_only_post_paths": ["/api/listing"],
            "max_request_body_bytes": 64,
        }
        captures[0].save(update_fields=("configuration", "updated_at"))
        captures.append(
            BrowserCapture.objects.create(
                source_run=second_run,
                endpoint=self.endpoint,
                status=BrowserCapture.Status.COMPLETED,
                profile="bounded-chromium-v1",
                configuration={
                    "read_only_post_paths": ["/api/listing"],
                    "max_request_body_bytes": 64,
                },
                configuration_hash="4" * 64,
                requested_url=self.endpoint.discovery_url,
                final_url=self.endpoint.discovery_url,
                response_status=200,
                original_artifact=self.original,
                rendered_artifact=self.rendered,
                rendered_derivative=self.derivative,
                attempt_count=1,
                request_count=2,
                response_bytes=30,
                started_at=timezone.now(),
                finished_at=timezone.now(),
            )
        )
        body = b"draw=1"
        for capture in captures:
            BrowserNetworkExchange.objects.create(
                capture=capture,
                attempt_number=1,
                sequence=1,
                requested_url="https://example.com/api/listing",
                method="POST",
                resource_type="xhr",
                disposition=BrowserNetworkExchange.Disposition.ALLOWED,
                response_status=200,
                request_body_bytes=len(body),
                request_body_sha256=hashlib.sha256(body).hexdigest(),
            )

        candidate = DiscoveredCandidate.objects.create(
            endpoint=self.endpoint,
            latest_source_run=second_run,
            discovered_url="https://example.com/gazette.pdf",
            canonical_url="https://example.com/gazette.pdf",
            fingerprint="5" * 64,
            metadata_hints={"title": "Gazette"},
            first_discovered_at=timezone.now(),
            last_discovered_at=timezone.now(),
        )
        for source_run in (self.source_run, second_run):
            CandidateObservation.objects.create(source_run=source_run, candidate=candidate)
        fetch_attempt = FetchAttempt.objects.create(
            candidate=candidate,
            source_run=second_run,
            attempt_number=1,
            status=FetchAttempt.Status.SUCCEEDED,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            bytes_received=self.rendered.byte_size,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        ArtifactObservation.objects.create(
            raw_artifact=self.rendered,
            fetch_attempt=fetch_attempt,
            candidate=candidate,
            source_run=second_run,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            connector_configuration_version=1,
        )
        ExtractionRun.objects.create(
            raw_artifact=self.rendered,
            extractor_name="test",
            extractor_version="1",
            configuration_hash="6" * 64,
            status=ExtractionRun.Status.SUCCEEDED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        GraphNode.objects.create(
            node_type=GraphNode.NodeType.ARTIFACT,
            canonical_key=f"artifact:sha256:{self.rendered.sha256}",
            label="test artifact",
            source_type="raw_artifact",
            source_id=self.rendered.id,
        )

        with patch("aria.browser.admission.get_artifact_store") as artifact_store:
            artifact_store.return_value.read.return_value = b"<html><main>Loading</main></html>"
            report = evaluate_browser_admission(self.endpoint)
            call_command(
                "promote_browser_source",
                str(self.endpoint.id),
                confirm=True,
                stdout=StringIO(),
            )

        self.assertTrue(report.ready_for_promotion)
        self.assertTrue(all(gate.passed for gate in report.gates))
        self.endpoint.refresh_from_db()
        self.assertTrue(self.endpoint.is_enabled)
        self.assertIsNotNone(self.endpoint.next_poll_at)
        self.assertTrue(
            PipelineEvent.objects.filter(
                event_type="browser.source.promoted",
                aggregate_id=self.endpoint.id,
            ).exists()
        )
        with patch("aria.browser.admission.get_artifact_store") as artifact_store:
            artifact_store.return_value.read.return_value = b"<html><main>Loading</main></html>"
            self.assertTrue(evaluate_browser_admission(self.endpoint).ready_for_promotion)

    def test_incomplete_audit_is_recorded_without_promoting_source(self) -> None:
        self.endpoint.is_enabled = False
        self.endpoint.next_poll_at = None
        self.endpoint.save(update_fields=("is_enabled", "next_poll_at", "updated_at"))
        output = StringIO()

        call_command(
            "audit_browser_admission",
            str(self.endpoint.id),
            allow_incomplete=True,
            stdout=output,
        )

        self.assertIn('"ready_for_promotion": false', output.getvalue())
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)
        self.assertTrue(
            PipelineEvent.objects.filter(
                event_type="browser.admission.evaluated",
                aggregate_id=self.endpoint.id,
            ).exists()
        )
        with self.assertRaisesMessage(CommandError, "Admission gates failed"):
            call_command(
                "promote_browser_source",
                str(self.endpoint.id),
                confirm=True,
                stdout=StringIO(),
            )
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)


class AGCBrowserPilotRegistryTestCase(TestCase):
    def test_seed_registers_narrow_disabled_source_and_preserves_promotion(self) -> None:
        call_command("seed_agc", stdout=StringIO())

        endpoint = SourceEndpoint.objects.get(name="AGC updated principal Acts")
        configuration = endpoint.connector_configurations.get(version=1).configuration
        self.assertFalse(endpoint.is_enabled)
        self.assertEqual(endpoint.health_state, SourceEndpoint.HealthState.DISABLED)
        self.assertIsNone(endpoint.next_poll_at)
        self.assertEqual(endpoint.allowed_domains, ["lom.agc.gov.my"])
        self.assertEqual(
            configuration["browser_read_only_post_paths"],
            ["/json-updated-2024.php"],
        )
        self.assertEqual(
            configuration["browser_dependency_domains"],
            ["cdn.datatables.net", "cdnjs.cloudflare.com"],
        )

        endpoint.is_enabled = True
        endpoint.health_state = SourceEndpoint.HealthState.HEALTHY
        endpoint.save(update_fields=("is_enabled", "health_state", "updated_at"))
        call_command("seed_agc", stdout=StringIO())
        endpoint.refresh_from_db()
        self.assertTrue(endpoint.is_enabled)
        self.assertEqual(endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertIsNotNone(endpoint.next_poll_at)

    def test_run_option_queues_one_manual_pilot_without_enabling_schedule(self) -> None:
        with patch("aria.sources.management.commands.seed_agc.execute_source_run.delay") as delay:
            call_command("seed_agc", run=True, stdout=StringIO())

        endpoint = SourceEndpoint.objects.get(name="AGC updated principal Acts")
        source_run = SourceRun.objects.get(endpoint=endpoint)
        self.assertFalse(endpoint.is_enabled)
        self.assertEqual(source_run.trigger, SourceRun.Trigger.MANUAL)
        delay.assert_called_once_with(str(source_run.id))
