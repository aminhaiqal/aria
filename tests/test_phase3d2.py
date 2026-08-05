import hashlib
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import (
    DiscoveredCandidate,
    MonitoredResource,
    ResourceObservation,
    ResourceLinkObservation,
    ResourceRun,
    SourceRun,
)
from aria.discovery.resource_connectors import (
    ResourceStructureChanged,
    parse_detail_page,
    parse_feed,
)
from aria.discovery.services import (
    create_resource_run as create_monitored_resource_run,
    create_source_run,
    mark_resource_run_completed,
    reconcile_resource_links,
    record_resource_observation,
    register_monitored_resource,
    schedule_due_resource_runs,
)
from aria.discovery.tasks import execute_resource_run
from aria.fetching.client import FetchResponse
from aria.sources.models import SourceEndpoint


class ResourceMonitoringTestCase(TestCase):
    def setUp(self) -> None:
        authority = Authority.objects.create(
            name="Test official authority",
            slug="test-official-resource-authority",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        collection = PublicationCollection.objects.create(
            authority=authority,
            name="Resource monitored publications",
            slug="resource-monitored-publications",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=collection,
            name="Official resource monitor",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html", "application/rss+xml", "application/pdf"],
        )

    def create_resource_run(self, resource: MonitoredResource) -> ResourceRun:
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        return ResourceRun.objects.create(
            resource=resource,
            source_run=source_run,
            idempotency_key=f"resource-test:{resource.id}",
        )

    def response(
        self,
        resource: MonitoredResource,
        *,
        status: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> FetchResponse:
        return FetchResponse(
            requested_url=resource.url,
            final_url=resource.url,
            status_code=status,
            headers=headers or {"Content-Type": "text/html", "ETag": '"detail-v1"'},
            redirect_chain=[],
            resolved_addresses=["93.184.216.34"],
            content=content,
        )

    def test_resource_registration_normalizes_and_is_idempotent(self) -> None:
        resource, created = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="HTTPS://EXAMPLE.COM/publications/rule/#section",
            title="Rule",
            is_approved=True,
            approval_basis="Discovered from approved listing",
        )
        same, created_again = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/publications/rule/",
            is_approved=True,
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(same.id, resource.id)
        self.assertEqual(resource.url, "https://example.com/publications/rule/")
        self.assertEqual(
            resource.fingerprint,
            hashlib.sha256(resource.url.encode("utf-8")).hexdigest(),
        )

    def test_resource_observation_is_append_only_and_validates_ownership(self) -> None:
        resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/publications/rule/",
            is_approved=True,
        )
        resource_run = self.create_resource_run(resource)
        observation = ResourceObservation.objects.create(
            resource_run=resource_run,
            resource=resource,
            outcome=ResourceObservation.Outcome.CHANGED,
            requested_url=resource.url,
            final_url=resource.url,
            response_status=200,
            content_sha256="a" * 64,
            link_set_sha256="b" * 64,
        )

        observation.outcome = ResourceObservation.Outcome.UNCHANGED
        with self.assertRaises(ValidationError):
            observation.save()
        with self.assertRaises(ValidationError):
            observation.delete()

    def test_resource_apis_are_administrator_only_and_read_only(self) -> None:
        resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.RSS,
            url="https://example.com/feed/",
            is_approved=True,
            approval_basis="Declared by the official listing page",
        )
        self.assertEqual(
            self.client.get(reverse("monitoredresource-detail", args=[resource.id])).status_code,
            403,
        )
        admin = get_user_model().objects.create_superuser(
            username="resource-monitor-admin",
            password="test-password",
        )
        self.client.force_login(admin)
        detail_url = reverse("monitoredresource-detail", args=[resource.id])
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertEqual(self.client.post(reverse("monitoredresource-list"), {}).status_code, 405)

    def test_detail_link_snapshots_record_add_retain_remove_and_quarantine(self) -> None:
        resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/publications/rule/",
            is_approved=True,
        )
        first_content = b"""
            <div class="betterdocs-entry-content">
              <a href="/files/a.pdf">A</a>
              <a href="https://outside.example/b.pdf">Outside</a>
            </div>
        """
        first_links = parse_detail_page(
            first_content,
            resource_url=resource.url,
            allowed_domains=self.endpoint.allowed_domains,
        )
        first_run, _ = create_monitored_resource_run(
            resource,
            trigger=SourceRun.Trigger.MANUAL,
        )
        first = record_resource_observation(
            first_run,
            self.response(resource, content=first_content),
            links=first_links,
        )
        mark_resource_run_completed(first_run)

        first_rows = list(first.link_observations.order_by("target_url"))
        self.assertEqual([row.state for row in first_rows], ["added", "added"])
        self.assertEqual(
            [row.disposition for row in first_rows],
            ["accepted", "quarantined"],
        )
        self.assertEqual(first_rows[1].quarantine_reason, "hostname_not_allowlisted")

        second_content = b"""
            <div class="betterdocs-entry-content">
              <a href="/files/a.pdf">A retained</a>
              <a href="/files/c.pdf">C new</a>
            </div>
        """
        second_run, _ = create_monitored_resource_run(
            resource,
            trigger=SourceRun.Trigger.MANUAL,
        )
        second = record_resource_observation(
            second_run,
            self.response(resource, content=second_content, headers={"Content-Type": "text/html"}),
            links=parse_detail_page(
                second_content,
                resource_url=resource.url,
                allowed_domains=self.endpoint.allowed_domains,
            ),
        )

        states = {
            row.target_url: row.state for row in second.link_observations.order_by("target_url")
        }
        self.assertEqual(states["https://example.com/files/a.pdf"], "retained")
        self.assertEqual(states["https://example.com/files/c.pdf"], "added")
        self.assertEqual(states["https://outside.example/b.pdf"], "removed")

        accepted = reconcile_resource_links(second_run)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(DiscoveredCandidate.objects.count(), 2)
        self.assertFalse(
            DiscoveredCandidate.objects.filter(
                canonical_url="https://outside.example/b.pdf"
            ).exists()
        )
        self.assertTrue(
            all(
                candidate.metadata_hints["document_identity_url"] == resource.url
                for candidate in accepted
            )
        )

    @patch("aria.discovery.tasks.get_default_http_client")
    def test_detail_task_uses_conditional_headers_and_preserves_links_on_304(
        self,
        client_factory: Mock,
    ) -> None:
        resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/publications/rule/",
            is_approved=True,
        )
        first_content = b"""
            <div class="betterdocs-entry-content"><a href="/files/a.pdf">A</a></div>
        """
        first_run, _ = create_monitored_resource_run(
            resource,
            trigger=SourceRun.Trigger.MANUAL,
        )
        first_response = self.response(
            resource,
            content=first_content,
            headers={"Content-Type": "text/html", "ETag": '"detail-v1"'},
        )
        client_factory.return_value.fetch.return_value = first_response
        execute_resource_run.run(str(first_run.id))

        second_run, _ = create_monitored_resource_run(
            resource,
            trigger=SourceRun.Trigger.MANUAL,
        )
        client_factory.return_value.fetch.return_value = self.response(
            resource,
            status=304,
            headers={"ETag": '"detail-v1"'},
        )
        execute_resource_run.run(str(second_run.id))

        second_run.refresh_from_db()
        second = second_run.observation
        self.assertEqual(second.outcome, ResourceObservation.Outcome.NOT_MODIFIED)
        self.assertEqual(second.link_count, 1)
        self.assertEqual(
            second.link_observations.get().state,
            ResourceLinkObservation.State.RETAINED,
        )
        self.assertEqual(
            client_factory.return_value.fetch.call_args.kwargs["headers"],
            {"If-None-Match": '"detail-v1"'},
        )
        resource.refresh_from_db()
        self.endpoint.refresh_from_db()
        self.assertEqual(resource.health_state, MonitoredResource.HealthState.HEALTHY)
        self.assertEqual(self.endpoint.health_state, SourceEndpoint.HealthState.UNKNOWN)

    def test_feed_parser_supports_bounded_rss_and_atom(self) -> None:
        rss = b"""<?xml version="1.0"?>
          <rss version="2.0"><channel><title>Official updates</title>
            <item><title>Rule A</title><link>https://example.com/rule-a/</link>
              <guid>rule-a</guid><pubDate>Mon, 25 Aug 2025 00:40:41 +0000</pubDate></item>
            <item><title>Outside</title><link>https://outside.example/rule-b/</link></item>
            <item><title>Ignored by bound</title><link>https://example.com/rule-c/</link></item>
          </channel></rss>"""
        parsed_rss = parse_feed(
            rss,
            feed_url="https://example.com/feed/",
            allowed_domains=self.endpoint.allowed_domains,
            max_entries=2,
        )
        self.assertEqual(parsed_rss.feed_type, "rss")
        self.assertEqual(len(parsed_rss.links), 2)
        self.assertEqual(parsed_rss.links[0].external_identifier, "rule-a")
        self.assertEqual(parsed_rss.links[0].metadata["published_at"], "2025-08-25T00:40:41+00:00")
        self.assertEqual(parsed_rss.links[1].disposition, "quarantined")

        ambiguous = b"""<rss version="2.0"><channel><title>Official</title>
          <item><title>A</title><link>https://example.com/a/</link><guid>same</guid></item>
          <item><title>B</title><link>https://example.com/b/</link><guid>same</guid></item>
        </channel></rss>"""
        parsed_ambiguous = parse_feed(
            ambiguous,
            feed_url="https://example.com/feed/",
            allowed_domains=self.endpoint.allowed_domains,
        )
        self.assertTrue(
            all(link.quarantine_reason == "ambiguous_external_identifier" for link in parsed_ambiguous.links)
        )

        atom = b"""<?xml version="1.0"?>
          <feed xmlns="http://www.w3.org/2005/Atom"><title>Official Atom</title>
            <entry><title>Rule D</title><id>tag:example.com,2026:rule-d</id>
              <updated>2026-08-05T01:00:00Z</updated>
              <link rel="alternate" href="https://example.com/rule-d/" /></entry>
          </feed>"""
        parsed_atom = parse_feed(
            atom,
            feed_url="https://example.com/atom/",
            allowed_domains=self.endpoint.allowed_domains,
        )
        self.assertEqual(parsed_atom.feed_type, "atom")
        self.assertEqual(parsed_atom.links[0].target_url, "https://example.com/rule-d/")
        self.assertEqual(parsed_atom.links[0].metadata["updated_at"], "2026-08-05T01:00:00+00:00")

    def test_feed_parser_rejects_entities(self) -> None:
        malicious = b"""<?xml version="1.0"?>
          <!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
          <rss version="2.0"><channel><title>&xxe;</title>
            <item><title>A</title><link>https://example.com/a/</link></item>
          </channel></rss>"""
        with self.assertRaisesMessage(ResourceStructureChanged, "well-formed XML"):
            parse_feed(
                malicious,
                feed_url="https://example.com/feed/",
                allowed_domains=self.endpoint.allowed_domains,
            )

    @patch("aria.discovery.tasks.get_default_http_client")
    def test_approved_rss_task_records_feed_entries(self, client_factory: Mock) -> None:
        resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.RSS,
            url="https://example.com/feed/",
            is_approved=True,
            approval_basis="Declared by the official listing page",
        )
        resource_run, _ = create_monitored_resource_run(
            resource,
            trigger=SourceRun.Trigger.MANUAL,
        )
        rss = b"""<?xml version="1.0"?>
          <rss version="2.0"><channel><title>Official updates</title>
            <item><title>Rule A</title><link>https://example.com/rule-a/</link>
              <guid>rule-a</guid></item>
          </channel></rss>"""
        client_factory.return_value.fetch.return_value = self.response(
            resource,
            content=rss,
            headers={"Content-Type": "application/rss+xml", "ETag": '"feed-v1"'},
        )

        execute_resource_run.run(str(resource_run.id))

        resource_run.refresh_from_db()
        link = resource_run.observation.link_observations.get()
        self.assertEqual(resource_run.status, ResourceRun.Status.COMPLETED)
        self.assertEqual(link.relation, "entry")
        self.assertEqual(link.target_url, "https://example.com/rule-a/")
        self.assertEqual(link.external_identifier, "rule-a")
        child = MonitoredResource.objects.get(parent=resource)
        self.assertEqual(child.resource_type, MonitoredResource.ResourceType.DETAIL_PAGE)
        self.assertTrue(child.is_approved)
        self.assertTrue(child.is_enabled)

    def test_feed_reconciliation_keeps_out_of_scope_entries_disabled(self) -> None:
        feed_resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.RSS,
            url="https://example.com/feed/",
            is_approved=True,
        )
        resource_run, _ = create_monitored_resource_run(
            feed_resource,
            trigger=SourceRun.Trigger.MANUAL,
        )
        rss = b"""<rss version="2.0"><channel><title>Official</title>
          <item><title>In scope</title><link>https://example.com/rules/a/</link></item>
          <item><title>Review</title><link>https://example.com/news/b/</link></item>
        </channel></rss>"""
        record_resource_observation(
            resource_run,
            self.response(
                feed_resource,
                content=rss,
                headers={"Content-Type": "application/rss+xml"},
            ),
            links=parse_feed(
                rss,
                feed_url=feed_resource.url,
                allowed_domains=self.endpoint.allowed_domains,
            ).links,
        )

        reconcile_resource_links(resource_run, detail_path_prefixes=("/rules/",))

        approved = MonitoredResource.objects.get(url="https://example.com/rules/a/")
        pending = MonitoredResource.objects.get(url="https://example.com/news/b/")
        self.assertTrue(approved.is_approved)
        self.assertTrue(approved.is_enabled)
        self.assertFalse(pending.is_approved)
        self.assertFalse(pending.is_enabled)
        self.assertEqual(pending.metadata["scope_disposition"], "pending_review")

    @override_settings(
        MONITOR_RESOURCE_BATCH_SIZE=2,
        MONITOR_RESOURCE_BATCH_PER_ENDPOINT=2,
    )
    def test_resource_scheduler_is_bounded_and_prevents_overlap(self) -> None:
        resources = []
        for number in range(3):
            resource, _ = register_monitored_resource(
                self.endpoint,
                resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
                url=f"https://example.com/rules/{number}/",
                is_approved=True,
            )
            resource.next_poll_at = timezone.now() - timedelta(minutes=1)
            resource.save(update_fields=("next_poll_at", "updated_at"))
            resources.append(resource)

        first_batch = schedule_due_resource_runs()

        self.assertEqual(len(first_batch), 2)
        self.assertTrue(all(run.status == ResourceRun.Status.PENDING for run in first_batch))
        for run in first_batch:
            run.resource.next_poll_at = timezone.now() - timedelta(minutes=1)
            run.resource.save(update_fields=("next_poll_at", "updated_at"))
        second_batch = schedule_due_resource_runs()
        self.assertEqual(len(second_batch), 1)
        self.assertNotIn(second_batch[0].resource_id, {run.resource_id for run in first_batch})

    @override_settings(MONITOR_MAX_ENABLED_RESOURCES_PER_ENDPOINT=1)
    def test_resource_registration_staggers_checks_and_enforces_enabled_cap(self) -> None:
        before = timezone.now()
        first, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/rules/first/",
            is_approved=True,
            polling_interval_minutes=60,
        )
        second, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
            url="https://example.com/rules/second/",
            is_approved=True,
            polling_interval_minutes=60,
        )

        self.assertGreaterEqual(first.next_poll_at, before)
        self.assertLess(first.next_poll_at, before + timedelta(hours=1, seconds=1))
        self.assertTrue(first.is_enabled)
        self.assertTrue(second.is_approved)
        self.assertFalse(second.is_enabled)
        self.assertEqual(second.metadata["capacity_disposition"], "disabled_at_cap")

    def test_manual_resource_command_refuses_unapproved_resource(self) -> None:
        resource, _ = register_monitored_resource(
            self.endpoint,
            resource_type=MonitoredResource.ResourceType.RSS,
            url="https://example.com/unapproved-feed/",
            is_approved=False,
            is_enabled=False,
        )
        with self.assertRaisesMessage(CommandError, "not enabled, approved"):
            call_command("poll_resource", str(resource.id), stdout=StringIO())
