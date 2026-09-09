import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from aria.comparisons.models import DocumentComparison
from aria.comparisons.publications import publish_confirmed_comparison_changes


class Command(BaseCommand):
    help = "Create idempotent outbox events for currently confirmed textual changes."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--comparison", required=True)
        parser.add_argument("--publisher")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            comparison = DocumentComparison.objects.get(pk=options["comparison"])
        except (DocumentComparison.DoesNotExist, ValueError) as error:
            raise CommandError("The requested comparison does not exist.") from error
        publisher = None
        if options["publisher"]:
            try:
                publisher = get_user_model().objects.get(
                    username=options["publisher"],
                    is_active=True,
                    is_staff=True,
                )
            except get_user_model().DoesNotExist as error:
                raise CommandError("The publisher must be an active staff user.") from error
        if settings.REQUIRE_SEPARATE_PUBLISHER and publisher is None:
            raise CommandError("--publisher is required by the two-person publication rule.")
        try:
            summary = publish_confirmed_comparison_changes(comparison, publisher=publisher)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        payload = summary.__dict__
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Reviewed change publication: {details}"))
