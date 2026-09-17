import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from aria.comparisons.models import ComparisonItem, ComparisonReview, DocumentComparison
from aria.events.services import record_audit_event
from aria.impacts.models import (
    ApplicabilityTaxonomy,
    ImpactGeneration,
    ImpactTarget,
    RegulatoryImpact,
)
from aria.impacts.services import add_impact_target, create_regulatory_impact
from aria.openrouter import OpenRouterClient, OpenRouterError, RetryableOpenRouterError

IMPACT_PROMPT_VERSION = "aria-impact-candidate-v1"
DETERMINISTIC_MODEL = "aria-impact-rules-v1"

IMPACT_SYSTEM_PROMPT = """You identify candidate business impacts from one human-confirmed
textual change in an official regulatory publication. Use only the supplied before/after evidence.
Do not state that a candidate is law, legal advice, in force, or applicable. Do not infer an
effective date. Return no candidate when the text is insufficient. Cite every supplied anchor ID
for each candidate and copy IDs exactly. Use only supplied taxonomy dimension/code pairs. Every
candidate remains review-required. Set legal_effect_not_assessed to true and preserve the supplied
comparison_item_id and confirmation_review_id exactly."""

ImpactTypeValue = Literal[
    "obligation",
    "reporting",
    "registration",
    "deadline",
    "prohibition",
    "penalty",
    "exemption",
    "permission",
    "governance",
    "record_keeping",
    "other",
]
DimensionValue = Literal[
    "sector",
    "organization_type",
    "regulated_role",
    "activity",
    "jurisdiction",
    "size",
]
DispositionValue = Literal["included", "excluded"]


class StructuredImpactTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: DimensionValue
    code: str = Field(min_length=1, max_length=128)
    disposition: DispositionValue
    rationale: str = Field(min_length=1, max_length=1000)


class StructuredImpactCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impact_type: ImpactTypeValue
    title: str = Field(min_length=1, max_length=300)
    statement: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    effective_date_text: str = Field(max_length=255)
    citation_anchor_ids: list[str] = Field(min_length=1, max_length=2)
    targets: list[StructuredImpactTarget] = Field(min_length=1, max_length=20)


class StructuredImpactCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_item_id: str
    confirmation_review_id: str
    legal_effect_not_assessed: bool
    candidates: list[StructuredImpactCandidate] = Field(max_length=10)


class ImpactGenerationError(RuntimeError):
    pass


class RetryableImpactGenerationError(ImpactGenerationError):
    pass


class ImpactGenerationEligibilityError(ImpactGenerationError):
    pass


@dataclass(frozen=True)
class ImpactGenerationResult:
    generation: ImpactGeneration
    created: bool
    impact_count: int


def _bounded_anchor(item: ComparisonItem, side: str) -> dict | None:
    anchor = item.before_anchor if side == "before" else item.after_anchor
    if anchor is None:
        return None
    maximum = settings.OPENROUTER_IMPACT_MAX_CHARS_PER_ANCHOR
    return {
        "anchor_id": str(anchor.id),
        "canonical_key": anchor.canonical_key,
        "text": anchor.text[:maximum],
        "text_truncated": len(anchor.text) > maximum,
        "text_sha256": anchor.text_sha256,
        "artifact_sha256": anchor.source_artifact.sha256,
        "source_locator": anchor.source_locator,
    }


def build_impact_input(
    item: ComparisonItem,
    taxonomy: ApplicabilityTaxonomy,
) -> tuple[dict, ComparisonReview]:
    if item.comparison.status != DocumentComparison.Status.COMPLETED:
        raise ImpactGenerationEligibilityError("Only completed comparisons are eligible.")
    if item.change_type == ComparisonItem.ChangeType.UNCHANGED:
        raise ImpactGenerationEligibilityError(
            "Unchanged text is not eligible for impact analysis."
        )
    confirmation = item.reviews.order_by("-created_at", "-id").first()
    if confirmation is None or confirmation.decision != ComparisonReview.Decision.CONFIRMED:
        raise ImpactGenerationEligibilityError(
            "Impact generation requires a current confirmed change review."
        )
    terms = list(taxonomy.terms.order_by("dimension", "code"))
    if not terms:
        raise ImpactGenerationEligibilityError("The selected taxonomy has no controlled terms.")
    return (
        {
            "document_title": item.comparison.identity.canonical_title,
            "comparison_item_id": str(item.id),
            "confirmation_review_id": str(confirmation.id),
            "change_type": item.change_type,
            "before": _bounded_anchor(item, "before"),
            "after": _bounded_anchor(item, "after"),
            "taxonomy": {
                "id": str(taxonomy.id),
                "slug": taxonomy.slug,
                "version": taxonomy.version,
                "checksum": taxonomy.checksum,
                "disclaimer": taxonomy.disclaimer,
                "terms": [
                    {
                        "dimension": term.dimension,
                        "code": term.code,
                        "label": term.label,
                        "description": term.description,
                    }
                    for term in terms
                ],
            },
        },
        confirmation,
    )


def _input_hash(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _expected_anchor_ids(payload: dict) -> set[str]:
    return {
        side["anchor_id"]
        for side in (payload["before"], payload["after"])
        if side is not None
    }


def _validate_output(
    output: StructuredImpactCandidates,
    payload: dict,
    taxonomy: ApplicabilityTaxonomy,
) -> None:
    if output.comparison_item_id != payload["comparison_item_id"]:
        raise ImpactGenerationError("Structured output changed the comparison item ID.")
    if output.confirmation_review_id != payload["confirmation_review_id"]:
        raise ImpactGenerationError("Structured output changed the confirmation review ID.")
    if not output.legal_effect_not_assessed:
        raise ImpactGenerationError("Structured output crossed the legal-effect boundary.")
    expected_anchors = _expected_anchor_ids(payload)
    allowed_terms = set(taxonomy.terms.values_list("dimension", "code"))
    for candidate in output.candidates:
        citation_ids = candidate.citation_anchor_ids
        if len(citation_ids) != len(set(citation_ids)) or set(citation_ids) != expected_anchors:
            raise ImpactGenerationError(
                "Every candidate must cite each supplied exact anchor once and no other anchor."
            )
        target_keys = [(target.dimension, target.code) for target in candidate.targets]
        if len(target_keys) != len(set(target_keys)):
            raise ImpactGenerationError("A candidate contains duplicate applicability targets.")
        if any(key not in allowed_terms for key in target_keys):
            raise ImpactGenerationError("A candidate cited an unknown applicability taxonomy term.")


def _term_target(
    dimension: DimensionValue,
    code: str,
    rationale: str,
) -> StructuredImpactTarget:
    return StructuredImpactTarget(
        dimension=dimension,
        code=code,
        disposition=ImpactTarget.Disposition.INCLUDED,
        rationale=rationale,
    )


def _deterministic_targets(
    text: str, taxonomy: ApplicabilityTaxonomy
) -> list[StructuredImpactTarget]:
    available = set(taxonomy.terms.values_list("dimension", "code"))
    specs: list[tuple[DimensionValue, str, str]] = [
        ("jurisdiction", "malaysia", "The source collection is scoped to Malaysia."),
        ("size", "any-size", "The cited wording states no organization-size threshold."),
    ]
    keyword_specs: list[tuple[str, DimensionValue, str, str]] = [
        (
            r"\bpersonal data\b",
            "activity",
            "process-personal-data",
            "The cited text expressly mentions personal data.",
        ),
        (
            r"\b(data user|data controller|controller)\b",
            "regulated_role",
            "data-user",
            "The cited text names a data-user or controller role.",
        ),
        (
            r"\b(data processor|processor)\b",
            "regulated_role",
            "data-processor",
            "The cited text names a processor role.",
        ),
        (r"\bemployer\b", "regulated_role", "employer", "The cited text names employers."),
        (
            r"\b(direct marketing|marketing)\b",
            "activity",
            "direct-marketing",
            "The cited text names direct-marketing activity.",
        ),
        (
            r"\b(cross-border|outside malaysia|overseas transfer)\b",
            "activity",
            "cross-border-transfer",
            "The cited text refers to an international or cross-border transfer.",
        ),
        (
            r"\b(digital service|online platform|software)\b",
            "sector",
            "digital-services",
            "The cited text names a digital service or platform.",
        ),
        (
            r"\b(bank|banking|insurance|financial)\b",
            "sector",
            "financial-services",
            "The cited text names financial services.",
        ),
    ]
    sector_added = False
    for pattern, dimension, code, rationale in keyword_specs:
        if re.search(pattern, text, flags=re.IGNORECASE):
            specs.append((dimension, code, rationale))
            sector_added = sector_added or dimension == "sector"
    if not sector_added:
        specs.append(
            ("sector", "cross-sector", "The cited wording does not name a specific sector.")
        )
    seen = set()
    targets = []
    for dimension, code, rationale in specs:
        key = (dimension, code)
        if key not in available or key in seen:
            continue
        seen.add(key)
        targets.append(_term_target(dimension, code, rationale))
    return targets


def _deterministic_output(
    payload: dict,
    taxonomy: ApplicabilityTaxonomy,
) -> StructuredImpactCandidates:
    source = payload["after"] or payload["before"]
    text = source["text"] if source else ""
    rules: list[tuple[str, ImpactTypeValue, str]] = [
        (r"\b(shall|must|required to|is required)\b", "obligation", "obligation wording"),
        (r"\b(report|notify|notification)\b", "reporting", "reporting wording"),
        (r"\b(register|registration|licen[cs]e)\b", "registration", "registration wording"),
        (
            r"\b(within \d+|no later than|by \d{1,2} [A-Za-z]+)\b",
            "deadline",
            "deadline wording",
        ),
        (r"\b(must not|shall not|prohibited|forbidden)\b", "prohibition", "prohibitory wording"),
        (r"\b(penalty|fine|offence|liable)\b", "penalty", "penalty wording"),
        (r"\b(exempt|exemption)\b", "exemption", "exemption wording"),
        (r"\bmay\b", "permission", "permission wording"),
        (r"\b(record|retain|retention)\b", "record_keeping", "record-keeping wording"),
        (r"\b(board|officer|policy|governance)\b", "governance", "governance wording"),
    ]
    candidates = []
    citations = sorted(_expected_anchor_ids(payload))
    targets = _deterministic_targets(text, taxonomy)
    for pattern, impact_type, basis in rules:
        if not re.search(pattern, text, flags=re.IGNORECASE):
            continue
        candidates.append(
            StructuredImpactCandidate(
                impact_type=impact_type,
                title=f"Potential {impact_type.replace('_', ' ')} impact",
                statement=(
                    f"The confirmed {payload['change_type']} text may change {basis} for "
                    "organizations matching the cited applicability terms."
                ),
                rationale=f"Deterministic rules detected {basis} in the confirmed text.",
                effective_date_text="",
                citation_anchor_ids=citations,
                targets=targets,
            )
        )
        if len(candidates) >= 5:
            break
    return StructuredImpactCandidates(
        comparison_item_id=payload["comparison_item_id"],
        confirmation_review_id=payload["confirmation_review_id"],
        legal_effect_not_assessed=True,
        candidates=candidates,
    )


def _request_openrouter(payload: dict, *, client=None):
    try:
        selected_client = client or OpenRouterClient()
    except OpenRouterError as error:
        raise ImpactGenerationError(str(error)) from error
    try:
        result = selected_client.generate_structured(
            model=settings.OPENROUTER_IMPACT_MODEL,
            system_prompt=IMPACT_SYSTEM_PROMPT,
            input_payload=payload,
            output_model=StructuredImpactCandidates,
            schema_name="aria_impact_candidates",
            reasoning_effort=settings.OPENROUTER_IMPACT_REASONING_EFFORT,
            max_output_tokens=settings.OPENROUTER_IMPACT_MAX_OUTPUT_TOKENS,
        )
    except RetryableOpenRouterError as error:
        raise RetryableImpactGenerationError(str(error)) from error
    except OpenRouterError as error:
        raise ImpactGenerationError(str(error)) from error
    if not isinstance(result.output, StructuredImpactCandidates):
        raise ImpactGenerationError("OpenRouter returned invalid structured impact candidates.")
    return result, result.output


def _materialize_candidates(
    generation: ImpactGeneration,
    output: StructuredImpactCandidates,
) -> list[RegulatoryImpact]:
    impacts = []
    with transaction.atomic():
        for candidate in output.candidates:
            impact, _ = create_regulatory_impact(
                generation.comparison_item,
                impact_type=candidate.impact_type,
                origin=(
                    RegulatoryImpact.Origin.GPT
                    if generation.provider == "openrouter"
                    else RegulatoryImpact.Origin.DETERMINISTIC
                ),
                title=candidate.title,
                statement=candidate.statement,
                rationale=candidate.rationale,
                effective_date_text=candidate.effective_date_text,
                actor_type="impact_generation",
                actor_identifier=str(generation.id),
                generation=generation,
            )
            for target in candidate.targets:
                add_impact_target(
                    impact,
                    generation.taxonomy,
                    dimension=target.dimension,
                    code=target.code,
                    disposition=target.disposition,
                    origin=(
                        ImpactTarget.Origin.GPT
                        if generation.provider == "openrouter"
                        else ImpactTarget.Origin.DETERMINISTIC
                    ),
                    rationale=target.rationale,
                    actor_type="impact_generation",
                    actor_identifier=str(generation.id),
                )
            impacts.append(impact)
    return impacts


def generate_impact_candidates(
    item: ComparisonItem,
    taxonomy: ApplicabilityTaxonomy,
    *,
    provider: str = "deterministic",
    client=None,
) -> ImpactGenerationResult:
    if provider not in {"deterministic", "openrouter"}:
        raise ImpactGenerationError("Provider must be deterministic or openrouter.")
    payload, confirmation = build_impact_input(item, taxonomy)
    input_hash = _input_hash(payload)
    model = (
        DETERMINISTIC_MODEL
        if provider == "deterministic"
        else settings.OPENROUTER_IMPACT_MODEL
    )
    generation, created = ImpactGeneration.objects.get_or_create(
        provider=provider,
        model=model,
        prompt_version=IMPACT_PROMPT_VERSION,
        input_hash=input_hash,
        defaults={
            "comparison_item": item,
            "confirmation_review": confirmation,
            "taxonomy": taxonomy,
            "input_snapshot": payload,
        },
    )
    if created:
        generation.full_clean()
    if generation.status == ImpactGeneration.Status.COMPLETED:
        return ImpactGenerationResult(
            generation=generation,
            created=False,
            impact_count=generation.generated_impacts.count(),
        )
    generation.status = ImpactGeneration.Status.RUNNING
    generation.started_at = timezone.now()
    generation.finished_at = None
    generation.error_code = ""
    generation.error_message = ""
    generation.save(
        update_fields=(
            "status",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "updated_at",
        )
    )
    try:
        if provider == "deterministic":
            response = None
            output = _deterministic_output(payload, taxonomy)
        else:
            response, output = _request_openrouter(payload, client=client)
        _validate_output(output, payload, taxonomy)
        impacts = _materialize_candidates(generation, output)
    except Exception as error:
        generation.status = ImpactGeneration.Status.FAILED
        generation.finished_at = timezone.now()
        generation.error_code = type(error).__name__
        generation.error_message = str(error)
        generation.save(
            update_fields=(
                "status",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
        raise

    generation.status = ImpactGeneration.Status.COMPLETED
    generation.output = output.model_dump(mode="json")
    generation.response_id = response.response_id if response is not None else ""
    generation.input_tokens = response.input_tokens if response is not None else 0
    generation.output_tokens = response.output_tokens if response is not None else 0
    generation.finished_at = timezone.now()
    generation.save(
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
        action="impact.generation_completed",
        target_type="impact_generation",
        target_id=generation.id,
        details={
            "comparison_item_id": str(item.id),
            "confirmation_review_id": str(confirmation.id),
            "taxonomy_checksum": taxonomy.checksum,
            "provider": provider,
            "model": model,
            "prompt_version": IMPACT_PROMPT_VERSION,
            "input_hash": input_hash,
            "impact_ids": [str(impact.id) for impact in impacts],
            "legal_effect_assessed": False,
        },
    )
    return ImpactGenerationResult(
        generation=generation,
        created=created,
        impact_count=len(impacts),
    )
