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

## Deterministic business-profile matching

Phase 4C.5 represents an organization as an owner-scoped `BusinessProfile` containing terms from
exactly one installed taxonomy version. Profile edits pass through a transactional service that
validates ownership, rejects mixed taxonomy versions, limits jurisdiction, organization type, and
size to one value each, and writes an actor-attributed audit event.

The `aria-exact-applicability-v1` matcher evaluates only the latest `approved` or `amended` impact
review while its underlying source confirmation remains current. It does not use embeddings, GPT,
aliases, or fuzzy text. Included terms are grouped by dimension and matched exactly; the declared
`cross-sector` and `any-size` terms are the only wildcard rules. An exact reviewed exclusion wins
over inclusions.

Every evaluation is an append-only `ProfileImpactMatch` with one of three outcomes: `matched`,
`not_matched`, or `insufficient_context`. It snapshots the complete profile, reviewed wording,
controlled targets, taxonomy checksum, ruleset, matched/excluded terms, and unresolved or unmet
dimensions. Its content fingerprint makes identical replays idempotent while preserving an earlier
explanation after the mutable profile is edited. This is an explainable relevance screen, not a
legal determination.

## Reader presentation

Phase 4C.6 makes the relevance layer usable in the self-hosted React reader. Any active account can
create an owner-scoped profile from the current controlled taxonomy. The CSRF-protected profile
write immediately runs a bounded deterministic evaluation; an explicit re-evaluate endpoint is
also available when reviewed impacts are added later.

Selecting a profile adds an exact-relevance filter after ordinary textual retrieval. Search results
must have at least one current `matched` evaluation and show the matched profile plus reviewed
impact count. The document view renders a "why this matters" evidence spine with human-reviewed
wording, matched controlled terms, exact before/after anchor text, artifact and anchor hashes, and
source locators. Without a profile, current approved/amended impacts remain inspectable but are not
presented as organization-specific matches. Stale source confirmation or a later rejection or
needs-context review removes the impact from reader presentation without deleting its history.

## Reviewed publication and optional delivery

Phase 4C.7 adds a second explicit release boundary after review. A staff operator must type
`PUBLISH` for the latest approved/amended review while its source confirmation is still current.
The transaction creates one append-only `ReviewedImpactPublication`, one
`regulatory.impact.confirmed` pipeline event, one durable outbox row, and one actor-attributed audit
record. Repeating the exact action is idempotent. A later amended review is a new decision and may
produce its own publication without replacing the earlier record.

The event snapshots reviewed wording, the comparison and version identifiers/hashes, every
controlled target and taxonomy checksum, and exact before/after anchor text, hashes, and locators.
`legal_effect_assessed` remains false. Owner-scoped business profiles and match snapshots are
deliberately excluded from the outbound payload.

Webhook delivery is self-hosted and disabled by default. When configured, the ordinary worker and
beat services claim only outbox rows backed by a `ReviewedImpactPublication`. Delivery uses a public
HTTPS allowlist, pins the validated DNS address while preserving TLS SNI, rejects credentials,
non-standard ports, redirects, and private/non-global addresses, and ignores forged lookalike
topics. The canonical JSON body is signed with HMAC-SHA256 using the event timestamp and includes a
stable event ID for receiver deduplication. Transient failures retry with bounded backoff; permanent
or unsafe destinations exhaust the configured attempt budget.

```dotenv
ARIA_IMPACT_WEBHOOK_URL=https://hooks.example.com/aria/impact
ARIA_IMPACT_WEBHOOK_ALLOWED_DOMAINS=hooks.example.com
ARIA_IMPACT_WEBHOOK_SECRET=replace-with-at-least-32-random-characters
```

The destination should return any 2xx status only after durably accepting the event. Leaving the URL
blank keeps publication events pending without generating delivery failures. The same explicit
publication can be invoked from the command line with:

```bash
make publish-impact REVIEW_ID=<approved-review-uuid> PUBLISHER=<active-username>
```

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
| Business profiles with controlled terms | `/api/v1/business-profiles/` |
| Append-only deterministic evaluations | `/api/v1/profile-impact-matches/` |
| Explicit reviewed-impact publications | `/api/v1/reviewed-impact-publications/` |

These endpoints are read-only. The supported write paths are transactional domain services, which
lock their subject, verify current human confirmation, validate every evidence relationship, and
record the actor in the audit trail.

## Verification

Run the phase suite with:

```bash
docker compose run --rm api python manage.py test tests.test_phase4c
```
