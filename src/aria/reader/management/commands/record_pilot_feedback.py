import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from aria.reader.usage import record_pilot_feedback

CATEGORIES = (
    "search_usefulness",
    "evidence_clarity",
    "relevance_explanation",
    "impact_actionability",
)


class Command(BaseCommand):
    help = "Record append-only structured feedback from one active Phase 5 pilot user."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username")
        parser.add_argument("--category", choices=CATEGORIES, required=True)
        parser.add_argument("--rating", type=int, required=True)
        parser.add_argument("--comment", required=True)
        parser.add_argument("--recorded-by", required=True)
        parser.add_argument("--confirm", required=True)

    def handle(self, *args, **options) -> None:
        if options["confirm"] != "RECORD":
            raise CommandError("Recording pilot feedback requires --confirm RECORD.")
        if options["rating"] not in range(1, 6):
            raise CommandError("Pilot feedback rating must be from 1 to 5.")
        comment = options["comment"].strip()
        if len(comment) < 10 or len(comment) > 2000:
            raise CommandError("Pilot feedback comment must contain 10 to 2000 characters.")
        recorded_by = options["recorded_by"].strip()
        if not recorded_by:
            raise CommandError("--recorded-by must identify the person recording the feedback.")
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=options["username"], is_active=True)
        except user_model.DoesNotExist as error:
            raise CommandError("The active pilot user was not found.") from error
        if user.is_staff:
            raise CommandError("Pilot feedback must belong to a non-staff reader account.")
        record_pilot_feedback(
            user=user,
            category=options["category"],
            rating=options["rating"],
            comment=comment,
            recorded_by=recorded_by,
        )
        self.stdout.write(
            json.dumps(
                {
                    "username": user.get_username(),
                    "category": options["category"],
                    "rating": options["rating"],
                    "recorded": True,
                },
                sort_keys=True,
            )
        )
