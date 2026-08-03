import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db import transaction

from aria.comparisons.contracts import ANCHOR_RULESET
from aria.comparisons.lineage import project_version_lineage
from aria.comparisons.models import StructuralAnchor, VersionLineageAssessment
from aria.documents.models import DocumentVersion, NormalizedSection

ANCHOR_CONFIGURATION = {
    "ruleset": ANCHOR_RULESET,
    "languages": ["en", "ms"],
    "fallback": "normalized_section_v1",
    "duplicate_strategy": "ordered_occurrence_v1",
}

ANCHOR_PATTERNS = (
    (
        "part",
        re.compile(
            r"^(?:part|bahagian)\s+(?P<reference>[ivxlcdm]+|\d+[a-z]?)\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "division",
        re.compile(
            r"^(?:division|penggal)\s+(?P<reference>[ivxlcdm]+|\d+[a-z]?)\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "section",
        re.compile(
            r"^(?:section|seksyen)\s+(?P<reference>\d{1,3}[a-z]?(?:\([a-z0-9]+\))*)"
            r"\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "regulation",
        re.compile(
            r"^(?:regulation|peraturan)\s+"
            r"(?P<reference>\d{1,3}[a-z]?(?:\([a-z0-9]+\))*)\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "schedule",
        re.compile(
            r"^(?:schedule|jadual)(?:\s+(?P<reference>[a-z0-9ivxlcdm]+))?\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "circular",
        re.compile(
            r"^(?:circular|pekeliling).*?(?:no\.?|bil\.?)\s*"
            r"(?P<reference>\d+(?:\s*/\s*\d{4})?)\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "order",
        re.compile(
            r"^(?:order|perintah)(?:\s+(?P<reference>[a-z0-9./()-]+))?\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "paragraph",
        re.compile(
            r"^(?:paragraph|perenggan)\s+"
            r"(?P<reference>\d{1,3}[a-z]?(?:\([a-z0-9]+\))*)\b(?P<title>.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "clause",
        re.compile(
            r"^(?P<reference>\d{1,3}[a-z]?(?:\([a-z0-9]+\))*)[.)]?\s+"
            r"(?P<title>\S.*)$",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class AnchorSpec:
    anchor_type: str
    canonical_key: str
    label: str
    text: str
    relative_start: int
    relative_end: int


@dataclass(frozen=True)
class AnchorProjectionSummary:
    version_count: int
    eligible_version_count: int
    quarantined_version_count: int
    candidate_count: int
    created_count: int
    skipped_count: int
    configuration_hash: str


def anchor_configuration_hash() -> str:
    serialized = json.dumps(ANCHOR_CONFIGURATION, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _canonical_reference(value: str) -> str:
    normalized = re.sub(r"\s+", "", value.casefold())
    return normalized.strip(".-–—:()") or "main"


def _line_match(line: str) -> tuple[str, str] | None:
    for anchor_type, pattern in ANCHOR_PATTERNS:
        match = pattern.match(line)
        if match:
            return anchor_type, _canonical_reference(match.group("reference") or "main")
    return None


def extract_anchor_specs(section: NormalizedSection) -> list[AnchorSpec]:
    text = section.text
    matches: list[tuple[int, str, str, str]] = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.strip()
        leading = len(raw_line) - len(raw_line.lstrip())
        result = _line_match(stripped) if stripped else None
        if result:
            anchor_type, reference = result
            matches.append((cursor + leading, anchor_type, reference, stripped))
        cursor += len(raw_line)

    if not matches:
        fallback_type = "page" if section.page_number else "section"
        fallback_reference = str(section.page_number or section.ordinal)
        return [
            AnchorSpec(
                anchor_type=fallback_type,
                canonical_key=f"{fallback_type}:{fallback_reference}",
                label=section.heading or f"{fallback_type.title()} {fallback_reference}",
                text=text,
                relative_start=0,
                relative_end=len(text),
            )
        ]

    specs = []
    if matches[0][0] > 0 and text[: matches[0][0]].strip():
        specs.append(
            AnchorSpec(
                anchor_type="preamble",
                canonical_key=f"preamble:{section.ordinal}",
                label=section.heading or "Preamble",
                text=text[: matches[0][0]].strip(),
                relative_start=0,
                relative_end=matches[0][0],
            )
        )
    for index, (start, anchor_type, reference, label) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        specs.append(
            AnchorSpec(
                anchor_type=anchor_type,
                canonical_key=f"{anchor_type}:{reference}",
                label=label,
                text=text[start:end].strip(),
                relative_start=start,
                relative_end=end,
            )
        )
    return specs


@transaction.atomic
def project_version_anchors(version: DocumentVersion) -> tuple[int, int]:
    assessment, _ = project_version_lineage(version)
    if assessment.provenance_status == VersionLineageAssessment.ProvenanceStatus.QUARANTINED:
        return 0, 0

    configuration_hash = anchor_configuration_hash()
    sections = list(
        version.sections.select_related("source_artifact", "extraction_run").order_by("ordinal")
    )
    projected: list[tuple[NormalizedSection, AnchorSpec]] = []
    for section in sections:
        projected.extend((section, spec) for spec in extract_anchor_specs(section))
    key_totals = Counter(spec.canonical_key for _, spec in projected)
    key_occurrences: defaultdict[str, int] = defaultdict(int)

    existing_ordinals = set(
        StructuralAnchor.objects.filter(
            document_version=version,
            configuration_hash=configuration_hash,
        ).values_list("ordinal", flat=True)
    )
    pending = []
    for ordinal, (section, spec) in enumerate(projected, start=1):
        key_occurrences[spec.canonical_key] += 1
        canonical_key = spec.canonical_key
        if key_totals[spec.canonical_key] > 1:
            canonical_key = f"{spec.canonical_key}:occurrence:{key_occurrences[spec.canonical_key]}"
        if ordinal in existing_ordinals:
            continue
        locator = {
            **section.source_locator,
            "anchor_relative_start": spec.relative_start,
            "anchor_relative_end": spec.relative_end,
            "anchor_ruleset": ANCHOR_RULESET,
            "anchor_base_canonical_key": spec.canonical_key,
        }
        pending.append(
            StructuralAnchor(
                document_version=version,
                normalized_section=section,
                source_artifact=section.source_artifact,
                extraction_run=section.extraction_run,
                ruleset=ANCHOR_RULESET,
                configuration_hash=configuration_hash,
                anchor_type=spec.anchor_type,
                canonical_key=canonical_key,
                ordinal=ordinal,
                label=spec.label,
                text=spec.text,
                text_sha256=hashlib.sha256(spec.text.encode()).hexdigest(),
                char_start=section.char_start + spec.relative_start,
                char_end=section.char_start + spec.relative_end,
                source_locator=locator,
            )
        )
    StructuralAnchor.objects.bulk_create(pending, ignore_conflicts=True)
    return len(projected), len(pending)


def project_active_version_anchors() -> AnchorProjectionSummary:
    versions = list(
        DocumentVersion.objects.filter(identity__superseded_by__isnull=True)
        .select_related("identity")
        .order_by("identity_id", "created_at", "id")
    )
    eligible = quarantined = candidate_count = created_count = 0
    for version in versions:
        assessment, _ = project_version_lineage(version)
        if assessment.provenance_status == VersionLineageAssessment.ProvenanceStatus.QUARANTINED:
            quarantined += 1
            continue
        eligible += 1
        candidate, created = project_version_anchors(version)
        candidate_count += candidate
        created_count += created
    return AnchorProjectionSummary(
        version_count=len(versions),
        eligible_version_count=eligible,
        quarantined_version_count=quarantined,
        candidate_count=candidate_count,
        created_count=created_count,
        skipped_count=candidate_count - created_count,
        configuration_hash=anchor_configuration_hash(),
    )
