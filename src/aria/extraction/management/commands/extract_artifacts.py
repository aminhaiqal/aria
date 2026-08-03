from django.core.management.base import BaseCommand, CommandError

from aria.artifacts.models import RawArtifact
from aria.extraction.services import RetryableExtractionError, extract_artifact
from aria.extraction.tasks import extract_raw_artifact


class Command(BaseCommand):
    help = "Extract stored artifacts, or queue them for the extraction worker."

    def add_arguments(self, parser):
        parser.add_argument("--artifact", action="append", dest="artifact_ids")
        parser.add_argument("--limit", type=int)
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run in this process instead of dispatching Celery tasks.",
        )

    def handle(self, *args, **options):
        queryset = RawArtifact.objects.order_by("created_at")
        artifact_ids = options["artifact_ids"]
        if artifact_ids:
            queryset = queryset.filter(id__in=artifact_ids)
        if options["limit"] is not None:
            if options["limit"] < 1:
                raise CommandError("--limit must be at least 1.")
            queryset = queryset[: options["limit"]]

        completed = failed = queued = 0
        for artifact in queryset.iterator():
            if options["sync"]:
                try:
                    run = extract_artifact(artifact)
                except RetryableExtractionError as error:
                    failed += 1
                    self.stderr.write(f"{artifact.id}: retryable failure: {error}")
                else:
                    completed += 1
                    self.stdout.write(f"{artifact.id}: {run.status}")
            else:
                extract_raw_artifact.delay(str(artifact.id))
                queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Extraction complete: completed={completed} failed={failed} queued={queued}"
            )
        )
