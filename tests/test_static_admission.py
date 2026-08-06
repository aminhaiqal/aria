import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from aria.artifacts.models import ArtifactObservation
from aria.browser.models import SourceAdmissionAssessment, SourceAdmissionPromotion
from aria.discovery.models import CandidateObservation, EndpointObservation, SourceRun
from aria.events.models import AuditEvent, PipelineEvent
from aria.fetching.models import FetchAttempt
from aria.reliability.confidence import collect_source_confidence, serialize_source_confidence
from aria.sources.admission import assess_static_admission, promote_static_source
from aria.sources.models import ConnectorConfiguration, SourceEndpoint, SourcePackSnapshot
from aria.sources.pilots import SourcePilotError, queue_disabled_source_pilot
from tests.test_reliability import ReliabilityFixture


@override_settings(
    EMBEDDING_PROVIDER="openai",
    OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
    LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
    SOURCE_FRESHNESS_GRACE_MINUTES=60,
    SOURCE_PIPELINE_GRACE_MINUTES=30,
)
class StaticSourceAdmissionTestCase(ReliabilityFixture):
    def setUp(self) -> None:
        super().setUp()
        SourceEndpoint.objects.filter(pk=self.endpoint.pk).update(
            is_enabled=False,
            next_poll_at=None,
            health_state=SourceEndpoint.HealthState.DISABLED,
        )
        self.endpoint.refresh_from_db()
        ConnectorConfiguration.objects.create(
            endpoint=self.endpoint,
            version=1,
            configuration={
                "link_selector": "a[href]",
                "include_path_prefixes": ["/publications/"],
                "upload_path_prefixes": ["/publications/"],
                "document_extensions": [".pdf"],
                "document_content_types": ["application/pdf"],
                "max_candidates": 10,
            },
        )
        self.snapshot = SourcePackSnapshot.objects.create(
            endpoint=self.endpoint,
            pack_slug="static-test-source",
            schema_version=1,
            pack_version=1,
            checksum="9" * 64,
            definition={
                "schema_version": 1,
                "slug": "static-test-source",
                "version": 1,
            },
        )
        self.first_endpoint_observation = self._endpoint_observation(
            self.source_run,
            outcome=EndpointObservation.Outcome.CHANGED,
            response_status=200,
            content_sha256="a" * 64,
        )
        self.second_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="static-admission-run-2",
            connector_configuration_version=1,
            started_at=self.now + timedelta(minutes=1),
            finished_at=self.now + timedelta(minutes=2),
            discovered_candidate_count=1,
        )
        CandidateObservation.objects.create(source_run=self.second_run, candidate=self.candidate)
        self.candidate.latest_source_run = self.second_run
        self.candidate.last_discovered_at = self.second_run.finished_at
        self.candidate.save(update_fields=("latest_source_run", "last_discovered_at", "updated_at"))
        first_artifact = self.candidate.artifact_observations.get(
            source_run=self.source_run
        ).raw_artifact
        attempt = FetchAttempt.objects.create(
            candidate=self.candidate,
            source_run=self.second_run,
            attempt_number=1,
            status=FetchAttempt.Status.SUCCEEDED,
            requested_url=self.candidate.canonical_url,
            final_url=self.candidate.canonical_url,
            response_status=200,
            bytes_received=first_artifact.byte_size,
            started_at=self.second_run.started_at,
            finished_at=self.second_run.finished_at,
        )
        ArtifactObservation.objects.create(
            raw_artifact=first_artifact,
            fetch_attempt=attempt,
            candidate=self.candidate,
            source_run=self.second_run,
            requested_url=self.candidate.canonical_url,
            final_url=self.candidate.canonical_url,
            response_status=200,
            content_changed=False,
            connector_configuration_version=1,
        )
        self.second_endpoint_observation = self._endpoint_observation(
            self.second_run,
            outcome=EndpointObservation.Outcome.UNCHANGED,
            response_status=200,
            content_sha256="a" * 64,
            previous=self.first_endpoint_observation,
        )

    def _endpoint_observation(
        self,
        source_run,
        *,
        outcome,
        response_status,
        content_sha256,
        previous=None,
    ):
        return EndpointObservation.objects.create(
            source_run=source_run,
            endpoint=self.endpoint,
            previous_observation=previous,
            outcome=outcome,
            requested_url=self.endpoint.discovery_url,
            final_url=self.endpoint.discovery_url,
            response_status=response_status,
            byte_size=100,
            content_sha256=content_sha256,
            connector_configuration_version=1,
            checked_at=source_run.finished_at or self.now,
        )

    def test_assessment_is_ready_immutable_and_idempotent(self) -> None:
        assessment, created = assess_static_admission(self.endpoint)
        repeated, repeated_created = assess_static_admission(self.endpoint)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(repeated.id, assessment.id)
        self.assertEqual(assessment.status, SourceAdmissionAssessment.Status.READY)
        self.assertEqual(assessment.admission_profile, assessment.Profile.STATIC_LISTING)
        self.assertEqual(
            assessment.evaluated_source_run_ids,
            [str(self.source_run.id), str(self.second_run.id)],
        )
        self.assertTrue(all(gate["passed"] for gate in assessment.gates))
        self.assertEqual(
            PipelineEvent.objects.filter(event_type="source.admission.evaluated").count(),
            1,
        )

    def test_exact_ready_assessment_promotes_and_schedules_source(self) -> None:
        assessment, _ = assess_static_admission(self.endpoint)
        promotion = promote_static_source(
            self.endpoint,
            assessment,
            actor_type="user",
            actor_identifier="operator-1",
        )

        self.endpoint.refresh_from_db()
        self.assertTrue(self.endpoint.is_enabled)
        self.assertEqual(self.endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertIsNotNone(self.endpoint.next_poll_at)
        self.assertEqual(SourceAdmissionPromotion.objects.get(), promotion)
        audit = AuditEvent.objects.get(action="source.admission.promoted")
        self.assertEqual(audit.actor_type, "user")
        self.assertEqual(audit.actor_identifier, "operator-1")
        confidence = serialize_source_confidence(collect_source_confidence(self.endpoint))
        self.assertEqual(confidence["state"], "operational")
        self.assertEqual(confidence["evidence_coverage_percent"], 100)

    def test_promotion_rejects_stale_evidence(self) -> None:
        assessment, _ = assess_static_admission(self.endpoint)
        SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="static-admission-run-3",
            connector_configuration_version=1,
            started_at=self.now + timedelta(minutes=3),
            finished_at=self.now + timedelta(minutes=4),
        )

        with self.assertRaisesMessage(ValueError, "evidence changed"):
            promote_static_source(self.endpoint, assessment)
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)
        self.assertEqual(SourceAdmissionPromotion.objects.count(), 0)

    def test_disabled_pilot_is_audited_unscheduled_and_overlap_guarded(self) -> None:
        with (
            patch("aria.sources.pilots.execute_source_run.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            run = queue_disabled_source_pilot(
                self.endpoint,
                actor_type="user",
                actor_identifier="operator-2",
            )

        delay.assert_called_once_with(str(run.id))
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)
        self.assertIsNone(self.endpoint.next_poll_at)
        audit = AuditEvent.objects.get(action="source.pilot.queued")
        self.assertEqual(audit.actor_identifier, "operator-2")
        self.assertEqual(audit.details["source_pack_checksum"], self.snapshot.checksum)
        with self.assertRaisesMessage(SourcePilotError, "active work"):
            queue_disabled_source_pilot(
                self.endpoint,
                actor_type="user",
                actor_identifier="operator-2",
            )

    def test_management_commands_require_exact_confirmations_and_report_json(self) -> None:
        with self.assertRaisesMessage(CommandError, "--confirm RUN"):
            call_command("run_source_pilot", "static-test-source", stdout=StringIO())
        assessment, _ = assess_static_admission(self.endpoint)
        with self.assertRaisesMessage(CommandError, "--confirm PROMOTE"):
            call_command("promote_static_source", str(assessment.id), stdout=StringIO())

        output = StringIO()
        call_command("source_confidence_report", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report[0]["endpoint_id"], str(self.endpoint.id))
        self.assertEqual(report[0]["state"], "admission_ready")
