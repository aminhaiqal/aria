import json

from django.core.management.base import BaseCommand, CommandError

from aria.collections.models import PublicationCollection
from aria.quality.services import QualityAssessmentInProgress, assess_collection_quality


class Command(BaseCommand):
    help = "Assess extraction quality for every immutable version in a publication collection."

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
            "--json",
            action="store_true",
            help="Emit a machine-readable summary.",
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
            run = assess_collection_quality(collection)
        except QualityAssessmentInProgress as error:
            raise CommandError(str(error)) from error
        except Exception as error:
            raise CommandError(f"Quality assessment failed: {error}") from error

        summary = {
            "run_id": str(run.id),
            "status": run.status,
            "ruleset": run.ruleset,
            "configuration_hash": run.configuration_hash,
            "corpus_fingerprint": run.corpus_fingerprint,
            "document_count": run.document_count,
            "passed_count": run.passed_count,
            "warning_count": run.warning_count,
            "review_required_count": run.review_required_count,
            "finding_count": run.finding_count,
        }
        if options["json"]:
            self.stdout.write(json.dumps(summary, sort_keys=True))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Quality assessment complete: "
                    f"run={run.id} documents={run.document_count} passed={run.passed_count} "
                    f"warning={run.warning_count} "
                    f"review_required={run.review_required_count} findings={run.finding_count}"
                )
            )
