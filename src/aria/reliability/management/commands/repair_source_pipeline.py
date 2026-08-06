import json

from django.core.management.base import BaseCommand, CommandError

from aria.reliability.repair import build_source_repair_plan, queue_source_repairs
from aria.sources.models import SourceEndpoint


class Command(BaseCommand):
    help = "Plan or explicitly queue idempotent repairs for one source's latest pipeline."

    def add_arguments(self, parser) -> None:
        parser.add_argument("endpoint_id", nargs="?")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            if options["endpoint_id"]:
                endpoint = SourceEndpoint.objects.get(pk=options["endpoint_id"])
            else:
                endpoint = SourceEndpoint.objects.get(name="AGC updated principal Acts")
        except (SourceEndpoint.DoesNotExist, ValueError) as error:
            raise CommandError("Source endpoint was not found.") from error
        if options["apply"] != options["confirm"]:
            raise CommandError("Queueing repairs requires both --apply and --confirm.")
        plan = build_source_repair_plan(endpoint)
        output = {**plan.as_dict(), "plan_fingerprint": plan.fingerprint, "queued": 0}
        if options["apply"]:
            output["queued"] = queue_source_repairs(endpoint, plan)
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
