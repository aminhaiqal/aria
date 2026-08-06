import json

from django.core.management.base import BaseCommand

from aria.reliability.soak import collect_autonomous_cycle_acceptance


class Command(BaseCommand):
    help = "Report the first autonomous post-promotion cycle acceptance gate."

    def handle(self, *args, **options):
        report = collect_autonomous_cycle_acceptance()
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
        if report["status"] == "failed":
            raise SystemExit(1)
