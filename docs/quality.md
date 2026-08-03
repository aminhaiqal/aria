# Phase 3B extraction quality

## Scope

Phase 3B measures whether deterministic extraction produced usable,
source-traceable text before ARIA adds OCR, version diffs, or legal interpretation. It is entirely
offline and self-hosted: the evaluator reads PostgreSQL records derived from immutable artifacts
and stores its report in PostgreSQL.

Quality reports do not rewrite artifacts, extracted text, or immutable versions. Deterministic
linked-file remediation may supersede an earlier identity alias, but it does not delete its audit
history. This slice does not run OCR, infer legal relationships, or mark findings as reviewed.

## Reproducible run model

Each `QualityAssessmentRun` records:

- a versioned ruleset and its complete configuration;
- a configuration hash that includes the ruleset identity;
- a corpus fingerprint covering each explicitly selected immutable version, section hash, locator,
  and source artifact in the collection;
- lifecycle timestamps, outcome totals, and failure details.

An unchanged collection and ruleset reuse the same completed run. A changed corpus or ruleset
creates a new report, retaining prior results for audit. Each `DocumentQualityAssessment` points to
one immutable `DocumentVersion` and `RawArtifact`; each append-only `QualityFinding` repeats those
links so a finding can always be traced without relying on mutable context.

## Ruleset v2

The current `aria-extraction-quality-v2` ruleset checks:

- empty, very short, or unusually short normalized content;
- low section counts and missing titles;
- weak title-token coverage in the extracted body;
- missing page or DOM locators;
- equal normalized content assigned to multiple document identities;
- long repeated blocks shared across documents in the same collection;
- PDF OCR routing and extracted-page coverage;
- HTML landing pages whose primary linked document has not become the current evidence version.

Operational runs select the latest explicit immutable version for each active identity. Historical
versions, superseded identity aliases, and earlier quality runs remain stored and inspectable. When
a PDF lacks embedded title metadata, title checks fall back to the official landing-page identity
title and record that source in the metrics.

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

## JPDP baseline and remediation

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

The selector/linked-file remediation then:

- selected `.betterdocs-entry-content` instead of the outer navigation wrapper;
- found exactly 18 official PDF links across the 19 archived HTML detail pages;
- archived all 18 PDFs in Cloudflare R2 before extraction;
- projected 13 native-text PDFs as newer versions of their landing-page identities;
- retained 13 earlier file-URL identities as superseded audit history;
- routed five PDFs with zero extractable characters to `ocr_required`.

The v2 operational quality run covers 20 active identities and reports **10 passed, 5 warnings, and
5 review required**, with 23 findings. Duplicate and repeated-content findings fell to zero. An
immediate replay returned the same run ID, `f203f41b-385c-4006-96be-7de9dc9bb1ad`, and the same
corpus fingerprint, `d9b13f9aeacb48cf40f820bcbc14a10b9d052622c3598b875125d2b3c1db019c`.

## Next boundary

Add self-hosted OCR for only the five image-only PDFs now identified by artifact SHA-256 and page
count. Preserve the original PDFs, store OCR output separately, and project page-level provenance
before rerunning quality. Version diffing and legal interpretation remain outside that slice.
