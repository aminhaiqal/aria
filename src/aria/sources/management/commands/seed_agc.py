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
    help = "Register the disabled-by-default AGC Updated Principal Acts browser pilot."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--run",
            action="store_true",
            help="Queue one bounded manual pilot without enabling scheduled polling.",
        )

    def handle(self, *args, **options) -> None:
        authority, _ = Authority.objects.update_or_create(
            slug="attorney-generals-chambers-malaysia",
            defaults={
                "name": "Attorney General's Chambers of Malaysia",
                "aliases": ["AGC Malaysia", "Jabatan Peguam Negara"],
                "jurisdiction": "Malaysia",
                "country_code": "MY",
                "authority_type": Authority.AuthorityType.OTHER,
                "regulatory_domains": ["federal-legislation"],
                "official_domains": ["agc.gov.my"],
                "trust_classification": Authority.TrustClassification.AUTHORITATIVE,
                "is_enabled": True,
            },
        )
        collection, _ = PublicationCollection.objects.update_or_create(
            authority=authority,
            slug="updated-principal-acts",
            defaults={
                "name": "Updated principal Acts",
                "document_family": PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
                "default_legal_status": "Official federal legislation publication",
                "is_evidence_eligible": True,
                "priority": PublicationCollection.Priority.HIGH,
                "expected_update_frequency": "Ad hoc; pilot daily after admission",
                "is_enabled": True,
            },
        )
        endpoint, created = SourceEndpoint.objects.get_or_create(
            collection=collection,
            name="AGC updated principal Acts",
            defaults={
                "discovery_url": "https://lom.agc.gov.my/principal.php?type=updated",
                "connector_type": SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING,
                "allowed_domains": ["lom.agc.gov.my"],
                "polling_interval_minutes": 1440,
                "pagination_strategy": SourceEndpoint.PaginationStrategy.NONE,
                "expected_content_types": ["text/html", "application/pdf"],
                "requires_javascript": True,
                "connector_configuration_version": 1,
                "health_state": SourceEndpoint.HealthState.DISABLED,
                "is_enabled": False,
            },
        )
        if not created:
            endpoint.discovery_url = "https://lom.agc.gov.my/principal.php?type=updated"
            endpoint.connector_type = SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING
            endpoint.allowed_domains = ["lom.agc.gov.my"]
            endpoint.polling_interval_minutes = 1440
            endpoint.pagination_strategy = SourceEndpoint.PaginationStrategy.NONE
            endpoint.expected_content_types = ["text/html", "application/pdf"]
            endpoint.requires_javascript = True
            endpoint.connector_configuration_version = 1
            endpoint.full_clean()
            endpoint.save(
                update_fields=(
                    "discovery_url",
                    "connector_type",
                    "allowed_domains",
                    "polling_interval_minutes",
                    "pagination_strategy",
                    "expected_content_types",
                    "requires_javascript",
                    "connector_configuration_version",
                    "updated_at",
                )
            )

        ConnectorConfiguration.objects.update_or_create(
            endpoint=endpoint,
            version=1,
            defaults={
                "configuration": {
                    "link_selector": "#data-updated a.event_kira_download_updated[href]",
                    "include_path_prefixes": ["/ilims/upload/portal/akta/outputaktap/"],
                    "upload_path_prefixes": ["/ilims/upload/portal/akta/outputaktap/"],
                    "document_extensions": [".pdf"],
                    "max_candidates": 20,
                    "ready_selector": "#data-updated tbody tr",
                    "render_wait_milliseconds": 1500,
                    "browser_dependency_domains": [
                        "cdn.datatables.net",
                        "cdnjs.cloudflare.com",
                    ],
                    "browser_read_only_post_paths": ["/json-updated-2024.php"],
                },
                "notes": (
                    "Phase 3D.7 controlled pilot. The raw official page contains an empty "
                    "DataTable; its own JavaScript loads rows from the exact read-only AGC "
                    "JSON endpoint. CDN dependencies are network-only and can never become "
                    "publication candidates. Keep the source disabled until two repeatable "
                    "captures, candidate review, and downstream lineage checks pass."
                ),
                "is_active": True,
            },
        )

        if not endpoint.is_enabled:
            endpoint.next_poll_at = None
            endpoint.health_state = SourceEndpoint.HealthState.DISABLED
            endpoint.save(update_fields=("next_poll_at", "health_state", "updated_at"))
        elif endpoint.next_poll_at is None:
            endpoint.next_poll_at = timezone.now() + timedelta(days=1)
            endpoint.save(update_fields=("next_poll_at", "updated_at"))

        self.stdout.write(
            self.style.SUCCESS(
                f"AGC browser pilot ready: {endpoint.id} enabled={str(endpoint.is_enabled).lower()}"
            )
        )
        if options["run"]:
            source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
            execute_source_run.delay(str(source_run.id))
            mode = "enabled-source verification" if endpoint.is_enabled else "disabled-source pilot"
            self.stdout.write(
                self.style.SUCCESS(f"Queued {mode} run: {source_run.id}; scheduling unchanged")
            )
