import json

from django.core.management.base import BaseCommand

from aria.reliability.confidence import (
    collect_source_confidence_report,
    serialize_source_confidence,
)


class Command(BaseCommand):
    help = "Report read-only admission and end-to-end evidence confidence for every source."

    def handle(self, *args, **options) -> None:
        report = [serialize_source_confidence(row) for row in collect_source_confidence_report()]
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
