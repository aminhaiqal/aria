from celery import shared_task

from aria.reliability.services import assess_source_reliability
from aria.sources.models import SourceEndpoint


@shared_task(name="aria.reliability.tasks.assess_enabled_sources")
def assess_enabled_sources() -> dict:
    assessed = 0
    created = 0
    status_counts: dict[str, int] = {}
    endpoints = SourceEndpoint.objects.filter(
        is_enabled=True,
        collection__is_enabled=True,
        collection__authority__is_enabled=True,
    ).select_related("collection", "collection__authority")
    for endpoint in endpoints.iterator():
        assessment, was_created = assess_source_reliability(endpoint)
        assessed += 1
        created += int(was_created)
        status_counts[assessment.status] = status_counts.get(assessment.status, 0) + 1
    return {"assessed": assessed, "created": created, "statuses": status_counts}
