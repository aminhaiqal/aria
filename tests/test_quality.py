from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run, observe_candidate
from aria.extraction.services import extract_artifact
from aria.fetching.client import FetchResponse
from aria.fetching.services import begin_fetch_attempt, complete_fetch
from aria.quality.models import (
    DocumentQualityAssessment,
    QualityAssessmentRun,
    QualityFinding,
)
from aria.quality.services import assess_collection_quality
from aria.sources.models import SourceEndpoint


@override_settings(
    EMBEDDING_PROVIDER="local_hash",
    EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
)
class QualityAssessmentTestCase(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage_root = Path(self.temporary_directory.name)
        self.authority = Authority.objects.create(
            name="Test official regulator",
            slug="quality-test-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Quality corpus",
            slug="quality-corpus",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official quality corpus",
            discovery_url="https://example.com/library/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html"],
        )
        self.source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)

        shared_content = self._html(
            "Personal Data Protection Requirements",
            "Personal data protection requirements govern accountable processing activities. ",
        )
        unique_content = self._html(
            "Security Safeguards Standard",
            "Security safeguards standard requires proportionate technical controls. ",
        )
        first_artifact = self._observe("shared-one", shared_content)
        second_artifact = self._observe("shared-two", shared_content)
        third_artifact = self._observe("unique", unique_content)
        self.assertEqual(first_artifact.id, second_artifact.id)

        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(first_artifact)
            extract_artifact(third_artifact)

    @staticmethod
    def _html(title: str, sentence: str) -> bytes:
        paragraphs = "".join(f"<p>{sentence}{index}</p>" for index in range(1, 9))
        return f"<html><body><main><h1>{title}</h1>{paragraphs}</main></body></html>".encode()

    def _observe(self, slug: str, content: bytes):
        url = f"https://example.com/library/{slug}/"
        candidate, _ = observe_candidate(
            self.source_run,
            CandidateData(
                discovered_url=url,
                canonical_url=url,
                fingerprint=(slug.encode().hex() + "0" * 64)[:64],
            ),
        )
        attempt = begin_fetch_attempt(candidate, self.source_run, request_headers={})
        observation = complete_fetch(
            attempt,
            FetchResponse(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                redirect_chain=[],
                resolved_addresses=["93.184.216.34"],
                content=content,
            ),
            store=FilesystemArtifactStore(self.storage_root),
        )
        return observation.raw_artifact

    def test_corpus_quality_flags_duplicates_and_preserves_provenance(self) -> None:
        run = assess_collection_quality(self.collection)

        self.assertEqual(run.status, QualityAssessmentRun.Status.COMPLETED)
        self.assertEqual(run.document_count, 3)
        self.assertEqual(run.passed_count, 1)
        self.assertEqual(run.warning_count, 0)
        self.assertEqual(run.review_required_count, 2)
        duplicate_findings = QualityFinding.objects.filter(
            assessment__quality_run=run,
            code="duplicate_normalized_content",
        )
        self.assertEqual(duplicate_findings.count(), 2)
        for finding in QualityFinding.objects.filter(assessment__quality_run=run):
            self.assertEqual(finding.document_version_id, finding.assessment.document_version_id)
            self.assertEqual(finding.source_artifact_id, finding.assessment.source_artifact_id)

    def test_unchanged_corpus_reuses_completed_quality_run(self) -> None:
        first = assess_collection_quality(self.collection)
        replay = assess_collection_quality(self.collection)

        self.assertEqual(first.id, replay.id)
        self.assertEqual(QualityAssessmentRun.objects.count(), 1)
        self.assertEqual(DocumentQualityAssessment.objects.count(), 3)

    def test_operational_quality_run_assesses_latest_version_per_identity(self) -> None:
        identity_url = "https://example.com/library/unique/"
        identity = self.collection.document_identities.get(canonical_url=identity_url)
        existing_version = identity.versions.get()
        source_run = (
            existing_version.evidence_records.get().raw_artifact.observations.get().source_run
        )
        linked_url = "https://example.com/files/unique-current/"
        candidate, _ = observe_candidate(
            source_run,
            CandidateData(
                discovered_url=linked_url,
                canonical_url=linked_url,
                fingerprint="f" * 64,
                metadata_hints={"document_identity_url": identity_url},
            ),
        )
        current_content = self._html(
            "Current Security Safeguards Standard",
            "Current security safeguards require tested technical controls. ",
        )
        attempt = begin_fetch_attempt(candidate, source_run, request_headers={})
        observation = complete_fetch(
            attempt,
            FetchResponse(
                requested_url=linked_url,
                final_url=linked_url,
                status_code=200,
                headers={"content-type": "text/html"},
                redirect_chain=[],
                resolved_addresses=["93.184.216.34"],
                content=current_content,
            ),
            store=FilesystemArtifactStore(self.storage_root),
        )
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(observation.raw_artifact)

        run = assess_collection_quality(self.collection)

        identity.refresh_from_db()
        self.assertEqual(identity.versions.count(), 2)
        self.assertEqual(run.document_count, 3)
        assessment = run.document_assessments.get(document_version__identity=identity)
        self.assertEqual(assessment.document_version.title, "Current Security Safeguards Standard")

    def test_quality_api_exposes_metrics_and_findings_read_only(self) -> None:
        run = assess_collection_quality(self.collection)
        user = get_user_model().objects.create_superuser(
            username="quality-admin", password="test-password"
        )
        self.client.force_login(user)

        run_response = self.client.get(reverse("qualityassessmentrun-detail", args=[run.id]))
        assessment_response = self.client.get(reverse("documentqualityassessment-list"))
        finding_response = self.client.get(reverse("qualityfinding-list"))

        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["document_count"], 3)
        self.assertEqual(assessment_response.status_code, 200)
        self.assertEqual(assessment_response.json()["count"], 3)
        self.assertIn("artifact_sha256", assessment_response.json()["results"][0])
        self.assertEqual(finding_response.status_code, 200)
        self.assertGreaterEqual(finding_response.json()["count"], 2)
        self.assertEqual(
            self.client.post(reverse("qualityassessmentrun-list"), {}).status_code, 405
        )
