import hmac
import math
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from aria.comparisons.models import ComparisonItem
from aria.discovery.models import ResourceStructureIncident, SourceRun
from aria.events.models import OutboxEvent
from aria.impacts.models import RegulatoryImpact
from aria.orchestration.models import ChangeOrchestration
from aria.sources.models import SourceEndpoint

PIPELINE_HEARTBEAT_KEY = "aria:heartbeat:scheduler-worker"


def record_pipeline_heartbeat() -> datetime:
    observed_at = timezone.now()
    cache.set(PIPELINE_HEARTBEAT_KEY, observed_at.isoformat(), timeout=300)
    return observed_at


def pipeline_heartbeat() -> datetime | None:
    raw = cache.get(PIPELINE_HEARTBEAT_KEY)
    if not isinstance(raw, str):
        return None
    try:
        observed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return observed if timezone.is_aware(observed) else timezone.make_aware(observed)


def metrics_token_is_valid(authorization: str) -> bool:
    token = settings.METRICS_TOKEN
    if not token or not authorization.startswith("Bearer "):
        return False
    supplied = authorization.removeprefix("Bearer ")
    return hmac.compare_digest(supplied.encode(), token.encode())


def _grouped_counts(queryset, field: str, expected: tuple[str, ...] = ()) -> dict[str, int]:
    counts = {
        str(row[field]): row["count"]
        for row in queryset.values(field).annotate(count=Count("id")).order_by(field)
    }
    for value in expected:
        counts.setdefault(value, 0)
    return dict(sorted(counts.items()))


def _metric_family(name: str, help_text: str, values: dict[str, int], label: str) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    lines.extend(f'{name}{{{label}="{key}"}} {value}' for key, value in values.items())
    return lines


def render_operational_metrics() -> str:
    lines = [
        "# HELP aria_build_info Static information about the running ARIA service.",
        "# TYPE aria_build_info gauge",
        'aria_build_info{service="aria-core"} 1',
    ]
    lines.extend(
        _metric_family(
            "aria_source_endpoints",
            "Configured source endpoints by health state.",
            _grouped_counts(SourceEndpoint.objects.filter(is_enabled=True), "health_state"),
            "health_state",
        )
    )
    lines.extend(
        _metric_family(
            "aria_source_runs",
            "Source runs by persisted state.",
            _grouped_counts(SourceRun.objects.all(), "status", tuple(SourceRun.Status.values)),
            "status",
        )
    )
    lines.extend(
        _metric_family(
            "aria_change_orchestrations",
            "Changed-artifact orchestrations by persisted state.",
            _grouped_counts(
                ChangeOrchestration.objects.all(),
                "status",
                tuple(ChangeOrchestration.Status.values),
            ),
            "status",
        )
    )
    lines.extend(
        _metric_family(
            "aria_outbox_events",
            "Delivery outbox events by persisted state.",
            _grouped_counts(
                OutboxEvent.objects.all(), "status", tuple(OutboxEvent.Status.values)
            ),
            "status",
        )
    )
    lines.extend(
        (
            "# HELP aria_resource_structure_incidents_total Durable source-structure incidents.",
            "# TYPE aria_resource_structure_incidents_total counter",
            f"aria_resource_structure_incidents_total {ResourceStructureIncident.objects.count()}",
            (
                "# HELP aria_change_review_backlog Textual comparison items without a current "
                "decision."
            ),
            "# TYPE aria_change_review_backlog gauge",
            f"aria_change_review_backlog {_change_review_backlog()}",
            "# HELP aria_impact_review_backlog Regulatory impacts without a current decision.",
            "# TYPE aria_impact_review_backlog gauge",
            f"aria_impact_review_backlog {_impact_review_backlog()}",
        )
    )
    heartbeat = pipeline_heartbeat()
    heartbeat_timestamp = heartbeat.timestamp() if heartbeat else 0
    heartbeat_age = (
        max(0.0, (timezone.now() - heartbeat).total_seconds()) if heartbeat else math.inf
    )
    lines.extend(
        (
            (
                "# HELP aria_scheduler_worker_heartbeat_unixtime Last completed "
                "scheduler-worker heartbeat."
            ),
            "# TYPE aria_scheduler_worker_heartbeat_unixtime gauge",
            f"aria_scheduler_worker_heartbeat_unixtime {heartbeat_timestamp:.3f}",
            (
                "# HELP aria_scheduler_worker_heartbeat_age_seconds Age of the "
                "scheduler-worker heartbeat."
            ),
            "# TYPE aria_scheduler_worker_heartbeat_age_seconds gauge",
            f"aria_scheduler_worker_heartbeat_age_seconds "
            f"{heartbeat_age if math.isfinite(heartbeat_age) else '+Inf'}",
        )
    )
    return "\n".join(lines) + "\n"


def _change_review_backlog() -> int:
    return (
        ComparisonItem.objects.exclude(change_type=ComparisonItem.ChangeType.UNCHANGED)
        .annotate(review_count=Count("reviews"))
        .filter(review_count=0)
        .count()
    )


def _impact_review_backlog() -> int:
    return (
        RegulatoryImpact.objects.annotate(review_count=Count("reviews"))
        .filter(review_count=0)
        .count()
    )
