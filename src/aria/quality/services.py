import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from aria.collections.models import PublicationCollection
from aria.documents.models import DocumentVersion, NormalizedSection
from aria.quality.models import (
    DocumentQualityAssessment,
    QualityAssessmentRun,
    QualityFinding,
)

RULESET = "aria-extraction-quality-v1"
DEFAULT_CONFIGURATION = {
    "minimum_non_whitespace_characters_review": 100,
    "minimum_non_whitespace_characters_warning": 400,
    "minimum_section_count_warning": 2,
    "minimum_title_token_coverage_warning": 0.5,
    "repeated_section_minimum_characters": 80,
    "repeated_content_ratio_warning": 0.5,
    "repeated_content_ratio_review": 0.8,
    "pdf_page_coverage_warning": 1.0,
    "pdf_page_coverage_review": 0.8,
}


class QualityAssessmentInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class FindingSpec:
    code: str
    severity: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class AssessmentSpec:
    document_version: DocumentVersion
    source_artifact_id: Any
    outcome: str
    score: int
    metrics: dict[str, Any]
    findings: tuple[FindingSpec, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _versions_for_collection(collection: PublicationCollection):
    return (
        DocumentVersion.objects.filter(identity__collection=collection)
        .select_related("identity", "identity__collection")
        .prefetch_related(
            "evidence_records__raw_artifact",
            "evidence_records__extraction_run__extracted_document",
            "sections__source_artifact",
            "sections__extraction_run__extracted_document",
        )
        .order_by("id")
    )


def corpus_fingerprint(versions: list[DocumentVersion]) -> str:
    corpus: list[dict[str, Any]] = []
    for version in versions:
        sections = sorted(version.sections.all(), key=lambda section: section.ordinal)
        evidence = sorted(version.evidence_records.all(), key=lambda item: str(item.id))
        corpus.append(
            {
                "version_id": str(version.id),
                "identity_id": str(version.identity_id),
                "content_sha256": version.normalized_content_sha256,
                "title": version.title,
                "canonical_url": version.canonical_url,
                "extractor": [version.extractor_name, version.extractor_version],
                "sections": [
                    {
                        "ordinal": section.ordinal,
                        "text_sha256": section.text_sha256,
                        "page_number": section.page_number,
                        "source_locator": section.source_locator,
                        "artifact_sha256": section.source_artifact.sha256,
                    }
                    for section in sections
                ],
                "evidence_artifacts": [item.raw_artifact.sha256 for item in evidence],
            }
        )
    return _sha256_json(corpus)


def _tokenize(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"\w+", value) if len(token) >= 3}


def _has_precise_locator(section: NormalizedSection) -> bool:
    locator = section.source_locator or {}
    return any(
        locator.get(key) not in (None, "")
        for key in ("html_path", "css_selector", "xpath", "page", "page_number")
    )


def _source_context(version: DocumentVersion):
    sections = sorted(version.sections.all(), key=lambda section: section.ordinal)
    if sections:
        section = sections[0]
        return section.source_artifact, section.extraction_run.extracted_document, sections
    evidence = sorted(version.evidence_records.all(), key=lambda item: str(item.id))
    if not evidence:
        raise ValueError(f"Document version {version.id} has no source artifact provenance.")
    item = evidence[0]
    return item.raw_artifact, item.extraction_run.extracted_document, sections


def _finding(
    code: str,
    severity: str,
    message: str,
    **evidence: Any,
) -> FindingSpec:
    return FindingSpec(code=code, severity=severity, message=message, evidence=evidence)


def _assess_version(
    version: DocumentVersion,
    *,
    duplicate_members: dict[str, list[str]],
    shared_section_hashes: set[str],
    configuration: dict[str, Any],
) -> AssessmentSpec:
    artifact, extracted_document, sections = _source_context(version)
    non_whitespace_characters = len(re.sub(r"\s+", "", version.plain_content))
    section_characters = sum(len(re.sub(r"\s+", "", section.text)) for section in sections)
    heading_count = sum(section.section_type == "heading" for section in sections)
    missing_locator_count = sum(not _has_precise_locator(section) for section in sections)

    title_tokens = _tokenize(version.title)
    content_tokens = _tokenize(version.plain_content)
    title_token_coverage = (
        len(title_tokens & content_tokens) / len(title_tokens) if title_tokens else None
    )

    repeated_minimum = configuration["repeated_section_minimum_characters"]
    repeated_characters = sum(
        len(re.sub(r"\s+", "", section.text))
        for section in sections
        if section.text_sha256 in shared_section_hashes
        and len(re.sub(r"\s+", "", section.text)) >= repeated_minimum
    )
    repeated_content_ratio = repeated_characters / section_characters if section_characters else 0.0

    is_pdf = artifact.detected_content_type == "application/pdf"
    page_count = extracted_document.page_count if is_pdf else None
    extracted_pages = len(
        {section.page_number for section in sections if section.page_number is not None}
    )
    page_coverage = extracted_pages / page_count if page_count and page_count > 0 else None

    findings: list[FindingSpec] = []
    if not version.plain_content.strip():
        findings.append(
            _finding(
                "empty_content",
                QualityFinding.Severity.REVIEW_REQUIRED,
                "The normalized document contains no text.",
                non_whitespace_characters=non_whitespace_characters,
            )
        )
    elif non_whitespace_characters < configuration["minimum_non_whitespace_characters_review"]:
        findings.append(
            _finding(
                "very_short_content",
                QualityFinding.Severity.REVIEW_REQUIRED,
                "The normalized text is too short for reliable downstream use.",
                non_whitespace_characters=non_whitespace_characters,
                threshold=configuration["minimum_non_whitespace_characters_review"],
            )
        )
    elif non_whitespace_characters < configuration["minimum_non_whitespace_characters_warning"]:
        findings.append(
            _finding(
                "short_content",
                QualityFinding.Severity.WARNING,
                "The normalized text is shorter than the quality baseline.",
                non_whitespace_characters=non_whitespace_characters,
                threshold=configuration["minimum_non_whitespace_characters_warning"],
            )
        )

    if len(sections) < configuration["minimum_section_count_warning"]:
        findings.append(
            _finding(
                "low_section_count",
                QualityFinding.Severity.WARNING,
                "The document has fewer normalized sections than expected.",
                section_count=len(sections),
                threshold=configuration["minimum_section_count_warning"],
            )
        )
    if not version.title.strip():
        findings.append(
            _finding(
                "missing_title",
                QualityFinding.Severity.WARNING,
                "The extractor did not produce a document title.",
            )
        )
    elif (
        title_token_coverage is not None
        and title_token_coverage < configuration["minimum_title_token_coverage_warning"]
    ):
        findings.append(
            _finding(
                "low_title_token_coverage",
                QualityFinding.Severity.WARNING,
                "Few title tokens occur in the normalized document text.",
                title_token_coverage=round(title_token_coverage, 6),
                threshold=configuration["minimum_title_token_coverage_warning"],
            )
        )
    if missing_locator_count:
        findings.append(
            _finding(
                "missing_source_locators",
                QualityFinding.Severity.REVIEW_REQUIRED,
                "One or more sections lack a precise page or DOM locator.",
                missing_locator_count=missing_locator_count,
                section_count=len(sections),
            )
        )

    duplicate_group = duplicate_members.get(version.normalized_content_sha256, [])
    if len(duplicate_group) > 1:
        findings.append(
            _finding(
                "duplicate_normalized_content",
                QualityFinding.Severity.REVIEW_REQUIRED,
                "The same normalized content is assigned to multiple document identities.",
                normalized_content_sha256=version.normalized_content_sha256,
                duplicate_group_size=len(duplicate_group),
                document_version_ids=duplicate_group,
            )
        )

    if repeated_content_ratio >= configuration["repeated_content_ratio_review"]:
        findings.append(
            _finding(
                "high_repeated_content",
                QualityFinding.Severity.REVIEW_REQUIRED,
                "Most extracted text is shared verbatim with other documents in the collection.",
                repeated_content_ratio=round(repeated_content_ratio, 6),
                repeated_characters=repeated_characters,
                threshold=configuration["repeated_content_ratio_review"],
            )
        )
    elif repeated_content_ratio >= configuration["repeated_content_ratio_warning"]:
        findings.append(
            _finding(
                "repeated_content",
                QualityFinding.Severity.WARNING,
                "A substantial portion of extracted text is shared with other documents.",
                repeated_content_ratio=round(repeated_content_ratio, 6),
                repeated_characters=repeated_characters,
                threshold=configuration["repeated_content_ratio_warning"],
            )
        )

    if is_pdf and extracted_document.requires_ocr:
        findings.append(
            _finding(
                "pdf_requires_ocr",
                QualityFinding.Severity.REVIEW_REQUIRED,
                "The PDF extractor marked this artifact as requiring OCR.",
                page_count=page_count,
                extracted_pages=extracted_pages,
            )
        )
    if is_pdf and page_coverage is not None:
        if page_coverage < configuration["pdf_page_coverage_review"]:
            findings.append(
                _finding(
                    "low_pdf_page_coverage",
                    QualityFinding.Severity.REVIEW_REQUIRED,
                    "Extracted text covers too few pages in the source PDF.",
                    page_coverage=round(page_coverage, 6),
                    page_count=page_count,
                    extracted_pages=extracted_pages,
                    threshold=configuration["pdf_page_coverage_review"],
                )
            )
        elif page_coverage < configuration["pdf_page_coverage_warning"]:
            findings.append(
                _finding(
                    "incomplete_pdf_page_coverage",
                    QualityFinding.Severity.WARNING,
                    "Some PDF pages produced no normalized text section.",
                    page_coverage=round(page_coverage, 6),
                    page_count=page_count,
                    extracted_pages=extracted_pages,
                    threshold=configuration["pdf_page_coverage_warning"],
                )
            )

    severity_counts = Counter(finding.severity for finding in findings)
    if severity_counts[QualityFinding.Severity.REVIEW_REQUIRED]:
        outcome = DocumentQualityAssessment.Outcome.REVIEW_REQUIRED
    elif severity_counts[QualityFinding.Severity.WARNING]:
        outcome = DocumentQualityAssessment.Outcome.WARNING
    else:
        outcome = DocumentQualityAssessment.Outcome.PASSED
    score = max(
        0,
        100
        - 15 * severity_counts[QualityFinding.Severity.WARNING]
        - 40 * severity_counts[QualityFinding.Severity.REVIEW_REQUIRED],
    )
    metrics = {
        "artifact_sha256": artifact.sha256,
        "content_type": artifact.detected_content_type,
        "extractor_name": version.extractor_name,
        "extractor_version": version.extractor_version,
        "content_selector": version.normalized_metadata.get("content_selector", ""),
        "non_whitespace_characters": non_whitespace_characters,
        "section_characters": section_characters,
        "section_count": len(sections),
        "heading_count": heading_count,
        "missing_locator_count": missing_locator_count,
        "title_token_coverage": (
            round(title_token_coverage, 6) if title_token_coverage is not None else None
        ),
        "repeated_characters": repeated_characters,
        "repeated_content_ratio": round(repeated_content_ratio, 6),
        "duplicate_group_size": len(duplicate_group),
        "duplicate_document_version_ids": duplicate_group,
        "page_count": page_count,
        "extracted_pages": extracted_pages if is_pdf else None,
        "page_coverage": round(page_coverage, 6) if page_coverage is not None else None,
        "requires_ocr": extracted_document.requires_ocr if is_pdf else False,
    }
    return AssessmentSpec(
        document_version=version,
        source_artifact_id=artifact.id,
        outcome=outcome,
        score=score,
        metrics=metrics,
        findings=tuple(sorted(findings, key=lambda finding: finding.code)),
    )


def _build_assessments(
    versions: list[DocumentVersion], configuration: dict[str, Any]
) -> list[AssessmentSpec]:
    duplicate_members: dict[str, list[str]] = defaultdict(list)
    section_documents: dict[str, set[str]] = defaultdict(set)
    section_lengths: dict[str, int] = {}
    for version in versions:
        duplicate_members[version.normalized_content_sha256].append(str(version.id))
        for section in version.sections.all():
            section_documents[section.text_sha256].add(str(version.id))
            section_lengths[section.text_sha256] = len(re.sub(r"\s+", "", section.text))
    duplicate_members = {digest: sorted(members) for digest, members in duplicate_members.items()}
    shared_section_hashes = {
        digest
        for digest, document_ids in section_documents.items()
        if len(document_ids) > 1
        and section_lengths[digest] >= configuration["repeated_section_minimum_characters"]
    }
    return [
        _assess_version(
            version,
            duplicate_members=duplicate_members,
            shared_section_hashes=shared_section_hashes,
            configuration=configuration,
        )
        for version in versions
    ]


def _claim_run(run: QualityAssessmentRun) -> tuple[QualityAssessmentRun, bool]:
    now = timezone.now()
    with transaction.atomic():
        locked = QualityAssessmentRun.objects.select_for_update().get(pk=run.pk)
        if locked.status == QualityAssessmentRun.Status.COMPLETED:
            return locked, False
        stale_before = now - timedelta(minutes=30)
        if (
            locked.status == QualityAssessmentRun.Status.RUNNING
            and locked.started_at
            and locked.started_at > stale_before
        ):
            raise QualityAssessmentInProgress(
                "A quality assessment is already processing this collection corpus."
            )
        locked.status = QualityAssessmentRun.Status.RUNNING
        locked.started_at = now
        locked.finished_at = None
        locked.error_code = ""
        locked.error_message = ""
        locked.save(
            update_fields=(
                "status",
                "started_at",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
        return locked, True


def assess_collection_quality(
    collection: PublicationCollection,
    *,
    configuration: dict[str, Any] | None = None,
) -> QualityAssessmentRun:
    effective_configuration = {**DEFAULT_CONFIGURATION, **(configuration or {})}
    versions = list(_versions_for_collection(collection))
    configuration_hash = _sha256_json(
        {"ruleset": RULESET, "configuration": effective_configuration}
    )
    fingerprint = corpus_fingerprint(versions)
    run, _ = QualityAssessmentRun.objects.get_or_create(
        collection=collection,
        configuration_hash=configuration_hash,
        corpus_fingerprint=fingerprint,
        defaults={
            "ruleset": RULESET,
            "configuration": effective_configuration,
        },
    )
    run, claimed = _claim_run(run)
    if not claimed:
        return run

    try:
        specifications = _build_assessments(versions, effective_configuration)
        with transaction.atomic():
            locked = QualityAssessmentRun.objects.select_for_update().get(pk=run.pk)
            assessments = [
                DocumentQualityAssessment(
                    quality_run=locked,
                    document_version=specification.document_version,
                    source_artifact_id=specification.source_artifact_id,
                    outcome=specification.outcome,
                    score=specification.score,
                    metrics=specification.metrics,
                )
                for specification in specifications
            ]
            DocumentQualityAssessment.objects.bulk_create(assessments)
            QualityFinding.objects.bulk_create(
                [
                    QualityFinding(
                        assessment=assessment,
                        document_version=assessment.document_version,
                        source_artifact_id=assessment.source_artifact_id,
                        code=finding.code,
                        severity=finding.severity,
                        message=finding.message,
                        evidence=finding.evidence,
                    )
                    for assessment, specification in zip(assessments, specifications, strict=True)
                    for finding in specification.findings
                ]
            )
            counts = Counter(item.outcome for item in specifications)
            locked.status = QualityAssessmentRun.Status.COMPLETED
            locked.finished_at = timezone.now()
            locked.document_count = len(specifications)
            locked.passed_count = counts[DocumentQualityAssessment.Outcome.PASSED]
            locked.warning_count = counts[DocumentQualityAssessment.Outcome.WARNING]
            locked.review_required_count = counts[DocumentQualityAssessment.Outcome.REVIEW_REQUIRED]
            locked.finding_count = sum(len(item.findings) for item in specifications)
            locked.save(
                update_fields=(
                    "status",
                    "finished_at",
                    "document_count",
                    "passed_count",
                    "warning_count",
                    "review_required_count",
                    "finding_count",
                    "updated_at",
                )
            )
            run = locked
    except Exception as error:
        QualityAssessmentRun.objects.filter(
            pk=run.pk, status=QualityAssessmentRun.Status.RUNNING
        ).update(
            status=QualityAssessmentRun.Status.FAILED,
            error_code=type(error).__name__,
            error_message=str(error)[:4000],
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        raise
    return run
