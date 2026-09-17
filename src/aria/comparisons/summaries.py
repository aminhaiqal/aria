import hashlib
import json
from typing import Literal

from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from aria.comparisons.contracts import SUMMARY_PROMPT_VERSION
from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    ComparisonSummary,
    DocumentComparison,
)
from aria.events.services import record_audit_event
from aria.openrouter import OpenRouterClient, OpenRouterError, RetryableOpenRouterError

SUMMARY_SYSTEM_PROMPT = """You summarize deterministic textual comparisons of official
regulatory publications. Use only the supplied before/after text. Do not infer legal effect,
amendment status, effective dates, or obligations that are not explicitly stated. Preserve each
supplied comparison_item_id and change_type exactly. Include every supplied item once. Set
legal_effect_not_assessed to true. Write concise, neutral language suitable for a human reviewer."""

SummaryChangeType = Literal[
    "added",
    "removed",
    "modified",
    "moved",
    "format_only",
    "ambiguous",
]


class SummaryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_item_id: str
    change_type: SummaryChangeType
    explanation: str = Field(min_length=1, max_length=1200)


class StructuredComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    overview: str = Field(min_length=1, max_length=2400)
    changes: list[SummaryChange]
    caveats: list[str]
    legal_effect_not_assessed: bool


class SummaryError(RuntimeError):
    pass


class RetryableSummaryError(SummaryError):
    pass


class SummaryEligibilityError(SummaryError):
    pass


def _latest_confirmed_items(
    comparison: DocumentComparison,
) -> list[tuple[ComparisonItem, ComparisonReview]]:
    confirmed = []
    items = comparison.items.exclude(
        change_type=ComparisonItem.ChangeType.UNCHANGED
    ).prefetch_related("reviews")
    for item in items:
        latest = item.reviews.order_by("-created_at", "-id").first()
        if latest and latest.decision == ComparisonReview.Decision.CONFIRMED:
            confirmed.append((item, latest))
    return sorted(confirmed, key=lambda pair: (pair[0].created_at, str(pair[0].id)))


def _bounded_anchor_text(item: ComparisonItem, side: str) -> dict | None:
    anchor = item.before_anchor if side == "before" else item.after_anchor
    if anchor is None:
        return None
    maximum = settings.OPENROUTER_SUMMARY_MAX_CHARS_PER_ANCHOR
    text = anchor.text
    return {
        "canonical_key": anchor.canonical_key,
        "text": text[:maximum],
        "text_truncated": len(text) > maximum,
    }


def build_summary_input(comparison: DocumentComparison) -> dict:
    if comparison.status != DocumentComparison.Status.COMPLETED:
        raise SummaryEligibilityError("Only completed comparisons can be summarized.")
    items = _latest_confirmed_items(comparison)
    if not items:
        raise SummaryEligibilityError("At least one currently confirmed change is required.")
    if len(items) > settings.OPENROUTER_SUMMARY_MAX_ITEMS:
        raise SummaryEligibilityError(
            f"Comparison has {len(items)} confirmed items; the configured maximum is "
            f"{settings.OPENROUTER_SUMMARY_MAX_ITEMS}."
        )
    return {
        "document_title": comparison.identity.canonical_title,
        "comparison_id": str(comparison.id),
        "items": [
            {
                "comparison_item_id": str(item.id),
                "confirmation_review_id": str(review.id),
                "change_type": item.change_type,
                "before": _bounded_anchor_text(item, "before"),
                "after": _bounded_anchor_text(item, "after"),
            }
            for item, review in items
        ],
    }


def _summary_input_hash(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _validate_summary_output(output: StructuredComparisonSummary, payload: dict) -> None:
    if not output.legal_effect_not_assessed:
        raise SummaryError("Structured summary did not preserve the legal-effect boundary.")
    expected = {item["comparison_item_id"]: item["change_type"] for item in payload["items"]}
    actual_ids = [change.comparison_item_id for change in output.changes]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
        raise SummaryError("Structured summary did not cite every confirmed comparison item once.")
    if any(change.change_type != expected[change.comparison_item_id] for change in output.changes):
        raise SummaryError("Structured summary changed a deterministic change classification.")


def _request_structured_summary(payload: dict, *, client=None):
    try:
        selected_client = client or OpenRouterClient()
    except OpenRouterError as error:
        raise SummaryError(str(error)) from error
    try:
        result = selected_client.generate_structured(
            model=settings.OPENROUTER_SUMMARY_MODEL,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            input_payload=payload,
            output_model=StructuredComparisonSummary,
            schema_name="aria_comparison_summary",
            reasoning_effort=settings.OPENROUTER_SUMMARY_REASONING_EFFORT,
            max_output_tokens=settings.OPENROUTER_SUMMARY_MAX_OUTPUT_TOKENS,
        )
    except RetryableOpenRouterError as error:
        raise RetryableSummaryError(str(error)) from error
    except OpenRouterError as error:
        raise SummaryError(str(error)) from error
    output = result.output
    if not isinstance(output, StructuredComparisonSummary):
        raise SummaryError("OpenRouter returned an invalid structured summary.")
    _validate_summary_output(output, payload)
    return result, output


def generate_comparison_summary(
    comparison: DocumentComparison,
    *,
    client=None,
) -> tuple[ComparisonSummary, bool]:
    payload = build_summary_input(comparison)
    input_hash = _summary_input_hash(payload)
    summary, created = ComparisonSummary.objects.get_or_create(
        comparison=comparison,
        provider="openrouter",
        model=settings.OPENROUTER_SUMMARY_MODEL,
        prompt_version=SUMMARY_PROMPT_VERSION,
        input_hash=input_hash,
        defaults={"input_snapshot": payload},
    )
    if summary.status == ComparisonSummary.Status.COMPLETED:
        return summary, False
    summary.status = ComparisonSummary.Status.RUNNING
    summary.started_at = timezone.now()
    summary.error_code = ""
    summary.error_message = ""
    summary.save(
        update_fields=("status", "started_at", "error_code", "error_message", "updated_at")
    )
    try:
        response, output = _request_structured_summary(payload, client=client)
    except Exception as error:
        summary.status = ComparisonSummary.Status.FAILED
        summary.finished_at = timezone.now()
        summary.error_code = type(error).__name__
        summary.error_message = str(error)
        summary.save(
            update_fields=(
                "status",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
        raise

    summary.status = ComparisonSummary.Status.COMPLETED
    summary.output = output.model_dump(mode="json")
    summary.response_id = response.response_id
    summary.input_tokens = response.input_tokens
    summary.output_tokens = response.output_tokens
    summary.finished_at = timezone.now()
    summary.save(
        update_fields=(
            "status",
            "output",
            "response_id",
            "input_tokens",
            "output_tokens",
            "finished_at",
            "updated_at",
        )
    )
    record_audit_event(
        action="comparison.summary_generated",
        target_type="document_comparison",
        target_id=comparison.id,
        details={
            "summary_id": str(summary.id),
            "provider": summary.provider,
            "model": summary.model,
            "prompt_version": summary.prompt_version,
            "input_hash": summary.input_hash,
        },
    )
    return summary, created
