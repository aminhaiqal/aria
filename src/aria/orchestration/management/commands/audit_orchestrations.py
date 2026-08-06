from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation
from aria.orchestration.models import ChangeOrchestration


class Command(BaseCommand):
    help = "Report orchestration coverage and operational state without changing data."

    def handle(self, *args, **options):
        changed = ArtifactObservation.objects.filter(content_changed=True)
        missing = changed.filter(change_orchestration__isnull=True).count()
        stale_before = timezone.now() - timedelta(
            minutes=settings.ORCHESTRATION_STALE_AFTER_MINUTES
        )
        stale = ChangeOrchestration.objects.filter(
            status=ChangeOrchestration.Status.RUNNING,
            heartbeat_at__lt=stale_before,
        ).count()
        status_counts = {
            row["status"]: row["count"]
            for row in ChangeOrchestration.objects.values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        }
        self.stdout.write(
            f"changed_observations={changed.count()} "
            f"orchestrations={ChangeOrchestration.objects.count()} "
            f"missing={missing} stale_running={stale}"
        )
        self.stdout.write(f"status_counts={status_counts}")
