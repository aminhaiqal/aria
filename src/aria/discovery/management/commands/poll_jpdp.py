import json

from django.core.management.base import BaseCommand, CommandError

from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run
from aria.discovery.tasks import execute_source_run
from aria.sources.models import SourceEndpoint


class Command(BaseCommand):
    help = "Run one bounded monitor cycle for the approved JPDP regulatory library."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Execute in this process instead of queuing the discovery worker.",
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            endpoint = SourceEndpoint.objects.get(
                collection__authority__slug="personal-data-protection-commissioner-malaysia",
                collection__slug="act-709-regulatory-publications",
                connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
                is_enabled=True,
            )
        except SourceEndpoint.DoesNotExist as error:
            raise CommandError("The enabled JPDP endpoint is not seeded.") from error
        except SourceEndpoint.MultipleObjectsReturned as error:
            raise CommandError("More than one enabled JPDP endpoint is configured.") from error

        source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
        if options["sync"]:
            execute_source_run.run(str(source_run.id))
            source_run.refresh_from_db()
        else:
            execute_source_run.delay(str(source_run.id))

        payload = {
            "endpoint_id": str(endpoint.id),
            "source_run_id": str(source_run.id),
            "status": source_run.status,
            "execution": "synchronous" if options["sync"] else "queued",
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "JPDP monitor cycle: "
                + " ".join(f"{key}={value}" for key, value in payload.items())
            )
        )
