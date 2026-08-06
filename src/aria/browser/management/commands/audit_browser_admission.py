import json

from django.core.management.base import BaseCommand, CommandError

from aria.browser.admission import assess_browser_admission
from aria.sources.models import SourceEndpoint


class Command(BaseCommand):
    help = "Evaluate and record the browser-source admission gates without enabling the source."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "endpoint_id",
            nargs="?",
            help="Endpoint UUID; defaults to the registered AGC browser pilot.",
        )
        parser.add_argument("--required-captures", type=int, default=2)
        parser.add_argument(
            "--allow-incomplete",
            action="store_true",
            help="Return success while reporting failed gates (useful while a pilot is running).",
        )

    def handle(self, *args, **options) -> None:
        try:
            if options["endpoint_id"]:
                endpoint = SourceEndpoint.objects.get(pk=options["endpoint_id"])
            else:
                endpoint = SourceEndpoint.objects.get(name="AGC updated principal Acts")
        except (SourceEndpoint.DoesNotExist, ValueError) as error:
            raise CommandError("Browser source endpoint was not found.") from error
        if endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING:
            raise CommandError("Admission auditing requires a JavaScript listing endpoint.")

        try:
            assessment, created = assess_browser_admission(
                endpoint,
                required_captures=options["required_captures"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        payload = {
            "assessment_id": str(assessment.id),
            "report_signature": assessment.report_signature,
            "created": created,
            "endpoint_id": str(endpoint.id),
            "endpoint_name": endpoint.name,
            "required_captures": assessment.required_captures,
            "evaluated_capture_ids": assessment.evaluated_capture_ids,
            "candidate_set_sha256": assessment.candidate_set_sha256,
            "candidate_count": assessment.candidate_count,
            "ready_for_promotion": assessment.status == assessment.Status.READY,
            "gates": assessment.gates,
        }
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        if assessment.status != assessment.Status.READY and not options["allow_incomplete"]:
            raise CommandError("Browser source is not ready for promotion; inspect failed gates.")
