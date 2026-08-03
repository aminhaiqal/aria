import json

from django.core.management.base import BaseCommand

from aria.comparisons.audit import audit_active_versions


class Command(BaseCommand):
    help = "Audit active document-version provenance and representation lineage without mutation."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        payload = audit_active_versions()
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "Version lineage audit: "
                f"versions={payload['version_count']} "
                f"verified={payload['verified_count']} "
                f"reconstructable={payload['reconstructable_count']} "
                f"quarantined={payload['quarantined_count']}"
            )
        )
