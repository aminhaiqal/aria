import hmac
import math
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation
from aria.comparisons.models import ComparisonItem
from aria.discovery.models import ResourceStructureIncident, SourceRun
from aria.events.models import OutboxEvent
from aria.impacts.models import RegulatoryImpact
from aria.orchestration.models import ChangeOrchestration
from aria.reliability.models import SourceReliabilityAssessment
from aria.sources.models import SourceEndpoint

PIPELINE_HEARTBEAT_KEY = "aria:heartbeat:scheduler-worker"
PERFORMANCE_WINDOW = timedelta(hours=24)
PERFORMANCE_WINDOW_LABEL = "24h"


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


def _metric_value(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _duration_seconds(queryset) -> list[float]:
    return [
        max(0.0, (finished_at - started_at).total_seconds())
        for started_at, finished_at in queryset.values_list("started_at", "finished_at")
        if started_at and finished_at
    ]


def _latest_evidence_coverage() -> dict[str, float]:
    totals = {
        "artifact": [0, 0],
        "extraction": [0, 0],
        "graph": [0, 0],
        "embedding": [0, 0],
    }
    for endpoint in SourceEndpoint.objects.filter(is_enabled=True).only("id"):
        assessment = (
            SourceReliabilityAssessment.objects.filter(endpoint=endpoint)
            .order_by("-assessed_at", "-id")
            .first()
        )
        if assessment is None:
            continue
        totals["artifact"][0] += assessment.artifact_ready_count
        totals["artifact"][1] += assessment.candidate_count
        totals["extraction"][0] += assessment.extraction_ready_count
        totals["extraction"][1] += assessment.candidate_count
        totals["graph"][0] += assessment.graph_ready_count
        totals["graph"][1] += assessment.candidate_count
        totals["embedding"][0] += assessment.configured_embedding_count
        totals["embedding"][1] += assessment.section_count
    return {
        stage: ready / total if total else math.nan
        for stage, (ready, total) in totals.items()
    }


def _oldest_active_age_seconds(now: datetime) -> float:
    active_source = (
        SourceRun.objects.filter(status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING))
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    active_orchestration = (
        ChangeOrchestration.objects.filter(
            status__in=(
                ChangeOrchestration.Status.PENDING,
                ChangeOrchestration.Status.QUEUED,
                ChangeOrchestration.Status.RUNNING,
                ChangeOrchestration.Status.WAITING_OCR,
                ChangeOrchestration.Status.SUMMARY_PENDING,
            )
        )
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    oldest = min(
        (value for value in (active_source, active_orchestration) if value is not None),
        default=None,
    )
    return max(0.0, (now - oldest).total_seconds()) if oldest else 0.0


def _performance_metrics(now: datetime) -> list[str]:
    window_start = now - PERFORMANCE_WINDOW
    recent_runs = SourceRun.objects.filter(created_at__gte=window_start)
    run_counts = _grouped_counts(recent_runs, "status", tuple(SourceRun.Status.values))
    terminal_count = run_counts[SourceRun.Status.COMPLETED] + run_counts[SourceRun.Status.FAILED]
    success_ratio = (
        run_counts[SourceRun.Status.COMPLETED] / terminal_count
        if terminal_count
        else math.nan
    )
    source_durations = _duration_seconds(
        recent_runs.filter(
            status=SourceRun.Status.COMPLETED,
            started_at__isnull=False,
            finished_at__isnull=False,
        )
    )
    orchestration_durations = _duration_seconds(
        ChangeOrchestration.objects.filter(
            finished_at__gte=window_start,
            started_at__isnull=False,
            finished_at__isnull=False,
        )
    )
    artifact_counts = {
        "true": ArtifactObservation.objects.filter(
            retrieved_at__gte=window_start,
            content_changed=True,
        ).count(),
        "false": ArtifactObservation.objects.filter(
            retrieved_at__gte=window_start,
            content_changed=False,
        ).count(),
    }
    lines = _metric_family(
        "aria_source_runs_window",
        "Source runs created in the bounded rolling window by persisted state.",
        run_counts,
        "status",
    )
    lines.extend(
        (
            (
                "# HELP aria_source_run_success_ratio Successful terminal source runs in "
                "the rolling window."
            ),
            "# TYPE aria_source_run_success_ratio gauge",
            (
                'aria_source_run_success_ratio{window="24h"} '
                f"{_metric_value(success_ratio)}"
            ),
            (
                "# HELP aria_source_run_duration_quantile_seconds Completed source-run "
                "latency in the rolling window."
            ),
            "# TYPE aria_source_run_duration_quantile_seconds gauge",
            (
                'aria_source_run_duration_quantile_seconds{window="24h",quantile="0.50"} '
                f"{_metric_value(_nearest_rank(source_durations, 0.50))}"
            ),
            (
                'aria_source_run_duration_quantile_seconds{window="24h",quantile="0.95"} '
                f"{_metric_value(_nearest_rank(source_durations, 0.95))}"
            ),
            (
                "# HELP aria_orchestration_duration_quantile_seconds Finished "
                "change-workflow latency in the rolling window."
            ),
            "# TYPE aria_orchestration_duration_quantile_seconds gauge",
            (
                'aria_orchestration_duration_quantile_seconds{window="24h",quantile="0.50"} '
                f"{_metric_value(_nearest_rank(orchestration_durations, 0.50))}"
            ),
            (
                'aria_orchestration_duration_quantile_seconds{window="24h",quantile="0.95"} '
                f"{_metric_value(_nearest_rank(orchestration_durations, 0.95))}"
            ),
            (
                "# HELP aria_pipeline_oldest_active_age_seconds Age of the oldest active "
                "source run or change workflow."
            ),
            "# TYPE aria_pipeline_oldest_active_age_seconds gauge",
            f"aria_pipeline_oldest_active_age_seconds {_oldest_active_age_seconds(now):.3f}",
        )
    )
    lines.extend(
        _metric_family(
            "aria_artifact_observations_window",
            "Artifact observations in the rolling window by content-change result.",
            artifact_counts,
            "content_changed",
        )
    )
    lines.extend(
        (
            (
                "# HELP aria_evidence_coverage_ratio Latest aggregate evidence coverage "
                "across enabled sources."
            ),
            "# TYPE aria_evidence_coverage_ratio gauge",
            *(
                f'aria_evidence_coverage_ratio{{stage="{stage}"}} {_metric_value(ratio)}'
                for stage, ratio in _latest_evidence_coverage().items()
            ),
        )
    )
    return [
        line.replace("{status=", f'{{window="{PERFORMANCE_WINDOW_LABEL}",status=').replace(
            "{content_changed=",
            f'{{window="{PERFORMANCE_WINDOW_LABEL}",content_changed=',
        )
        for line in lines
    ]


def render_operational_metrics() -> str:
    now = timezone.now()
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
    lines.extend(_performance_metrics(now))
    heartbeat = pipeline_heartbeat()
    heartbeat_timestamp = heartbeat.timestamp() if heartbeat else 0
    heartbeat_age = (
        max(0.0, (now - heartbeat).total_seconds()) if heartbeat else math.inf
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
