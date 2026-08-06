# Phase 3C evidence-backed version comparison

## Scope

Phase 3C detects reviewable textual differences between comparable immutable versions. It does not
infer that a legal amendment occurred, assess legal effect, or compare a landing page with its PDF
or OCR representation. JPDP remains the official source of record.

```text
DocumentVersion
  -> VersionLineageAssessment
  -> StructuralAnchor
  -> DocumentComparison + ComparisonItem
  -> ComparisonReview
  -> optional ComparisonSummary
  -> explicit ReviewedChangePublication -> PipelineEvent -> OutboxEvent
```

## Provenance and eligibility

`audit_version_lineage` is read-only. It classifies each active version as verified,
reconstructable from one unambiguous section artifact/extraction lineage, or quarantined. The
persisted assessment is append-only and records its ruleset, configuration hash, source artifact,
extraction run, comparison track, and complete machine-readable basis.

Representations use separate kinds:

- `landing_html`
- `official_pdf`
- `ocr_derived`
- `unknown`

PDF and OCR text share the `official_file` track, while HTML uses `landing_page`. A comparison still
requires distinct source artifacts. Re-extractions or OCR transformations of one artifact are not
treated as temporal revisions. Quarantined versions are never eligible.

## Structural anchors and comparison

The versioned bilingual anchor rules recognize English and Malay parts, divisions, sections,
regulations, schedules, circulars, orders, paragraphs, and numbered clauses. Unrecognized content
gets a page or normalized-section fallback. Repeated keys receive deterministic ordered-occurrence
suffixes. Every anchor stores the exact section, artifact, extraction run, offsets, locator, and
text hash.

The deterministic comparison order is:

1. canonical key plus exact text hash;
2. canonical key plus whitespace-normalized or changed text;
3. unique exact text hash under another key;
4. constrained same-type text similarity;
5. explicit ambiguous, added, or removed candidates.

Results are `unchanged`, `added`, `removed`, `modified`, `moved`, `format_only`, or `ambiguous`.
Word-level opcodes are bounded in the stored delta; complete before/after text remains available
through the linked anchors. A configuration and input fingerprint make replay idempotent.

## Review and publication

Comparison records, items, anchors, summaries, and publications are administrator-only and
read-only over the API. Reviews are added through Django administration or the domain service.
Each new decision links to its previous decision and creates an audit event; earlier decisions are
never edited or deleted.

Only a currently `confirmed` item can be published. Publication is an explicit, idempotent command
that creates `regulatory.textual_change.confirmed` in the transactional outbox. `needs_context`,
`rejected`, unreviewed, and unchanged items cannot publish. The event contains exact before/after
artifact hashes and locators and states `legal_effect_assessed=false`.

## Structured GPT summaries

GPT is an optional presentation layer after deterministic comparison and human confirmation. ARIA
uses the OpenAI Responses API with a Pydantic structured-output schema, `store=false`, and the
current configurable default `gpt-5.6-sol`. OpenAI's current guidance describes GPT-5.6 model roles
and recommends the Responses API for reasoning workflows; Structured Outputs guarantees adherence
to a supplied schema and supports Pydantic parsing in the Python SDK. See the official
[GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6)
and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

The hosted request contains only:

- document title;
- opaque comparison, item, and confirming-review identifiers;
- deterministic change type;
- anchor canonical keys;
- bounded before/after public text and truncation flags.

It does not contain PDF bytes, R2 details, artifact hashes, source locators, extraction metadata,
review rationale, database credentials, or unconfirmed candidates. Output validation requires each
confirmed item exactly once, preserves its deterministic change type, and requires
`legal_effect_not_assessed=true`. Review current
[OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) before using hosted
summaries for non-public material.

Phase 3D.3 can queue this summary automatically only after every non-unchanged comparison item has
a current human decision and at least one item is confirmed. A failed summary remains isolated from
the deterministic comparison and can be retried explicitly; it never publishes a change event.

## Operations

```bash
docker compose exec api python manage.py audit_version_lineage --json
docker compose exec api python manage.py classify_version_lineage --json
docker compose exec api python manage.py project_structural_anchors --json
docker compose exec api python manage.py compare_versions --sync --json

docker compose exec api python manage.py summarize_comparison \
  --comparison <comparison-uuid> --sync --json
docker compose exec api python manage.py publish_reviewed_changes \
  --comparison <comparison-uuid> --json
```

Omit `--sync` from comparison or summary commands to use the isolated Celery `diff` queue.

Read-only API resources:

| Resource | Endpoint |
|---|---|
| Lineage assessments | `/api/v1/version-lineage/` |
| Structural anchors | `/api/v1/structural-anchors/` |
| Comparisons | `/api/v1/document-comparisons/` |
| Comparison items | `/api/v1/comparison-items/` |
| Reviews | `/api/v1/comparison-reviews/` |
| GPT summaries | `/api/v1/comparison-summaries/` |
| Reviewed publications | `/api/v1/reviewed-change-publications/` |

## Verified JPDP result

On 2026-08-04, the read-only audit found 57 versions across 20 active identities: 38 had direct
version evidence, 19 were safely reconstructable from one artifact/extraction lineage, and none
were ambiguous. Projection created 1,554 current anchors with zero empty text, invalid ranges, or
duplicate canonical keys; replay created zero records.

The corpus currently contains zero eligible temporal version pairs. Its multiple versions are
landing-page, official-file, OCR, or same-artifact re-extraction representations, so Phase 3C
correctly created zero comparisons and zero change events. A bounded synthetic live GPT request
validated `gpt-5.6-sol`, the Responses API, and structured parsing in 387 input and 79 output tokens
without writing a fake JPDP comparison. All 21 Phase 3C tests pass with external requests mocked.
The complete repository suite also passes: 63 tests, with clean Django system and migration checks,
Ruff formatting/lint, Compose configuration, and runtime health checks.
