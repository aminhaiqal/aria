from django.core.management.base import BaseCommand

from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run
from aria.discovery.tasks import execute_source_run
from aria.sources.source_packs import apply_source_pack, load_source_pack


class Command(BaseCommand):
    help = "Install the AGC source pack and optionally queue one bounded manual capture."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--run",
            action="store_true",
            help="Queue one bounded manual pilot without changing scheduled activation.",
        )

    def handle(self, *args, **options) -> None:
        result = apply_source_pack(load_source_pack("agc-updated-principal-acts"))
        endpoint = result.endpoint
        self.stdout.write(
            self.style.SUCCESS(
                f"AGC browser pilot ready: {endpoint.id} "
                f"enabled={str(endpoint.is_enabled).lower()} pack={result.snapshot.checksum[:12]}"
            )
        )
        if options["run"]:
            source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
            execute_source_run.delay(str(source_run.id))
            mode = "enabled-source verification" if endpoint.is_enabled else "disabled-source pilot"
            self.stdout.write(
                self.style.SUCCESS(f"Queued {mode} run: {source_run.id}; scheduling unchanged")
            )
