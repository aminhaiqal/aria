import json
from dataclasses import replace
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from aria.comparisons.models import ComparisonItem, ComparisonReview
from aria.comparisons.reviews import record_comparison_review
from aria.comparisons.services import compare_document_versions
from aria.events.models import AuditEvent
from aria.impacts.models import (
    ApplicabilityTaxonomy,
    ApplicabilityTerm,
    ImpactEvidence,
    ImpactTarget,
    RegulatoryImpact,
)
from aria.impacts.services import add_impact_target, create_regulatory_impact
from aria.impacts.taxonomies import (
    TaxonomyDefinitionError,
    apply_taxonomy_definition,
    build_taxonomy_plan,
    load_taxonomy_definition,
)
from tests.test_phase3c import Phase3CFixture


class ImpactEvidenceFoundationTestCase(Phase3CFixture):
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


class ApplicabilityTaxonomyTestCase(ImpactEvidenceFoundationTestCase):
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
