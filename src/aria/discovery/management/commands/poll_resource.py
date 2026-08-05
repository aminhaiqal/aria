import json

from django.core.management.base import BaseCommand, CommandError

from aria.discovery.models import MonitoredResource, SourceRun
from aria.discovery.services import create_resource_run
from aria.discovery.tasks import execute_resource_run


class Command(BaseCommand):
    help = "Run one explicitly selected, approved monitored resource."

    def add_arguments(self, parser) -> None:
        parser.add_argument("resource_id")
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Execute in this process instead of queuing the discovery worker.",
        )
        parser.add_argument(
            "--replay",
            action="store_true",
            help="Label this fresh conditional check as an operator-requested replay.",
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            resource = MonitoredResource.objects.select_related("endpoint").get(
                pk=options["resource_id"],
                is_enabled=True,
                is_approved=True,
                endpoint__is_enabled=True,
            )
        except (MonitoredResource.DoesNotExist, ValueError) as error:
            raise CommandError("The resource is not enabled, approved, and addressable.") from error

        trigger = SourceRun.Trigger.REPLAY if options["replay"] else SourceRun.Trigger.MANUAL
        resource_run, _ = create_resource_run(resource, trigger=trigger)
        if options["sync"]:
            execute_resource_run.run(str(resource_run.id))
            resource_run.refresh_from_db()
        else:
            execute_resource_run.delay(str(resource_run.id))

        payload = {
            "resource_id": str(resource.id),
            "resource_run_id": str(resource_run.id),
            "source_run_id": str(resource_run.source_run_id),
            "trigger": trigger,
            "status": resource_run.status,
            "execution": "synchronous" if options["sync"] else "queued",
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "Resource monitor cycle: "
                + " ".join(f"{key}={value}" for key, value in payload.items())
            )
        )
