from django.core.management.base import BaseCommand, CommandError

from aria.artifacts.models import ArtifactObservation
from aria.orchestration.services import ensure_change_orchestration
from aria.orchestration.tasks import process_change_orchestration


class Command(BaseCommand):
    help = "Plan or create orchestrations for historical changed observations."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--queue", action="store_true")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be at least 1.")
        if options["queue"] and not options["apply"]:
            raise CommandError("--queue requires --apply.")
        observations = list(
            ArtifactObservation.objects.filter(
                content_changed=True,
                change_orchestration__isnull=True,
            )
            .select_related("raw_artifact")
            .order_by("retrieved_at", "id")[: options["limit"]]
        )
        if not options["apply"]:
            self.stdout.write(f"Backfill plan: count={len(observations)} apply_required=true")
            for observation in observations:
                self.stdout.write(
                    f"observation={observation.id} artifact={observation.raw_artifact.sha256}"
                )
            return

        queued = 0
        for observation in observations:
            orchestration, _ = ensure_change_orchestration(observation)
            if options["queue"]:
                process_change_orchestration.delay(str(orchestration.id))
                queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete: created_or_existing={len(observations)} queued={queued}"
            )
        )
