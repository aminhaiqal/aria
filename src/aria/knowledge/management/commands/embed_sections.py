import json

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from aria.collections.models import PublicationCollection
from aria.knowledge.embedding_services import (
    current_sections_queryset,
    project_section_embeddings,
)
from aria.knowledge.embeddings import SUPPORTED_PROVIDERS, EmbeddingError
from aria.knowledge.tasks import embed_collection_sections


class Command(BaseCommand):
    help = "Populate versioned section embeddings for the current collection corpus."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--authority",
            default="personal-data-protection-commissioner-malaysia",
        )
        parser.add_argument(
            "--collection",
            default="act-709-regulatory-publications",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Project every current section across all enabled collections (sync only).",
        )
        parser.add_argument(
            "--provider",
            choices=sorted(SUPPORTED_PROVIDERS),
            default="openrouter",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run the embedding projection in this process instead of Celery.",
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        if options["all"] and not options["sync"]:
            raise CommandError("--all requires --sync to keep the operation explicitly bounded.")

        collection = None
        if not options["all"]:
            try:
                collection = PublicationCollection.objects.select_related("authority").get(
                    authority__slug=options["authority"],
                    slug=options["collection"],
                )
            except PublicationCollection.DoesNotExist as error:
                raise CommandError("The requested authority collection does not exist.") from error

        if not options["sync"]:
            assert collection is not None
            task = embed_collection_sections.delay(str(collection.id), options["provider"])
            payload = {
                "status": "queued",
                "task_id": str(task.id),
                "provider": options["provider"],
            }
        else:
            try:
                summary = project_section_embeddings(
                    current_sections_queryset(collection).order_by("id"),
                    provider_name=options["provider"],
                )
            except (EmbeddingError, ImproperlyConfigured) as error:
                raise CommandError(f"Embedding projection failed: {error}") from error
            payload = {"status": "completed", **summary.__dict__}

        payload["scope"] = "all_current_sections" if options["all"] else str(collection.id)

        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Embedding projection: {details}"))
