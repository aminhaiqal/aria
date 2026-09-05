from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import RawArtifact
from aria.common.models import AppendOnlyModel, TimeStampedModel
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
    generation = models.ForeignKey(
        "ImpactGeneration",
        on_delete=models.PROTECT,
        related_name="generated_impacts",
        null=True,
        blank=True,
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
        if self.generation_id:
            if self.generation.comparison_item_id != self.comparison_item_id:
                errors["generation"] = "The generation must belong to the comparison item."
            elif self.generation.confirmation_review_id != self.confirmation_review_id:
                errors["generation"] = "The generation must use the same confirmation review."
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


class ApplicabilityTaxonomy(AppendOnlyModel):
    slug = models.SlugField(max_length=128)
    schema_version = models.PositiveIntegerField()
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField()
    jurisdiction = models.CharField(max_length=128)
    disclaimer = models.TextField()
    checksum = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    definition = models.JSONField()
    applied_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("slug", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("slug", "version"),
                name="unique_applicability_taxonomy_version",
            )
        ]

    def clean(self) -> None:
        if not isinstance(self.definition, dict):
            raise ValidationError("Taxonomy snapshots require an object definition.")
        expected = {
            "slug": self.slug,
            "schema_version": self.schema_version,
            "version": self.version,
        }
        if any(self.definition.get(key) != value for key, value in expected.items()):
            raise ValidationError("Taxonomy identity must match its definition snapshot.")

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class ApplicabilityTerm(AppendOnlyModel):
    class Dimension(models.TextChoices):
        SECTOR = "sector", "Sector"
        ORGANIZATION_TYPE = "organization_type", "Organization type"
        REGULATED_ROLE = "regulated_role", "Regulated role"
        ACTIVITY = "activity", "Activity"
        JURISDICTION = "jurisdiction", "Jurisdiction"
        SIZE = "size", "Size"

    taxonomy = models.ForeignKey(
        ApplicabilityTaxonomy,
        on_delete=models.PROTECT,
        related_name="terms",
    )
    dimension = models.CharField(max_length=32, choices=Dimension.choices, db_index=True)
    code = models.SlugField(max_length=128)
    label = models.CharField(max_length=255)
    description = models.TextField()
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    aliases = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("taxonomy", "dimension", "code")
        constraints = [
            models.UniqueConstraint(
                fields=("taxonomy", "dimension", "code"),
                name="unique_term_per_taxonomy_dimension",
            )
        ]
        indexes = [
            models.Index(fields=("dimension", "code"), name="impact_term_dimension_code_idx")
        ]

    def clean(self) -> None:
        errors = {}
        if not isinstance(self.aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in self.aliases
        ):
            errors["aliases"] = "Aliases must be a list of non-empty strings."
        if self.parent_id:
            if self.parent.taxonomy_id != self.taxonomy_id:
                errors["parent"] = "A parent term must use the same taxonomy version."
            elif self.parent.dimension != self.dimension:
                errors["parent"] = "A parent term must use the same applicability dimension."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.get_dimension_display()}: {self.label}"


class ImpactTarget(AppendOnlyModel):
    class Disposition(models.TextChoices):
        INCLUDED = "included", "Included"
        EXCLUDED = "excluded", "Excluded"

    class Origin(models.TextChoices):
        DETERMINISTIC = "deterministic", "Deterministic"
        GPT = "gpt", "GPT candidate"
        HUMAN = "human", "Human"

    impact = models.ForeignKey(
        RegulatoryImpact,
        on_delete=models.PROTECT,
        related_name="targets",
    )
    term = models.ForeignKey(
        ApplicabilityTerm,
        on_delete=models.PROTECT,
        related_name="impact_targets",
    )
    disposition = models.CharField(max_length=16, choices=Disposition.choices)
    origin = models.CharField(max_length=16, choices=Origin.choices)
    rationale = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("impact", "term__dimension", "term__code")
        constraints = [
            models.UniqueConstraint(
                fields=("impact", "term"),
                name="unique_applicability_term_per_impact",
            )
        ]
        indexes = [
            models.Index(
                fields=("term", "disposition", "created_at"),
                name="impact_target_term_state_idx",
            )
        ]

    def clean(self) -> None:
        errors = {}
        if not self.rationale.strip():
            errors["rationale"] = "A target rationale is required."
        if (
            self.impact_id
            and self.term_id
            and self.impact.targets.exclude(term__taxonomy_id=self.term.taxonomy_id).exists()
        ):
            errors["term"] = "One impact candidate cannot mix taxonomy versions."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.impact_id}:{self.term_id} [{self.disposition}]"


class ImpactGeneration(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    comparison_item = models.ForeignKey(
        ComparisonItem,
        on_delete=models.PROTECT,
        related_name="impact_generations",
    )
    confirmation_review = models.ForeignKey(
        ComparisonReview,
        on_delete=models.PROTECT,
        related_name="impact_generations",
    )
    taxonomy = models.ForeignKey(
        ApplicabilityTaxonomy,
        on_delete=models.PROTECT,
        related_name="impact_generations",
    )
    provider = models.CharField(max_length=32)
    model = models.CharField(max_length=128)
    prompt_version = models.CharField(max_length=128)
    input_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    input_snapshot = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    output = models.JSONField(default=dict, blank=True)
    response_id = models.CharField(max_length=255, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)
    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "model", "prompt_version", "input_hash"),
                name="unique_impact_generation_input",
            )
        ]
        indexes = [
            models.Index(
                fields=("comparison_item", "status", "created_at"),
                name="impact_generation_item_idx",
            )
        ]

    def clean(self) -> None:
        errors = {}
        if self.confirmation_review_id and self.comparison_item_id:
            if self.confirmation_review.comparison_item_id != self.comparison_item_id:
                errors["confirmation_review"] = "The review must belong to the comparison item."
            elif self.confirmation_review.decision != ComparisonReview.Decision.CONFIRMED:
                errors["confirmation_review"] = "Impact generation requires a confirmed review."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.comparison_item_id} {self.provider}/{self.model} [{self.status}]"


class ImpactReview(AppendOnlyModel):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        AMENDED = "amended", "Approved with amendments"
        REJECTED = "rejected", "Rejected"
        NEEDS_CONTEXT = "needs_context", "Needs context"

    impact = models.ForeignKey(
        RegulatoryImpact,
        on_delete=models.PROTECT,
        related_name="reviews",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices, db_index=True)
    reviewed_title = models.CharField(max_length=512)
    reviewed_statement = models.TextField()
    reviewed_effective_date_text = models.CharField(max_length=255, blank=True)
    rationale = models.TextField(blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="impact_reviews",
    )
    previous_review = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="superseding_reviews",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("impact", "created_at"), name="impact_review_history_idx"),
            models.Index(fields=("decision", "created_at"), name="impact_review_decision_idx"),
        ]

    def clean(self) -> None:
        errors = {}
        if self.previous_review_id and self.previous_review.impact_id != self.impact_id:
            errors["previous_review"] = "The previous review must belong to the same impact."
        if not self.reviewed_title.strip():
            errors["reviewed_title"] = "The reviewed title is required."
        if not self.reviewed_statement.strip():
            errors["reviewed_statement"] = "The reviewed statement is required."
        if (
            self.decision
            in (self.Decision.AMENDED, self.Decision.REJECTED, self.Decision.NEEDS_CONTEXT)
            and len(self.rationale.strip()) < 10
        ):
            errors["rationale"] = "Provide at least 10 characters of review rationale."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.impact_id} [{self.decision}]"


class ImpactReviewTarget(AppendOnlyModel):
    impact_review = models.ForeignKey(
        ImpactReview,
        on_delete=models.PROTECT,
        related_name="reviewed_targets",
    )
    term = models.ForeignKey(
        ApplicabilityTerm,
        on_delete=models.PROTECT,
        related_name="impact_review_targets",
    )
    disposition = models.CharField(max_length=16, choices=ImpactTarget.Disposition.choices)
    rationale = models.TextField()
    source_target = models.ForeignKey(
        ImpactTarget,
        on_delete=models.PROTECT,
        related_name="review_snapshots",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("impact_review", "term__dimension", "term__code")
        constraints = [
            models.UniqueConstraint(
                fields=("impact_review", "term"),
                name="unique_term_per_impact_review",
            )
        ]

    def clean(self) -> None:
        errors = {}
        if not self.rationale.strip():
            errors["rationale"] = "A reviewed-target rationale is required."
        if self.source_target_id:
            if self.source_target.impact_id != self.impact_review.impact_id:
                errors["source_target"] = "The source target must belong to the reviewed impact."
            elif self.source_target.term_id != self.term_id:
                errors["source_target"] = "The source target must use the reviewed term."
            elif self.source_target.disposition != self.disposition:
                errors["source_target"] = "The source target disposition must match."
        generation = self.impact_review.impact.generation
        if generation and self.term.taxonomy_id != generation.taxonomy_id:
            errors["term"] = "Reviewed targets must use the generation taxonomy version."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.impact_review_id}:{self.term_id} [{self.disposition}]"


class BusinessProfile(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_profiles",
    )
    taxonomy = models.ForeignKey(
        ApplicabilityTaxonomy,
        on_delete=models.PROTECT,
        related_name="business_profiles",
    )
    name = models.CharField(max_length=255)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    terms = models.ManyToManyField(
        ApplicabilityTerm,
        through="BusinessProfileTerm",
        related_name="business_profiles",
    )

    class Meta:
        ordering = ("owner", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_business_profile_name_per_owner",
            )
        ]

    def clean(self) -> None:
        if not self.name.strip():
            raise ValidationError({"name": "A business profile name is required."})

    def __str__(self) -> str:
        return self.name


class BusinessProfileTerm(TimeStampedModel):
    profile = models.ForeignKey(
        BusinessProfile,
        on_delete=models.CASCADE,
        related_name="profile_terms",
    )
    term = models.ForeignKey(
        ApplicabilityTerm,
        on_delete=models.PROTECT,
        related_name="profile_assignments",
    )

    class Meta:
        ordering = ("profile", "term__dimension", "term__code")
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "term"),
                name="unique_term_per_business_profile",
            )
        ]

    def clean(self) -> None:
        if (
            self.profile_id
            and self.term_id
            and self.profile.taxonomy_id != self.term.taxonomy_id
        ):
            raise ValidationError(
                {"term": "Business-profile terms must use the profile taxonomy version."}
            )

    def __str__(self) -> str:
        return f"{self.profile_id}:{self.term_id}"


class ProfileImpactMatch(AppendOnlyModel):
    class Outcome(models.TextChoices):
        MATCHED = "matched", "Matched"
        NOT_MATCHED = "not_matched", "Not matched"
        INSUFFICIENT_CONTEXT = "insufficient_context", "Insufficient profile context"

    profile = models.ForeignKey(
        BusinessProfile,
        on_delete=models.PROTECT,
        related_name="impact_matches",
    )
    impact_review = models.ForeignKey(
        ImpactReview,
        on_delete=models.PROTECT,
        related_name="profile_matches",
    )
    outcome = models.CharField(max_length=24, choices=Outcome.choices, db_index=True)
    ruleset = models.CharField(max_length=128)
    input_fingerprint = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    profile_snapshot = models.JSONField()
    impact_review_snapshot = models.JSONField()
    matched_terms = models.JSONField(default=list, blank=True)
    excluded_terms = models.JSONField(default=list, blank=True)
    unmet_dimensions = models.JSONField(default=list, blank=True)
    unresolved_dimensions = models.JSONField(default=list, blank=True)
    explanation = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=("profile", "outcome", "created_at"),
                name="profile_match_outcome_idx",
            ),
            models.Index(
                fields=("impact_review", "outcome"),
                name="review_match_outcome_idx",
            ),
        ]

    def clean(self) -> None:
        errors = {}
        if self.profile_id and self.impact_review_id:
            target = self.impact_review.reviewed_targets.select_related("term").first()
            if target and target.term.taxonomy_id != self.profile.taxonomy_id:
                errors["profile"] = "The profile and reviewed impact must use one taxonomy version."
        if not self.explanation.strip():
            errors["explanation"] = "A deterministic match explanation is required."
        for field in (
            "matched_terms",
            "excluded_terms",
            "unmet_dimensions",
            "unresolved_dimensions",
        ):
            if not isinstance(getattr(self, field), list):
                errors[field] = "Deterministic match details must be stored as a list."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.profile_id}:{self.impact_review_id} [{self.outcome}]"
