# Phase 3B extraction quality

## Scope

The first Phase 3B slice measures whether deterministic extraction produced usable,
source-traceable text before ARIA adds OCR, version diffs, or legal interpretation. It is entirely
offline and self-hosted: the evaluator reads PostgreSQL records derived from immutable artifacts
and stores its report in PostgreSQL.

This slice reports evidence quality. It does not change extracted text, merge document identities,
run OCR, infer legal relationships, or mark a finding as human-reviewed.

## Reproducible run model

Each `QualityAssessmentRun` records:

- a versioned ruleset and its complete configuration;
- a configuration hash that includes the ruleset identity;
- a corpus fingerprint covering every immutable version, section hash, locator, and source
  artifact in the selected collection;
- lifecycle timestamps, outcome totals, and failure details.

An unchanged collection and ruleset reuse the same completed run. A changed corpus or ruleset
creates a new report, retaining prior results for audit. Each `DocumentQualityAssessment` points to
one immutable `DocumentVersion` and `RawArtifact`; each append-only `QualityFinding` repeats those
links so a finding can always be traced without relying on mutable context.

## Ruleset v1

The `aria-extraction-quality-v1` ruleset checks:

- empty, very short, or unusually short normalized content;
- low section counts and missing titles;
- weak title-token coverage in the extracted body;
- missing page or DOM locators;
- equal normalized content assigned to multiple document identities;
- long repeated blocks shared across documents in the same collection;
- PDF OCR routing and extracted-page coverage.

Outcomes are `passed`, `warning`, or `review_required`. A deterministic score from 0 to 100 is a
triage aid, not a legal-confidence score. Thresholds and per-document measurements are persisted
with the report rather than hidden in logs.

## Operation

Assess the default JPDP collection:

```bash
docker compose exec api python manage.py assess_extraction_quality
```

Use `--json` for automation. Another registered collection can be selected with `--authority`
and `--collection` slugs. The command does not access the network.

Administrator-only read endpoints are:

| Resource | Endpoint |
| --- | --- |
| Quality runs | `/api/v1/quality-runs/` |
| Document assessments | `/api/v1/quality-assessments/` |
| Findings | `/api/v1/quality-findings/` |

The same records are visible in Django Admin and cannot be edited there.

## JPDP baseline

On 2026-08-04, the v1 ruleset assessed all 20 immutable JPDP document versions. The result was 2
passed, 6 warnings, and 12 requiring review, with 48 findings. An immediate replay returned the
same run and counts.

The most important signal is 12 `duplicate_normalized_content` findings. The duplicate groups
show that several distinct official URLs currently normalize to identical HTML content, which is
consistent with extracting shared page-shell content instead of each linked publication body. Six
documents also have `high_repeated_content` findings. These are extraction-quality failures to
resolve before version diffing or legal interpretation.

The archived 191-page PDF still has complete extracted-page coverage and is not marked for OCR.
No OCR was executed in this slice.

## Next boundary

Inspect the duplicate groups and their archived HTML locators, then refine the JPDP extraction
route so each publication page resolves to its actual official document content or linked file.
Replay extraction into new immutable versions and require a new quality run with improved
outcomes. OCR execution remains a separate, bounded step only for artifacts explicitly identified
by PDF quality findings.
