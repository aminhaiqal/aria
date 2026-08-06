import json

from django.core.management.base import BaseCommand, CommandError

from aria.sources.source_packs import (
    SourcePackError,
    apply_source_pack,
    available_source_packs,
    build_source_pack_plan,
    load_source_pack,
)


class Command(BaseCommand):
    help = "Validate, plan, or explicitly apply a repository-backed official source pack."

    def add_arguments(self, parser) -> None:
        parser.add_argument("pack_slug", nargs="?")
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options) -> None:
        if options["list"]:
            self.stdout.write(json.dumps({"source_packs": available_source_packs()}, indent=2))
            return
        if not options["pack_slug"]:
            raise CommandError("Provide a source-pack slug or use --list.")
        if options["apply"] != options["confirm"]:
            raise CommandError("Applying a source pack requires both --apply and --confirm.")
        try:
            pack = load_source_pack(options["pack_slug"])
            plan = build_source_pack_plan(pack)
            output = {"mode": "dry_run", **plan.as_dict()}
            if options["apply"]:
                result = apply_source_pack(pack)
                output = {"mode": "applied", **plan.as_dict(), **result.as_dict()}
        except SourcePackError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
