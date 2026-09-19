import hashlib
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.authorities.models import Authority
from aria.browser.models import SourceAdmissionAssessment, SourceAdmissionPromotion
from aria.collections.models import PublicationCollection
from aria.comparisons.models import ComparisonSummary, DocumentComparison
from aria.discovery.models import CandidateObservation, DiscoveredCandidate, SourceRun
from aria.documents.models import (
    DocumentIdentity,
    DocumentVersion,
    NormalizedSection,
    VersionEvidence,
)
from aria.events.models import AuditEvent
from aria.extraction.models import ExtractedBlock, ExtractedDocument, ExtractionRun
from aria.fetching.models import FetchAttempt
from aria.knowledge.embeddings import EmbeddingError
from aria.reader.evaluation import (
    ReaderRetrievalCase,
    _normalized_url_text,
    evaluate_reader_retrieval,
)
from aria.reader.services import _title_overlap_score, search_reader_documents
from aria.reliability.models import SourceReliabilityAssessment
from aria.reliability.soak import collect_autonomous_cycle_acceptance
from aria.sources.models import SourceEndpoint, SourcePackSnapshot


@override_settings(
    EMBEDDING_PROVIDER="local_hash",
    READER_EMBEDDING_PROVIDER="local_hash",
    READER_FRONTEND="server",
    LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
    READER_MAX_QUERY_CHARACTERS=500,
    READER_PAGE_SIZE=10,
    READER_MAX_PAGE_SIZE=20,
    READER_MAX_SEARCH_PAGES=10,
    READER_MAX_SEARCH_RESULTS=200,
    READER_SEARCH_CANDIDATE_LIMIT=100,
    READER_MAX_PASSAGES_PER_DOCUMENT=3,
    READER_MAX_DOCUMENT_SECTIONS=2000,
)
class ReaderInterfaceTestCase(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage_root = Path(self.temporary_directory.name)
        user_model = get_user_model()
        self.reader = user_model.objects.create_user(username="reader", password="reader-pass")
        self.staff = user_model.objects.create_user(
            username="operator",
            password="operator-pass",
            is_staff=True,
        )
        self.authority = Authority.objects.create(
            name="Test Official Authority",
            slug="test-official-authority",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.test"],
            trust_classification=Authority.TrustClassification.AUTHORITATIVE,
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Official Guidance",
            slug="official-guidance",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
            default_legal_status="Official guidance",
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official guidance listing",
            discovery_url="https://example.test/guidance/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.test"],
            expected_content_types=["text/html"],
            next_poll_at=timezone.now() + timedelta(hours=1),
        )
        now = timezone.now()
        self.source_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="reader-fixture",
            connector_configuration_version=1,
            started_at=now,
            finished_at=now,
            discovered_candidate_count=1,
        )
        self.candidate = DiscoveredCandidate.objects.create(
            endpoint=self.endpoint,
            latest_source_run=self.source_run,
            discovered_url="https://example.test/guidance/data-processing/",
            canonical_url="https://example.test/guidance/data-processing/",
            fingerprint="9" * 64,
            metadata_hints={"title": "Official Data Processing Guidance"},
            pipeline_state=DiscoveredCandidate.PipelineState.VERSIONED,
            first_discovered_at=now,
            last_discovered_at=now,
        )
        CandidateObservation.objects.create(source_run=self.source_run, candidate=self.candidate)
        self.fetch_attempt = FetchAttempt.objects.create(
            candidate=self.candidate,
            source_run=self.source_run,
            attempt_number=1,
            status=FetchAttempt.Status.SUCCEEDED,
            requested_url=self.candidate.canonical_url,
            final_url=self.candidate.canonical_url,
            response_status=200,
            response_headers={"content-type": "text/html"},
            started_at=now,
            finished_at=now,
        )
        storage_key = "sources/test-official-authority/official-guidance/guidance.html"
        artifact_path = self.storage_root / storage_key
        artifact_path.parent.mkdir(parents=True)
        artifact_content = b"official evidence bytes"
        artifact_path.write_bytes(artifact_content)
        self.artifact = RawArtifact.objects.create(
            sha256=hashlib.sha256(artifact_content).hexdigest(),
            byte_size=len(artifact_content),
            detected_content_type="text/html",
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key=storage_key,
        )
        self.observation = ArtifactObservation.objects.create(
            raw_artifact=self.artifact,
            fetch_attempt=self.fetch_attempt,
            candidate=self.candidate,
            source_run=self.source_run,
            requested_url=self.candidate.canonical_url,
            final_url=self.candidate.canonical_url,
            response_status=200,
            response_headers={"content-type": "text/html"},
            content_changed=True,
            retrieved_at=now,
            connector_configuration_version=1,
        )
        self.extraction_run = ExtractionRun.objects.create(
            raw_artifact=self.artifact,
            extractor_name="reader-test-html",
            extractor_version="1",
            configuration_hash="0" * 64,
            status=ExtractionRun.Status.SUCCEEDED,
            started_at=now,
            finished_at=now,
        )
        extracted = ExtractedDocument.objects.create(
            extraction_run=self.extraction_run,
            raw_artifact=self.artifact,
            title="Official Data Processing Guidance",
            language_hint="en",
            plain_text="Organizations must protect personal data during processing.",
            plain_text_sha256="b" * 64,
        )
        block = ExtractedBlock.objects.create(
            extracted_document=extracted,
            ordinal=1,
            block_type=ExtractedBlock.BlockType.PARAGRAPH,
            heading="Protection principle",
            text="Organizations must protect personal data during processing.",
            text_sha256="c" * 64,
            page_number=2,
            char_start=0,
            char_end=59,
            source_locator={"page": 2},
        )
        self.identity = DocumentIdentity.objects.create(
            collection=self.collection,
            stable_key="d" * 64,
            canonical_title="Official Data Processing Guidance",
            canonical_url=self.candidate.canonical_url,
            identity_basis={"kind": "canonical_url"},
        )
        self.version = DocumentVersion.objects.create(
            identity=self.identity,
            normalized_content_sha256="e" * 64,
            title="Official Data Processing Guidance",
            canonical_url=self.candidate.canonical_url,
            language_hint="en",
            plain_content=extracted.plain_text,
            normalized_metadata={},
            extractor_name=self.extraction_run.extractor_name,
            extractor_version=self.extraction_run.extractor_version,
        )
        VersionEvidence.objects.create(
            document_version=self.version,
            raw_artifact=self.artifact,
            extraction_run=self.extraction_run,
            artifact_observation=self.observation,
            observed_url=self.candidate.canonical_url,
        )
        self.section = NormalizedSection.objects.create(
            document_version=self.version,
            source_artifact=self.artifact,
            extraction_run=self.extraction_run,
            source_block=block,
            ordinal=1,
            section_type="paragraph",
            heading="Protection principle",
            text=block.text,
            text_sha256="f" * 64,
            page_number=2,
            char_start=0,
            char_end=59,
            source_locator={"page": 2},
        )

    def test_reader_is_private_but_accepts_non_staff_accounts(self) -> None:
        response = self.client.get(reverse("reader:search"))
        self.assertRedirects(
            response,
            f"{reverse('reader:login')}?next={reverse('reader:search')}",
        )
        self.client.force_login(self.reader)

        response = self.client.get(reverse("reader:search"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find the passage. Verify the evidence.")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertRedirects(
            self.client.get(reverse("console:dashboard")),
            f"{reverse('console:login')}?next={reverse('console:dashboard')}",
        )

    def test_full_text_search_groups_passages_with_traceable_evidence(self) -> None:
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("reader:search"),
            {"q": "protect personal data processing", "mode": "full_text"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Official Data Processing Guidance")
        self.assertContains(response, "Organizations must protect personal data")
        self.assertContains(response, self.artifact.sha256[:16])
        self.assertContains(response, "Test Official Authority")

    def test_reader_api_is_separate_authenticated_and_bounded(self) -> None:
        endpoint = reverse("reader-api:search")
        self.assertEqual(self.client.get(endpoint, {"q": "personal data"}).status_code, 403)
        self.client.force_login(self.reader)

        response = self.client.get(
            endpoint,
            {"q": "protect personal data", "mode": "full_text", "page_size": 5},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["bounded_result_count"], 1)
        self.assertEqual(payload["results"][0]["identity_id"], str(self.identity.id))
        self.assertEqual(
            payload["results"][0]["passages"][0]["artifact"]["sha256"],
            self.artifact.sha256,
        )
        document_response = self.client.get(
            reverse("reader-api:document-detail", kwargs={"identity_id": self.identity.id})
        )
        self.assertEqual(document_response.status_code, 200)
        self.assertEqual(document_response.json()["section_count"], 1)
        search_event = AuditEvent.objects.get(action="reader.search.completed")
        self.assertEqual(search_event.actor_identifier, str(self.reader.pk))
        self.assertEqual(search_event.details["bounded_result_count"], 1)
        self.assertNotIn("protect personal data", json.dumps(search_event.details))
        document_event = AuditEvent.objects.get(action="reader.document.viewed")
        self.assertEqual(document_event.target_id, self.identity.id)
        self.assertEqual(
            self.client.get(endpoint, {"q": "x", "page": 11}).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(endpoint, {"q": "x", "date_from": "07/08/2026"}).status_code,
            400,
        )
        options_response = self.client.get(reverse("reader-api:options"))
        self.assertEqual(options_response.status_code, 200)
        self.assertEqual(options_response.json()["authorities"][0]["slug"], self.authority.slug)
        self.assertEqual(
            options_response.json()["collections"][0]["id"],
            str(self.collection.id),
        )

    def test_reader_can_browse_current_documents_by_authority_without_a_query(self) -> None:
        self.client.force_login(self.reader)
        endpoint = reverse("reader-api:search")

        response = self.client.get(
            endpoint,
            {"authority": self.authority.slug, "page_size": 10},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "browse")
        self.assertEqual(payload["query"], "")
        self.assertEqual(payload["bounded_result_count"], 1)
        self.assertEqual(payload["results"][0]["identity_id"], str(self.identity.id))
        self.assertEqual(
            payload["results"][0]["passages"][0]["artifact"]["sha256"],
            self.artifact.sha256,
        )

        page_response = self.client.get(
            reverse("reader:search"),
            {"authority": self.authority.slug},
        )
        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "1 document available")
        self.assertContains(page_response, "Official Data Processing Guidance")

        unfiltered_response = self.client.get(endpoint)
        self.assertEqual(unfiltered_response.status_code, 400)
        self.assertIn("Choose an authority or collection", unfiltered_response.json()["detail"])

    def test_hybrid_search_falls_back_to_full_text_without_hiding_it(self) -> None:
        with patch("aria.reader.services.embed_text", side_effect=EmbeddingError("offline")):
            result = search_reader_documents(
                "protect personal data",
                mode="hybrid",
                provider_name="local_hash",
            )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["embedding"], None)
        self.assertIn("exact text ranking only", result["warnings"][0])

    def test_reader_relevance_evaluation_reports_stable_document_rank(self) -> None:
        report = evaluate_reader_retrieval(
            mode="full_text",
            cases=(
                ReaderRetrievalCase(
                    name="fixture",
                    query="protect personal data processing",
                    authority_slug=self.authority.slug,
                    expected_url_fragment="DATA-PROCESSING/",
                ),
            ),
        )

        self.assertEqual(report["benchmark"], "aria-reader-multisource-v1")
        self.assertEqual(report["mean_reciprocal_rank"], 1.0)
        self.assertEqual(report["hit_at_3"], 1.0)
        self.assertEqual(report["cases"][0]["rank"], 1)

    def test_reader_benchmark_matches_encoded_urls_case_insensitively(self) -> None:
        expected = _normalized_url_text("GOVERNMENT PROCUREMENT ACT 2026.pdf")
        observed = _normalized_url_text("Act%20882%20-%20GOVERNMENT%20PROCUREMENT%20ACT%202026.pdf")

        self.assertIn(expected, observed)

    def test_title_overlap_favors_the_named_official_publication(self) -> None:
        query = "When is an organization required to appoint a data protection officer?"

        expected = _title_overlap_score(
            query,
            "appointment-of-data-protection-officer",
        )
        adjacent = _title_overlap_score(
            query,
            "data-protection-impact-assessment-guideline",
        )

        self.assertGreater(expected, adjacent)

    def test_vector_only_failure_returns_service_unavailable(self) -> None:
        self.client.force_login(self.reader)
        with patch("aria.reader.services.embed_text", side_effect=EmbeddingError("offline")):
            response = self.client.get(
                reverse("reader-api:search"),
                {"q": "protect personal data", "mode": "vector"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("temporarily unavailable", response.json()["detail"])

    def test_document_page_exposes_exact_text_and_evidence_boundary(self) -> None:
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("reader:document-detail", kwargs={"identity_id": self.identity.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Organizations must protect personal data")
        self.assertContains(response, self.artifact.sha256)
        self.assertContains(response, "not a legal commencement date")
        self.assertNotContains(response, "GPT-generated change summary")

    def test_review_gated_gpt_change_summary_is_labelled(self) -> None:
        before = DocumentVersion.objects.create(
            identity=self.identity,
            normalized_content_sha256="6" * 64,
            title="Official Data Processing Guidance",
            canonical_url=self.candidate.canonical_url,
            language_hint="en",
            plain_content="Earlier official text.",
            normalized_metadata={},
            extractor_name=self.extraction_run.extractor_name,
            extractor_version=self.extraction_run.extractor_version,
            created_at=timezone.now() - timedelta(days=1),
        )
        comparison = DocumentComparison.objects.create(
            identity=self.identity,
            before_version=before,
            after_version=self.version,
            comparison_track_key="reader-test-track",
            ruleset="reader-test-ruleset",
            configuration={},
            configuration_hash="7" * 64,
            input_fingerprint="8" * 64,
            status=DocumentComparison.Status.COMPLETED,
            modified_count=1,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        ComparisonSummary.objects.create(
            comparison=comparison,
            provider="openrouter",
            model="gpt-test",
            prompt_version="reader-test-v1",
            input_hash="0" * 64,
            input_snapshot={},
            status=ComparisonSummary.Status.COMPLETED,
            output={
                "title": "Reviewed textual change",
                "overview": "One human-confirmed passage changed.",
                "changes": [],
                "caveats": ["Read the official evidence."],
                "legal_effect_not_assessed": True,
            },
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("reader:document-detail", kwargs={"identity_id": self.identity.id})
        )

        self.assertContains(response, "GPT-generated change summary")
        self.assertContains(response, "Reviewed textual change")
        self.assertContains(response, "Legal effect was not assessed")

    def test_only_document_evidence_can_be_downloaded(self) -> None:
        self.client.force_login(self.reader)
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            response = self.client.get(
                reverse("reader:artifact-content", kwargs={"artifact_id": self.artifact.id})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-SHA256"], self.artifact.sha256)
        self.assertEqual(response["X-ARIA-Evidence"], "immutable-artifact")
        self.assertEqual(
            AuditEvent.objects.filter(action="reader.evidence.downloaded").count(),
            1,
        )
        (self.storage_root / self.artifact.storage_key).write_bytes(b"tampered")
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            corrupt_response = self.client.get(
                reverse("reader:artifact-content", kwargs={"artifact_id": self.artifact.id})
            )
        self.assertEqual(corrupt_response.status_code, 409)
        self.assertEqual(
            AuditEvent.objects.filter(action="reader.evidence.downloaded").count(),
            1,
        )
        orphan = RawArtifact.objects.create(
            sha256="1" * 64,
            byte_size=1,
            detected_content_type="application/octet-stream",
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key="orphan.bin",
        )
        self.assertEqual(
            self.client.get(
                reverse("reader:artifact-content", kwargs={"artifact_id": orphan.id})
            ).status_code,
            404,
        )

    def test_search_template_escapes_query_content(self) -> None:
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("reader:search"),
            {"q": "personal data <script>alert(1)</script>", "mode": "full_text"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert(1)</script>", html=False)
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)

    def _promotion(self) -> SourceAdmissionPromotion:
        snapshot = SourcePackSnapshot.objects.create(
            endpoint=self.endpoint,
            pack_slug="reader-test-source",
            schema_version=1,
            pack_version=1,
            checksum="2" * 64,
            definition={"slug": "reader-test-source", "schema_version": 1, "version": 1},
        )
        assessment = SourceAdmissionAssessment.objects.create(
            endpoint=self.endpoint,
            source_pack_snapshot=snapshot,
            admission_profile=SourceAdmissionAssessment.Profile.STATIC_LISTING,
            status=SourceAdmissionAssessment.Status.READY,
            report_signature="3" * 64,
            required_evidence_count=2,
            evaluated_source_run_ids=[str(self.source_run.id)],
            candidate_set_sha256="4" * 64,
            candidate_count=1,
            gates=[{"name": "ready", "passed": True, "detail": "Ready."}],
        )
        return SourceAdmissionPromotion.objects.create(
            endpoint=self.endpoint,
            assessment=assessment,
            next_poll_at=timezone.now() + timedelta(hours=1),
            actor_identifier="test",
        )

    def test_autonomous_acceptance_stays_pending_before_first_due_cycle(self) -> None:
        self._promotion()

        report = collect_autonomous_cycle_acceptance(assessed_at=timezone.now())

        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["sources"][0]["status"], "pending")
        self.assertIn("Awaiting", report["sources"][0]["detail"])

    def test_completed_scheduled_cycle_passes_all_acceptance_gates(self) -> None:
        self._promotion()
        now = timezone.now()
        scheduled = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="reader-scheduled-fixture",
            connector_configuration_version=1,
            started_at=now,
            finished_at=now,
            discovered_candidate_count=1,
        )
        CandidateObservation.objects.create(source_run=scheduled, candidate=self.candidate)
        attempt = FetchAttempt.objects.create(
            candidate=self.candidate,
            source_run=scheduled,
            attempt_number=1,
            status=FetchAttempt.Status.NOT_MODIFIED,
            requested_url=self.candidate.canonical_url,
            final_url=self.candidate.canonical_url,
            response_status=304,
            started_at=now,
            finished_at=now,
        )
        ArtifactObservation.objects.create(
            raw_artifact=self.artifact,
            fetch_attempt=attempt,
            candidate=self.candidate,
            source_run=scheduled,
            requested_url=self.candidate.canonical_url,
            final_url=self.candidate.canonical_url,
            response_status=304,
            content_changed=False,
            retrieved_at=now,
            connector_configuration_version=1,
        )
        self.endpoint.next_poll_at = now + timedelta(days=1)
        self.endpoint.save(update_fields=("next_poll_at", "updated_at"))
        SourceReliabilityAssessment.objects.create(
            endpoint=self.endpoint,
            source_run=scheduled,
            status=SourceReliabilityAssessment.Status.HEALTHY,
            assessment_signature="5" * 64,
            candidate_count=1,
            artifact_ready_count=1,
            extraction_ready_count=1,
            graph_ready_count=1,
            section_count=1,
            local_embedding_count=1,
            configured_embedding_count=1,
        )

        report = collect_autonomous_cycle_acceptance(assessed_at=now + timedelta(minutes=1))

        self.assertEqual(report["status"], "passed")
        self.assertTrue(all(gate["passed"] for gate in report["sources"][0]["gates"]))
        self.assertEqual(report["sources"][0]["artifact_changes"]["unchanged"], 1)


@override_settings(READER_FRONTEND="react")
class ReaderReactShellTestCase(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.dist = Path(self.temporary_directory.name)
        manifest_directory = self.dist / ".vite"
        asset_directory = self.dist / "assets"
        manifest_directory.mkdir(parents=True)
        asset_directory.mkdir(parents=True)
        (manifest_directory / "manifest.json").write_text(
            """{
              "index.html": {
                "file": "assets/reader-test.js",
                "css": ["assets/reader-test.css"]
              }
            }""",
            encoding="utf-8",
        )
        (asset_directory / "reader-test.js").write_text(
            "document.documentElement.dataset.readerLoaded = 'true';",
            encoding="utf-8",
        )
        (asset_directory / "reader-test.css").write_text(
            ":root { color-scheme: light dark; }",
            encoding="utf-8",
        )
        self.reader = get_user_model().objects.create_user(
            username="react-reader",
            password="reader-pass",
        )

    def test_react_shell_is_private_same_origin_and_uses_hashed_build_contract(self) -> None:
        response = self.client.get(reverse("reader:search"))
        self.assertRedirects(
            response,
            f"{reverse('reader:login')}?next={reverse('reader:search')}",
        )
        self.client.force_login(self.reader)

        with override_settings(READER_FRONTEND_DIST=self.dist):
            response = self.client.get(reverse("reader:search"))
            script_response = self.client.get(
                reverse(
                    "reader:app-asset",
                    kwargs={"asset_path": "assets/reader-test.js"},
                )
            )
            manifest_response = self.client.get(
                reverse(
                    "reader:app-asset",
                    kwargs={"asset_path": ".vite/manifest.json"},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="aria-reader-root"')
        self.assertContains(response, "assets/app/assets/reader-test.js")
        self.assertContains(response, 'data-user-name="react-reader"')
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(manifest_response.status_code, 404)
        self.assertEqual(script_response["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(
            b"".join(script_response.streaming_content),
            b"document.documentElement.dataset.readerLoaded = 'true';",
        )

    def test_document_route_bootstraps_identity_without_reading_it_in_html_view(self) -> None:
        self.client.force_login(self.reader)
        identity_id = "11111111-1111-4111-8111-111111111111"

        with override_settings(READER_FRONTEND_DIST=self.dist):
            response = self.client.get(
                reverse("reader:document-detail", kwargs={"identity_id": identity_id})
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-document-id="{identity_id}"')

    def test_missing_frontend_bundle_fails_closed_with_recovery_command(self) -> None:
        self.client.force_login(self.reader)
        missing_dist = self.dist / "missing"

        with override_settings(READER_FRONTEND_DIST=missing_dist):
            response = self.client.get(reverse("reader:search"))

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "make frontend-build", status_code=503)
