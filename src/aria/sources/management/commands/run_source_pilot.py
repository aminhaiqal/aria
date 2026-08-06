from django.core.management.base import BaseCommand, CommandError

from aria.sources.pilots import (
    SourcePilotError,
    queue_disabled_source_pilot,
    resolve_installed_source,
)


class Command(BaseCommand):
    help = "Queue one guarded manual run for a disabled source-pack endpoint."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "source",
            nargs="?",
            default="parliament-dewan-rakyat-bills",
            help="Source-pack slug or endpoint UUID; defaults to the Parliament pilot.",
        )
        parser.add_argument(
            "--confirm",
            help="Type RUN to acknowledge bounded external retrieval without scheduling.",
        )

    def handle(self, *args, **options) -> None:
        if options["confirm"] != "RUN":
            raise CommandError("Pilot execution requires --confirm RUN.")
        endpoint = resolve_installed_source(options["source"])
        if endpoint is None:
            raise CommandError("Installed source-pack endpoint was not found.")
        try:
            source_run = queue_disabled_source_pilot(
                endpoint,
                actor_type="system",
                actor_identifier="management_command",
            )
        except SourcePilotError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued disabled source pilot: endpoint={endpoint.id} run={source_run.id}; "
                "schedule remains disabled"
            )
        )
