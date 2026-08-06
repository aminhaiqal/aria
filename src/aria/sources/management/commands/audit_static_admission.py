import json

from django.core.management.base import BaseCommand, CommandError

from aria.sources.admission import assess_static_admission
from aria.sources.pilots import resolve_installed_source


class Command(BaseCommand):
    help = "Evaluate and record static-source admission gates without enabling the source."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "source",
            nargs="?",
            default="parliament-dewan-rakyat-bills",
            help="Source-pack slug or endpoint UUID; defaults to the Parliament pilot.",
        )
        parser.add_argument("--required-runs", type=int, default=2)
        parser.add_argument("--allow-incomplete", action="store_true")

    def handle(self, *args, **options) -> None:
        endpoint = resolve_installed_source(options["source"])
        if endpoint is None:
            raise CommandError("Installed source-pack endpoint was not found.")
        try:
            assessment, created = assess_static_admission(
                endpoint,
                required_runs=options["required_runs"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        payload = {
            "assessment_id": str(assessment.id),
            "report_signature": assessment.report_signature,
            "created": created,
            "endpoint_id": str(endpoint.id),
            "endpoint_name": endpoint.name,
            "admission_profile": assessment.admission_profile,
            "required_runs": assessment.required_evidence_count,
            "evaluated_source_run_ids": assessment.evaluated_source_run_ids,
            "candidate_set_sha256": assessment.candidate_set_sha256,
            "candidate_count": assessment.candidate_count,
            "ready_for_promotion": assessment.status == assessment.Status.READY,
            "gates": assessment.gates,
        }
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        if assessment.status != assessment.Status.READY and not options["allow_incomplete"]:
            raise CommandError("Static source is not ready for promotion; inspect failed gates.")
