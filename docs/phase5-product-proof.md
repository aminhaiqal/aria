# Phase 5 first product proof

## Goal

Phase 5 proves ARIA as a usable product by carrying an evidence-backed regulatory change through
human review, business-profile matching, explicit publication, and signed delivery. The phase also
requires real reader use and structured feedback from at least three non-staff pilot users.

The technical foundation remains evidence-first. A controlled rehearsal can verify the mechanics,
but only real pilot activity satisfies the user-validation criteria.

## Status report

Run the read-only report at any time:

```bash
make phase5-status
```

The JSON report evaluates four criteria:

- every enabled source is operational, healthy, and at 100 percent evidence coverage, with no
  workflow waiting at the quality-review gate;
- at least one reviewed impact has an exact business-profile match and a published delivery event;
- at least three active non-staff users have a business profile, use it in the reader, download
  immutable evidence, and provide structured feedback; and
- search, evidence-download, and pilot-feedback measurements exist.

The command reports `complete` only when all four criteria pass.

## Controlled product rehearsal

Run the isolated end-to-end acceptance test with:

```bash
make rehearse-product
```

The rehearsal creates two evidence-backed document versions in the test database, confirms the
exact textual change, generates and approves a controlled impact, matches a business profile,
publishes with a separate actor, and verifies an HMAC-signed webhook delivery. It never writes test
records to the production database or calls a real webhook.

## Reader measurements

Successful reader searches, document views, and evidence downloads create append-only audit events.
Search events store a SHA-256 digest and query length rather than raw query text. They also record
the search mode, profile selection, result counts, and warning count. Set
`ARIA_READER_USAGE_TRACKING=false` to disable these measurements.

## Pilot feedback

Create each pilot as an active non-staff Django account and let the participant create an
owner-scoped business profile in the reader. After a guided task, record the participant's exact
feedback through the guarded command:

```bash
docker compose exec api python manage.py record_pilot_feedback PILOT_USERNAME \
  --category evidence_clarity \
  --rating 5 \
  --comment "The exact source passage made the result easy to verify." \
  --recorded-by FACILITATOR_USERNAME \
  --confirm RECORD
```

Available categories are `search_usefulness`, `evidence_clarity`, `relevance_explanation`, and
`impact_actionability`. Ratings use a 1–5 scale. Feedback is append-only and attributed to both the
pilot account and the person recording it.

## Duplicate official pages

When two official landing pages expose the same normalized publication, preview the governed
identity resolution first:

```bash
docker compose exec api python manage.py supersede_duplicate_identity \
  SOURCE_IDENTITY_ID TARGET_IDENTITY_ID \
  --reason "Both official pages expose the same normalized publication and evidence." \
  --actor OPERATOR_USERNAME
```

Apply only after reviewing the identical content hash and both URLs:

```bash
docker compose exec api python manage.py supersede_duplicate_identity \
  SOURCE_IDENTITY_ID TARGET_IDENTITY_ID \
  --reason "Both official pages expose the same normalized publication and evidence." \
  --actor OPERATOR_USERNAME --apply --confirm SUPERSEDE
```

The source identity remains as evidence history and routes future observations into the selected
operational identity. The operation is idempotent, actor-attributed, and emits audit and pipeline
events.
