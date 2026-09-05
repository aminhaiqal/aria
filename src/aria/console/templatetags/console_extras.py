import json

from django import template

register = template.Library()


@register.filter
def status_tone(value):
    if value in {
        "healthy",
        "completed",
        "succeeded",
        "confirmed",
        "passed",
        "published",
        "accepted",
        "approved",
        "amended",
        "unchanged",
        "not_modified",
    }:
        return "positive"
    if value in {
        "failed",
        "critical",
        "unhealthy",
        "permanent_failure",
        "lineage_rejected",
        "summary_failed",
        "rejected",
    }:
        return "danger"
    if value in {
        "degraded",
        "warning",
        "review_required",
        "quality_review_required",
        "needs_context",
        "waiting_ocr",
        "ambiguous",
        "quarantined",
        "changed",
    }:
        return "warning"
    if value in {
        "running",
        "queued",
        "pending",
        "processing",
        "summary_pending",
        "retryable_failure",
    }:
        return "info"
    return "neutral"


@register.filter
def short_id(value, length=8):
    return str(value)[: int(length)] if value else "—"


@register.filter
def short_hash(value, length=12):
    return f"{str(value)[: int(length)]}…" if value else "—"


@register.filter
def pretty_json(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
