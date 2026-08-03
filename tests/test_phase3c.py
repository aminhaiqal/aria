import hashlib
import json
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from aria.artifacts.models import RawArtifact
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.comparisons.anchors import extract_anchor_specs, project_active_version_anchors
from aria.comparisons.audit import audit_active_versions, audit_document_version
from aria.comparisons.contracts import ProvenanceStatus, RepresentationKind
from aria.comparisons.lineage import (
    LINEAGE_CONFIGURATION,
    lineage_configuration_hash,
    project_active_version_lineage,
    project_version_lineage,
)
from aria.comparisons.models import (
    ComparisonItem,
    DocumentComparison,
    StructuralAnchor,
    VersionLineageAssessment,
)
from aria.comparisons.services import (
    ComparisonEligibilityError,
    compare_all_eligible_versions,
    compare_document_versions,
    eligible_comparison_pairs,
)
from aria.documents.models import (
    DocumentIdentity,
    DocumentVersion,
    NormalizedSection,
    VersionEvidence,
)
from aria.extraction.models import ExtractedBlock, ExtractedDocument, ExtractionRun


class Phase3CFixture(TestCase):
    def setUp(self) -> None:
        self.authority = Authority.objects.create(
            name="Phase 3C regulator",
            slug="phase-3c-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Phase 3C publications",
            slug="phase-3c-publications",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.identity = DocumentIdentity.objects.create(
            collection=self.collection,
            stable_key="1" * 64,
            canonical_title="Test Regulation",
            canonical_url="https://example.com/regulation/",
            identity_basis={"strategy": "test"},
        )

    def create_version(
        self,
        text: str,
        *,
        extractor_name: str = "html",
        metadata: dict | None = None,
        with_evidence: bool = True,
        with_section: bool = True,
    ) -> DocumentVersion:
        digest = hashlib.sha256(text.encode()).hexdigest()
        artifact = RawArtifact.objects.create(
            sha256=digest,
            byte_size=len(text.encode()),
            detected_content_type=(
                "text/html" if extractor_name.startswith("html") else "application/pdf"
            ),
            storage_backend=RawArtifact.StorageBackend.FILESYSTEM,
            storage_key=f"test/{digest}",
        )
        run = ExtractionRun.objects.create(
            raw_artifact=artifact,
            extractor_name=extractor_name,
            extractor_version="test-v1",
            configuration_hash="2" * 64,
            status=ExtractionRun.Status.SUCCEEDED,
        )
        extracted = ExtractedDocument.objects.create(
            extraction_run=run,
            raw_artifact=artifact,
            title="Test Regulation",
            plain_text=text,
            plain_text_sha256=digest,
            metadata=metadata or {},
        )
        block = ExtractedBlock.objects.create(
            extracted_document=extracted,
            ordinal=1,
            block_type=ExtractedBlock.BlockType.PARAGRAPH,
            heading="Section 1",
            text=text,
            text_sha256=digest,
            char_start=0,
            char_end=len(text),
            source_locator={"html_path": "main > p"},
        )
        version = DocumentVersion.objects.create(
            identity=self.identity,
            normalized_content_sha256=digest,
            title="Test Regulation",
            canonical_url=self.identity.canonical_url,
            plain_content=text,
            normalized_metadata=metadata or {},
            extractor_name=extractor_name,
            extractor_version="test-v1",
        )
        if with_evidence:
            VersionEvidence.objects.create(
                document_version=version,
                raw_artifact=artifact,
                extraction_run=run,
                observed_url=self.identity.canonical_url,
            )
        if with_section:
            NormalizedSection.objects.create(
                document_version=version,
                source_artifact=artifact,
                extraction_run=run,
                source_block=block,
                ordinal=1,
                section_type="paragraph",
                heading="Section 1",
                text=text,
                text_sha256=digest,
                char_start=0,
                char_end=len(text),
                source_locator={"html_path": "main > p"},
            )
        return version


class VersionLineageAuditTestCase(Phase3CFixture):
    def test_audit_distinguishes_verified_reconstructable_and_quarantined_versions(self) -> None:
        verified = self.create_version("Verified regulation text")
        reconstructable = self.create_version(
            "Reconstructable regulation text", with_evidence=False
        )
        quarantined = self.create_version(
            "Unknown representation text",
            extractor_name="unknown",
            with_evidence=False,
            with_section=False,
        )

        verified_result = audit_document_version(verified)
        reconstructable_result = audit_document_version(reconstructable)
        quarantined_result = audit_document_version(quarantined)

        self.assertEqual(verified_result.provenance_status, ProvenanceStatus.VERIFIED)
        self.assertEqual(verified_result.representation_kind, RepresentationKind.LANDING_HTML)
        self.assertEqual(
            reconstructable_result.provenance_status,
            ProvenanceStatus.RECONSTRUCTABLE,
        )
        self.assertEqual(quarantined_result.provenance_status, ProvenanceStatus.QUARANTINED)
        self.assertIn("version_has_no_normalized_sections", quarantined_result.reasons)

    def test_ocr_lineage_is_classified_separately_on_the_official_file_track(self) -> None:
        version = self.create_version(
            "OCR regulation text",
            extractor_name="pdf",
            metadata={"artifact_lineage": {"transformation_type": "ocr_searchable_pdf"}},
        )

        result = audit_document_version(version)

        self.assertEqual(result.representation_kind, RepresentationKind.OCR_DERIVED)
        self.assertTrue(result.comparison_track_key.endswith(":official_file"))

    def test_audit_command_is_read_only_and_machine_readable(self) -> None:
        self.create_version("Audited regulation text")
        before = {
            "versions": DocumentVersion.objects.count(),
            "evidence": VersionEvidence.objects.count(),
        }
        output = StringIO()

        call_command("audit_version_lineage", "--json", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["version_count"], 1)
        self.assertEqual(payload["verified_count"], 1)
        self.assertEqual(before["versions"], DocumentVersion.objects.count())
        self.assertEqual(before["evidence"], VersionEvidence.objects.count())
        self.assertEqual(audit_active_versions()["ruleset"], "aria-version-lineage-v1")


class VersionLineageProjectionTestCase(Phase3CFixture):
    def test_projection_is_append_only_idempotent_and_preserves_source_records(self) -> None:
        verified = self.create_version("Verified projection text")
        reconstructable = self.create_version(
            "Reconstructable projection text",
            with_evidence=False,
        )
        evidence_count = VersionEvidence.objects.count()

        first = project_active_version_lineage()
        replay = project_active_version_lineage()

        self.assertEqual(first.created_count, 2)
        self.assertEqual(first.verified_count, 1)
        self.assertEqual(first.reconstructable_count, 1)
        self.assertEqual(replay.created_count, 0)
        self.assertEqual(replay.skipped_count, 2)
        self.assertEqual(VersionEvidence.objects.count(), evidence_count)
        self.assertEqual(
            verified.lineage_assessments.get().provenance_status,
            VersionLineageAssessment.ProvenanceStatus.VERIFIED,
        )
        reconstructed_assessment = reconstructable.lineage_assessments.get()
        self.assertEqual(
            reconstructed_assessment.provenance_status,
            VersionLineageAssessment.ProvenanceStatus.RECONSTRUCTABLE,
        )
        self.assertIsNotNone(reconstructed_assessment.source_artifact_id)
        self.assertIsNotNone(reconstructed_assessment.extraction_run_id)

        reconstructed_assessment.basis = {"tampered": True}
        with self.assertRaises(ValidationError):
            reconstructed_assessment.save()

    def test_unknown_representation_is_persisted_as_quarantined(self) -> None:
        version = self.create_version(
            "Unclassified projection text",
            extractor_name="unknown",
            with_evidence=False,
            with_section=False,
        )

        assessment, created = project_version_lineage(version)

        self.assertTrue(created)
        self.assertEqual(
            assessment.provenance_status,
            VersionLineageAssessment.ProvenanceStatus.QUARANTINED,
        )
        self.assertIsNone(assessment.source_artifact_id)
        self.assertIsNone(assessment.extraction_run_id)


class StructuralAnchorTestCase(Phase3CFixture):
    def test_extracts_english_and_malay_legal_anchors(self) -> None:
        version = self.create_version(
            "PART I\nPreliminary\nSection 1 Short title\nText.\n"
            "BAHAGIAN II\nSEKSYEN 2 Pemakaian\nTeks.\nJADUAL PERTAMA\nButiran."
        )
        section = version.sections.get()

        specs = extract_anchor_specs(section)

        self.assertEqual(
            [(spec.anchor_type, spec.canonical_key) for spec in specs],
            [
                ("part", "part:i"),
                ("section", "section:1"),
                ("part", "part:ii"),
                ("section", "section:2"),
                ("schedule", "schedule:pertama"),
            ],
        )

    def test_projection_preserves_evidence_and_is_idempotent(self) -> None:
        version = self.create_version(
            "Regulation 1 Citation\nThis regulation may be cited as the Test Regulation.\n"
            "2 Application\nThis regulation applies to data controllers."
        )

        first = project_active_version_anchors()
        replay = project_active_version_anchors()

        self.assertEqual(first.eligible_version_count, 1)
        self.assertEqual(first.created_count, 2)
        self.assertEqual(replay.created_count, 0)
        self.assertEqual(replay.skipped_count, 2)
        anchors = list(version.structural_anchors.order_by("ordinal"))
        self.assertEqual([anchor.canonical_key for anchor in anchors], ["regulation:1", "clause:2"])
        for anchor in anchors:
            self.assertEqual(
                anchor.source_artifact_id, anchor.normalized_section.source_artifact_id
            )
            self.assertEqual(anchor.extraction_run_id, anchor.normalized_section.extraction_run_id)
            self.assertIn("anchor_relative_start", anchor.source_locator)

    def test_section_without_recognized_structure_gets_traceable_fallback(self) -> None:
        version = self.create_version("General explanatory publication text.")

        project_active_version_anchors()

        anchor = StructuralAnchor.objects.get(document_version=version)
        self.assertEqual(anchor.anchor_type, "section")
        self.assertEqual(anchor.canonical_key, "section:1")
        self.assertEqual(anchor.text, "General explanatory publication text.")

    def test_repeated_clause_keys_receive_deterministic_occurrence_suffixes(self) -> None:
        version = self.create_version("1 First item\nText.\n1 Second item\nMore text.")

        project_active_version_anchors()

        self.assertEqual(
            list(
                version.structural_anchors.order_by("ordinal").values_list(
                    "canonical_key", flat=True
                )
            ),
            ["clause:1:occurrence:1", "clause:1:occurrence:2"],
        )


class DocumentComparisonTestCase(Phase3CFixture):
    def test_deterministic_comparison_classifies_changes_and_replays(self) -> None:
        before = self.create_version(
            "Section 1 Purpose\nOrganizations protect personal data.\n"
            "Section 2 Notice\nGive notice."
        )
        after = self.create_version(
            "Section 1 Purpose\nOrganizations must protect personal data.\n"
            "Section 3 Security\nUse safeguards.\nSection 2 Notice\nGive notice."
        )

        comparison, created = compare_document_versions(before, after)
        replay, replay_created = compare_document_versions(before, after)

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(comparison.id, replay.id)
        self.assertEqual(DocumentComparison.objects.count(), 1)
        self.assertEqual(comparison.modified_count, 1)
        self.assertEqual(comparison.added_count, 1)
        self.assertEqual(comparison.unchanged_count, 1)
        self.assertEqual(comparison.items.count(), 3)
        modified = comparison.items.get(change_type=ComparisonItem.ChangeType.MODIFIED)
        self.assertEqual(modified.match_strategy, "canonical_key")
        self.assertEqual(
            modified.evidence["before"]["artifact_sha256"],
            modified.before_anchor.source_artifact.sha256,
        )
        self.assertEqual(
            modified.evidence["after"]["artifact_sha256"],
            modified.after_anchor.source_artifact.sha256,
        )

    def test_formatting_only_and_moved_anchors_are_not_content_modifications(self) -> None:
        before = self.create_version("Section 1 First\nA  B\nSection 2 Second\nSame text.")
        after = self.create_version("Section 2 Second\nSame text.\nSection 1 First\nA B")

        comparison, _ = compare_document_versions(before, after)

        self.assertEqual(comparison.format_only_count, 1)
        self.assertEqual(comparison.moved_count, 1)
        self.assertEqual(comparison.modified_count, 0)

    def test_cross_representation_and_same_artifact_pairs_are_blocked(self) -> None:
        html = self.create_version("HTML publication")
        pdf = self.create_version("PDF publication", extractor_name="pdf")

        with self.assertRaisesMessage(
            ComparisonEligibilityError,
            "Cross-representation comparisons",
        ):
            compare_document_versions(html, pdf)

        duplicate_extraction = self.create_version("Duplicate extraction", with_evidence=False)
        html_assessment, _ = project_version_lineage(html)
        VersionLineageAssessment.objects.create(
            document_version=duplicate_extraction,
            representation_kind=VersionLineageAssessment.RepresentationKind.LANDING_HTML,
            comparison_track_key=html_assessment.comparison_track_key,
            provenance_status=VersionLineageAssessment.ProvenanceStatus.RECONSTRUCTABLE,
            source_artifact=html_assessment.source_artifact,
            extraction_run=html_assessment.extraction_run,
            ruleset=html_assessment.ruleset,
            configuration=LINEAGE_CONFIGURATION,
            configuration_hash=lineage_configuration_hash(),
            basis={"test": "same_artifact_reextraction"},
        )

        with self.assertRaisesMessage(
            ComparisonEligibilityError,
            "same artifact",
        ):
            compare_document_versions(html, duplicate_extraction)

    def test_eligible_pair_projection_uses_distinct_artifacts_in_one_track(self) -> None:
        before = self.create_version("Section 1 Earlier\nEarlier text.")
        after = self.create_version("Section 1 Later\nLater text.")
        project_active_version_lineage()

        pairs = eligible_comparison_pairs(self.identity)
        summary = compare_all_eligible_versions()

        self.assertEqual([(pair[0].id, pair[1].id) for pair in pairs], [(before.id, after.id)])
        self.assertEqual(summary.eligible_pair_count, 1)
        self.assertEqual(summary.completed_count, 1)
        self.assertEqual(summary.reused_count, 0)

    def test_removed_and_ambiguous_candidates_remain_explicit(self) -> None:
        removed_before = self.create_version(
            "Section 1 Stable\nStable text.\nSection 2 Removed\nRemoved text."
        )
        removed_after = self.create_version("Section 1 Stable\nStable text.")

        removed_comparison, _ = compare_document_versions(removed_before, removed_after)

        self.assertEqual(removed_comparison.removed_count, 1)
        self.assertEqual(removed_comparison.modified_count, 0)

        ambiguous_before = self.create_version("Section 9 Requirement\nShared requirements.")
        ambiguous_after = self.create_version(
            "Section 10 Candidate A\nShared requirements.\n"
            "Section 11 Candidate B\nShared requirements."
        )

        ambiguous_comparison, _ = compare_document_versions(ambiguous_before, ambiguous_after)

        self.assertEqual(ambiguous_comparison.ambiguous_count, 1)
        ambiguous = ambiguous_comparison.items.get(change_type=ComparisonItem.ChangeType.AMBIGUOUS)
        self.assertEqual(len(ambiguous.text_delta["candidate_after_anchor_ids"]), 2)
