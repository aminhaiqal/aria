from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.authorities.models import Authority
from aria.browser.models import SourceAdmissionAssessment
from aria.collections.models import PublicationCollection
from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    ComparisonSummary,
    ReviewedChangePublication,
)
from aria.comparisons.services import compare_document_versions
from aria.discovery.models import DiscoveredCandidate, MonitoredResource, ResourceRun, SourceRun
from aria.events.models import AuditEvent, OutboxEvent, PipelineEvent
from aria.fetching.models import FetchAttempt
from aria.orchestration.models import ChangeOrchestration
from aria.sources.models import SourceEndpoint
from aria.sources.source_packs import apply_source_pack, load_source_pack
from tests.test_phase3c import Phase3CFixture


class ConsoleSourceAndWorkflowTestCase(TestCase):
    def setUp(self) -> None:
        self.authority = Authority.objects.create(
            name="Console regulator",
            slug="console-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Console publications",
            slug="console-publications",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official console source",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html"],
        )
        self.resource = MonitoredResource.objects.create(
            endpoint=self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/publications/guidance/",
            fingerprint="1" * 64,
            title="Official guidance index",
            is_approved=True,
            approval_basis="Approved fixture",
        )
        self.staff = get_user_model().objects.create_user(
            username="console-operator",
            password="test-password",
            is_staff=True,
        )
        self.nonstaff = get_user_model().objects.create_user(
            username="console-reader",
            password="test-password",
        )

    def test_console_requires_staff_and_serves_only_allowlisted_assets(self) -> None:
        response = self.client.get(reverse("console:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('console:login')}?next={reverse('console:dashboard')}",
        )

        self.client.force_login(self.nonstaff)
        response = self.client.get(reverse("console:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('console:login')}?next={reverse('console:dashboard')}",
        )

        stylesheet = self.client.get(reverse("console:asset", args=["console.css"]))
        self.assertEqual(stylesheet.status_code, 200)
        self.assertEqual(stylesheet["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            self.client.get(reverse("console:asset", args=["secrets.env"])).status_code,
            404,
        )

    def test_console_login_rejects_valid_nonstaff_credentials(self) -> None:
        response = self.client.post(
            reverse("console:login"),
            {"username": self.nonstaff.username, "password": "test-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "requires an active staff account")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_can_render_overview_source_resource_and_audit_surfaces(self) -> None:
        self.client.force_login(self.staff)
        for route in (
            reverse("console:dashboard"),
            reverse("console:source-list"),
            reverse("console:source-confidence"),
            reverse("console:source-detail", args=[self.endpoint.id]),
            reverse("console:resource-detail", args=[self.resource.id]),
            reverse("console:orchestration-list"),
            reverse("console:comparison-list"),
            reverse("console:audit-list"),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

        source_page = self.client.get(reverse("console:source-detail", args=[self.endpoint.id]))
        self.assertContains(source_page, "Official console source")
        self.assertContains(source_page, "Safe manual check")

    def test_dashboard_counts_source_runs_and_ignores_buffered_outbox_events(self) -> None:
        SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            status=SourceRun.Status.PENDING,
            idempotency_key="console-active-source-run",
            connector_configuration_version=1,
        )
        event = PipelineEvent.objects.create(
            event_type="console.buffered",
            aggregate_type="source_endpoint",
            aggregate_id=self.endpoint.id,
            payload={},
        )
        OutboxEvent.objects.create(
            pipeline_event=event,
            topic=event.event_type,
            payload={},
            status=OutboxEvent.Status.PENDING,
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("console:dashboard"))

        self.assertContains(response, "Active processing")
        self.assertContains(response, "<strong>1</strong>", html=True)
        self.assertNotContains(response, "pending outbox")
        self.assertNotContains(response, "Operator attention")

    def test_dashboard_alerts_on_failed_outbox_delivery(self) -> None:
        event = PipelineEvent.objects.create(
            event_type="console.delivery_failed",
            aggregate_type="source_endpoint",
            aggregate_id=self.endpoint.id,
            payload={},
        )
        OutboxEvent.objects.create(
            pipeline_event=event,
            topic=event.event_type,
            payload={},
            status=OutboxEvent.Status.FAILED,
            last_error="Configured delivery transport rejected the event.",
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("console:dashboard"))

        self.assertContains(response, "Operator attention")
        self.assertContains(response, "1 failed outbox delivery")

    def test_source_poll_is_post_only_audited_and_overlap_guarded(self) -> None:
        self.client.force_login(self.staff)
        route = reverse("console:source-poll", args=[self.endpoint.id])
        self.assertEqual(self.client.get(route).status_code, 405)

        with (
            patch("aria.console.operations.execute_source_run.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(route)

        self.assertRedirects(response, reverse("console:source-detail", args=[self.endpoint.id]))
        run = SourceRun.objects.get(endpoint=self.endpoint)
        self.assertEqual(run.trigger, SourceRun.Trigger.MANUAL)
        delay.assert_called_once_with(str(run.id))
        audit = AuditEvent.objects.get(action="console.source_poll_queued")
        self.assertEqual(audit.actor_identifier, str(self.staff.id))
        self.assertEqual(audit.details["source_run_id"], str(run.id))

        with patch("aria.console.operations.execute_source_run.delay") as replay_delay:
            self.client.post(route)
        self.assertEqual(SourceRun.objects.filter(endpoint=self.endpoint).count(), 1)
        replay_delay.assert_not_called()

    def test_resource_poll_is_post_only_audited_and_requires_approval(self) -> None:
        self.client.force_login(self.staff)
        route = reverse("console:resource-poll", args=[self.resource.id])
        self.assertEqual(self.client.get(route).status_code, 405)

        with (
            patch("aria.console.operations.execute_resource_run.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(route)

        self.assertRedirects(response, reverse("console:resource-detail", args=[self.resource.id]))
        run = ResourceRun.objects.get(resource=self.resource)
        delay.assert_called_once_with(str(run.id))
        self.assertTrue(
            AuditEvent.objects.filter(
                action="console.resource_poll_queued",
                actor_identifier=str(self.staff.id),
            ).exists()
        )

        MonitoredResource.objects.filter(pk=self.resource.pk).update(is_approved=False)
        ResourceRun.objects.filter(pk=run.pk).update(status=ResourceRun.Status.COMPLETED)
        with patch("aria.console.operations.execute_resource_run.delay") as blocked_delay:
            self.client.post(route)
        self.assertEqual(ResourceRun.objects.filter(resource=self.resource).count(), 1)
        blocked_delay.assert_not_called()

    def _failed_workflow(self) -> ChangeOrchestration:
        source_run = SourceRun.objects.create(
            endpoint=self.endpoint,
            trigger=SourceRun.Trigger.MANUAL,
            status=SourceRun.Status.COMPLETED,
            idempotency_key="console-workflow-source-run",
            connector_configuration_version=1,
        )
        now = timezone.now()
        candidate = DiscoveredCandidate.objects.create(
            endpoint=self.endpoint,
            latest_source_run=source_run,
            discovered_url="https://example.com/publications/notice/",
            canonical_url="https://example.com/publications/notice/",
            fingerprint="2" * 64,
            first_discovered_at=now,
            last_discovered_at=now,
        )
        fetch_attempt = FetchAttempt.objects.create(
            candidate=candidate,
            source_run=source_run,
            attempt_number=1,
            status=FetchAttempt.Status.SUCCEEDED,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            bytes_received=18,
            started_at=now,
            finished_at=now,
        )
        artifact = RawArtifact.objects.create(
            sha256="3" * 64,
            byte_size=18,
            detected_content_type="text/html",
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key="console/workflow-artifact",
        )
        observation = ArtifactObservation.objects.create(
            raw_artifact=artifact,
            fetch_attempt=fetch_attempt,
            candidate=candidate,
            source_run=source_run,
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            response_status=200,
            content_changed=True,
            connector_configuration_version=1,
        )
        return ChangeOrchestration.objects.create(
            artifact_observation=observation,
            source_artifact=artifact,
            idempotency_key="4" * 64,
            status=ChangeOrchestration.Status.FAILED,
            error_code="TemporaryEvidenceError",
            error_message="The evidence store was temporarily unavailable.",
            finished_at=now,
        )

    def test_workflow_trace_and_retry_preserve_history_and_record_actor(self) -> None:
        workflow = self._failed_workflow()
        self.client.force_login(self.staff)
        detail_route = reverse("console:orchestration-detail", args=[workflow.id])
        response = self.client.get(detail_route)
        self.assertContains(response, "The evidence store was temporarily unavailable")
        workflow.refresh_from_db()
        self.assertEqual(workflow.status, ChangeOrchestration.Status.FAILED)

        retry_route = reverse("console:orchestration-retry", args=[workflow.id])
        self.assertEqual(self.client.get(retry_route).status_code, 405)
        with (
            patch("aria.console.operations.process_change_orchestration.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(retry_route)

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, ChangeOrchestration.Status.PENDING)
        self.assertEqual(workflow.retry_count, 1)
        delay.assert_called_once_with(str(workflow.id))
        audit = AuditEvent.objects.get(action="console.orchestration_retry_queued")
        self.assertEqual(audit.actor_identifier, str(self.staff.id))

    def test_mutation_routes_enforce_csrf(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("console:source-poll", args=[self.endpoint.id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SourceRun.objects.count(), 0)


class ConsoleAdmissionWorkbenchTestCase(TestCase):
    def setUp(self) -> None:
        self.endpoint = apply_source_pack(load_source_pack("agc-updated-principal-acts")).endpoint
        self.staff = get_user_model().objects.create_user(
            username="admission-console-operator",
            password="test-password",
            is_staff=True,
        )

    def test_workbench_is_staff_only_and_renders_pack_gates_and_repair_preview(self) -> None:
        list_route = reverse("console:admission-list")
        self.assertRedirects(
            self.client.get(list_route),
            f"{reverse('console:login')}?next={list_route}",
        )
        self.client.force_login(self.staff)
        self.client.post(
            reverse("console:admission-assess", args=[self.endpoint.id]),
            {"required_captures": 2},
        )

        list_response = self.client.get(list_route)
        detail_response = self.client.get(
            reverse("console:admission-detail", args=[self.endpoint.id])
        )

        self.assertContains(list_response, "agc-updated-principal-acts")
        self.assertContains(detail_response, "Installed source pack")
        self.assertContains(detail_response, "repeat_capture_count")
        self.assertContains(detail_response, "Read-only repair preview")
        self.assertContains(detail_response, self.endpoint.source_pack_snapshots.get().checksum)

    def test_capture_and_assessment_are_post_only_audited_and_keep_pilot_disabled(self) -> None:
        self.client.force_login(self.staff)
        capture_route = reverse("console:admission-capture", args=[self.endpoint.id])
        assess_route = reverse("console:admission-assess", args=[self.endpoint.id])
        promote_route = reverse("console:admission-promote", args=[self.endpoint.id])
        for route in (capture_route, assess_route, promote_route):
            self.assertEqual(self.client.get(route).status_code, 405)

        with (
            patch("aria.console.operations.execute_source_run.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(capture_route)
        self.assertRedirects(
            response,
            reverse("console:admission-detail", args=[self.endpoint.id]),
        )
        run = SourceRun.objects.get(endpoint=self.endpoint)
        delay.assert_called_once_with(str(run.id))
        self.assertTrue(
            AuditEvent.objects.filter(
                action="console.admission_capture_queued",
                actor_identifier=str(self.staff.id),
            ).exists()
        )

        self.client.post(assess_route, {"required_captures": 2})
        assessment = SourceAdmissionAssessment.objects.get(endpoint=self.endpoint)
        self.assertEqual(assessment.status, SourceAdmissionAssessment.Status.INCOMPLETE)
        self.client.post(
            promote_route,
            {"assessment_id": assessment.id, "confirmation": "PROMOTE"},
        )
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="console.admission_assessed",
                actor_identifier=str(self.staff.id),
            ).exists()
        )

    def test_admission_mutations_enforce_csrf_and_exact_confirmation(self) -> None:
        assessment, _ = SourceAdmissionAssessment.objects.get_or_create(
            endpoint=self.endpoint,
            report_signature="1" * 64,
            defaults={
                "status": SourceAdmissionAssessment.Status.INCOMPLETE,
                "required_evidence_count": 2,
                "evaluated_capture_ids": [],
                "candidate_set_sha256": "",
                "candidate_count": 0,
                "gates": [
                    {
                        "name": "repeat_capture_count",
                        "passed": False,
                        "detail": "0/2 completed captures",
                    }
                ],
            },
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        route = reverse("console:admission-promote", args=[self.endpoint.id])
        self.assertEqual(
            csrf_client.post(
                route,
                {"assessment_id": assessment.id, "confirmation": "PROMOTE"},
            ).status_code,
            403,
        )
        self.client.force_login(self.staff)
        self.client.post(
            route,
            {"assessment_id": assessment.id, "confirmation": "promote"},
        )
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)


@override_settings(ORCHESTRATION_AUTO_GPT_SUMMARIES=False, OPENAI_API_KEY="test-key")
class ConsoleComparisonTestCase(Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        self.staff = get_user_model().objects.create_user(
            username="comparison-operator",
            password="test-password",
            is_staff=True,
        )
        before = self.create_version("Section 1 Security\nA controller shall implement safeguards.")
        after = self.create_version(
            "Section 1 Security\nA controller shall implement appropriate safeguards."
        )
        self.comparison, _ = compare_document_versions(before, after)
        self.item = self.comparison.items.exclude(
            change_type=ComparisonItem.ChangeType.UNCHANGED
        ).get()
        self.client.force_login(self.staff)

    def _review(self, *, decision=ComparisonReview.Decision.CONFIRMED, rationale=""):
        route = reverse("console:comparison-item-review", args=[self.item.id])
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(route, {"decision": decision, "rationale": rationale})

    def test_comparison_workbench_renders_exact_evidence_and_safety_boundary(self) -> None:
        response = self.client.get(reverse("console:comparison-detail", args=[self.comparison.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A controller shall implement safeguards.")
        self.assertContains(response, "A controller shall implement appropriate safeguards.")
        self.assertContains(response, "Evidence, not legal advice")
        self.assertContains(response, self.item.before_anchor.source_artifact.sha256)
        self.assertEqual(ComparisonReview.objects.count(), 0)

    def test_review_decisions_are_append_only_and_rationale_is_validated(self) -> None:
        response = self._review(rationale="Confirmed against both official anchors.")
        detail_url = reverse("console:comparison-detail", args=[self.comparison.id])
        self.assertRedirects(
            response,
            f"{detail_url}#item-{self.item.id}",
        )
        first = ComparisonReview.objects.get()
        self.assertEqual(first.decision, ComparisonReview.Decision.CONFIRMED)

        self._review(decision=ComparisonReview.Decision.REJECTED, rationale="short")
        self.assertEqual(ComparisonReview.objects.count(), 1)

        self._review(
            decision=ComparisonReview.Decision.NEEDS_CONTEXT,
            rationale="The visible anchors omit the referenced schedule.",
        )
        latest = ComparisonReview.objects.first()
        self.assertEqual(ComparisonReview.objects.count(), 2)
        self.assertEqual(latest.previous_review_id, first.id)
        self.assertEqual(latest.decision, ComparisonReview.Decision.NEEDS_CONTEXT)
        self.assertEqual(
            AuditEvent.objects.filter(action="comparison.review_recorded").count(),
            2,
        )

    def test_summary_requires_complete_review_and_has_duplicate_queue_guard(self) -> None:
        route = reverse("console:comparison-summarize", args=[self.comparison.id])
        with patch("aria.console.operations.summarize_comparison.delay") as delay:
            self.client.post(route)
        delay.assert_not_called()

        self._review(rationale="Confirmed against both official anchors.")
        with (
            patch("aria.console.operations.summarize_comparison.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(route)
        delay.assert_called_once_with(str(self.comparison.id))
        self.assertTrue(
            AuditEvent.objects.filter(action="console.comparison_summary_queued").exists()
        )

        ComparisonSummary.objects.create(
            comparison=self.comparison,
            model="gpt-test",
            prompt_version="console-test-v1",
            input_hash="5" * 64,
            status=ComparisonSummary.Status.PENDING,
        )
        with patch("aria.console.operations.summarize_comparison.delay") as replay_delay:
            self.client.post(route)
        replay_delay.assert_not_called()

    def test_explicit_publication_is_confirmed_audited_and_idempotent(self) -> None:
        self._review(rationale="Confirmed against both official anchors.")
        route = reverse("console:comparison-publish", args=[self.comparison.id])
        self.assertEqual(self.client.get(route).status_code, 405)

        self.client.post(route, {"confirmation": "publish"})
        self.assertEqual(ReviewedChangePublication.objects.count(), 0)

        self.client.post(route, {"confirmation": "PUBLISH"})
        self.assertEqual(ReviewedChangePublication.objects.count(), 1)
        self.assertEqual(
            PipelineEvent.objects.filter(event_type="regulatory.textual_change.confirmed").count(),
            1,
        )
        self.assertEqual(
            OutboxEvent.objects.filter(topic="regulatory.textual_change.confirmed").count(),
            1,
        )

        self.client.post(route, {"confirmation": "PUBLISH"})
        self.assertEqual(ReviewedChangePublication.objects.count(), 1)
        self.assertEqual(
            OutboxEvent.objects.filter(topic="regulatory.textual_change.confirmed").count(),
            1,
        )
        latest_audit = AuditEvent.objects.filter(
            action="console.reviewed_changes_published"
        ).first()
        self.assertEqual(latest_audit.actor_identifier, str(self.staff.id))
        self.assertEqual(latest_audit.details["published_count"], 0)
        self.assertEqual(latest_audit.details["skipped_count"], 1)

    def test_nonstaff_cannot_record_review_or_publish(self) -> None:
        nonstaff = get_user_model().objects.create_user(
            username="comparison-nonstaff",
            password="test-password",
        )
        self.client.force_login(nonstaff)
        review_route = reverse("console:comparison-item-review", args=[self.item.id])
        publish_route = reverse("console:comparison-publish", args=[self.comparison.id])
        self.assertEqual(
            self.client.post(
                review_route,
                {"decision": "confirmed", "rationale": "Unauthorized attempt."},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(publish_route, {"confirmation": "PUBLISH"}).status_code,
            302,
        )
        self.assertEqual(ComparisonReview.objects.count(), 0)
        self.assertEqual(ReviewedChangePublication.objects.count(), 0)


class ConsoleStaticAdmissionTestCase(TestCase):
    def setUp(self) -> None:
        self.endpoint = apply_source_pack(
            load_source_pack("parliament-dewan-rakyat-bills")
        ).endpoint
        self.staff = get_user_model().objects.create_user(
            username="static-admission-operator",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_static_workbench_renders_pack_and_guarded_actions(self) -> None:
        response = self.client.get(reverse("console:source-detail", args=[self.endpoint.id]))

        self.assertContains(response, "Static source admission")
        self.assertContains(response, "parliament-dewan-rakyat-bills")
        self.assertContains(response, "Run disabled pilot")
        self.assertContains(response, "Assessment required")

    def test_pilot_and_admission_routes_are_post_only_audited_and_safe(self) -> None:
        pilot_route = reverse("console:static-source-pilot", args=[self.endpoint.id])
        assess_route = reverse("console:static-source-assess", args=[self.endpoint.id])
        promote_route = reverse("console:static-source-promote", args=[self.endpoint.id])
        for route in (pilot_route, assess_route, promote_route):
            self.assertEqual(self.client.get(route).status_code, 405)

        with (
            patch("aria.sources.pilots.execute_source_run.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(pilot_route)
        run = SourceRun.objects.get(endpoint=self.endpoint)
        delay.assert_called_once_with(str(run.id))
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)
        self.assertIsNone(self.endpoint.next_poll_at)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="source.pilot.queued",
                actor_identifier=str(self.staff.id),
            ).exists()
        )

        self.client.post(assess_route, {"required_runs": 2})
        assessment = SourceAdmissionAssessment.objects.get(endpoint=self.endpoint)
        self.assertEqual(assessment.status, SourceAdmissionAssessment.Status.INCOMPLETE)
        self.assertEqual(assessment.admission_profile, assessment.Profile.STATIC_LISTING)
        self.client.post(
            promote_route,
            {"assessment_id": assessment.id, "confirmation": "PROMOTE"},
        )
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_enabled)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="console.static_admission_assessed",
                actor_identifier=str(self.staff.id),
            ).exists()
        )

    def test_static_mutations_enforce_csrf(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(
            reverse("console:static-source-pilot", args=[self.endpoint.id])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SourceRun.objects.count(), 0)
