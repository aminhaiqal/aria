from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation, RawArtifact
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import CandidateObservation, DiscoveredCandidate, SourceRun
from aria.documents.models import (
    DocumentIdentity,
    DocumentVersion,
    NormalizedSection,
    VersionEvidence,
)
from aria.events.models import AuditEvent, PipelineEvent
from aria.extraction.models import ExtractedBlock, ExtractedDocument, ExtractionRun
from aria.fetching.models import FetchAttempt
from aria.knowledge.models import GraphNode, SectionEmbedding
from aria.ocr.models import OCRRun
from aria.reliability.models import SourceReliabilityAssessment
from aria.reliability.repair import build_source_repair_plan, queue_source_repairs
from aria.reliability.services import assess_source_reliability
from aria.reliability.tasks import assess_enabled_sources
from aria.sources.models import SourceEndpoint


@override_settings(
    EMBEDDING_PROVIDER="openai",
    OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
    LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
    SOURCE_FRESHNESS_GRACE_MINUTES=60,
    SOURCE_PIPELINE_GRACE_MINUTES=30,
)
class ReliabilityFixture(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.authority = Authority.objects.create(
            name="Reliability regulator",
            slug="reliability-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Reliability publications",
            slug="reliability-publications",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official reliability source",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html"],
            polling_interval_minutes=60,
            next_poll_at=self.now + timedelta(minutes=60),
            last_successful_run_at=self.now,
        )
        self.source_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="reliability-run",
            connector_configuration_version=1,
            started_at=self.now - timedelta(minutes=1),
            finished_at=self.now,
            discovered_candidate_count=1,
        )
        self.candidate = self._candidate("https://example.com/publications/rule.pdf", "1" * 64)
        self._complete_downstream(self.candidate, "2" * 64)

    def _candidate(self, url: str, fingerprint: str) -> DiscoveredCandidate:
        candidate = DiscoveredCandidate.objects.create(
            endpoint=self.endpoint,
            latest_source_run=self.source_run,
            discovered_url=url,
            canonical_url=url,
            fingerprint=fingerprint,
            metadata_hints={"title": "Official rule"},
            first_discovered_at=self.now,
            last_discovered_at=self.now,
        )
        CandidateObservation.objects.create(
            source_run=self.source_run,
            candidate=candidate,
        )
        return candidate

    def _complete_downstream(self, candidate, artifact_hash: str, *, project: bool = True):
        attempt = FetchAttempt.objects.create(
            candidate=candidate,
            source_run=self.source_run,
            attempt_number=1,
            status=FetchAttempt.Status.SUCCEEDED,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            bytes_received=100,
            started_at=self.now,
            finished_at=self.now,
        )
        artifact = RawArtifact.objects.create(
            sha256=artifact_hash,
            byte_size=100,
            detected_content_type="application/pdf",
            storage_backend=RawArtifact.StorageBackend.S3,
            storage_key=f"reliability/{artifact_hash}",
        )
        observation = ArtifactObservation.objects.create(
            raw_artifact=artifact,
            fetch_attempt=attempt,
            candidate=candidate,
            source_run=self.source_run,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            connector_configuration_version=1,
        )
        extraction = ExtractionRun.objects.create(
            raw_artifact=artifact,
            extractor_name="pypdf",
            extractor_version="1",
            configuration_hash="3" * 64,
            status=ExtractionRun.Status.SUCCEEDED,
            started_at=self.now,
            finished_at=self.now,
        )
        extracted = ExtractedDocument.objects.create(
            extraction_run=extraction,
            raw_artifact=artifact,
            title="Official rule",
            language_hint="en",
            plain_text="Section 1\nOfficial rule text.",
            plain_text_sha256="4" * 64,
            page_count=1,
        )
        block = ExtractedBlock.objects.create(
            extracted_document=extracted,
            ordinal=0,
            block_type=ExtractedBlock.BlockType.PARAGRAPH,
            text="Official rule text.",
            text_sha256="5" * 64,
            page_number=1,
            char_start=0,
            char_end=19,
        )
        identity = DocumentIdentity.objects.create(
            collection=self.collection,
            stable_key=artifact_hash,
            canonical_title="Official rule",
            canonical_url=candidate.canonical_url,
        )
        version = DocumentVersion.objects.create(
            identity=identity,
            normalized_content_sha256=artifact_hash,
            title="Official rule",
            canonical_url=candidate.canonical_url,
            plain_content="Section 1\nOfficial rule text.",
            extractor_name="pypdf",
            extractor_version="1",
        )
        VersionEvidence.objects.create(
            document_version=version,
            raw_artifact=artifact,
            extraction_run=extraction,
            artifact_observation=observation,
            observed_url=candidate.canonical_url,
        )
        section = NormalizedSection.objects.create(
            document_version=version,
            source_artifact=artifact,
            extraction_run=extraction,
            source_block=block,
            ordinal=0,
            section_type=ExtractedBlock.BlockType.PARAGRAPH,
            text="Official rule text.",
            text_sha256="5" * 64,
            page_number=1,
            char_start=0,
            char_end=19,
        )
        if project:
            GraphNode.objects.create(
                node_type=GraphNode.NodeType.VERSION,
                canonical_key=f"version:{version.id}",
                label="Official rule version",
                source_type="document_version",
                source_id=version.id,
            )
            GraphNode.objects.create(
                node_type=GraphNode.NodeType.ARTIFACT,
                canonical_key=f"artifact:sha256:{artifact.sha256}",
                label="Official artifact",
                source_type="raw_artifact",
                source_id=artifact.id,
            )
            for provider, model in (
                ("local_hash", "aria-token-hash-v1"),
                ("openai", "text-embedding-3-small"),
            ):
                SectionEmbedding.objects.create(
                    normalized_section=section,
                    provider=provider,
                    model=model,
                    dimensions=384,
                    source_text_sha256=section.text_sha256,
                    embedding=[0.0] * 384,
                )
        return artifact, version

    def _archive_only(self, candidate, artifact_hash: str):
        attempt = FetchAttempt.objects.create(
            candidate=candidate,
            source_run=self.source_run,
            attempt_number=1,
            status=FetchAttempt.Status.SUCCEEDED,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            bytes_received=100,
            started_at=self.now,
            finished_at=self.now,
        )
        artifact = RawArtifact.objects.create(
            sha256=artifact_hash,
            byte_size=100,
            detected_content_type="application/pdf",
            storage_backend=RawArtifact.StorageBackend.S3,
            storage_key=f"reliability/{artifact_hash}",
        )
        ArtifactObservation.objects.create(
            raw_artifact=artifact,
            fetch_attempt=attempt,
            candidate=candidate,
            source_run=self.source_run,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            connector_configuration_version=1,
        )
        return artifact


class SourceReliabilityAssessmentTestCase(ReliabilityFixture):
    def test_healthy_assessment_is_complete_append_only_and_idempotent(self) -> None:
        assessment, created = assess_source_reliability(self.endpoint, assessed_at=self.now)
        replay, replay_created = assess_source_reliability(
            self.endpoint, assessed_at=self.now + timedelta(minutes=1)
        )

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, assessment.id)
        self.assertEqual(assessment.status, SourceReliabilityAssessment.Status.HEALTHY)
        self.assertEqual(assessment.candidate_count, 1)
        self.assertEqual(assessment.artifact_ready_count, 1)
        self.assertEqual(assessment.extraction_ready_count, 1)
        self.assertEqual(assessment.graph_ready_count, 1)
        self.assertEqual(assessment.section_count, 1)
        self.assertEqual(assessment.local_embedding_count, 1)
        self.assertEqual(assessment.configured_embedding_count, 1)
        self.assertEqual(
            PipelineEvent.objects.filter(event_type="source.reliability.assessed").count(),
            1,
        )

    def test_stale_transition_alerts_once_and_recovery_is_explicit(self) -> None:
        healthy, _ = assess_source_reliability(self.endpoint, assessed_at=self.now)
        stale, _ = assess_source_reliability(
            self.endpoint,
            assessed_at=healthy.freshness_deadline + timedelta(seconds=1),
        )
        replay, created = assess_source_reliability(
            self.endpoint,
            assessed_at=healthy.freshness_deadline + timedelta(minutes=1),
        )
        self.assertEqual(stale.status, SourceReliabilityAssessment.Status.CRITICAL)
        self.assertFalse(created)
        self.assertEqual(replay.id, stale.id)
        self.assertEqual(
            PipelineEvent.objects.filter(event_type="source.reliability.alert").count(),
            1,
        )

        self.endpoint.next_poll_at = self.now + timedelta(hours=4)
        self.endpoint.save(update_fields=("next_poll_at", "updated_at"))
        recovered, _ = assess_source_reliability(
            self.endpoint,
            assessed_at=self.now + timedelta(hours=2),
        )
        self.assertEqual(recovered.status, SourceReliabilityAssessment.Status.HEALTHY)
        self.assertEqual(recovered.previous_assessment_id, stale.id)
        self.assertEqual(
            PipelineEvent.objects.filter(event_type="source.reliability.recovered").count(),
            1,
        )

    def test_incomplete_new_run_is_processing_then_warning_after_grace(self) -> None:
        incomplete_endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Incomplete source",
            discovery_url="https://example.com/incomplete/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            polling_interval_minutes=60,
            next_poll_at=self.now + timedelta(hours=1),
        )
        incomplete_run = SourceRun.objects.create(
            endpoint=incomplete_endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="incomplete-run",
            connector_configuration_version=1,
            finished_at=self.now,
        )
        candidate = DiscoveredCandidate.objects.create(
            endpoint=incomplete_endpoint,
            latest_source_run=incomplete_run,
            discovered_url="https://example.com/incomplete/rule.pdf",
            canonical_url="https://example.com/incomplete/rule.pdf",
            fingerprint="8" * 64,
            first_discovered_at=self.now,
            last_discovered_at=self.now,
        )
        CandidateObservation.objects.create(source_run=incomplete_run, candidate=candidate)

        processing, _ = assess_source_reliability(
            incomplete_endpoint,
            assessed_at=self.now + timedelta(minutes=5),
        )
        warning, _ = assess_source_reliability(
            incomplete_endpoint,
            assessed_at=self.now + timedelta(minutes=31),
        )

        self.assertEqual(processing.status, SourceReliabilityAssessment.Status.PROCESSING)
        self.assertEqual(warning.status, SourceReliabilityAssessment.Status.WARNING)
        self.assertTrue(any(item["code"] == "artifact_coverage" for item in warning.findings))
        self.assertEqual(
            PipelineEvent.objects.filter(
                event_type="source.reliability.alert",
                aggregate_id=incomplete_endpoint.id,
            ).count(),
            1,
        )

    def test_latest_run_does_not_reuse_prior_run_artifact_observations(self) -> None:
        latest_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="latest-run-awaiting-fetches",
            connector_configuration_version=1,
            started_at=self.now + timedelta(minutes=1),
            finished_at=self.now + timedelta(minutes=2),
            discovered_candidate_count=1,
        )
        CandidateObservation.objects.create(source_run=latest_run, candidate=self.candidate)

        assessment, _ = assess_source_reliability(
            self.endpoint,
            assessed_at=self.now + timedelta(minutes=3),
        )

        self.assertEqual(assessment.status, SourceReliabilityAssessment.Status.PROCESSING)
        self.assertEqual(assessment.artifact_ready_count, 0)
        self.assertTrue(any(item["code"] == "artifact_coverage" for item in assessment.findings))

    def test_periodic_task_and_read_only_api_are_operator_visible(self) -> None:
        result = assess_enabled_sources.run()
        self.assertEqual(result["assessed"], 1)
        assessment = SourceReliabilityAssessment.objects.get(endpoint=self.endpoint)

        list_url = reverse("sourcereliabilityassessment-list")
        self.assertEqual(self.client.get(list_url).status_code, 403)
        staff = get_user_model().objects.create_user(
            username="reliability-operator",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(
            reverse("sourcereliabilityassessment-detail", args=[assessment.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(self.client.post(list_url, {}).status_code, 405)

        source_page = self.client.get(reverse("console:source-detail", args=[self.endpoint.id]))
        self.assertContains(source_page, "End-to-end reliability")
        self.assertContains(source_page, "1/1")
        source_list = self.client.get(reverse("console:source-list"))
        self.assertContains(source_list, "Reliability")
        dashboard = self.client.get(reverse("console:dashboard"))
        self.assertContains(dashboard, "Latest source assessments")

    def test_ocr_derivative_counts_as_candidate_downstream_coverage(self) -> None:
        scanned_candidate = self._candidate(
            "https://example.com/publications/scanned.pdf", "6" * 64
        )
        scanned_artifact = self._archive_only(scanned_candidate, "7" * 64)
        ExtractionRun.objects.create(
            raw_artifact=scanned_artifact,
            extractor_name="pypdf",
            extractor_version="1",
            configuration_hash="8" * 64,
            status=ExtractionRun.Status.OCR_REQUIRED,
            started_at=self.now,
            finished_at=self.now,
        )
        extracted_artifact = RawArtifact.objects.get(sha256="2" * 64)
        ArtifactDerivative.objects.create(
            source_artifact=scanned_artifact,
            derived_artifact=extracted_artifact,
            transformation_type=ArtifactDerivative.TransformationType.OCR_SEARCHABLE_PDF,
            profile="test-ocr:1",
            configuration_hash="9" * 64,
        )

        assessment, _ = assess_source_reliability(self.endpoint, assessed_at=self.now)

        self.assertEqual(assessment.status, SourceReliabilityAssessment.Status.HEALTHY)
        self.assertEqual(assessment.candidate_count, 2)
        self.assertEqual(assessment.extraction_ready_count, 2)
        self.assertEqual(assessment.graph_ready_count, 2)


class SourceRepairPlanTestCase(ReliabilityFixture):
    def test_healthy_source_has_empty_repair_plan(self) -> None:
        plan = build_source_repair_plan(self.endpoint)
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.executable_count, 0)

    def test_missing_candidate_artifact_produces_idempotent_audited_fetch_repair(self) -> None:
        missing = self._candidate("https://example.com/publications/missing.pdf", "9" * 64)
        plan = build_source_repair_plan(self.endpoint)

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].kind, "fetch_candidate")
        self.assertEqual(plan.actions[0].object_id, str(missing.id))
        with (
            patch("aria.fetching.tasks.fetch_candidate.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            queued = queue_source_repairs(self.endpoint, plan)

        self.assertEqual(queued, 1)
        delay.assert_called_once_with(str(missing.id), str(self.source_run.id))
        self.assertTrue(
            AuditEvent.objects.filter(
                action="source.reliability.repair_queued",
                target_id=self.endpoint.id,
            ).exists()
        )

    def test_repair_requires_a_fresh_plan(self) -> None:
        self._candidate("https://example.com/publications/missing.pdf", "a" * 64)
        stale_plan = build_source_repair_plan(self.endpoint)
        self._candidate("https://example.com/publications/second.pdf", "b" * 64)

        with self.assertRaisesMessage(ValueError, "plan changed"):
            queue_source_repairs(self.endpoint, stale_plan)

    def test_missing_extraction_queues_the_archived_artifact(self) -> None:
        candidate = self._candidate("https://example.com/publications/unextracted.pdf", "c" * 64)
        artifact = self._archive_only(candidate, "d" * 64)
        plan = build_source_repair_plan(self.endpoint)
        action = next(item for item in plan.actions if item.kind == "extract_artifact")
        self.assertEqual(action.object_id, str(artifact.id))

        with (
            patch("aria.extraction.tasks.extract_raw_artifact.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            queued = queue_source_repairs(self.endpoint, plan)
        self.assertEqual(queued, 1)
        delay.assert_called_once_with(str(artifact.id))

    def test_missing_graph_and_embeddings_queue_one_projection(self) -> None:
        candidate = self._candidate("https://example.com/publications/unprojected.pdf", "e" * 64)
        _, version = self._complete_downstream(candidate, "f" * 64, project=False)
        plan = build_source_repair_plan(self.endpoint)
        action = next(item for item in plan.actions if item.kind == "project_knowledge")
        self.assertEqual(action.object_id, str(version.id))

        with (
            patch("aria.knowledge.tasks.project_document_version_knowledge.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            queued = queue_source_repairs(self.endpoint, plan)
        self.assertEqual(queued, 1)
        delay.assert_called_once_with(str(version.id))

    def test_ocr_required_artifact_queues_ocr_instead_of_reextraction(self) -> None:
        candidate = self._candidate("https://example.com/publications/scanned.pdf", "6" * 64)
        artifact = self._archive_only(candidate, "7" * 64)
        ExtractionRun.objects.create(
            raw_artifact=artifact,
            extractor_name="pypdf",
            extractor_version="1",
            configuration_hash="8" * 64,
            status=ExtractionRun.Status.OCR_REQUIRED,
            started_at=self.now,
            finished_at=self.now,
        )
        plan = build_source_repair_plan(self.endpoint)
        action = next(item for item in plan.actions if item.kind == "ocr_artifact")
        self.assertEqual(action.object_id, str(artifact.id))

        with (
            patch("aria.ocr.tasks.process_ocr.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            queued = queue_source_repairs(self.endpoint, plan)
        self.assertEqual(queued, 1)
        ocr_run = OCRRun.objects.get(source_artifact=artifact)
        delay.assert_called_once_with(str(ocr_run.id))
