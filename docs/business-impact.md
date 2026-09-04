# Phase 4C regulatory impact intelligence

## Evidence boundary

Phase 4C turns confirmed textual changes into reviewable business-impact intelligence. It does not
turn model output into law, infer legal effect automatically, or treat semantic similarity as proof
that a business is regulated.

The first implemented slice, Phase 4C.1, establishes two append-only records:

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

Both endpoints are read-only. The supported write path is the transactional domain service, which
locks the comparison item, verifies its current human confirmation, validates every evidence
relationship, and records the actor in the audit trail.

## Verification

Run the phase suite with:

```bash
docker compose run --rm api python manage.py test tests.test_phase4c
```
