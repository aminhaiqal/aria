from enum import StrEnum

LINEAGE_RULESET = "aria-version-lineage-v1"
ANCHOR_RULESET = "aria-legal-anchors-v1"
COMPARISON_RULESET = "aria-structural-diff-v1"
SUMMARY_PROMPT_VERSION = "aria-change-summary-v1"


class RepresentationKind(StrEnum):
    LANDING_HTML = "landing_html"
    OFFICIAL_PDF = "official_pdf"
    OCR_DERIVED = "ocr_derived"
    UNKNOWN = "unknown"


class ProvenanceStatus(StrEnum):
    VERIFIED = "verified"
    RECONSTRUCTABLE = "reconstructable"
    QUARANTINED = "quarantined"


def representation_track(kind: RepresentationKind) -> str:
    if kind in {RepresentationKind.OFFICIAL_PDF, RepresentationKind.OCR_DERIVED}:
        return "official_file"
    if kind == RepresentationKind.LANDING_HTML:
        return "landing_page"
    return "unknown"
