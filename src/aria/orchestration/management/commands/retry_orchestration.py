from django.core.management.base import BaseCommand, CommandError

from aria.comparisons.tasks import summarize_comparison
from aria.orchestration.models import ChangeOrchestration
from aria.orchestration.services import prepare_orchestration_retry
from aria.orchestration.tasks import process_change_orchestration


class Command(BaseCommand):
    help = "Explicitly retry one failed or OCR-ready orchestration."

    def add_arguments(self, parser):
        parser.add_argument("orchestration_id")

    def handle(self, *args, **options):
        try:
            orchestration = ChangeOrchestration.objects.get(pk=options["orchestration_id"])
        except (ChangeOrchestration.DoesNotExist, ValueError) as error:
            raise CommandError("Unknown orchestration ID.") from error
        was_summary_failure = orchestration.status == ChangeOrchestration.Status.SUMMARY_FAILED
        try:
            orchestration = prepare_orchestration_retry(orchestration)
        except ValueError as error:
            raise CommandError(str(error)) from error
        if was_summary_failure:
            if orchestration.comparison_id is None:
                raise CommandError("Summary retry has no comparison.")
            summarize_comparison.delay(str(orchestration.comparison_id))
        else:
            process_change_orchestration.delay(str(orchestration.id))
        source_status = (
            ChangeOrchestration.Status.SUMMARY_FAILED if was_summary_failure else "retryable"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued orchestration {orchestration.id} from status {source_status}"
            )
        )
