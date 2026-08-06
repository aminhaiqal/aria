from django.core.management.base import BaseCommand, CommandError

from aria.browser.admission import assess_browser_admission, promote_admitted_source
from aria.browser.models import SourceAdmissionAssessment
from aria.sources.models import SourceEndpoint


class Command(BaseCommand):
    help = "Enable a browser source only after every admission gate passes."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "endpoint_id",
            nargs="?",
            help="Endpoint UUID; defaults to the registered AGC browser pilot.",
        )
        parser.add_argument("--required-captures", type=int, default=2)
        parser.add_argument(
            "--assessment",
            help="Optional admission assessment UUID; current evidence must still match it.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required acknowledgement that this changes scheduled external retrieval.",
        )

    def handle(self, *args, **options) -> None:
        if not options["confirm"]:
            raise CommandError("Promotion requires --confirm.")
        try:
            endpoints = SourceEndpoint.objects.all()
            if options["endpoint_id"]:
                endpoint = endpoints.get(pk=options["endpoint_id"])
            else:
                endpoint = endpoints.get(name="AGC updated principal Acts")
        except (SourceEndpoint.DoesNotExist, ValueError) as error:
            raise CommandError("Browser source endpoint was not found.") from error
        if endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING:
            raise CommandError("Promotion requires a JavaScript listing endpoint.")
        if endpoint.is_enabled:
            self.stdout.write(self.style.SUCCESS(f"Browser source already enabled: {endpoint.id}"))
            return

        try:
            if options["assessment"]:
                assessment = SourceAdmissionAssessment.objects.get(
                    pk=options["assessment"],
                    endpoint=endpoint,
                )
            else:
                assessment, _ = assess_browser_admission(
                    endpoint,
                    required_captures=options["required_captures"],
                )
            promotion = promote_admitted_source(
                endpoint,
                assessment,
                actor_identifier="management_command",
            )
        except (SourceAdmissionAssessment.DoesNotExist, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Browser source enabled: {endpoint.id}; promotion={promotion.id}; "
                f"next_poll_at={promotion.next_poll_at}"
            )
        )
