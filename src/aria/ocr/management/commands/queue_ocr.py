from django.core.management.base import BaseCommand, CommandError

from aria.collections.models import PublicationCollection
from aria.extraction.extractors import configuration_hash, get_extractor
from aria.extraction.models import ExtractionRun
from aria.extraction.services import RetryableExtractionError, extract_artifact
from aria.extraction.tasks import extract_raw_artifact
from aria.ocr.models import OCRRun
from aria.ocr.services import RetryableOCRError, plan_collection_ocr, process_ocr_run
from aria.ocr.tasks import process_ocr


def _has_current_successful_extraction(run: OCRRun) -> bool:
    artifact = run.searchable_pdf_derivative.derived_artifact
    extractor = get_extractor(artifact.detected_content_type)
    return artifact.extraction_runs.filter(
        extractor_name=extractor.name,
        extractor_version=extractor.version,
        configuration_hash=configuration_hash(extractor.configuration),
        status=ExtractionRun.Status.SUCCEEDED,
    ).exists()


class Command(BaseCommand):
    help = "Queue or synchronously process deterministic OCR work for a collection."

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
            "--sync",
            action="store_true",
            help="Run OCR and derivative extraction in this process.",
        )

    def handle(self, *args, **options) -> None:
        try:
            collection = PublicationCollection.objects.get(
                authority__slug=options["authority"],
                slug=options["collection"],
            )
        except PublicationCollection.DoesNotExist as error:
            raise CommandError("The requested authority collection does not exist.") from error

        completed = queued = extraction_queued = skipped = failed = 0
        for item in plan_collection_ocr(collection):
            run = item.ocr_run
            if run.status == OCRRun.Status.SUCCEEDED:
                skipped += 1
                if options["sync"]:
                    try:
                        extract_artifact(run.searchable_pdf_derivative.derived_artifact)
                    except RetryableExtractionError as error:
                        failed += 1
                        self.stderr.write(f"{run.id}: extraction failure: {error}")
                elif not _has_current_successful_extraction(run):
                    extract_raw_artifact.delay(
                        str(run.searchable_pdf_derivative.derived_artifact_id)
                    )
                    extraction_queued += 1
                continue
            if options["sync"]:
                try:
                    run = process_ocr_run(run, dispatch_extraction=False)
                    if run.status != OCRRun.Status.SUCCEEDED:
                        failed += 1
                        self.stderr.write(f"{run.id}: {run.status}: {run.error_message}")
                        continue
                    extract_artifact(run.searchable_pdf_derivative.derived_artifact)
                except (RetryableOCRError, RetryableExtractionError) as error:
                    failed += 1
                    self.stderr.write(f"{run.id}: retryable failure: {error}")
                else:
                    completed += 1
            else:
                process_ocr.delay(str(run.id))
                queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"OCR dispatch complete: completed={completed} queued={queued} "
                f"extraction_queued={extraction_queued} skipped={skipped} failed={failed}"
            )
        )
