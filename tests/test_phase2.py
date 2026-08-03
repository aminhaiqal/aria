import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.html_connector import ConfiguredHTMLListingConnector
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.discovery.services import create_source_run, observe_candidate
from aria.fetching.client import (
    FetchResponse,
    RateLimiter,
    ResponseTooLargeError,
    SafeHttpClient,
    UnsafeTargetError,
    validate_target_url,
)
from aria.fetching.models import FetchAttempt
from aria.fetching.services import begin_fetch_attempt, complete_fetch, complete_not_modified
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

        candidates = connector.discover(self.endpoint, cursor=None)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].canonical_url, "https://example.com/files/rule-two.pdf")
        self.assertEqual(candidates[0].metadata_hints["title"], "Rule Two PDF")
        self.assertEqual(len(candidates[0].fingerprint), 64)

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
            first_attempt = begin_fetch_attempt(candidate, first_run, request_headers={})
            first_observation = complete_fetch(first_attempt, response, store=store)
            second_attempt = begin_fetch_attempt(candidate, second_run, request_headers={})
            second_observation = complete_fetch(second_attempt, response, store=store)

            artifact = RawArtifact.objects.get()
            self.assertEqual(artifact.sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(store.read(artifact.storage_key), content)
            self.assertEqual(RawArtifact.objects.count(), 1)
            self.assertEqual(ArtifactObservation.objects.count(), 2)
            self.assertEqual(first_observation.raw_artifact_id, second_observation.raw_artifact_id)
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
            content=b"first",
        )
        with TemporaryDirectory() as temporary_directory:
            first_attempt = begin_fetch_attempt(candidate, first_run, request_headers={})
            first_observation = complete_fetch(
                first_attempt,
                first_response,
                store=FilesystemArtifactStore(temporary_directory),
            )
            second_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
            candidate = self.make_candidate(second_run)
            second_attempt = begin_fetch_attempt(
                candidate,
                second_run,
                request_headers={"If-None-Match": '"one"'},
            )
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
        second_attempt.refresh_from_db()
        self.assertEqual(second_attempt.status, FetchAttempt.Status.NOT_MODIFIED)


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
        self.assertEqual(endpoint.connector_configurations.count(), 1)
        self.assertIn("/ppdpv1/en/akta/", endpoint.discovery_url)
        self.assertEqual(endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertEqual(endpoint.next_poll_at, original_next_poll_at)
