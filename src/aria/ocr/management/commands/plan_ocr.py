import json

from django.core.management.base import BaseCommand, CommandError

from aria.collections.models import PublicationCollection
from aria.ocr.services import plan_collection_ocr


class Command(BaseCommand):
    help = "Plan deterministic OCR work for a publication collection without processing files."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--authority",
            default="personal-data-protection-commissioner-malaysia",
        )
        parser.add_argument(
            "--collection",
            default="act-709-regulatory-publications",
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            collection = PublicationCollection.objects.get(
                authority__slug=options["authority"],
                slug=options["collection"],
            )
        except PublicationCollection.DoesNotExist as error:
            raise CommandError("The requested authority collection does not exist.") from error
        items = plan_collection_ocr(collection)
        payload = {
            "count": len(items),
            "items": [
                {
                    "source_artifact_id": str(item.source_artifact.id),
                    "source_sha256": item.source_artifact.sha256,
                    "byte_size": item.source_artifact.byte_size,
                    "page_count": item.extraction_run.extracted_document.page_count,
                    "ocr_run_id": str(item.ocr_run.id),
                    "status": item.ocr_run.status,
                    "profile": (f"{item.ocr_run.profile_name}:{item.ocr_run.profile_version}"),
                    "configuration_hash": item.ocr_run.configuration_hash,
                }
                for item in items
            ],
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        for item in payload["items"]:
            self.stdout.write(
                f"{item['source_artifact_id']} {item['source_sha256']} "
                f"pages={item['page_count']} status={item['status']}"
            )
        self.stdout.write(self.style.SUCCESS(f"OCR plan complete: count={payload['count']}"))
