from django.core.management.base import BaseCommand

from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run
from aria.discovery.tasks import execute_source_run
from aria.sources.source_packs import apply_source_pack, load_source_pack


class Command(BaseCommand):
    help = "Install the JPDP Act 709 source pack and optionally queue one manual run."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--run",
            action="store_true",
            help="Queue a manual source run after source-pack synchronization.",
        )

    def handle(self, *args, **options) -> None:
        result = apply_source_pack(load_source_pack("jpdp-act-709"))
        endpoint = result.endpoint
        feed = endpoint.monitored_resources.get(resource_type="rss")
        self.stdout.write(
            self.style.SUCCESS(
                f"JPDP endpoint ready: {endpoint.id} pack={result.snapshot.checksum[:12]}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"JPDP monitored resources ready: feed={feed.id} "
                f"backfilled_details={result.backfilled_detail_count}"
            )
        )
        if options["run"]:
            source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
            execute_source_run.delay(str(source_run.id))
            self.stdout.write(self.style.SUCCESS(f"Queued source run: {source_run.id}"))
