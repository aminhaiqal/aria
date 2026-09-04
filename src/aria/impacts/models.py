from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import RawArtifact
from aria.common.models import AppendOnlyModel
from aria.comparisons.models import ComparisonItem, ComparisonReview, StructuralAnchor
from aria.documents.models import NormalizedSection


class RegulatoryImpact(AppendOnlyModel):
    class ImpactType(models.TextChoices):
        OBLIGATION = "obligation", "Obligation"
        REPORTING = "reporting", "Reporting"
        REGISTRATION = "registration", "Registration"
        DEADLINE = "deadline", "Deadline"
        PROHIBITION = "prohibition", "Prohibition"
        PENALTY = "penalty", "Penalty"
        EXEMPTION = "exemption", "Exemption"
        PERMISSION = "permission", "Permission"
        GOVERNANCE = "governance", "Governance"
        RECORD_KEEPING = "record_keeping", "Record keeping"
        OTHER = "other", "Other"

    class Origin(models.TextChoices):
        DETERMINISTIC = "deterministic", "Deterministic"
        GPT = "gpt", "GPT candidate"
        HUMAN = "human", "Human candidate"

    comparison_item = models.ForeignKey(
        ComparisonItem,
        on_delete=models.PROTECT,
        related_name="regulatory_impacts",
    )
    confirmation_review = models.ForeignKey(
        ComparisonReview,
        on_delete=models.PROTECT,
        related_name="regulatory_impacts",
    )
    impact_type = models.CharField(max_length=32, choices=ImpactType.choices, db_index=True)
    origin = models.CharField(max_length=16, choices=Origin.choices, db_index=True)
    title = models.CharField(max_length=512)
    statement = models.TextField()
    rationale = models.TextField(blank=True)
    effective_date_text = models.CharField(max_length=255, blank=True)
    legal_effect_assessed = models.BooleanField(default=False)
    input_fingerprint = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=("comparison_item", "impact_type", "created_at"),
                name="impact_item_type_created_idx",
            ),
            models.Index(
                fields=("confirmation_review", "created_at"),
                name="impact_review_created_idx",
            ),
        ]

    def clean(self) -> None:
        errors = {}
        if self.confirmation_review_id and self.comparison_item_id:
            if self.confirmation_review.comparison_item_id != self.comparison_item_id:
                errors["confirmation_review"] = (
                    "The confirmation review must belong to the comparison item."
                )
            if self.confirmation_review.decision != ComparisonReview.Decision.CONFIRMED:
                errors["confirmation_review"] = "The referenced review must confirm the change."
        if (
            self.comparison_item_id
            and self.comparison_item.change_type == ComparisonItem.ChangeType.UNCHANGED
        ):
            errors["comparison_item"] = "Unchanged text cannot support an impact candidate."
        if (
            self.comparison_item_id
            and self.comparison_item.comparison.status
            != self.comparison_item.comparison.Status.COMPLETED
        ):
            errors["comparison_item"] = "The source comparison must be complete."
        if not self.title.strip():
            errors["title"] = "An impact title is required."
        if not self.statement.strip():
            errors["statement"] = "An evidence-bounded impact statement is required."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.get_impact_type_display()}: {self.title}"


class ImpactEvidence(AppendOnlyModel):
    class Side(models.TextChoices):
        BEFORE = "before", "Before"
        AFTER = "after", "After"

    impact = models.ForeignKey(
        RegulatoryImpact,
        on_delete=models.PROTECT,
        related_name="evidence_records",
    )
    side = models.CharField(max_length=8, choices=Side.choices)
    structural_anchor = models.ForeignKey(
        StructuralAnchor,
        on_delete=models.PROTECT,
        related_name="impact_evidence_records",
    )
    normalized_section = models.ForeignKey(
        NormalizedSection,
        on_delete=models.PROTECT,
        related_name="impact_evidence_records",
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="impact_evidence_records",
    )
    artifact_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    anchor_text = models.TextField()
    anchor_text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    section_text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    source_locator = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("impact", "side")
        constraints = [
            models.UniqueConstraint(
                fields=("impact", "side"),
                name="unique_evidence_side_per_impact",
            )
        ]
        indexes = [
            models.Index(
                fields=("structural_anchor", "side"),
                name="impact_anchor_side_idx",
            ),
            models.Index(
                fields=("source_artifact", "created_at"),
                name="impact_artifact_created_idx",
            ),
        ]

    def clean(self) -> None:
        expected_anchor_id = None
        if self.impact_id:
            item = self.impact.comparison_item
            expected_anchor_id = (
                item.before_anchor_id if self.side == self.Side.BEFORE else item.after_anchor_id
            )
        errors = {}
        if not expected_anchor_id or self.structural_anchor_id != expected_anchor_id:
            errors["structural_anchor"] = (
                "Impact evidence must use the comparison item's exact anchor for this side."
            )
        if self.structural_anchor_id:
            anchor = self.structural_anchor
            if self.normalized_section_id != anchor.normalized_section_id:
                errors["normalized_section"] = "The section must own the cited anchor."
            if self.source_artifact_id != anchor.source_artifact_id:
                errors["source_artifact"] = "The artifact must own the cited anchor."
            if self.artifact_sha256 != anchor.source_artifact.sha256:
                errors["artifact_sha256"] = "The artifact hash snapshot does not match."
            if self.anchor_text != anchor.text:
                errors["anchor_text"] = "The anchor text snapshot does not match."
            if self.anchor_text_sha256 != anchor.text_sha256:
                errors["anchor_text_sha256"] = "The anchor text hash snapshot does not match."
            if self.section_text_sha256 != anchor.normalized_section.text_sha256:
                errors["section_text_sha256"] = "The section text hash snapshot does not match."
            if self.source_locator != anchor.source_locator:
                errors["source_locator"] = "The source locator snapshot does not match."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.impact_id}:{self.side}"
