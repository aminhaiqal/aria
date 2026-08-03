from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run
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
                "connector_configuration_version": 1,
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
                "notes": "First official ARIA source approved for the Phase 2 pilot.",
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"JPDP endpoint ready: {endpoint.id}"))

        if options["run"]:
            source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
            execute_source_run.delay(str(source_run.id))
            self.stdout.write(self.style.SUCCESS(f"Queued source run: {source_run.id}"))
