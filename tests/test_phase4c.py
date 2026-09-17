import hashlib
import hmac
import json
from dataclasses import replace
from io import StringIO
from unittest.mock import MagicMock

import httpx
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from aria.comparisons.models import ComparisonItem, ComparisonReview
from aria.comparisons.reviews import record_comparison_review
from aria.comparisons.services import compare_document_versions
from aria.events.delivery import ImpactWebhookClient, deliver_impact_outbox_event
from aria.events.models import AuditEvent, OutboxEvent, PipelineEvent
from aria.impacts.generation import (
    ImpactGenerationEligibilityError,
    ImpactGenerationError,
    StructuredImpactCandidate,
    StructuredImpactCandidates,
    StructuredImpactTarget,
    generate_impact_candidates,
)
from aria.impacts.models import (
    ApplicabilityTaxonomy,
    ApplicabilityTerm,
    ImpactEvidence,
    ImpactGeneration,
    ImpactReview,
    ImpactReviewTarget,
    ImpactTarget,
    ProfileImpactMatch,
    RegulatoryImpact,
    ReviewedImpactPublication,
)
from aria.impacts.profiles import match_business_profile, save_business_profile
from aria.impacts.publications import publish_reviewed_impact
from aria.impacts.reviews import record_impact_review
from aria.impacts.services import add_impact_target, create_regulatory_impact
from aria.impacts.taxonomies import (
    TaxonomyDefinitionError,
    apply_taxonomy_definition,
    build_taxonomy_plan,
    load_taxonomy_definition,
)
from aria.openrouter import OpenRouterStructuredResult
from tests.test_phase3c import Phase3CFixture


class ImpactFixtureMixin:
    def setUp(self) -> None:
        super().setUp()
        before = self.create_version(
            "Section 1 Security\nA data controller shall use reasonable safeguards.\n"
            "Section 2 Stable\nStable text."
        )
        after = self.create_version(
            "Section 1 Security\nA data controller shall use appropriate technical safeguards.\n"
            "Section 2 Stable\nStable text."
        )
        self.comparison, _ = compare_document_versions(before, after)
        self.item = self.comparison.items.get(change_type=ComparisonItem.ChangeType.MODIFIED)
        self.reviewer = get_user_model().objects.create_superuser(
            username="impact-foundation-reviewer",
            password="test-password",
        )

    def confirm_change(self):
        return record_comparison_review(
            self.item,
            decision=ComparisonReview.Decision.CONFIRMED,
            reviewer=self.reviewer,
            rationale="Confirmed against both exact official text anchors.",
        )

    def create_impact(self):
        return create_regulatory_impact(
            self.item,
            impact_type=RegulatoryImpact.ImpactType.OBLIGATION,
            origin=RegulatoryImpact.Origin.HUMAN,
            title="Safeguard obligation changed",
            statement="Data controllers may need to reassess their technical safeguards.",
            rationale="The confirmed wording adds a technical qualification.",
            actor_type="user",
            actor_identifier=str(self.reviewer.id),
        )


class ImpactEvidenceFoundationTestCase(ImpactFixtureMixin, Phase3CFixture):

    def test_candidate_snapshots_exact_confirmed_before_and_after_evidence(self) -> None:
        confirmation = self.confirm_change()

        impact, created = self.create_impact()
        replay, replay_created = self.create_impact()

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, impact.id)
        self.assertEqual(impact.confirmation_review, confirmation)
        self.assertFalse(impact.legal_effect_assessed)
        evidence = {row.side: row for row in impact.evidence_records.all()}
        self.assertEqual(set(evidence), {ImpactEvidence.Side.BEFORE, ImpactEvidence.Side.AFTER})
        for side, anchor in (
            (ImpactEvidence.Side.BEFORE, self.item.before_anchor),
            (ImpactEvidence.Side.AFTER, self.item.after_anchor),
        ):
            row = evidence[side]
            self.assertEqual(row.structural_anchor, anchor)
            self.assertEqual(row.normalized_section, anchor.normalized_section)
            self.assertEqual(row.artifact_sha256, anchor.source_artifact.sha256)
            self.assertEqual(row.anchor_text, anchor.text)
            self.assertEqual(row.anchor_text_sha256, anchor.text_sha256)
            self.assertEqual(row.section_text_sha256, anchor.normalized_section.text_sha256)
            self.assertEqual(row.source_locator, anchor.source_locator)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="impact.candidate_created", target_id=impact.id
            ).count(),
            1,
        )

        impact.title = "Mutated conclusion"
        with self.assertRaisesMessage(ValidationError, "append-only"):
            impact.save()

    def test_candidate_requires_the_current_confirmation_review(self) -> None:
        with self.assertRaisesMessage(ValidationError, "current confirmed"):
            self.create_impact()

        self.confirm_change()
        record_comparison_review(
            self.item,
            decision=ComparisonReview.Decision.NEEDS_CONTEXT,
            reviewer=self.reviewer,
            rationale="A commencement notice must be checked before impact analysis.",
        )

        with self.assertRaisesMessage(ValidationError, "current confirmed"):
            self.create_impact()
        self.assertEqual(RegulatoryImpact.objects.count(), 0)

    def test_evidence_validation_rejects_a_different_anchor_or_snapshot(self) -> None:
        self.confirm_change()
        impact, _ = self.create_impact()
        invalid = ImpactEvidence(
            impact=impact,
            side=ImpactEvidence.Side.BEFORE,
            structural_anchor=self.item.after_anchor,
            normalized_section=self.item.after_anchor.normalized_section,
            source_artifact=self.item.after_anchor.source_artifact,
            artifact_sha256="0" * 64,
            anchor_text=self.item.after_anchor.text,
            anchor_text_sha256=self.item.after_anchor.text_sha256,
            section_text_sha256=self.item.after_anchor.normalized_section.text_sha256,
            source_locator=self.item.after_anchor.source_locator,
        )

        with self.assertRaises(ValidationError) as raised:
            invalid.full_clean(
                exclude=("impact", "side"),
                validate_unique=False,
                validate_constraints=False,
            )
        self.assertIn("structural_anchor", raised.exception.message_dict)
        self.assertIn("artifact_sha256", raised.exception.message_dict)

    def test_admin_api_is_read_only_and_returns_evidence_chain(self) -> None:
        self.confirm_change()
        impact, _ = self.create_impact()
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("regulatoryimpact-detail", args=[impact.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["comparison_item"], str(self.item.id))
        self.assertEqual(payload["impact_type"], "obligation")
        self.assertFalse(payload["legal_effect_assessed"])
        self.assertEqual(len(payload["evidence_records"]), 2)
        self.assertIn("artifact_sha256", payload["evidence_records"][0])
        self.assertEqual(
            self.client.post(reverse("regulatoryimpact-list"), {}).status_code,
            405,
        )


class ApplicabilityTaxonomyTestCase(ImpactFixtureMixin, Phase3CFixture):
    def test_repository_taxonomy_dry_run_apply_and_replay_are_governed(self) -> None:
        definition = load_taxonomy_definition()
        expected_terms = len(definition.definition["terms"])
        output = StringIO()

        call_command("sync_impact_taxonomy", stdout=output)

        plan = json.loads(output.getvalue())
        self.assertEqual(plan["mode"], "dry_run")
        self.assertEqual(plan["action"], "create")
        self.assertEqual(plan["term_count"], expected_terms)
        self.assertEqual(ApplicabilityTaxonomy.objects.count(), 0)

        with self.assertRaises(CommandError):
            call_command("sync_impact_taxonomy", "--apply", "--confirm", "WRONG")
        self.assertEqual(ApplicabilityTaxonomy.objects.count(), 0)

        taxonomy, created = apply_taxonomy_definition(
            definition,
            actor_type="user",
            actor_identifier=str(self.reviewer.id),
        )
        replay, replay_created = apply_taxonomy_definition(definition)

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, taxonomy.id)
        self.assertEqual(taxonomy.terms.count(), expected_terms)
        self.assertIn("not an official legal classification", taxonomy.disclaimer)
        self.assertTrue(
            taxonomy.terms.filter(
                dimension=ApplicabilityTerm.Dimension.JURISDICTION,
                code="malaysia",
            ).exists()
        )
        taxonomy.name = "Mutated taxonomy"
        with self.assertRaisesMessage(ValidationError, "append-only"):
            taxonomy.save()

    def test_existing_version_with_different_checksum_requires_a_new_version(self) -> None:
        definition = load_taxonomy_definition()
        apply_taxonomy_definition(definition)

        with self.assertRaisesMessage(TaxonomyDefinitionError, "new version"):
            build_taxonomy_plan(replace(definition, checksum="0" * 64))

    def test_impact_targets_resolve_only_versioned_controlled_terms(self) -> None:
        self.confirm_change()
        impact, _ = self.create_impact()
        taxonomy, _ = apply_taxonomy_definition(load_taxonomy_definition())

        target, created = add_impact_target(
            impact,
            taxonomy,
            dimension=ApplicabilityTerm.Dimension.ACTIVITY,
            code="process-personal-data",
            disposition=ImpactTarget.Disposition.INCLUDED,
            origin=ImpactTarget.Origin.HUMAN,
            rationale="The exact changed wording names personal-data safeguards.",
            actor_type="user",
            actor_identifier=str(self.reviewer.id),
        )
        replay, replay_created = add_impact_target(
            impact,
            taxonomy,
            dimension=ApplicabilityTerm.Dimension.ACTIVITY,
            code="process-personal-data",
            disposition=ImpactTarget.Disposition.INCLUDED,
            origin=ImpactTarget.Origin.HUMAN,
            rationale="Replay does not replace the original rationale.",
        )

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, target.id)
        self.assertEqual(target.term.taxonomy.checksum, taxonomy.checksum)
        with self.assertRaisesMessage(ValidationError, "Unknown applicability term"):
            add_impact_target(
                impact,
                taxonomy,
                dimension=ApplicabilityTerm.Dimension.ACTIVITY,
                code="invented-activity",
                disposition=ImpactTarget.Disposition.INCLUDED,
                origin=ImpactTarget.Origin.GPT,
                rationale="This must never create an ungoverned free-text target.",
            )
        with self.assertRaisesMessage(ValidationError, "different disposition"):
            add_impact_target(
                impact,
                taxonomy,
                dimension=ApplicabilityTerm.Dimension.ACTIVITY,
                code="process-personal-data",
                disposition=ImpactTarget.Disposition.EXCLUDED,
                origin=ImpactTarget.Origin.HUMAN,
                rationale="Conflicting immutable target dispositions are rejected.",
            )

        second_taxonomy = ApplicabilityTaxonomy(
            slug=taxonomy.slug,
            schema_version=1,
            version=2,
            name="ARIA Malaysia business applicability v2 fixture",
            description="A test-only successor taxonomy.",
            jurisdiction="Malaysia",
            disclaimer="Not an official legal classification.",
            checksum="b" * 64,
            definition={"slug": taxonomy.slug, "schema_version": 1, "version": 2},
        )
        second_taxonomy.full_clean()
        second_taxonomy.save()
        second_term = ApplicabilityTerm(
            taxonomy=second_taxonomy,
            dimension=ApplicabilityTerm.Dimension.SECTOR,
            code="cross-sector",
            label="Cross-sector",
            description="Test successor term.",
        )
        second_term.full_clean()
        second_term.save()
        with self.assertRaisesMessage(ValidationError, "cannot mix taxonomy versions"):
            add_impact_target(
                impact,
                second_taxonomy,
                dimension=ApplicabilityTerm.Dimension.SECTOR,
                code="cross-sector",
                disposition=ImpactTarget.Disposition.INCLUDED,
                origin=ImpactTarget.Origin.HUMAN,
                rationale="Targets for one candidate must remain version-consistent.",
            )

        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("regulatoryimpact-detail", args=[impact.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["targets"][0]["code"], "process-personal-data")
        self.assertEqual(response.json()["targets"][0]["taxonomy_checksum"], taxonomy.checksum)


@override_settings(
    OPENROUTER_IMPACT_MODEL="openai/gpt-5.6-sol",
    OPENROUTER_IMPACT_REASONING_EFFORT="low",
    OPENROUTER_IMPACT_MAX_OUTPUT_TOKENS=3000,
    OPENROUTER_IMPACT_MAX_CHARS_PER_ANCHOR=12000,
)
class ImpactGenerationTestCase(ImpactFixtureMixin, Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        self.taxonomy, _ = apply_taxonomy_definition(load_taxonomy_definition())

    def _structured_output(self, *, anchor_ids=None, target_code="data-user"):
        return StructuredImpactCandidates(
            comparison_item_id=str(self.item.id),
            confirmation_review_id=str(self.item.reviews.first().id),
            legal_effect_not_assessed=True,
            candidates=[
                StructuredImpactCandidate(
                    impact_type="obligation",
                    title="Potential safeguard obligation",
                    statement=(
                        "Organizations acting as data users may need to review "
                        "technical safeguards."
                    ),
                    rationale="The confirmed text changes the safeguard wording.",
                    effective_date_text="",
                    citation_anchor_ids=anchor_ids
                    or [str(self.item.before_anchor_id), str(self.item.after_anchor_id)],
                    targets=[
                        StructuredImpactTarget(
                            dimension="regulated_role",
                            code=target_code,
                            disposition="included",
                            rationale="The exact text names a controller role.",
                        )
                    ],
                )
            ],
        )

    def test_deterministic_generation_is_review_required_evidence_bound_and_idempotent(self):
        self.confirm_change()

        result = generate_impact_candidates(self.item, self.taxonomy)
        replay = generate_impact_candidates(self.item, self.taxonomy)

        self.assertTrue(result.created)
        self.assertFalse(replay.created)
        self.assertEqual(result.impact_count, 1)
        self.assertEqual(replay.impact_count, 1)
        self.assertEqual(ImpactGeneration.objects.count(), 1)
        generation = result.generation
        self.assertEqual(generation.status, ImpactGeneration.Status.COMPLETED)
        self.assertEqual(generation.provider, "deterministic")
        impact = generation.generated_impacts.get()
        self.assertEqual(impact.origin, RegulatoryImpact.Origin.DETERMINISTIC)
        self.assertEqual(impact.evidence_records.count(), 2)
        self.assertTrue(impact.targets.filter(term__code="data-user").exists())
        self.assertFalse(impact.legal_effect_assessed)

    def test_generation_rejects_unconfirmed_text_before_any_model_call(self):
        client = MagicMock()

        with self.assertRaises(ImpactGenerationEligibilityError):
            generate_impact_candidates(
                self.item,
                self.taxonomy,
                provider="openrouter",
                client=client,
            )

        client.generate_structured.assert_not_called()
        self.assertEqual(ImpactGeneration.objects.count(), 0)

    def test_openrouter_structured_output_is_validated_then_materialized(self):
        self.confirm_change()
        client = MagicMock()
        client.generate_structured.return_value = OpenRouterStructuredResult(
            response_id="resp-impact-1",
            output=self._structured_output(),
            input_tokens=321,
            output_tokens=123,
        )

        result = generate_impact_candidates(
            self.item,
            self.taxonomy,
            provider="openrouter",
            client=client,
        )

        generation = result.generation
        self.assertEqual(generation.status, ImpactGeneration.Status.COMPLETED)
        self.assertEqual(generation.response_id, "resp-impact-1")
        self.assertEqual(generation.input_tokens, 321)
        impact = generation.generated_impacts.get()
        self.assertEqual(impact.origin, RegulatoryImpact.Origin.GPT)
        self.assertEqual(impact.targets.get().term.code, "data-user")
        request = client.generate_structured.call_args.kwargs
        self.assertIs(request["output_model"], StructuredImpactCandidates)
        self.assertEqual(request["model"], "openai/gpt-5.6-sol")

        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("impactgeneration-detail", args=[generation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["impact_count"], 1)

    def test_hallucinated_anchor_or_taxonomy_code_fails_without_partial_impacts(self):
        self.confirm_change()
        client = MagicMock()
        client.generate_structured.return_value = OpenRouterStructuredResult(
            response_id="resp-impact-invalid",
            output=self._structured_output(anchor_ids=["not-an-anchor"]),
        )

        with self.assertRaisesMessage(ImpactGenerationError, "exact anchor"):
            generate_impact_candidates(
                self.item,
                self.taxonomy,
                provider="openrouter",
                client=client,
            )

        failed = ImpactGeneration.objects.get()
        self.assertEqual(failed.status, ImpactGeneration.Status.FAILED)
        self.assertEqual(RegulatoryImpact.objects.count(), 0)

        client.generate_structured.return_value = OpenRouterStructuredResult(
            response_id="resp-impact-invalid-term",
            output=self._structured_output(target_code="invented-role"),
        )
        with self.assertRaisesMessage(ImpactGenerationError, "unknown applicability"):
            generate_impact_candidates(
                self.item,
                self.taxonomy,
                provider="openrouter",
                client=client,
            )
        self.assertEqual(RegulatoryImpact.objects.count(), 0)


class ImpactHumanReviewTestCase(ImpactFixtureMixin, Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        self.confirm_change()
        self.taxonomy, _ = apply_taxonomy_definition(load_taxonomy_definition())
        result = generate_impact_candidates(self.item, self.taxonomy)
        self.impact = result.generation.generated_impacts.get()

    def test_review_history_is_append_only_chained_and_snapshots_targets(self):
        first = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.NEEDS_CONTEXT,
            reviewer=self.reviewer,
            rationale="Confirm whether a separate commencement instrument applies.",
        )
        second = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.APPROVED,
            reviewer=self.reviewer,
            rationale="The proposed impact remains bounded to the cited change.",
        )

        self.assertEqual(second.previous_review, first)
        self.assertEqual(self.impact.reviews.count(), 2)
        self.assertEqual(second.reviewed_title, self.impact.title)
        self.assertEqual(
            second.reviewed_targets.count(),
            self.impact.targets.count(),
        )
        for reviewed_target in second.reviewed_targets.all():
            self.assertEqual(reviewed_target.source_target.impact, self.impact)
            self.assertEqual(reviewed_target.term, reviewed_target.source_target.term)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="impact.review_recorded", target_id=self.impact.id
            ).count(),
            2,
        )
        second.rationale = "Mutated"
        with self.assertRaisesMessage(ValidationError, "append-only"):
            second.save()

    def test_amendment_snapshots_reviewer_wording_and_controlled_targets(self):
        role = self.taxonomy.terms.get(
            dimension=ApplicabilityTerm.Dimension.REGULATED_ROLE,
            code="data-user",
        )
        review = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.AMENDED,
            reviewer=self.reviewer,
            rationale="The evidence supports a narrower data-user statement.",
            amended_title="Technical safeguards may need review",
            amended_statement="Data users may need to review their technical safeguards.",
            target_specs=[
                {
                    "term": role,
                    "disposition": ImpactTarget.Disposition.INCLUDED,
                    "rationale": "The cited text names the controller role.",
                }
            ],
        )

        self.assertEqual(review.reviewed_title, "Technical safeguards may need review")
        self.assertEqual(review.reviewed_targets.get().term, role)
        self.assertIsNone(review.reviewed_targets.get().source_target)

    def test_review_is_blocked_when_textual_change_confirmation_is_superseded(self):
        record_comparison_review(
            self.item,
            decision=ComparisonReview.Decision.REJECTED,
            reviewer=self.reviewer,
            rationale="A second source check invalidated the earlier confirmation.",
        )

        with self.assertRaisesMessage(ValidationError, "no longer current"):
            record_impact_review(
                self.impact,
                decision=ImpactReview.Decision.APPROVED,
                reviewer=self.reviewer,
            )
        self.assertEqual(ImpactReview.objects.count(), 0)

    def test_console_review_is_post_only_validated_and_evidence_complete(self):
        self.client.force_login(self.reviewer)
        list_route = reverse("console:impact-list")
        detail_route = reverse("console:impact-detail", args=[self.impact.id])
        review_route = reverse("console:impact-review", args=[self.impact.id])

        self.assertEqual(self.client.get(list_route).status_code, 200)
        detail = self.client.get(detail_route)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Exact before and after evidence")
        self.assertContains(detail, self.item.before_anchor.source_artifact.sha256)
        self.assertContains(detail, "Potential obligation impact")
        self.assertEqual(self.client.get(review_route).status_code, 405)

        invalid = self.client.post(
            review_route,
            {
                "decision": ImpactReview.Decision.REJECTED,
                "title": self.impact.title,
                "statement": self.impact.statement,
                "rationale": "short",
            },
        )
        self.assertRedirects(invalid, detail_route)
        self.assertEqual(ImpactReview.objects.count(), 0)

        response = self.client.post(
            review_route,
            {
                "decision": ImpactReview.Decision.APPROVED,
                "title": self.impact.title,
                "statement": self.impact.statement,
                "rationale": "Approved against the exact evidence shown.",
            },
        )
        self.assertRedirects(response, detail_route)
        review = ImpactReview.objects.get()
        self.assertEqual(review.decision, ImpactReview.Decision.APPROVED)
        self.assertEqual(review.reviewer, self.reviewer)

        self.client.force_login(self.reviewer)
        api_response = self.client.get(reverse("impactreview-detail", args=[review.id]))
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(
            len(api_response.json()["reviewed_targets"]),
            self.impact.targets.count(),
        )
        self.assertEqual(
            self.client.post(reverse("impactreview-list"), {}).status_code,
            405,
        )

    def test_review_target_rejects_cross_version_taxonomy(self):
        review = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.APPROVED,
            reviewer=self.reviewer,
        )
        foreign_taxonomy = ApplicabilityTaxonomy(
            slug=self.taxonomy.slug,
            schema_version=1,
            version=2,
            name="Foreign taxonomy version",
            description="Test fixture",
            jurisdiction="Malaysia",
            disclaimer="Not an official legal classification.",
            checksum="c" * 64,
            definition={"slug": self.taxonomy.slug, "schema_version": 1, "version": 2},
        )
        foreign_taxonomy.full_clean()
        foreign_taxonomy.save()
        foreign_term = ApplicabilityTerm.objects.create(
            taxonomy=foreign_taxonomy,
            dimension=ApplicabilityTerm.Dimension.SECTOR,
            code="cross-sector",
            label="Cross-sector",
            description="Test term",
        )
        invalid = ImpactReviewTarget(
            impact_review=review,
            term=foreign_term,
            disposition=ImpactTarget.Disposition.INCLUDED,
            rationale="This different version must be rejected.",
        )
        with self.assertRaisesMessage(ValidationError, "generation taxonomy"):
            invalid.full_clean(validate_constraints=False)


class BusinessProfileMatchingTestCase(ImpactFixtureMixin, Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        self.confirm_change()
        self.taxonomy, _ = apply_taxonomy_definition(load_taxonomy_definition())
        result = generate_impact_candidates(self.item, self.taxonomy)
        self.impact = result.generation.generated_impacts.get()
        self.review = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.APPROVED,
            reviewer=self.reviewer,
            rationale="Approved for exact business-profile evaluation.",
        )

    def terms(self, *keys):
        return [
            self.taxonomy.terms.get(dimension=dimension, code=code)
            for dimension, code in keys
        ]

    def create_profile(self, terms=None, name="Test organization"):
        terms = terms or self.terms(
            (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
            (ApplicabilityTerm.Dimension.REGULATED_ROLE, "data-user"),
            (ApplicabilityTerm.Dimension.ACTIVITY, "process-personal-data"),
        )
        return save_business_profile(
            owner=self.reviewer,
            taxonomy=self.taxonomy,
            name=name,
            terms=terms,
            notes="A test profile expressed only with controlled terms.",
        )

    def test_exact_match_is_explainable_snapshotted_and_idempotent(self):
        profile = self.create_profile()

        result = match_business_profile(profile, self.review)
        replay = match_business_profile(profile, self.review)

        self.assertTrue(result.created)
        self.assertFalse(replay.created)
        self.assertEqual(result.match, replay.match)
        self.assertEqual(result.match.outcome, ProfileImpactMatch.Outcome.MATCHED)
        self.assertEqual(result.match.ruleset, "aria-exact-applicability-v1")
        self.assertEqual(result.match.profile_snapshot["taxonomy_checksum"], self.taxonomy.checksum)
        self.assertEqual(
            result.match.impact_review_snapshot["impact_review_id"],
            str(self.review.id),
        )
        self.assertTrue(result.match.matched_terms)
        self.assertEqual(ProfileImpactMatch.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="business_profile.impact_evaluated").count(),
            1,
        )

    def test_missing_and_conflicting_dimensions_have_distinct_outcomes(self):
        profile = self.create_profile(
            self.terms(
                (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
            )
        )
        missing = match_business_profile(profile, self.review).match
        self.assertEqual(missing.outcome, ProfileImpactMatch.Outcome.INSUFFICIENT_CONTEXT)
        self.assertEqual(
            missing.unresolved_dimensions,
            [ApplicabilityTerm.Dimension.REGULATED_ROLE],
        )

        profile = save_business_profile(
            profile=profile,
            owner=self.reviewer,
            taxonomy=self.taxonomy,
            name=profile.name,
            terms=self.terms(
                (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
                (ApplicabilityTerm.Dimension.REGULATED_ROLE, "data-processor"),
            ),
        )
        conflict = match_business_profile(profile, self.review).match
        self.assertEqual(conflict.outcome, ProfileImpactMatch.Outcome.NOT_MATCHED)
        self.assertEqual(conflict.unmet_dimensions, [ApplicabilityTerm.Dimension.REGULATED_ROLE])
        self.assertFalse(
            any(term["code"] == "data-processor" for term in missing.profile_snapshot["terms"])
        )

    def test_reviewed_exclusion_overrides_included_dimensions(self):
        malaysia, data_user = self.terms(
            (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
            (ApplicabilityTerm.Dimension.REGULATED_ROLE, "data-user"),
        )
        amended = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.AMENDED,
            reviewer=self.reviewer,
            rationale="Exclude the specifically identified data-user profile.",
            amended_title=self.impact.title,
            amended_statement=self.impact.statement,
            target_specs=[
                {
                    "term": malaysia,
                    "disposition": ImpactTarget.Disposition.INCLUDED,
                    "rationale": "The source is within the Malaysian collection.",
                },
                {
                    "term": data_user,
                    "disposition": ImpactTarget.Disposition.EXCLUDED,
                    "rationale": "Reviewer explicitly excludes this controlled role.",
                },
            ],
        )
        profile = self.create_profile()

        match = match_business_profile(profile, amended).match

        self.assertEqual(match.outcome, ProfileImpactMatch.Outcome.NOT_MATCHED)
        self.assertEqual(match.excluded_terms[0]["code"], "data-user")

    def test_matching_rejects_stale_or_nonapproved_review(self):
        profile = self.create_profile()
        latest = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.NEEDS_CONTEXT,
            reviewer=self.reviewer,
            rationale="More business context is required before matching can continue.",
        )

        with self.assertRaisesMessage(ValidationError, "latest impact review"):
            match_business_profile(profile, self.review)
        with self.assertRaisesMessage(ValidationError, "approved or amended"):
            match_business_profile(profile, latest)
        self.assertEqual(ProfileImpactMatch.objects.count(), 0)

    def test_profile_taxonomy_is_strict_and_admin_api_is_read_only(self):
        profile = self.create_profile()
        duplicate_size_terms = self.terms(
            (ApplicabilityTerm.Dimension.SIZE, "small"),
            (ApplicabilityTerm.Dimension.SIZE, "large"),
        )
        with self.assertRaisesMessage(ValidationError, "only one size"):
            save_business_profile(
                owner=self.reviewer,
                taxonomy=self.taxonomy,
                name="Invalid profile",
                terms=duplicate_size_terms,
            )

        match = match_business_profile(profile, self.review).match
        self.client.force_login(self.reviewer)
        profile_response = self.client.get(reverse("businessprofile-detail", args=[profile.id]))
        match_response = self.client.get(reverse("profileimpactmatch-detail", args=[match.id]))
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(len(profile_response.json()["terms"]), 3)
        self.assertEqual(match_response.status_code, 200)
        self.assertEqual(match_response.json()["outcome"], "matched")
        self.assertEqual(self.client.post(reverse("businessprofile-list"), {}).status_code, 405)

    def test_reader_profile_api_is_owner_scoped_and_evaluates_on_create(self):
        self.client.force_login(self.reviewer)
        terms = self.terms(
            (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
            (ApplicabilityTerm.Dimension.REGULATED_ROLE, "data-user"),
        )
        response = self.client.post(
            reverse("reader-api:profile-list"),
            data=json.dumps(
                {
                    "name": "Malaysia data team",
                    "taxonomy_id": str(self.taxonomy.id),
                    "term_ids": [str(term.id) for term in terms],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        profile_id = payload["profile"]["id"]
        self.assertEqual(payload["evaluation"]["matched"], 1)
        options = self.client.get(reverse("reader-api:options")).json()
        self.assertEqual(options["applicability_taxonomy"]["id"], str(self.taxonomy.id))
        self.assertEqual(options["business_profiles"][0]["id"], profile_id)

        document = self.client.get(
            reverse(
                "reader-api:document-detail",
                kwargs={"identity_id": self.comparison.identity_id},
            ),
            {"profile": profile_id},
        )
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document.json()["selected_profile"]["id"], profile_id)
        self.assertEqual(len(document.json()["reviewed_impacts"]), 1)
        after_evidence = next(
            evidence
            for evidence in document.json()["reviewed_impacts"][0]["evidence"]
            if evidence["side"] == "after"
        )
        self.assertEqual(
            after_evidence["artifact_sha256"],
            self.item.after_anchor.source_artifact.sha256,
        )

        outsider = get_user_model().objects.create_user(username="profile-outsider")
        self.client.force_login(outsider)
        hidden = self.client.get(
            reverse(
                "reader-api:document-detail",
                kwargs={"identity_id": self.comparison.identity_id},
            ),
            {"profile": profile_id},
        )
        self.assertEqual(hidden.status_code, 404)

    def test_reader_profile_filter_returns_only_exact_reviewed_matches(self):
        profile = self.create_profile(
            self.terms(
                (ApplicabilityTerm.Dimension.JURISDICTION, "malaysia"),
                (ApplicabilityTerm.Dimension.REGULATED_ROLE, "data-user"),
            )
        )
        match_business_profile(profile, self.review)
        self.client.force_login(self.reviewer)

        response = self.client.get(
            reverse("reader-api:search"),
            {
                "q": "technical safeguards",
                "mode": "full_text",
                "profile": str(profile.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["bounded_result_count"], 1)
        self.assertEqual(payload["results"][0]["relevance"]["profile_id"], str(profile.id))
        self.assertEqual(payload["results"][0]["relevance"]["impact_count"], 1)

        record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.NEEDS_CONTEXT,
            reviewer=self.reviewer,
            rationale="New context is required before this impact can remain visible.",
        )
        hidden = self.client.get(
            reverse(
                "reader-api:document-detail",
                kwargs={"identity_id": self.comparison.identity_id},
            ),
            {"profile": str(profile.id)},
        )
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json()["reviewed_impacts"], [])


class ReviewedImpactPublicationTestCase(ImpactFixtureMixin, Phase3CFixture):
    def setUp(self) -> None:
        super().setUp()
        self.confirm_change()
        self.taxonomy, _ = apply_taxonomy_definition(load_taxonomy_definition())
        result = generate_impact_candidates(self.item, self.taxonomy)
        self.impact = result.generation.generated_impacts.get()
        self.review = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.APPROVED,
            reviewer=self.reviewer,
            rationale="Approved for explicit reviewed-impact publication.",
        )

    def test_publication_is_explicit_evidence_complete_and_idempotent(self):
        result = publish_reviewed_impact(self.review, publisher=self.reviewer)
        replay = publish_reviewed_impact(self.review, publisher=self.reviewer)

        self.assertTrue(result.created)
        self.assertFalse(replay.created)
        self.assertEqual(result.publication, replay.publication)
        self.assertEqual(ReviewedImpactPublication.objects.count(), 1)
        self.assertEqual(OutboxEvent.objects.count(), 1)
        outbox = OutboxEvent.objects.get()
        self.assertEqual(outbox.topic, "regulatory.impact.confirmed")
        payload = outbox.payload["data"]
        self.assertEqual(payload["impact_review_id"], str(self.review.id))
        self.assertFalse(payload["legal_effect_assessed"])
        self.assertEqual(len(payload["evidence"]), 2)
        self.assertEqual(payload["targets"][0]["taxonomy_checksum"], self.taxonomy.checksum)
        self.assertNotIn("business_profiles", json.dumps(payload))
        self.assertEqual(
            AuditEvent.objects.filter(action="impact.review_published").count(),
            1,
        )

        self.client.force_login(self.reviewer)
        api = self.client.get(
            reverse("reviewedimpactpublication-detail", args=[result.publication.id])
        )
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["impact_review"], str(self.review.id))
        self.assertEqual(
            self.client.post(reverse("reviewedimpactpublication-list"), {}).status_code,
            405,
        )

    @override_settings(REQUIRE_SEPARATE_PUBLISHER=True)
    def test_two_person_rule_separates_impact_review_from_publication(self):
        with self.assertRaisesMessage(ValidationError, "different authenticated publisher"):
            publish_reviewed_impact(self.review, publisher=self.reviewer)

        publisher = get_user_model().objects.create_superuser(
            username="independent-impact-publisher",
            password="test-password",
        )
        result = publish_reviewed_impact(self.review, publisher=publisher)

        self.assertTrue(result.created)
        self.assertEqual(result.publication.published_by, publisher)

    def test_stale_or_nonapproved_reviews_cannot_publish(self):
        context_review = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.NEEDS_CONTEXT,
            reviewer=self.reviewer,
            rationale="A commencement instrument must be checked before publication.",
        )
        with self.assertRaisesMessage(ValidationError, "latest impact review"):
            publish_reviewed_impact(self.review, publisher=self.reviewer)
        with self.assertRaisesMessage(ValidationError, "approved or amended"):
            publish_reviewed_impact(context_review, publisher=self.reviewer)
        restored = record_impact_review(
            self.impact,
            decision=ImpactReview.Decision.APPROVED,
            reviewer=self.reviewer,
            rationale="Approved again before the source confirmation changes.",
        )
        record_comparison_review(
            self.item,
            decision=ComparisonReview.Decision.REJECTED,
            reviewer=self.reviewer,
            rationale="The underlying textual-change confirmation was withdrawn.",
        )
        with self.assertRaisesMessage(ValidationError, "source confirmation"):
            publish_reviewed_impact(restored, publisher=self.reviewer)
        self.assertEqual(ReviewedImpactPublication.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_console_and_command_require_exact_publication_confirmation(self):
        self.client.force_login(self.reviewer)
        detail_route = reverse("console:impact-detail", args=[self.impact.id])
        publish_route = reverse("console:impact-publish", args=[self.impact.id])
        self.assertContains(self.client.get(detail_route), "Publish reviewed impact")
        self.assertEqual(self.client.get(publish_route).status_code, 405)

        rejected = self.client.post(publish_route, {"confirmation": "publish"})
        self.assertRedirects(rejected, detail_route)
        self.assertEqual(ReviewedImpactPublication.objects.count(), 0)
        accepted = self.client.post(publish_route, {"confirmation": "PUBLISH"})
        self.assertRedirects(accepted, detail_route)
        publication = ReviewedImpactPublication.objects.get()

        output = StringIO()
        call_command(
            "publish_reviewed_impact",
            str(self.review.id),
            publisher=self.reviewer.username,
            confirm="PUBLISH",
            stdout=output,
        )
        self.assertFalse(json.loads(output.getvalue())["created"])
        self.assertEqual(ReviewedImpactPublication.objects.count(), 1)
        self.assertEqual(publication.impact_review, self.review)

        with self.assertRaisesMessage(CommandError, "--confirm PUBLISH"):
            call_command(
                "publish_reviewed_impact",
                str(self.review.id),
                publisher=self.reviewer.username,
                confirm="publish",
            )

    @override_settings(
        IMPACT_WEBHOOK_URL="https://hooks.example.test/aria/impact",
        IMPACT_WEBHOOK_ALLOWED_DOMAINS=["hooks.example.test"],
        IMPACT_WEBHOOK_SECRET="test-secret-with-at-least-thirty-two-characters",
        IMPACT_WEBHOOK_MAX_ATTEMPTS=3,
        IMPACT_WEBHOOK_STALE_MINUTES=15,
    )
    def test_delivery_pins_public_https_and_signs_canonical_payload(self):
        publication = publish_reviewed_impact(self.review, publisher=self.reviewer).publication
        outbox = publication.pipeline_event.outbox_event
        captured = {}

        def handler(request):
            captured["request"] = request
            return httpx.Response(204)

        client = ImpactWebhookClient(
            resolver=lambda _hostname, _port: ["8.8.8.8"],
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)

        delivered = deliver_impact_outbox_event(outbox.id, client=client)

        self.assertEqual(delivered.status, OutboxEvent.Status.PUBLISHED)
        self.assertEqual(delivered.attempts, 1)
        request = captured["request"]
        self.assertEqual(request.url.host, "8.8.8.8")
        self.assertEqual(request.headers["host"], "hooks.example.test")
        self.assertEqual(json.loads(request.content), outbox.payload)
        timestamp = request.headers["x-aria-timestamp"]
        expected = hmac.new(
            b"test-secret-with-at-least-thirty-two-characters",
            timestamp.encode() + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(request.headers["x-aria-signature"], f"sha256={expected}")
        self.assertEqual(request.headers["x-aria-event-id"], str(publication.pipeline_event_id))

    @override_settings(
        IMPACT_WEBHOOK_URL="https://hooks.example.test/aria/impact",
        IMPACT_WEBHOOK_ALLOWED_DOMAINS=["hooks.example.test"],
        IMPACT_WEBHOOK_SECRET="test-secret-with-at-least-thirty-two-characters",
        IMPACT_WEBHOOK_MAX_ATTEMPTS=3,
        IMPACT_WEBHOOK_STALE_MINUTES=15,
    )
    def test_delivery_retries_transient_failure_and_rejects_private_dns(self):
        publication = publish_reviewed_impact(self.review, publisher=self.reviewer).publication
        outbox = publication.pipeline_event.outbox_event
        retry_client = ImpactWebhookClient(
            resolver=lambda _hostname, _port: ["8.8.8.8"],
            transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
        )
        self.addCleanup(retry_client.close)

        failed = deliver_impact_outbox_event(outbox.id, client=retry_client)

        self.assertEqual(failed.status, OutboxEvent.Status.FAILED)
        self.assertEqual(failed.attempts, 1)
        self.assertIn("retryable status 503", failed.last_error)
        OutboxEvent.objects.filter(pk=failed.id).update(available_at=timezone.now())
        success_client = ImpactWebhookClient(
            resolver=lambda _hostname, _port: ["8.8.8.8"],
            transport=httpx.MockTransport(lambda _request: httpx.Response(202)),
        )
        self.addCleanup(success_client.close)
        delivered = deliver_impact_outbox_event(outbox.id, client=success_client)
        self.assertEqual(delivered.status, OutboxEvent.Status.PUBLISHED)
        self.assertEqual(delivered.attempts, 2)

        second_impact = self.impact
        next_review = record_impact_review(
            second_impact,
            decision=ImpactReview.Decision.AMENDED,
            reviewer=self.reviewer,
            rationale="Publish a separately amended, evidence-bounded review.",
            amended_title=self.impact.title,
            amended_statement=self.impact.statement,
            target_specs=[
                {
                    "term": target.term,
                    "disposition": target.disposition,
                    "rationale": target.rationale,
                }
                for target in self.impact.targets.all()
            ],
        )
        unsafe_outbox = publish_reviewed_impact(
            next_review,
            publisher=self.reviewer,
        ).publication.pipeline_event.outbox_event
        unsafe_client = ImpactWebhookClient(
            resolver=lambda _hostname, _port: ["127.0.0.1"],
            transport=httpx.MockTransport(lambda _request: httpx.Response(204)),
        )
        self.addCleanup(unsafe_client.close)
        rejected = deliver_impact_outbox_event(unsafe_outbox.id, client=unsafe_client)
        self.assertEqual(rejected.status, OutboxEvent.Status.FAILED)
        self.assertEqual(rejected.attempts, 3)
        self.assertIn("non-public address", rejected.last_error)

    @override_settings(
        IMPACT_WEBHOOK_URL="",
        IMPACT_WEBHOOK_ALLOWED_DOMAINS=[],
        IMPACT_WEBHOOK_SECRET="",
    )
    def test_disabled_delivery_leaves_publication_pending_and_forged_events_are_ignored(self):
        publication = publish_reviewed_impact(self.review, publisher=self.reviewer).publication
        outbox = publication.pipeline_event.outbox_event
        self.assertIsNone(deliver_impact_outbox_event(outbox.id))
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, OutboxEvent.Status.PENDING)

        forged_event = PipelineEvent.objects.create(
            event_type="regulatory.impact.confirmed",
            aggregate_type="regulatory_impact",
            aggregate_id=self.impact.id,
            payload={},
        )
        forged = OutboxEvent.objects.create(
            pipeline_event=forged_event,
            topic="regulatory.impact.confirmed",
            payload={"forged": True},
        )
        client = MagicMock()
        self.assertIsNone(deliver_impact_outbox_event(forged.id, client=client))
        client.deliver.assert_not_called()
