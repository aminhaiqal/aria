import json

from django.core.management.base import BaseCommand, CommandError

from aria.health.phase5 import collect_phase5_status, record_next_product_milestone


class Command(BaseCommand):
    help = "Record the evidence-based product milestone selected at the end of Phase 5."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--title", required=True)
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--selected-by", required=True)
        parser.add_argument("--confirm", required=True)

    def handle(self, *args, **options) -> None:
        if options["confirm"] != "SELECT":
            raise CommandError("Selecting the next milestone requires --confirm SELECT.")
        status = collect_phase5_status()
        prerequisites = {
            key: passed
            for key, passed in status["criteria"].items()
            if key != "next_product_milestone_selected"
        }
        incomplete = [key for key, passed in prerequisites.items() if not passed]
        if incomplete:
            raise CommandError(
                "The Phase 5 evidence prerequisites are incomplete: " + ", ".join(incomplete)
            )
        try:
            event = record_next_product_milestone(
                title=options["title"],
                evidence=options["evidence"],
                selected_by=options["selected_by"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            json.dumps(
                {
                    "audit_event_id": str(event.id),
                    "title": event.details["title"],
                    "selected": True,
                },
                sort_keys=True,
            )
        )
