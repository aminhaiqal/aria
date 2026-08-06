from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from aria.browser.admission import evaluate_browser_admission
from aria.events.services import record_audit_event, record_pipeline_event
from aria.sources.models import SourceEndpoint


class Command(BaseCommand):
    help = "Enable a browser source only after every admission gate passes."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "endpoint_id",
            nargs="?",
            help="Endpoint UUID; defaults to the registered AGC browser pilot.",
        )
        parser.add_argument("--required-captures", type=int, default=2)
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required acknowledgement that this changes scheduled external retrieval.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        if not options["confirm"]:
            raise CommandError("Promotion requires --confirm.")
        try:
            endpoints = SourceEndpoint.objects.select_for_update()
            if options["endpoint_id"]:
                endpoint = endpoints.get(pk=options["endpoint_id"])
            else:
                endpoint = endpoints.get(name="AGC updated principal Acts")
        except (SourceEndpoint.DoesNotExist, ValueError) as error:
            raise CommandError("Browser source endpoint was not found.") from error
        if endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING:
            raise CommandError("Promotion requires a JavaScript listing endpoint.")
        if endpoint.is_enabled:
            self.stdout.write(self.style.SUCCESS(f"Browser source already enabled: {endpoint.id}"))
            return

        try:
            report = evaluate_browser_admission(
                endpoint,
                required_captures=options["required_captures"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        if not report.ready_for_promotion:
            failed = ", ".join(gate.name for gate in report.gates if not gate.passed)
            raise CommandError(f"Admission gates failed: {failed}.")

        endpoint.is_enabled = True
        endpoint.next_poll_at = timezone.now() + timedelta(
            minutes=endpoint.polling_interval_minutes
        )
        endpoint.health_state = SourceEndpoint.HealthState.HEALTHY
        endpoint.save(update_fields=("is_enabled", "next_poll_at", "health_state", "updated_at"))
        payload = {
            "admission": report.as_dict(),
            "next_poll_at": endpoint.next_poll_at.isoformat(),
        }
        record_audit_event(
            action="browser.source.promoted",
            target_type="source_endpoint",
            target_id=endpoint.id,
            details=payload,
        )
        record_pipeline_event(
            event_type="browser.source.promoted",
            aggregate_type="source_endpoint",
            aggregate_id=endpoint.id,
            payload=payload,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Browser source enabled: {endpoint.id}; next_poll_at={endpoint.next_poll_at}"
            )
        )
