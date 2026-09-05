# Phase 4C regulatory impact intelligence

## Evidence boundary

Phase 4C turns confirmed textual changes into reviewable business-impact intelligence. It does not
turn model output into law, infer legal effect automatically, or treat semantic similarity as proof
that a business is regulated.

Phase 4C.1 establishes two append-only records:

- `RegulatoryImpact` records a candidate statement, its type and origin, the exact comparison item,
  and the human confirmation review that made impact analysis eligible; and
- `ImpactEvidence` snapshots each available before/after structural anchor together with its
  normalized section, official artifact, artifact SHA-256, anchor and section hashes, exact anchor
  text, and source locator.

A candidate can be created only while the latest review of a completed, non-unchanged comparison
item is `confirmed`. Rejection or `needs_context` prevents generation. Once written, neither the
candidate nor its evidence snapshots can be updated or deleted. Replaying identical input reuses
the same fingerprinted candidate and does not duplicate its audit event.

`legal_effect_assessed` remains false. Candidate creation emits an internal audit record but no
publication or delivery event. Later slices add controlled applicability terms, extraction,
separate human impact review, deterministic profile matching, reader presentation, and reviewed
delivery in that order.

## Versioned applicability vocabulary

Phase 4C.2 adds the repository-backed `aria-my-business-applicability` taxonomy. Every applied
version stores its canonical JSON definition and SHA-256 checksum. Its append-only terms cover six
explicit dimensions: jurisdiction, sector, organization type, regulated role, activity, and size.
An `ImpactTarget` may reference only one of these versioned terms and must retain an included or
excluded disposition, its origin, and a written rationale. Free-text target labels cannot enter the
matching path.

The initial vocabulary is deliberately small and ARIA-maintained. Its stored disclaimer states
that it is not an official legal classification. A changed definition cannot replace an installed
`slug + version`; maintainers must publish a new version, which preserves the exact terms used by
older impact candidates.

Validate and preview the repository definition without writing:

```bash
make plan-impact-taxonomy
```

Apply it through the explicit gate:

```bash
make apply-impact-taxonomy
```

The underlying command requires both `--apply` and the exact `--confirm APPLY`. Reapplication of
identical bytes is idempotent and does not duplicate taxonomy terms or its audit event.

## Candidate extraction

Phase 4C.3 adds two candidate providers behind one evidence contract:

- `deterministic` applies a small, versioned ruleset for explicit obligation, reporting,
  registration, deadline, prohibition, penalty, exemption, permission, governance, and
  record-keeping wording; and
- `openai` uses the Responses API with a strict Pydantic structured-output schema to propose
  concise statements and controlled targets.

Both providers accept exactly one currently confirmed comparison item and one installed taxonomy
version. Input snapshots contain bounded before/after anchor text plus anchor, artifact, review,
and taxonomy IDs/hashes. Output is rejected unless it preserves the item and review IDs, cites
every available exact anchor once, keeps the legal-effect flag false, and uses only supplied
taxonomy terms. Schema-valid GPT output still passes these application-level evidence checks before
any impact record is written. This follows the [official OpenAI Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs)
while retaining ARIA's stricter domain validation.

Every attempt is represented by a mutable `ImpactGeneration` execution record with provider,
model, prompt version, input hash and snapshot, output, token counts, response ID, and terminal
status. The resulting impact, evidence, and target records remain append-only. Invalid output marks
the attempt failed and the materialization transaction leaves no partial candidates.

Generate deterministic candidates synchronously for an exact confirmed item:

```bash
make extract-impacts ITEM_ID=<comparison-item-uuid>
```

Select GPT explicitly when desired:

```bash
make extract-impacts ITEM_ID=<comparison-item-uuid> PROVIDER=openai
```

Asynchronous command calls use the existing self-hosted `diff` worker queue. Candidate generation
does not publish, notify, or bypass the separate human impact-review phase.

## Human impact review

Phase 4C.4 adds a staff-only review desk at `/console/impacts/`. Its detail view keeps the full
evidence spine visible in one place: the confirmed textual change, exact before/after anchors and
artifact hashes, proposed impact wording, controlled applicability targets, and decision history.

Reviewers may approve the candidate, amend its wording and targets, reject it, or request more
context. Every decision is a new append-only `ImpactReview`; amendments and approvals snapshot the
exact controlled terms used at that moment in append-only `ImpactReviewTarget` records. Later
decisions link to their predecessor instead of replacing history. A source-change confirmation that
has since been superseded makes the candidate ineligible for review and requires regeneration from
the current evidence.

The console mutation is POST-only, CSRF-protected, actor-attributed, and transactionally locks the
candidate. Review remains a governance boundary: an approval does not publish or deliver the impact,
and `legal_effect_assessed` remains false.

## Candidate types

The controlled first vocabulary is: obligation, reporting, registration, deadline, prohibition,
penalty, exemption, permission, governance, record keeping, and other. These describe the proposed
business-impact category, not a legal conclusion.

## Administrative inspection

Staff can inspect the append-only records in Django Admin or the administrator-only read APIs:

| Record | Endpoint |
|---|---|
| Impact candidates with nested evidence | `/api/v1/regulatory-impacts/` |
| Individual evidence snapshots | `/api/v1/impact-evidence/` |
| Versioned taxonomy snapshots | `/api/v1/applicability-taxonomies/` |
| Controlled applicability terms | `/api/v1/applicability-terms/` |
| Candidate-to-term links | `/api/v1/impact-targets/` |
| Deterministic/GPT execution records | `/api/v1/impact-generations/` |
| Append-only human impact reviews | `/api/v1/impact-reviews/` |
| Reviewed controlled-term snapshots | `/api/v1/impact-review-targets/` |

These endpoints are read-only. The supported write paths are transactional domain services, which
lock their subject, verify current human confirmation, validate every evidence relationship, and
record the actor in the audit trail.

## Verification

Run the phase suite with:

```bash
docker compose run --rm api python manage.py test tests.test_phase4c
```
