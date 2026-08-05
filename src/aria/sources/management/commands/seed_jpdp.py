from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, MonitoredResource, SourceRun
from aria.discovery.services import create_source_run, register_monitored_resource
from aria.discovery.tasks import execute_source_run
from aria.sources.models import ConnectorConfiguration, SourceEndpoint


class Command(BaseCommand):
    help = "Create or update the JPDP Act 709 source registry entries."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--run",
            action="store_true",
            help="Queue a manual source run after seeding.",
        )

    def handle(self, *args, **options) -> None:
        authority, _ = Authority.objects.update_or_create(
            slug="personal-data-protection-commissioner-malaysia",
            defaults={
                "name": "Personal Data Protection Commissioner, Malaysia",
                "aliases": ["JPDP", "PPDP"],
                "jurisdiction": "Malaysia",
                "country_code": "MY",
                "authority_type": Authority.AuthorityType.REGULATOR,
                "regulatory_domains": ["personal-data-protection"],
                "official_domains": ["pdp.gov.my"],
                "trust_classification": Authority.TrustClassification.AUTHORITATIVE,
                "is_enabled": True,
            },
        )
        collection, _ = PublicationCollection.objects.update_or_create(
            authority=authority,
            slug="act-709-regulatory-publications",
            defaults={
                "name": "Act 709 regulatory publications",
                "document_family": PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
                "default_legal_status": "Official publication",
                "is_evidence_eligible": True,
                "priority": PublicationCollection.Priority.CRITICAL,
                "expected_update_frequency": "Ad hoc; poll every six hours",
                "is_enabled": True,
            },
        )
        endpoint, _ = SourceEndpoint.objects.update_or_create(
            collection=collection,
            name="JPDP Act 709 regulatory library",
            defaults={
                "discovery_url": ("https://www.pdp.gov.my/ppdpv1/en/akta/pdp-act-2010-en/"),
                "connector_type": SourceEndpoint.ConnectorType.HTML_LISTING,
                "allowed_domains": ["pdp.gov.my"],
                "polling_interval_minutes": 360,
                "pagination_strategy": SourceEndpoint.PaginationStrategy.NONE,
                "expected_content_types": ["text/html", "application/pdf"],
                "requires_javascript": False,
                "connector_configuration_version": 3,
                "is_enabled": True,
            },
        )
        if endpoint.next_poll_at is None:
            endpoint.next_poll_at = timezone.now() + timedelta(hours=6)
            endpoint.save(update_fields=("next_poll_at", "updated_at"))
        ConnectorConfiguration.objects.update_or_create(
            endpoint=endpoint,
            version=1,
            defaults={
                "configuration": {
                    "link_selector": "a[href]",
                    "include_path_prefixes": [
                        "/ppdpv1/en/akta/",
                        "/ppdpv1/wp-content/uploads/",
                    ],
                    "upload_path_prefixes": ["/ppdpv1/wp-content/uploads/"],
                    "exclude_path_suffixes": ["/feed/"],
                    "document_extensions": [".pdf", ".doc", ".docx", ".csv", ".json", ".xml"],
                    "max_candidates": 20,
                },
                "notes": "Historical Phase 2 listing-only configuration.",
                "is_active": False,
            },
        )
        ConnectorConfiguration.objects.update_or_create(
            endpoint=endpoint,
            version=2,
            defaults={
                "configuration": {
                    "link_selector": "a[href]",
                    "include_path_prefixes": [
                        "/ppdpv1/en/akta/",
                        "/ppdpv1/wp-content/uploads/",
                    ],
                    "upload_path_prefixes": ["/ppdpv1/wp-content/uploads/"],
                    "exclude_path_suffixes": ["/feed/"],
                    "document_extensions": [
                        ".pdf",
                        ".doc",
                        ".docx",
                        ".csv",
                        ".json",
                        ".xml",
                    ],
                    "max_candidates": 20,
                    "follow_detail_pages": True,
                    "detail_content_selector": ".betterdocs-entry-content",
                    "max_detail_pages": 20,
                },
                "notes": (
                    "JPDP Phase 3B configuration: follow official document links from "
                    "BetterDocs detail pages while retaining the page URL as document identity."
                ),
                "is_active": True,
            },
        )
        ConnectorConfiguration.objects.filter(endpoint=endpoint, version=2).update(is_active=False)
        ConnectorConfiguration.objects.update_or_create(
            endpoint=endpoint,
            version=3,
            defaults={
                "configuration": {
                    "link_selector": "a[href]",
                    "include_path_prefixes": [
                        "/ppdpv1/en/akta/",
                        "/ppdpv1/wp-content/uploads/",
                    ],
                    "upload_path_prefixes": ["/ppdpv1/wp-content/uploads/"],
                    "exclude_path_suffixes": ["/feed/"],
                    "document_extensions": [
                        ".pdf",
                        ".doc",
                        ".docx",
                        ".csv",
                        ".json",
                        ".xml",
                    ],
                    "max_candidates": 20,
                    "follow_detail_pages": True,
                    "detail_content_selector": ".betterdocs-entry-content",
                    "detail_resource_path_prefixes": ["/ppdpv1/en/akta/"],
                    "max_detail_pages": 20,
                    "max_feed_entries": 20,
                },
                "notes": (
                    "JPDP Phase 3D.2 configuration: retain bounded listing discovery while "
                    "monitoring approved detail pages and the official English RSS feed "
                    "independently. Feed entries outside the Act 709 path remain disabled "
                    "pending review."
                ),
                "is_active": True,
            },
        )
        feed, _ = register_monitored_resource(
            endpoint,
            resource_type=MonitoredResource.ResourceType.RSS,
            url="https://www.pdp.gov.my/ppdpv1/en/feed/",
            title="JPDP English official site feed",
            is_approved=True,
            approval_basis="Declared as application/rss+xml by the approved JPDP listing page",
            polling_interval_minutes=60,
            metadata={
                "declaration_page": endpoint.discovery_url,
                "excludes": ["comments feeds"],
            },
        )
        detail_urls = {
            str(candidate.metadata_hints.get("source_detail_page")): str(
                candidate.metadata_hints.get("title", "")
            )
            for candidate in DiscoveredCandidate.objects.filter(endpoint=endpoint)
            if candidate.metadata_hints.get("source_detail_page")
        }
        for detail_url, title in sorted(detail_urls.items()):
            register_monitored_resource(
                endpoint,
                resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
                url=detail_url,
                title=title,
                is_approved=True,
                approval_basis="Backfilled from prior approved JPDP listing evidence",
                polling_interval_minutes=360,
                metadata={"source_listing": endpoint.discovery_url},
            )
        self.stdout.write(self.style.SUCCESS(f"JPDP endpoint ready: {endpoint.id}"))
        self.stdout.write(
            self.style.SUCCESS(
                f"JPDP monitored resources ready: feed={feed.id} "
                f"backfilled_details={len(detail_urls)}"
            )
        )

        if options["run"]:
            source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
            execute_source_run.delay(str(source_run.id))
            self.stdout.write(self.style.SUCCESS(f"Queued source run: {source_run.id}"))
