import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db import transaction
from django.utils import timezone

from aria.comparisons.anchors import anchor_configuration_hash, project_version_anchors
from aria.comparisons.contracts import COMPARISON_RULESET
from aria.comparisons.lineage import lineage_configuration_hash, project_version_lineage
from aria.comparisons.models import (
    ComparisonItem,
    DocumentComparison,
    StructuralAnchor,
    VersionLineageAssessment,
)
from aria.documents.models import DocumentIdentity, DocumentVersion

COMPARISON_CONFIGURATION = {
    "ruleset": COMPARISON_RULESET,
    "anchor_configuration_hash": anchor_configuration_hash(),
    "similarity_threshold": 0.72,
    "ambiguity_margin": 0.03,
    "maximum_delta_opcodes": 200,
}


class ComparisonEligibilityError(ValueError):
    pass


@dataclass(frozen=True)
class Alignment:
    before: StructuralAnchor | None
    after: StructuralAnchor | None
    change_type: str
    match_strategy: str
    similarity_score: float
    details: dict


@dataclass(frozen=True)
class ComparisonProjectionSummary:
    eligible_pair_count: int
    completed_count: int
    reused_count: int
    comparison_ids: tuple[str, ...]


def comparison_configuration_hash() -> str:
    serialized = json.dumps(COMPARISON_CONFIGURATION, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _input_fingerprint(before: DocumentVersion, after: DocumentVersion) -> str:
    serialized = json.dumps(
        {
            "before_id": str(before.id),
            "before": before.normalized_content_sha256,
            "after_id": str(after.id),
            "after": after.normalized_content_sha256,
            "configuration_hash": comparison_configuration_hash(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _matching_block_keys(before_keys: list[str], after_keys: list[str]) -> set[str]:
    matcher = SequenceMatcher(a=before_keys, b=after_keys, autojunk=False)
    stable = set()
    for block in matcher.get_matching_blocks():
        stable.update(before_keys[block.a : block.a + block.size])
    return stable


def align_anchors(
    before_anchors: list[StructuralAnchor],
    after_anchors: list[StructuralAnchor],
) -> list[Alignment]:
    before_by_key = {anchor.canonical_key: anchor for anchor in before_anchors}
    after_by_key = {anchor.canonical_key: anchor for anchor in after_anchors}
    stable_keys = _matching_block_keys(
        [anchor.canonical_key for anchor in before_anchors],
        [anchor.canonical_key for anchor in after_anchors],
    )
    alignments: list[Alignment] = []
    matched_before: set[uuid.UUID] = set()
    matched_after: set[uuid.UUID] = set()

    for key in before_by_key.keys() & after_by_key.keys():
        before = before_by_key[key]
        after = after_by_key[key]
        matched_before.add(before.id)
        matched_after.add(after.id)
        if before.text_sha256 == after.text_sha256:
            change_type = (
                ComparisonItem.ChangeType.UNCHANGED
                if key in stable_keys
                else ComparisonItem.ChangeType.MOVED
            )
            strategy = "canonical_key_exact_text"
            score = 1.0
        elif _normalize_text(before.text) == _normalize_text(after.text):
            change_type = ComparisonItem.ChangeType.FORMAT_ONLY
            strategy = "canonical_key_normalized_text"
            score = 1.0
        else:
            change_type = ComparisonItem.ChangeType.MODIFIED
            strategy = "canonical_key"
            score = SequenceMatcher(
                a=_normalize_text(before.text),
                b=_normalize_text(after.text),
                autojunk=False,
            ).ratio()
        alignments.append(Alignment(before, after, change_type, strategy, score, {}))

    unmatched_before = [anchor for anchor in before_anchors if anchor.id not in matched_before]
    unmatched_after = [anchor for anchor in after_anchors if anchor.id not in matched_after]
    before_hashes = defaultdict(list)
    after_hashes = defaultdict(list)
    for anchor in unmatched_before:
        before_hashes[anchor.text_sha256].append(anchor)
    for anchor in unmatched_after:
        after_hashes[anchor.text_sha256].append(anchor)
    for text_hash in before_hashes.keys() & after_hashes.keys():
        if len(before_hashes[text_hash]) != 1 or len(after_hashes[text_hash]) != 1:
            continue
        before = before_hashes[text_hash][0]
        after = after_hashes[text_hash][0]
        matched_before.add(before.id)
        matched_after.add(after.id)
        alignments.append(
            Alignment(before, after, ComparisonItem.ChangeType.MOVED, "exact_text_hash", 1.0, {})
        )

    unmatched_before = [anchor for anchor in before_anchors if anchor.id not in matched_before]
    unmatched_after = [anchor for anchor in after_anchors if anchor.id not in matched_after]
    for before in unmatched_before:
        candidates = []
        for after in unmatched_after:
            if after.id in matched_after or after.anchor_type != before.anchor_type:
                continue
            score = SequenceMatcher(
                a=_normalize_text(before.text),
                b=_normalize_text(after.text),
                autojunk=False,
            ).ratio()
            if score >= COMPARISON_CONFIGURATION["similarity_threshold"]:
                candidates.append((score, after))
        candidates.sort(key=lambda item: (-item[0], item[1].ordinal, str(item[1].id)))
        if not candidates:
            continue
        if (
            len(candidates) > 1
            and candidates[0][0] - candidates[1][0] <= COMPARISON_CONFIGURATION["ambiguity_margin"]
        ):
            matched_before.add(before.id)
            alignments.append(
                Alignment(
                    before,
                    None,
                    ComparisonItem.ChangeType.AMBIGUOUS,
                    "similarity_ambiguous",
                    candidates[0][0],
                    {
                        "candidate_after_anchor_ids": [
                            str(candidate.id) for _, candidate in candidates[:5]
                        ]
                    },
                )
            )
            continue
        score, after = candidates[0]
        matched_before.add(before.id)
        matched_after.add(after.id)
        alignments.append(
            Alignment(
                before,
                after,
                ComparisonItem.ChangeType.MODIFIED,
                "structural_text_similarity",
                score,
                {},
            )
        )

    for before in before_anchors:
        if before.id not in matched_before:
            alignments.append(
                Alignment(
                    before,
                    None,
                    ComparisonItem.ChangeType.REMOVED,
                    "unmatched_before",
                    0.0,
                    {},
                )
            )
    for after in after_anchors:
        if after.id not in matched_after:
            alignments.append(
                Alignment(
                    None,
                    after,
                    ComparisonItem.ChangeType.ADDED,
                    "unmatched_after",
                    0.0,
                    {},
                )
            )
    return sorted(
        alignments,
        key=lambda item: (
            item.after.ordinal if item.after else 10**9,
            item.before.ordinal if item.before else 10**9,
            item.change_type,
        ),
    )


def _text_delta(before: StructuralAnchor | None, after: StructuralAnchor | None) -> dict:
    before_words = before.text.split() if before else []
    after_words = after.text.split() if after else []
    opcodes = SequenceMatcher(a=before_words, b=after_words, autojunk=False).get_opcodes()
    maximum = COMPARISON_CONFIGURATION["maximum_delta_opcodes"]
    return {
        "opcodes": [
            {
                "tag": tag,
                "before": [before_start, before_end],
                "after": [after_start, after_end],
            }
            for tag, before_start, before_end, after_start, after_end in opcodes[:maximum]
        ],
        "truncated": len(opcodes) > maximum,
    }


def _anchor_evidence(anchor: StructuralAnchor | None) -> dict | None:
    if anchor is None:
        return None
    return {
        "anchor_id": str(anchor.id),
        "section_id": str(anchor.normalized_section_id),
        "artifact_id": str(anchor.source_artifact_id),
        "artifact_sha256": anchor.source_artifact.sha256,
        "extraction_run_id": str(anchor.extraction_run_id),
        "source_locator": anchor.source_locator,
        "text_sha256": anchor.text_sha256,
    }


def _lineage_for(version: DocumentVersion) -> VersionLineageAssessment:
    assessment, _ = project_version_lineage(version)
    if assessment.configuration_hash != lineage_configuration_hash():
        raise ComparisonEligibilityError("Version lineage assessment is not current.")
    if assessment.provenance_status == VersionLineageAssessment.ProvenanceStatus.QUARANTINED:
        raise ComparisonEligibilityError("Quarantined versions cannot be compared.")
    return assessment


def validate_comparison_pair(
    first: DocumentVersion,
    second: DocumentVersion,
) -> tuple[DocumentVersion, DocumentVersion, VersionLineageAssessment, VersionLineageAssessment]:
    if first.identity_id != second.identity_id:
        raise ComparisonEligibilityError("Versions must belong to the same document identity.")
    if first.id == second.id:
        raise ComparisonEligibilityError("A version cannot be compared with itself.")
    before, after = sorted(
        (first, second), key=lambda version: (version.created_at, str(version.id))
    )
    before_lineage = _lineage_for(before)
    after_lineage = _lineage_for(after)
    if before_lineage.comparison_track_key != after_lineage.comparison_track_key:
        raise ComparisonEligibilityError("Cross-representation comparisons are not allowed.")
    if before_lineage.source_artifact_id == after_lineage.source_artifact_id:
        raise ComparisonEligibilityError("Re-extractions of the same artifact are not revisions.")
    return before, after, before_lineage, after_lineage


def eligible_comparison_pairs(
    identity: DocumentIdentity | None = None,
) -> list[tuple[DocumentVersion, DocumentVersion]]:
    assessments = VersionLineageAssessment.objects.filter(
        configuration_hash=lineage_configuration_hash(),
        provenance_status__in=(
            VersionLineageAssessment.ProvenanceStatus.VERIFIED,
            VersionLineageAssessment.ProvenanceStatus.RECONSTRUCTABLE,
        ),
        document_version__identity__superseded_by__isnull=True,
    ).select_related("document_version", "document_version__identity")
    if identity is not None:
        assessments = assessments.filter(document_version__identity=identity)

    grouped: defaultdict[tuple[str, str], list[VersionLineageAssessment]] = defaultdict(list)
    for assessment in assessments:
        grouped[
            (str(assessment.document_version.identity_id), assessment.comparison_track_key)
        ].append(assessment)

    pairs = []
    for group in grouped.values():
        by_artifact: defaultdict[str, list[VersionLineageAssessment]] = defaultdict(list)
        for assessment in group:
            by_artifact[str(assessment.source_artifact_id)].append(assessment)
        representatives = []
        for artifact_assessments in by_artifact.values():
            representative = min(
                artifact_assessments,
                key=lambda item: (
                    item.provenance_status != VersionLineageAssessment.ProvenanceStatus.VERIFIED,
                    item.document_version.created_at,
                    str(item.document_version_id),
                ),
            )
            representatives.append(representative.document_version)
        representatives.sort(key=lambda version: (version.created_at, str(version.id)))
        pairs.extend(zip(representatives, representatives[1:], strict=False))
    return sorted(pairs, key=lambda pair: (pair[1].created_at, str(pair[1].id)))


@transaction.atomic
def compare_document_versions(
    first: DocumentVersion,
    second: DocumentVersion,
) -> tuple[DocumentComparison, bool]:
    before, after, before_lineage, _ = validate_comparison_pair(first, second)
    project_version_anchors(before)
    project_version_anchors(after)
    configuration_hash = comparison_configuration_hash()
    comparison, created = DocumentComparison.objects.get_or_create(
        before_version=before,
        after_version=after,
        configuration_hash=configuration_hash,
        defaults={
            "identity": before.identity,
            "comparison_track_key": before_lineage.comparison_track_key,
            "ruleset": COMPARISON_RULESET,
            "configuration": COMPARISON_CONFIGURATION,
            "input_fingerprint": _input_fingerprint(before, after),
        },
    )
    if comparison.status == DocumentComparison.Status.COMPLETED:
        return comparison, False

    comparison.status = DocumentComparison.Status.RUNNING
    comparison.started_at = timezone.now()
    comparison.error_code = ""
    comparison.error_message = ""
    comparison.save(
        update_fields=("status", "started_at", "error_code", "error_message", "updated_at")
    )
    anchor_hash = anchor_configuration_hash()
    before_anchors = list(
        StructuralAnchor.objects.filter(
            document_version=before,
            configuration_hash=anchor_hash,
        ).select_related("source_artifact", "normalized_section")
    )
    after_anchors = list(
        StructuralAnchor.objects.filter(
            document_version=after,
            configuration_hash=anchor_hash,
        ).select_related("source_artifact", "normalized_section")
    )
    alignments = align_anchors(before_anchors, after_anchors)
    items = []
    for alignment in alignments:
        fingerprint_data = {
            "comparison": str(comparison.id),
            "before": str(alignment.before.id) if alignment.before else None,
            "after": str(alignment.after.id) if alignment.after else None,
            "change_type": alignment.change_type,
            "match_strategy": alignment.match_strategy,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        items.append(
            ComparisonItem(
                comparison=comparison,
                before_anchor=alignment.before,
                after_anchor=alignment.after,
                change_type=alignment.change_type,
                match_strategy=alignment.match_strategy,
                similarity_score=alignment.similarity_score,
                text_delta={**_text_delta(alignment.before, alignment.after), **alignment.details},
                evidence={
                    "before": _anchor_evidence(alignment.before),
                    "after": _anchor_evidence(alignment.after),
                },
                fingerprint=fingerprint,
            )
        )
    ComparisonItem.objects.bulk_create(items, ignore_conflicts=True)
    counts = Counter(item.change_type for item in alignments)
    comparison.status = DocumentComparison.Status.COMPLETED
    comparison.finished_at = timezone.now()
    comparison.unchanged_count = counts[ComparisonItem.ChangeType.UNCHANGED]
    comparison.added_count = counts[ComparisonItem.ChangeType.ADDED]
    comparison.removed_count = counts[ComparisonItem.ChangeType.REMOVED]
    comparison.modified_count = counts[ComparisonItem.ChangeType.MODIFIED]
    comparison.moved_count = counts[ComparisonItem.ChangeType.MOVED]
    comparison.format_only_count = counts[ComparisonItem.ChangeType.FORMAT_ONLY]
    comparison.ambiguous_count = counts[ComparisonItem.ChangeType.AMBIGUOUS]
    comparison.save(
        update_fields=(
            "status",
            "finished_at",
            "unchanged_count",
            "added_count",
            "removed_count",
            "modified_count",
            "moved_count",
            "format_only_count",
            "ambiguous_count",
            "updated_at",
        )
    )
    return comparison, created


def compare_all_eligible_versions() -> ComparisonProjectionSummary:
    pairs = eligible_comparison_pairs()
    comparison_ids = []
    completed = reused = 0
    for before, after in pairs:
        comparison, created = compare_document_versions(before, after)
        comparison_ids.append(str(comparison.id))
        completed += created
        reused += not created
    return ComparisonProjectionSummary(
        eligible_pair_count=len(pairs),
        completed_count=completed,
        reused_count=reused,
        comparison_ids=tuple(comparison_ids),
    )
