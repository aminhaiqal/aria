from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation
from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.comparisons.models import ComparisonItem, ComparisonReview, ComparisonSummary
from aria.comparisons.reviews import record_comparison_review
from aria.discovery.connectors import CandidateData
from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run, observe_candidate
from aria.documents.models import DocumentVersion
from aria.fetching.client import FetchResponse
from aria.fetching.services import begin_fetch_attempt, complete_fetch
from aria.knowledge.models import GraphEdge, SectionEmbedding
from aria.orchestration.models import ChangeOrchestration, OrchestrationStepAttempt
from aria.orchestration.services import (
    RetryableOrchestrationError,
    claim_recoverable_orchestrations,
    complete_summary_orchestrations,
    ensure_change_orchestration,
    prepare_orchestration_retry,
    run_change_orchestration,
)
from aria.sources.models import SourceEndpoint


@override_settings(
    EMBEDDING_PROVIDER="local_hash",
    LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
    ORCHESTRATION_AUTO_GPT_SUMMARIES=True,
    ORCHESTRATION_STALE_AFTER_MINUTES=30,
    ORCHESTRATION_RECOVERY_BATCH_SIZE=5,
)
class Phase3D3OrchestrationTestCase(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage_root = Path(self.temporary_directory.name)
        storage_override = override_settings(OBJECT_STORAGE_ROOT=self.storage_root)
        storage_override.enable()
        self.addCleanup(storage_override.disable)
        self.store = FilesystemArtifactStore(self.storage_root)
        self.authority = Authority.objects.create(
            name="Phase 3D.3 regulator",
            slug="phase-3d3-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Phase 3D.3 publications",
            slug="phase-3d3-publications",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official publications",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html"],
        )
        self.url = "https://example.com/publications/standard/"

    @staticmethod
    def _html(version: str, *, comment: str = "") -> bytes:
        purpose = " ".join(
            f"Purpose requirement {version} establishes accountable processing rule {index}."
            for index in range(1, 7)
        )
        security = " ".join(
            f"Security requirement {version} requires documented safeguard {index}."
            for index in range(1, 7)
        )
        return (
            "<html><body><main>"
            "<h1>Personal Data Protection Standard</h1>"
            f"<h2>Section 1 Purpose</h2><p>{purpose}</p>"
            f"<h2>Section 2 Security</h2><p>{security}</p>"
            f"<!-- {comment} -->"
            "</main></body></html>"
        ).encode()

    def _fetch(self, content: bytes, *, url: str | None = None) -> ArtifactObservation:
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        selected_url = url or self.url
        candidate, _ = observe_candidate(
            source_run,
            CandidateData(
                discovered_url=selected_url,
                canonical_url=selected_url,
                fingerprint=("a" if selected_url == self.url else "b") * 64,
            ),
        )
        attempt = begin_fetch_attempt(candidate, source_run, request_headers={})
        return complete_fetch(
            attempt,
            FetchResponse(
                requested_url=selected_url,
                final_url=selected_url,
                status_code=200,
                headers={"content-type": "text/html"},
                redirect_chain=[],
                resolved_addresses=["93.184.216.34"],
                content=content,
            ),
            store=self.store,
        )

    def _run_fetch(self, content: bytes, *, url: str | None = None) -> ChangeOrchestration:
        observation = self._fetch(content, url=url)
        orchestration = ChangeOrchestration.objects.get(artifact_observation=observation)
        return run_change_orchestration(orchestration)

    def _create_material_comparison(self) -> ChangeOrchestration:
        baseline = self._run_fetch(self._html("v1"))
        self.assertEqual(baseline.status, ChangeOrchestration.Status.COMPLETED)
        changed = self._run_fetch(self._html("v2"))
        self.assertEqual(changed.status, ChangeOrchestration.Status.REVIEW_REQUIRED)
        return changed

    def test_changed_artifact_runs_gated_pipeline_and_terminal_replay_is_idempotent(self) -> None:
        orchestration = self._run_fetch(self._html("v1"))

        self.assertEqual(orchestration.status, ChangeOrchestration.Status.COMPLETED)
        self.assertEqual(orchestration.current_stage, ChangeOrchestration.Stage.COMPLETED)
        self.assertIsNotNone(orchestration.document_version_id)
        self.assertIsNotNone(orchestration.quality_run_id)
        self.assertIsNotNone(orchestration.lineage_assessment_id)
        self.assertIsNone(orchestration.comparison_id)
        version = orchestration.document_version
        self.assertTrue(version.graph_edges.exists())
        self.assertEqual(
            SectionEmbedding.objects.filter(normalized_section__document_version=version).count(),
            version.sections.count(),
        )
        self.assertEqual(
            list(orchestration.step_attempts.values_list("stage", flat=True)),
            [
                ChangeOrchestration.Stage.ARTIFACT,
                ChangeOrchestration.Stage.EXTRACTION,
                ChangeOrchestration.Stage.VERSION,
                ChangeOrchestration.Stage.QUALITY,
                ChangeOrchestration.Stage.KNOWLEDGE,
                ChangeOrchestration.Stage.LINEAGE,
                ChangeOrchestration.Stage.COMPARISON,
            ],
        )
        version_count = DocumentVersion.objects.count()
        attempt_count = orchestration.step_attempts.count()

        replay = run_change_orchestration(orchestration)

        self.assertEqual(replay.id, orchestration.id)
        self.assertEqual(DocumentVersion.objects.count(), version_count)
        self.assertEqual(replay.step_attempts.count(), attempt_count)

    def test_raw_byte_change_with_same_normalized_text_stops_before_quality_and_diff(self) -> None:
        baseline = self._run_fetch(self._html("v1", comment="first representation"))
        graph_edge_count = GraphEdge.objects.count()

        replay = self._run_fetch(self._html("v1", comment="different representation"))

        self.assertNotEqual(
            baseline.source_artifact.sha256,
            replay.source_artifact.sha256,
        )
        self.assertEqual(replay.status, ChangeOrchestration.Status.NO_CONTENT_CHANGE)
        self.assertEqual(replay.document_version_id, baseline.document_version_id)
        self.assertEqual(DocumentVersion.objects.count(), 1)
        self.assertEqual(GraphEdge.objects.count(), graph_edge_count)
        self.assertIsNone(replay.quality_run_id)
        self.assertIsNone(replay.comparison_id)

    def test_material_change_creates_one_comparison_and_waits_for_review(self) -> None:
        orchestration = self._create_material_comparison()

        self.assertIsNotNone(orchestration.comparison_id)
        self.assertEqual(orchestration.comparison.status, "completed")
        self.assertTrue(
            orchestration.comparison.items.exclude(
                change_type__in=(
                    ComparisonItem.ChangeType.UNCHANGED,
                    ComparisonItem.ChangeType.FORMAT_ONLY,
                )
            ).exists()
        )
        comparison_count = self.collection.document_identities.get().comparisons.count()
        attempt_count = OrchestrationStepAttempt.objects.count()

        run_change_orchestration(orchestration)

        self.assertEqual(
            self.collection.document_identities.get().comparisons.count(),
            comparison_count,
        )
        self.assertEqual(OrchestrationStepAttempt.objects.count(), attempt_count)

    def test_quality_review_gate_prevents_knowledge_projection(self) -> None:
        short_url = "https://example.com/publications/short/"
        orchestration = self._run_fetch(
            b"<html><body><main><h1>Tiny Notice</h1><p>This official notice is short "
            b"and requires human quality review.</p></main></body></html>",
            url=short_url,
        )

        self.assertEqual(
            orchestration.status,
            ChangeOrchestration.Status.QUALITY_REVIEW_REQUIRED,
        )
        version = orchestration.document_version
        self.assertFalse(version.graph_edges.exists())
        self.assertFalse(
            SectionEmbedding.objects.filter(normalized_section__document_version=version).exists()
        )
        self.assertFalse(
            orchestration.step_attempts.filter(stage=ChangeOrchestration.Stage.KNOWLEDGE).exists()
        )

    def test_artifact_verification_failure_is_auditable_and_explicitly_retryable(self) -> None:
        observation = self._fetch(self._html("v1"))
        orchestration, created = ensure_change_orchestration(observation)
        self.assertFalse(created)
        broken_store = MagicMock()
        broken_store.read.return_value = b"tampered bytes"

        with (
            patch("aria.orchestration.services.get_artifact_store", return_value=broken_store),
            self.assertRaises(RetryableOrchestrationError),
        ):
            run_change_orchestration(orchestration)

        orchestration.refresh_from_db()
        self.assertEqual(orchestration.status, ChangeOrchestration.Status.FAILED)
        failed_step = orchestration.step_attempts.get(stage=ChangeOrchestration.Stage.ARTIFACT)
        self.assertEqual(failed_step.outcome, OrchestrationStepAttempt.Outcome.FAILED)
        self.assertTrue(failed_step.error_code)

        prepare_orchestration_retry(orchestration)
        completed = run_change_orchestration(orchestration)

        self.assertEqual(completed.status, ChangeOrchestration.Status.COMPLETED)
        self.assertEqual(completed.retry_count, 1)

    def test_recovery_claims_pending_and_stale_work_without_duplicate_dispatch(self) -> None:
        observation = self._fetch(self._html("v1"))
        orchestration = ChangeOrchestration.objects.get(artifact_observation=observation)

        first = claim_recoverable_orchestrations()
        second = claim_recoverable_orchestrations()

        self.assertEqual([item.id for item in first], [orchestration.id])
        self.assertEqual(second, [])
        orchestration.refresh_from_db()
        self.assertEqual(orchestration.status, ChangeOrchestration.Status.QUEUED)

        ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
            heartbeat_at=timezone.now() - timedelta(minutes=31)
        )
        recovered = claim_recoverable_orchestrations()

        self.assertEqual([item.id for item in recovered], [orchestration.id])
        orchestration.refresh_from_db()
        self.assertEqual(orchestration.retry_count, 1)

    def test_completed_reviews_queue_summary_and_completion_closes_orchestration(self) -> None:
        orchestration = self._create_material_comparison()
        reviewer = get_user_model().objects.create_superuser(
            username="phase3d3-reviewer",
            password="test-password",
        )
        reviewable_items = list(
            orchestration.comparison.items.exclude(change_type=ComparisonItem.ChangeType.UNCHANGED)
        )

        with (
            patch("aria.comparisons.tasks.summarize_comparison.delay") as summary_delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            for item in reviewable_items:
                record_comparison_review(
                    item,
                    decision=ComparisonReview.Decision.CONFIRMED,
                    reviewer=reviewer,
                    rationale="Confirmed against the official evidence anchors.",
                )

        orchestration.refresh_from_db()
        self.assertEqual(orchestration.status, ChangeOrchestration.Status.SUMMARY_PENDING)
        summary_delay.assert_called_once_with(str(orchestration.comparison_id))
        summary = ComparisonSummary.objects.create(
            comparison=orchestration.comparison,
            model="gpt-test",
            prompt_version="phase3d3-test",
            input_hash="c" * 64,
            input_snapshot={"test": True},
            status=ComparisonSummary.Status.COMPLETED,
            output={"legal_effect_not_assessed": True},
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )

        updated = complete_summary_orchestrations(summary)

        orchestration.refresh_from_db()
        self.assertEqual(updated, 1)
        self.assertEqual(orchestration.status, ChangeOrchestration.Status.COMPLETED)
        self.assertEqual(orchestration.summary, summary)
        self.assertTrue(
            orchestration.step_attempts.filter(
                stage=ChangeOrchestration.Stage.SUMMARY,
                outcome=OrchestrationStepAttempt.Outcome.COMPLETED,
            ).exists()
        )

    def test_orchestration_api_is_admin_only_and_read_only(self) -> None:
        orchestration = self._run_fetch(self._html("v1"))
        detail_url = reverse("changeorchestration-detail", args=[orchestration.id])

        self.assertIn(self.client.get(detail_url).status_code, (401, 403))
        user = get_user_model().objects.create_superuser(
            username="phase3d3-admin",
            password="test-password",
        )
        self.client.force_login(user)
        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], ChangeOrchestration.Status.COMPLETED)
        self.assertEqual(response.json()["artifact_sha256"], orchestration.source_artifact.sha256)
        self.assertGreater(len(response.json()["step_attempts"]), 0)
        self.assertEqual(
            self.client.post(reverse("changeorchestration-list"), {}).status_code,
            405,
        )
