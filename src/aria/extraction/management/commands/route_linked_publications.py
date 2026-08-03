from django.core.management.base import BaseCommand, CommandError

from aria.collections.models import PublicationCollection
from aria.extraction.linked_documents import (
    LinkedDocumentRoutingError,
    route_linked_publications,
)


class Command(BaseCommand):
    help = "Route primary document links found in archived HTML extraction metadata."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--authority",
            default="personal-data-protection-commissioner-malaysia",
            help="Authority slug (defaults to the JPDP pilot authority).",
        )
        parser.add_argument(
            "--collection",
            default="act-709-regulatory-publications",
            help="Collection slug (defaults to the JPDP Act 709 collection).",
        )
        parser.add_argument(
            "--queue",
            action="store_true",
            help="Queue allowlisted fetches after creating the deterministic route run.",
        )

    def handle(self, *args, **options) -> None:
        try:
            collection = PublicationCollection.objects.select_related("authority").get(
                authority__slug=options["authority"],
                slug=options["collection"],
            )
        except PublicationCollection.DoesNotExist as error:
            raise CommandError("The requested authority collection does not exist.") from error
        try:
            result = route_linked_publications(
                collection,
                queue_fetches=options["queue"],
            )
        except LinkedDocumentRoutingError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Linked publication routing complete: run={result.source_run.id} "
                f"created={result.created} candidates={len(result.candidates)} "
                f"queued={result.queued_count}"
            )
        )
