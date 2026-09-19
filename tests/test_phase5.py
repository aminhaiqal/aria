import hashlib
import hmac
import json
from io import StringIO
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from aria.comparisons.models import ComparisonItem, ComparisonReview
from aria.comparisons.reviews import record_comparison_review
from aria.comparisons.services import compare_document_versions
from aria.documents.models import DocumentIdentity, DocumentVersion, VersionEvidence
from aria.documents.services import (
    queue_change_orchestration_retries,
    supersede_duplicate_identity,
)
from aria.events.delivery import ImpactWebhookClient, deliver_impact_outbox_event
from aria.events.models import AuditEvent, OutboxEvent, PipelineEvent
from aria.health.phase5 import collect_phase5_status
from aria.impacts.generation import generate_impact_candidates
from aria.impacts.models import ApplicabilityTerm, ImpactReview, ProfileImpactMatch
from aria.impacts.profiles import match_business_profile, save_business_profile
from aria.impacts.publications import publish_reviewed_impact
from aria.impacts.reviews import record_impact_review
from aria.impacts.taxonomies import apply_taxonomy_definition, load_taxonomy_definition
from aria.reader.usage import (
    record_pilot_feedback,
    record_reader_document_view,
    record_reader_evidence_download,
    record_reader_search,
)
from tests.test_phase3c import Phase3CFixture

HEALTHY_SOURCE = {
    "endpoint_id": "00000000-0000-0000-0000-000000000001",
    "endpoint_name": "Phase 5 source",
    "authority": "Phase 5 authority",
    "state": "operational",
    "is_enabled": True,
    "next_poll_at": "",
    "completed_run_count": 1,
    "evidence_coverage_percent": 100,
    "reliability_status": "healthy",
    "candidate_count": 1,
    "artifact_ready_count": 1,
    "extraction_ready_count": 1,
    "graph_ready_count": 1,
    "section_count": 1,
    "local_embedding_count": 1,
    "configured_embedding_count": 1,
    "configured_embedding_provider": "local_hash",
    "source_pack": None,
    "admission": None,
    "promotion": None,
    "findings": [],
}


@override_settings(
    REQUIRE_SEPARATE_PUBLISHER=True,
    IMPACT_WEBHOOK_URL="https://hooks.example.test/aria/impact",
    IMPACT_WEBHOOK_ALLOWED_DOMAINS=["hooks.example.test"],
    IMPACT_WEBHOOK_SECRET="phase-five-test-secret-with-thirty-two-characters",
    IMPACT_WEBHOOK_MAX_ATTEMPTS=3,
    IMPACT_WEBHOOK_STALE_MINUTES=15,
)
class Phase5ProductProofTestCase(Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        user_model = get_user_model()
        self.reviewer = user_model.objects.create_superuser(
            username="phase5-reviewer",
            password="test-password",
        )
        self.publisher = user_model.objects.create_superuser(
            username="phase5-publisher",
            password="test-password",
        )

    def _complete_change_journey(self):
        before = self.create_version(
            "Section 1 Security\nA data controller shall use reasonable safeguards."
        )
        after = self.create_version(
            "Section 1 Security\nA data controller shall use appropriate technical safeguards."
        )
        comparison, _ = compare_document_versions(before, after)
        item = comparison.items.get(change_type=ComparisonItem.ChangeType.MODIFIED)
        record_comparison_review(
            item,
            decision=ComparisonReview.Decision.CONFIRMED,
            reviewer=self.reviewer,
            rationale="Confirmed against the exact archived before and after anchors.",
        )
        taxonomy, _ = apply_taxonomy_definition(load_taxonomy_definition())
        generation = generate_impact_candidates(item, taxonomy)
        impact = generation.generation.generated_impacts.get()
        review = record_impact_review(
            impact,
            decision=ImpactReview.Decision.APPROVED,
            reviewer=self.reviewer,
            rationale="Approved for the controlled Phase 5 product proof.",
        )
        return after, taxonomy, review

    @staticmethod
    def _profile_terms(taxonomy):
        return [
            taxonomy.terms.get(dimension=dimension, code=code)
            for dimension, code in (
                (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
                (ApplicabilityTerm.Dimension.REGULATED_ROLE, "data-user"),
                (ApplicabilityTerm.Dimension.ACTIVITY, "process-personal-data"),
            )
        ]

    def _record_pilot_use(self, *, user, taxonomy, identity, artifact, ordinal: int):
        profile = save_business_profile(
            owner=user,
            taxonomy=taxonomy,
            name=f"Pilot organization {ordinal}",
            terms=self._profile_terms(taxonomy),
            notes="Controlled Phase 5 pilot profile.",
        )
        result = {
            "mode": "full_text",
            "embedding": {"provider": "local_hash"},
            "filters": {
                "authority": "",
                "collection": "",
                "profile": str(profile.id),
            },
            "page": 1,
            "page_size": 10,
            "bounded_result_count": 1,
            "warnings": [],
            "results": [{"identity_id": str(identity.id)}],
        }
        record_reader_search(user=user, query="technical safeguards", result=result)
        record_reader_document_view(
            user=user,
            identity_id=identity.id,
            profile_id=str(profile.id),
        )
        record_reader_evidence_download(user=user, artifact=artifact)
        record_pilot_feedback(
            user=user,
            category="evidence_clarity",
            rating=5,
            comment="The archived evidence and exact changed wording were clear and usable.",
            recorded_by="phase5-test-facilitator",
        )
        return profile

    def test_controlled_change_completes_review_match_publication_and_signed_delivery(self):
        after, taxonomy, review = self._complete_change_journey()
        pilot = get_user_model().objects.create_user(username="phase5-pilot-1")
        profile = self._record_pilot_use(
            user=pilot,
            taxonomy=taxonomy,
            identity=after.identity,
            artifact=after.evidence_records.get().raw_artifact,
            ordinal=1,
        )
        match = match_business_profile(profile, review).match
        self.assertEqual(match.outcome, ProfileImpactMatch.Outcome.MATCHED)
        publication = publish_reviewed_impact(review, publisher=self.publisher).publication
        outbox = publication.pipeline_event.outbox_event
        captured = {}

        def handler(request):
            captured["request"] = request
            return httpx.Response(204)

        client = ImpactWebhookClient(
            resolver=lambda _hostname, _port: ["8.8.8.8"],
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)
        delivered = deliver_impact_outbox_event(outbox.id, client=client)

        self.assertEqual(delivered.status, OutboxEvent.Status.PUBLISHED)
        request = captured["request"]
        timestamp = request.headers["x-aria-timestamp"]
        expected_signature = hmac.new(
            b"phase-five-test-secret-with-thirty-two-characters",
            timestamp.encode() + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(request.headers["x-aria-signature"], f"sha256={expected_signature}")
        status = collect_phase5_status(source_rows=[HEALTHY_SOURCE])
        self.assertTrue(status["criteria"]["complete_change_journey"])
        self.assertFalse(status["criteria"]["three_engaged_pilot_users"])

    def test_phase5_status_requires_three_profiled_evidence_users_with_feedback(self):
        after, taxonomy, review = self._complete_change_journey()
        artifact = after.evidence_records.get().raw_artifact
        profiles = []
        for ordinal in range(1, 4):
            user = get_user_model().objects.create_user(username=f"phase5-pilot-{ordinal}")
            profiles.append(
                self._record_pilot_use(
                    user=user,
                    taxonomy=taxonomy,
                    identity=after.identity,
                    artifact=artifact,
                    ordinal=ordinal,
                )
            )
        match_business_profile(profiles[0], review)
        publication = publish_reviewed_impact(review, publisher=self.publisher).publication
        OutboxEvent.objects.filter(pk=publication.pipeline_event.outbox_event.pk).update(
            status=OutboxEvent.Status.PUBLISHED
        )

        status = collect_phase5_status(source_rows=[HEALTHY_SOURCE])

        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["pilot"]["engaged_user_count"], 3)
        self.assertEqual(status["pilot"]["successful_search_count"], 3)
        self.assertEqual(status["pilot"]["average_ratings"], {"evidence_clarity": 5.0})
        self.assertTrue(all(status["criteria"].values()))

    def test_duplicate_identity_resolution_is_audited_and_idempotent(self):
        target_version = self.create_version("One official publication exposed at two URLs.")
        evidence = target_version.evidence_records.get()
        source_identity = DocumentIdentity.objects.create(
            collection=self.collection,
            stable_key="9" * 64,
            canonical_title=target_version.title,
            canonical_url="https://example.com/regulation/duplicate/",
            identity_basis={"strategy": "test_duplicate"},
        )
        source_version = DocumentVersion.objects.create(
            identity=source_identity,
            normalized_content_sha256=target_version.normalized_content_sha256,
            title=target_version.title,
            canonical_url=source_identity.canonical_url,
            plain_content=target_version.plain_content,
            normalized_metadata={},
            extractor_name=target_version.extractor_name,
            extractor_version=target_version.extractor_version,
        )
        VersionEvidence.objects.create(
            document_version=source_version,
            raw_artifact=evidence.raw_artifact,
            extraction_run=evidence.extraction_run,
            observed_url=source_identity.canonical_url,
        )

        result = supersede_duplicate_identity(
            source_identity,
            self.identity,
            reason="Both official pages expose the same normalized publication and evidence.",
            actor_type="operator",
            actor_identifier="phase5-test",
        )
        replay = supersede_duplicate_identity(
            source_identity,
            self.identity,
            reason="Both official pages expose the same normalized publication and evidence.",
            actor_type="operator",
            actor_identifier="phase5-test",
        )

        source_identity.refresh_from_db()
        self.assertTrue(result.created)
        self.assertFalse(replay.created)
        self.assertEqual(source_identity.superseded_by, self.identity)
        self.assertEqual(
            source_identity.supersession_basis["strategy"],
            "reviewed_duplicate_content_v1",
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="document.duplicate_identity_superseded").count(),
            1,
        )
        self.assertEqual(
            PipelineEvent.objects.filter(
                event_type="document.duplicate_identity_superseded"
            ).count(),
            1,
        )

    def test_pilot_feedback_command_is_guarded_and_actor_attributed(self):
        pilot = get_user_model().objects.create_user(username="feedback-pilot")
        with self.assertRaisesMessage(CommandError, "--confirm RECORD"):
            call_command(
                "record_pilot_feedback",
                pilot.username,
                category="search_usefulness",
                rating=5,
                comment="The evidence search returned the expected official publication.",
                recorded_by="pilot-facilitator",
                confirm="record",
            )
        output = StringIO()

        call_command(
            "record_pilot_feedback",
            pilot.username,
            category="search_usefulness",
            rating=5,
            comment="The evidence search returned the expected official publication.",
            recorded_by="pilot-facilitator",
            confirm="RECORD",
            stdout=output,
        )

        self.assertTrue(json.loads(output.getvalue())["recorded"])
        event = AuditEvent.objects.get(action="product.pilot_feedback.recorded")
        self.assertEqual(event.actor_identifier, str(pilot.pk))
        self.assertEqual(event.details["recorded_by"], "pilot-facilitator")

    def test_duplicate_resolution_queues_the_supported_orchestration_task(self):
        workflow_ids = (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
        with patch(
            "aria.orchestration.tasks.process_change_orchestration.delay"
        ) as delay:
            queue_change_orchestration_retries(workflow_ids)

        self.assertEqual(
            [call.args[0] for call in delay.call_args_list],
            list(workflow_ids),
        )
