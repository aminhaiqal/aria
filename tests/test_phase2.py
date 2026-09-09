import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import quote

import httpx
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.html_connector import ConfiguredHTMLListingConnector
from aria.discovery.models import DiscoveredCandidate, MonitoredResource, SourceRun
from aria.discovery.services import (
    create_source_run,
    mark_source_run_completed,
    observe_candidate,
    record_endpoint_observation,
)
from aria.fetching.client import (
    FetchResponse,
    RateLimiter,
    ResponseTooLargeError,
    SafeHttpClient,
    UnexpectedContentTypeError,
    UnsafeTargetError,
    build_tls_context,
    validate_target_url,
)
from aria.fetching.models import FetchAttempt
from aria.fetching.services import begin_fetch_attempt, complete_fetch, complete_not_modified
from aria.fetching.tasks import fetch_candidate
from aria.orchestration.models import ChangeOrchestration
from aria.sources.models import ConnectorConfiguration, SourceEndpoint

PUBLIC_IP = "93.184.216.34"


class PhaseTwoTestCase(TestCase):
    def setUp(self) -> None:
        self.authority = Authority.objects.create(
            name="Test regulator",
            slug="test-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Test publications",
            slug="test-publications",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official listing",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html", "application/pdf"],
        )

    def make_candidate(self, source_run: SourceRun) -> DiscoveredCandidate:
        candidate, _ = observe_candidate(
            source_run,
            CandidateData(
                discovered_url="https://example.com/files/act.pdf",
                canonical_url="https://example.com/files/act.pdf",
                fingerprint="a" * 64,
            ),
        )
        return candidate

    def test_url_validation_rejects_non_public_and_non_allowlisted_targets(self) -> None:
        with self.assertRaises(UnsafeTargetError):
            validate_target_url(
                "https://localhost/document.pdf",
                allowed_domains=["localhost"],
                resolver=lambda _hostname, _port: ["127.0.0.1"],
            )
        with self.assertRaises(UnsafeTargetError):
            validate_target_url(
                "https://attacker.example/document.pdf",
                allowed_domains=["example.com"],
                resolver=lambda _hostname, _port: ["10.0.0.8"],
            )
        with self.assertRaises(UnsafeTargetError):
            validate_target_url(
                "https://example.net/document.pdf",
                allowed_domains=["example.com"],
                resolver=lambda _hostname, _port: [PUBLIC_IP],
            )

    @override_settings(
        HTTP_SUPPLEMENTAL_CA_BUNDLE=str(
            Path(__file__).parents[1]
            / "config/certificates/sectigo-public-server-authentication-ca-dv-r36.pem"
        )
    )
    def test_supplemental_ca_keeps_verified_context_and_pinned_certificate(self) -> None:
        path = Path(__file__).parents[1] / (
            "config/certificates/sectigo-public-server-authentication-ca-dv-r36.pem"
        )
        encoded = "".join(
            line for line in path.read_text().splitlines() if not line.startswith("-----")
        )
        expected_fingerprint = "8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0"
        self.assertEqual(
            hashlib.sha256(base64.b64decode(encoded)).hexdigest(),
            expected_fingerprint,
        )

        context = build_tls_context()
        fingerprints = {
            hashlib.sha256(certificate).hexdigest()
            for certificate in context.get_ca_certs(binary_form=True)
        }
        self.assertIn(expected_fingerprint, fingerprints)

    def test_candidate_fetch_bootstraps_ephemeral_official_listing_session(self) -> None:
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "max_candidates": 10,
                "bootstrap_candidate_session": True,
            },
        )
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate = self.make_candidate(source_run)

        class RecordingClient:
            def __init__(self):
                self.calls = []

            def fetch(self, url, *, allowed_domains, headers=None):
                self.calls.append((url, allowed_domains, headers))
                return FetchResponse(
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    headers={
                        "content-type": (
                            "text/html" if url == self_endpoint.discovery_url else "application/pdf"
                        )
                    },
                    redirect_chain=[],
                    resolved_addresses=[PUBLIC_IP],
                    content=(b"<html></html>" if url == self_endpoint.discovery_url else b"%PDF-"),
                )

            def close(self):
                return None

        self_endpoint = self.endpoint
        client = RecordingClient()
        with (
            patch("aria.fetching.tasks.get_default_http_client", return_value=client),
            patch("aria.fetching.tasks.complete_fetch") as complete,
        ):
            fetch_candidate.run(str(candidate.id), str(source_run.id))

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][0], self.endpoint.discovery_url)
        self.assertIsNone(client.calls[0][2])
        self.assertEqual(client.calls[1][0], candidate.canonical_url)
        self.assertEqual(client.calls[1][2]["Referer"], self.endpoint.discovery_url)
        attempt = FetchAttempt.objects.get(candidate=candidate, source_run=source_run)
        self.assertEqual(attempt.request_headers["Referer"], self.endpoint.discovery_url)
        complete.assert_called_once()

    def test_redirect_target_is_revalidated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, PUBLIC_IP)
            self.assertEqual(request.headers["Host"], "example.com")
            return httpx.Response(
                302,
                headers={"Location": "https://internal.example.com/secret"},
            )

        def resolver(hostname: str, _port: int) -> list[str]:
            return ["10.0.0.9"] if hostname == "internal.example.com" else [PUBLIC_IP]

        client = SafeHttpClient(
            resolver=resolver,
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(UnsafeTargetError):
                client.fetch("https://example.com/start", allowed_domains=["example.com"])
        finally:
            client.close()

    def test_response_limit_is_enforced_while_streaming(self) -> None:
        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"too large")
            ),
            max_response_bytes=4,
        )
        try:
            with self.assertRaises(ResponseTooLargeError):
                client.fetch("https://example.com/file", allowed_domains=["example.com"])
        finally:
            client.close()

    def test_not_modified_is_returned_without_being_treated_as_a_redirect(self) -> None:
        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(lambda _request: httpx.Response(304)),
        )
        try:
            response = client.fetch(
                "https://example.com/file.pdf",
                allowed_domains=["example.com"],
                headers={"If-None-Match": '"one"'},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.redirect_chain, [])

    def test_html_listing_connector_discovers_and_prioritizes_documents(self) -> None:
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "include_path_prefixes": ["/publications/", "/files/"],
                "upload_path_prefixes": ["/files/"],
                "document_extensions": [".pdf"],
                "max_candidates": 2,
            },
        )
        html = b"""
            <html><body>
              <a href="/publications/rule-one/">Rule One</a>
              <a href="/files/rule-two.pdf">Rule Two PDF</a>
              <a href="https://untrusted.example.net/file.pdf">Untrusted</a>
            </body></html>
        """
        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/html; charset=UTF-8"},
                    content=html,
                )
            ),
        )
        connector = ConfiguredHTMLListingConnector(client_factory=lambda: client)

        result = connector.discover(self.endpoint, cursor=None)
        candidates = result.candidates

        self.assertEqual(len(candidates), 2)
        self.assertEqual(result.response.status_code, 200)
        self.assertEqual(candidates[0].canonical_url, "https://example.com/files/rule-two.pdf")
        self.assertEqual(candidates[0].metadata_hints["title"], "Rule Two PDF")
        self.assertEqual(len(candidates[0].fingerprint), 64)

    def test_html_listing_connector_extracts_bounded_non_href_value(self) -> None:
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "link_selector": "a[onclick]",
                "link_attribute": "onclick",
                "link_value_prefix": "loadResult('",
                "link_value_suffix": "'",
                "include_path_prefixes": ["/files/bills/"],
                "upload_path_prefixes": ["/files/bills/"],
                "document_extensions": [".pdf"],
                "max_candidates": 2,
            },
        )
        html = b"""
            <a href="#" onclick="loadResult('/files/bills/Bill 2026.pdf','Bill.pdf');">
              D.R.1/2026
            </a>
            <a href="#" onclick="mutate('/files/bills/unsafe.pdf');">Ignore</a>
        """
        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/html; charset=UTF-8"},
                    content=html,
                )
            ),
        )

        result = ConfiguredHTMLListingConnector(client_factory=lambda: client).discover(
            self.endpoint,
            cursor=None,
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].canonical_url,
            "https://example.com/files/bills/Bill%202026.pdf",
        )
        self.assertEqual(result.candidates[0].metadata_hints["title"], "D.R.1/2026")

    def test_html_listing_connector_decodes_bounded_signed_url_wrapper(self) -> None:
        target = "https://example.com/files/bills/Act 709.pdf"
        checksum = "a" * 64
        wrapped_value = base64.b64encode(f"{target}|{checksum}".encode()).decode()
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "link_selector": "a.download[href]",
                "include_path_prefixes": ["/files/bills/"],
                "upload_path_prefixes": ["/files/bills/"],
                "document_extensions": [".pdf"],
                "max_candidates": 2,
                "wrapped_link_target": {
                    "path": "/processFile.php",
                    "query_parameter": "token",
                    "encoding": "base64_url_sha256_v1",
                },
            },
        )
        malformed_digest = base64.b64encode(f"{target}|short".encode()).decode()
        html = (
            f'<a class="download" href="processFile.php?token='
            f'{quote(wrapped_value, safe="")}">Act 709</a>'
            '<a class="download" href="processFile.php?token=not-base64">Unsafe</a>'
            f'<a class="download" href="processFile.php?token={malformed_digest}">Bad</a>'
        ).encode()
        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/html; charset=UTF-8"},
                    content=html,
                )
            ),
        )

        result = ConfiguredHTMLListingConnector(client_factory=lambda: client).discover(
            self.endpoint,
            cursor=None,
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].canonical_url,
            "https://example.com/files/bills/Act%20709.pdf",
        )

    def test_signed_url_wrapper_cannot_bypass_source_allowlist(self) -> None:
        target = "https://untrusted.example.net/files/bills/Act.pdf"
        wrapped_value = base64.b64encode(f"{target}|{'b' * 64}".encode()).decode()
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "link_selector": "a[href]",
                "include_path_prefixes": ["/files/bills/"],
                "document_extensions": [".pdf"],
                "max_candidates": 2,
                "wrapped_link_target": {
                    "path": "/processFile.php",
                    "query_parameter": "token",
                    "encoding": "base64_url_sha256_v1",
                },
            },
        )
        html = (
            f'<a href="processFile.php?token={quote(wrapped_value, safe="")}">External</a>'
        ).encode()
        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    content=html,
                )
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "no qualifying publication links"):
            ConfiguredHTMLListingConnector(client_factory=lambda: client).discover(
                self.endpoint,
                cursor=None,
            )

    def test_html_listing_connector_routes_detail_page_primary_document(self) -> None:
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "include_path_prefixes": ["/publications/", "/files/"],
                "upload_path_prefixes": ["/files/"],
                "document_extensions": [".pdf"],
                "max_candidates": 2,
                "follow_detail_pages": True,
                "detail_content_selector": ".betterdocs-entry-content",
            },
        )
        listing = b'<a href="/publications/rule-one/">Rule One</a>'
        detail = b"""
            <div class="betterdocs-entry-content">
              <div class="wp-block-file">
                <a href="/files/rule-one.pdf">Rule One PDF</a>
                <a href="/files/rule-one.pdf">Download</a>
              </div>
            </div>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            content = detail if request.url.path.endswith("/rule-one/") else listing
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=UTF-8"},
                content=content,
            )

        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(handler),
        )
        connector = ConfiguredHTMLListingConnector(client_factory=lambda: client)

        result = connector.discover(self.endpoint, cursor=None)
        candidates = result.candidates

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].canonical_url, "https://example.com/files/rule-one.pdf")
        self.assertEqual(
            candidates[0].metadata_hints["document_identity_url"],
            "https://example.com/publications/rule-one/",
        )
        self.assertEqual(
            candidates[0].metadata_hints["source_detail_page"],
            "https://example.com/publications/rule-one/",
        )

    def test_identical_bytes_are_stored_once_but_observed_per_run(self) -> None:
        first_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate = self.make_candidate(first_run)
        second_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate = self.make_candidate(second_run)
        content = b"%PDF-1.7\nARIA evidence"
        response = FetchResponse(
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            status_code=200,
            headers={"content-type": "application/pdf", "etag": '"version-1"'},
            redirect_chain=[],
            resolved_addresses=[PUBLIC_IP],
            content=content,
        )

        with TemporaryDirectory() as temporary_directory:
            store = FilesystemArtifactStore(Path(temporary_directory))
            with patch(
                "aria.orchestration.tasks.process_change_orchestration.delay"
            ) as orchestration_delay:
                first_attempt = begin_fetch_attempt(candidate, first_run, request_headers={})
                with self.captureOnCommitCallbacks(execute=True):
                    first_observation = complete_fetch(first_attempt, response, store=store)
                DiscoveredCandidate.objects.filter(pk=candidate.id).update(
                    pipeline_state=DiscoveredCandidate.PipelineState.VERSIONED
                )
                second_attempt = begin_fetch_attempt(candidate, second_run, request_headers={})
                with self.captureOnCommitCallbacks(execute=True):
                    second_observation = complete_fetch(second_attempt, response, store=store)

            artifact = RawArtifact.objects.get()
            self.assertEqual(artifact.sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(store.read(artifact.storage_key), content)
            self.assertEqual(RawArtifact.objects.count(), 1)
            self.assertEqual(ArtifactObservation.objects.count(), 2)
            self.assertEqual(first_observation.raw_artifact_id, second_observation.raw_artifact_id)
            self.assertTrue(first_observation.content_changed)
            self.assertFalse(second_observation.content_changed)
            orchestration = ChangeOrchestration.objects.get(artifact_observation=first_observation)
            orchestration_delay.assert_called_once_with(str(orchestration.id))
            candidate.refresh_from_db()
            self.assertEqual(candidate.pipeline_state, DiscoveredCandidate.PipelineState.VERSIONED)
            self.assertTrue(
                artifact.storage_key.startswith("sources/test-regulator/test-publications/sha256/")
            )
            artifact.byte_size = 1
            with self.assertRaises(ValidationError):
                artifact.save()

    def test_not_modified_response_reuses_the_previous_artifact(self) -> None:
        first_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate = self.make_candidate(first_run)
        first_response = FetchResponse(
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            status_code=200,
            headers={"etag": '"one"'},
            redirect_chain=[],
            resolved_addresses=[PUBLIC_IP],
            content=b"%PDF-1.7\nfirst",
        )
        with TemporaryDirectory() as temporary_directory:
            with patch(
                "aria.orchestration.tasks.process_change_orchestration.delay"
            ) as orchestration_delay:
                first_attempt = begin_fetch_attempt(candidate, first_run, request_headers={})
                with self.captureOnCommitCallbacks(execute=True):
                    first_observation = complete_fetch(
                        first_attempt,
                        first_response,
                        store=FilesystemArtifactStore(temporary_directory),
                    )
                DiscoveredCandidate.objects.filter(pk=candidate.id).update(
                    pipeline_state=DiscoveredCandidate.PipelineState.VERSIONED
                )
                second_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
                candidate = self.make_candidate(second_run)
                second_attempt = begin_fetch_attempt(
                    candidate,
                    second_run,
                    request_headers={"If-None-Match": '"one"'},
                )
                with self.captureOnCommitCallbacks(execute=True):
                    second_observation = complete_not_modified(
                        second_attempt,
                        FetchResponse(
                            requested_url=candidate.canonical_url,
                            final_url=candidate.canonical_url,
                            status_code=304,
                            headers={"etag": '"one"'},
                            redirect_chain=[],
                            resolved_addresses=[PUBLIC_IP],
                            content=b"",
                        ),
                    )

        self.assertEqual(first_observation.raw_artifact_id, second_observation.raw_artifact_id)
        self.assertFalse(second_observation.content_changed)
        orchestration = ChangeOrchestration.objects.get(artifact_observation=first_observation)
        orchestration_delay.assert_called_once_with(str(orchestration.id))
        candidate.refresh_from_db()
        self.assertEqual(candidate.pipeline_state, DiscoveredCandidate.PipelineState.VERSIONED)
        second_attempt.refresh_from_db()
        self.assertEqual(second_attempt.status, FetchAttempt.Status.NOT_MODIFIED)

    def test_pdf_candidate_rejects_an_html_error_page_before_storage(self) -> None:
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate = self.make_candidate(source_run)
        attempt = begin_fetch_attempt(candidate, source_run, request_headers={})

        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(UnexpectedContentTypeError):
                complete_fetch(
                    attempt,
                    FetchResponse(
                        requested_url=candidate.canonical_url,
                        final_url=candidate.canonical_url,
                        status_code=200,
                        headers={"content-type": "text/html"},
                        redirect_chain=[],
                        resolved_addresses=[PUBLIC_IP],
                        content=b"<html>upstream error</html>",
                    ),
                    store=FilesystemArtifactStore(temporary_directory),
                )

        self.assertEqual(RawArtifact.objects.count(), 0)
        self.assertEqual(ArtifactObservation.objects.count(), 0)

    def test_listing_uses_conditional_headers_and_reuses_candidates_on_304(self) -> None:
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "include_path_prefixes": ["/publications/", "/files/"],
                "document_extensions": [".pdf"],
            },
        )
        first_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate = self.make_candidate(first_run)
        first_response = FetchResponse(
            requested_url=self.endpoint.discovery_url,
            final_url=self.endpoint.discovery_url,
            status_code=200,
            headers={"etag": '"listing-one"', "content-type": "text/html"},
            redirect_chain=[],
            resolved_addresses=[PUBLIC_IP],
            content=b'<a href="/files/act.pdf">Act</a>',
        )
        record_endpoint_observation(first_run, first_response)
        mark_source_run_completed(first_run)
        second_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.SCHEDULED)

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["If-None-Match"], '"listing-one"')
            return httpx.Response(304, headers={"ETag": '"listing-one"'})

        client = SafeHttpClient(
            resolver=lambda _hostname, _port: [PUBLIC_IP],
            rate_limiter=RateLimiter(),
            transport=httpx.MockTransport(handler),
        )
        connector = ConfiguredHTMLListingConnector(client_factory=lambda: client)

        result = connector.discover(self.endpoint, cursor=second_run.cursor_before)

        self.assertEqual(result.response.status_code, 304)
        self.assertEqual(result.request_headers, {"If-None-Match": '"listing-one"'})
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].fingerprint, candidate.fingerprint)


class JpdpSeedTestCase(TestCase):
    def test_seed_creates_the_approved_official_source_idempotently(self) -> None:
        call_command("seed_jpdp")
        endpoint = SourceEndpoint.objects.get(name="JPDP Act 709 regulatory library")
        endpoint.health_state = SourceEndpoint.HealthState.HEALTHY
        endpoint.save(update_fields=("health_state", "updated_at"))
        original_next_poll_at = endpoint.next_poll_at
        call_command("seed_jpdp")

        endpoint.refresh_from_db()
        self.assertEqual(endpoint.collection.authority.country_code, "MY")
        self.assertEqual(endpoint.allowed_domains, ["pdp.gov.my"])
        self.assertEqual(endpoint.connector_type, SourceEndpoint.ConnectorType.HTML_LISTING)
        self.assertEqual(endpoint.connector_configuration_version, 4)
        self.assertEqual(endpoint.connector_configurations.count(), 4)
        self.assertFalse(endpoint.connector_configurations.get(version=1).is_active)
        self.assertFalse(endpoint.connector_configurations.get(version=2).is_active)
        self.assertFalse(endpoint.connector_configurations.get(version=3).is_active)
        active_configuration = endpoint.connector_configurations.get(version=4)
        self.assertTrue(active_configuration.is_active)
        self.assertEqual(
            active_configuration.configuration["detail_content_selectors"],
            [".betterdocs-entry-content"],
        )
        feed = endpoint.monitored_resources.get(resource_type=MonitoredResource.ResourceType.RSS)
        self.assertEqual(feed.url, "https://www.pdp.gov.my/ppdpv1/en/feed/")
        self.assertTrue(feed.is_approved)
        self.assertIn("/ppdpv1/en/akta/", endpoint.discovery_url)
        self.assertEqual(endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertEqual(endpoint.next_poll_at, original_next_poll_at)

    @patch("aria.discovery.management.commands.poll_jpdp.execute_source_run.delay")
    def test_poll_jpdp_queues_one_manual_monitor_cycle(self, execute_delay) -> None:
        call_command("seed_jpdp")

        call_command("poll_jpdp")

        source_run = SourceRun.objects.get()
        self.assertEqual(source_run.trigger, SourceRun.Trigger.MANUAL)
        self.assertEqual(source_run.status, SourceRun.Status.PENDING)
        execute_delay.assert_called_once_with(str(source_run.id))
