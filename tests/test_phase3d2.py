import hashlib

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import (
    MonitoredResource,
    ResourceObservation,
    ResourceRun,
    SourceRun,
)
from aria.discovery.services import create_source_run, register_monitored_resource
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
