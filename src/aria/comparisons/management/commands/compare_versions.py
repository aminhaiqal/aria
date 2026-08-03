import json

from django.core.management.base import BaseCommand, CommandError

from aria.comparisons.services import (
    ComparisonEligibilityError,
    compare_all_eligible_versions,
    compare_document_versions,
)
from aria.comparisons.tasks import compare_eligible_versions, compare_version_pair
from aria.documents.models import DocumentVersion


class Command(BaseCommand):
    help = "Compare eligible immutable version pairs using deterministic structural evidence."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--before")
        parser.add_argument("--after")
        parser.add_argument("--sync", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        before_id = options["before"]
        after_id = options["after"]
        if bool(before_id) != bool(after_id):
            raise CommandError("--before and --after must be supplied together.")
        try:
            if before_id and after_id:
                before = DocumentVersion.objects.get(pk=before_id)
                after = DocumentVersion.objects.get(pk=after_id)
                if options["sync"]:
                    comparison, created = compare_document_versions(before, after)
                    payload = {
                        "status": "completed",
                        "comparison_id": str(comparison.id),
                        "created": created,
                    }
                else:
                    task = compare_version_pair.delay(str(before.id), str(after.id))
                    payload = {"status": "queued", "task_id": str(task.id)}
            elif options["sync"]:
                payload = {"status": "completed", **compare_all_eligible_versions().__dict__}
            else:
                task = compare_eligible_versions.delay()
                payload = {"status": "queued", "task_id": str(task.id)}
        except DocumentVersion.DoesNotExist as error:
            raise CommandError("The requested document version does not exist.") from error
        except ComparisonEligibilityError as error:
            raise CommandError(f"Comparison is not eligible: {error}") from error

        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Version comparison: {details}"))
