import json

from django.core.management.base import BaseCommand

from aria.health.phase5 import collect_phase5_status


class Command(BaseCommand):
    help = "Report read-only evidence against every Phase 5 exit criterion."

    def handle(self, *args, **options) -> None:
        self.stdout.write(json.dumps(collect_phase5_status(), indent=2, sort_keys=True))
