from django.core.management.base import BaseCommand, CommandError

from aria.browser.models import SourceAdmissionAssessment
from aria.sources.admission import promote_static_source


class Command(BaseCommand):
    help = "Enable a static source only from an exact current ready assessment."

    def add_arguments(self, parser) -> None:
        parser.add_argument("assessment_id", help="Exact static admission assessment UUID.")
        parser.add_argument(
            "--confirm",
            help="Type PROMOTE to acknowledge scheduled external retrieval.",
        )

    def handle(self, *args, **options) -> None:
        if options["confirm"] != "PROMOTE":
            raise CommandError("Static promotion requires --confirm PROMOTE.")
        try:
            assessment = SourceAdmissionAssessment.objects.select_related("endpoint").get(
                pk=options["assessment_id"],
                admission_profile=SourceAdmissionAssessment.Profile.STATIC_LISTING,
            )
            promotion = promote_static_source(
                assessment.endpoint,
                assessment,
                actor_type="system",
                actor_identifier="management_command",
            )
        except (SourceAdmissionAssessment.DoesNotExist, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Static source enabled: {assessment.endpoint_id}; promotion={promotion.id}; "
                f"next_poll_at={promotion.next_poll_at}"
            )
        )
