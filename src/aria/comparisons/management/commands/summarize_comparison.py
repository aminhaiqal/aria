import json

from django.core.management.base import BaseCommand, CommandError

from aria.comparisons.models import DocumentComparison
from aria.comparisons.summaries import SummaryError, generate_comparison_summary
from aria.comparisons.tasks import summarize_comparison


class Command(BaseCommand):
    help = "Generate a structured GPT summary for currently confirmed comparison items."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--comparison", required=True)
        parser.add_argument("--sync", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            comparison = DocumentComparison.objects.get(pk=options["comparison"])
        except (DocumentComparison.DoesNotExist, ValueError) as error:
            raise CommandError("The requested comparison does not exist.") from error
        try:
            if options["sync"]:
                summary, created = generate_comparison_summary(comparison)
                payload = {
                    "status": "completed",
                    "summary_id": str(summary.id),
                    "created": created,
                    "model": summary.model,
                    "input_tokens": summary.input_tokens,
                    "output_tokens": summary.output_tokens,
                }
            else:
                task = summarize_comparison.delay(str(comparison.id))
                payload = {"status": "queued", "task_id": str(task.id)}
        except SummaryError as error:
            raise CommandError(f"Summary generation failed: {error}") from error

        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Comparison summary: {details}"))
