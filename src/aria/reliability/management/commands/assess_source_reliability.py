import json

from django.core.management.base import BaseCommand, CommandError

from aria.reliability.services import assess_source_reliability
from aria.sources.models import SourceEndpoint


class Command(BaseCommand):
    help = "Assess one source or every enabled source and record transition alerts."

    def add_arguments(self, parser) -> None:
        parser.add_argument("endpoint_id", nargs="?")
        parser.add_argument("--all", action="store_true")

    def handle(self, *args, **options) -> None:
        if options["all"]:
            endpoints = SourceEndpoint.objects.filter(is_enabled=True)
        else:
            try:
                if options["endpoint_id"]:
                    endpoints = [SourceEndpoint.objects.get(pk=options["endpoint_id"])]
                else:
                    endpoints = [SourceEndpoint.objects.get(name="AGC updated principal Acts")]
            except (SourceEndpoint.DoesNotExist, ValueError) as error:
                raise CommandError("Source endpoint was not found.") from error
        reports = []
        for endpoint in endpoints:
            assessment, created = assess_source_reliability(endpoint)
            reports.append(
                {
                    "assessment_id": str(assessment.id),
                    "endpoint_id": str(endpoint.id),
                    "endpoint_name": endpoint.name,
                    "status": assessment.status,
                    "created": created,
                    "candidate_count": assessment.candidate_count,
                    "artifact_ready_count": assessment.artifact_ready_count,
                    "extraction_ready_count": assessment.extraction_ready_count,
                    "graph_ready_count": assessment.graph_ready_count,
                    "section_count": assessment.section_count,
                    "local_embedding_count": assessment.local_embedding_count,
                    "configured_embedding_count": assessment.configured_embedding_count,
                    "freshness_deadline": (
                        assessment.freshness_deadline.isoformat()
                        if assessment.freshness_deadline
                        else ""
                    ),
                    "findings": assessment.findings,
                }
            )
        self.stdout.write(json.dumps(reports, indent=2, sort_keys=True))
