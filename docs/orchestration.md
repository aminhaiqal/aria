# Phase 3D.3 evidence-gated change orchestration

## Scope

Phase 3D.3 connects continuous official-source monitoring to the existing deterministic evidence
pipeline. A changed HTTP payload starts a durable workflow; it does not itself assert that the
official publication or its legal meaning changed.

```text
changed ArtifactObservation
  -> verify archived size + SHA-256
  -> deterministic extraction -> immutable DocumentVersion evidence
  -> equal normalized content? stop: no_content_change
  -> extraction quality gate
  -> evidence graph + local vectors (+ configured hosted vectors)
  -> provenance lineage + bilingual structural anchors
  -> eligible deterministic comparison
  -> material candidates? wait for human review
  -> optional structured GPT summary after review completion
  -> completed (publication remains a separate explicit action)
```

Browser retrieval, claims about legal effect, automatic publication, and external outbox delivery
remain outside this phase.

## Durable state and idempotency

Every changed artifact observation has at most one `ChangeOrchestration`. Its idempotency key hashes
the orchestration strategy, observation ID, and immutable artifact SHA-256. Unchanged and HTTP 304
observations never create one.

The mutable orchestration records the current stage, terminal/waiting status, linked extraction,
version, quality, lineage, comparison, and summary records, heartbeat, retry count, and bounded
error details. `OrchestrationStepAttempt` is append-only and records a canonical input hash,
outcome, output identifiers/counts, timestamps, and any error for each attempt. Replaying a
terminal workflow creates no versions, comparisons, or step attempts.

Terminal and waiting outcomes are explicit:

- `no_content_change`: raw bytes changed but normalized document text did not;
- `quality_review_required`: extraction evidence needs human quality review and was not promoted;
- `lineage_rejected`: provenance was quarantined, so comparison was blocked;
- `review_required`: deterministic material candidates await decisions;
- `summary_pending` / `summary_failed`: hosted presentation work is isolated from the evidence
  result;
- `completed`: baseline, non-material comparison, reviewed-without-summary, or summary completion;
- `failed`: artifact, extraction, or downstream processing failed and requires explicit retry.

## Gates and hosted processing

Extraction creates immutable version evidence without projecting a new changed version into the
knowledge graph. Quality must pass or warn before graph edges and local vectors are created. The
configured non-local embedding provider is then dispatched through the existing normalization
queue.

Comparisons remain deterministic and representation-safe. Any material item stops the workflow at
human review. ARIA checks the current decision for every non-unchanged item. If all are reviewed,
at least one is confirmed, and `ARIA_AUTO_GPT_SUMMARIES=true`, one structured GPT summary task is
queued. The summary receives only the already bounded, confirmed comparison input described in
[Phase 3C version comparison](version-comparison.md). It cannot change classifications or publish
an event. With `OPENAI_API_KEY` populated, automatic summaries default on unless explicitly
disabled; `.env.example` keeps the feature off for deliberate local setup.

## Recovery and OCR

Celery beat scans every five minutes. Pending, OCR-ready, and stale queued/running workflows are
atomically marked `queued` under row locks before dispatch, preventing repeated scheduler passes
from enqueueing the same work. Fresh running work is never stolen. Retryable worker exceptions use
bounded exponential backoff; an exhausted failure remains visible until an operator retries it.

OCR retains the official PDF as evidence. The workflow waits while the dedicated OCR worker creates
immutable searchable-PDF and text-sidecar derivatives. Derivative extraction creates version
evidence without graph promotion, then resumes the original orchestration at the quality gate.

## Operations

```bash
# Read-only coverage, status, and stale-work audit.
docker compose exec api python manage.py audit_orchestrations

# Preview historical changed observations that predate Phase 3D.3. This writes nothing.
docker compose exec api python manage.py backfill_orchestrations --limit 100

# Deliberately create historical workflows; add --queue only after reviewing the preview.
docker compose exec api python manage.py backfill_orchestrations --limit 100 --apply
docker compose exec api python manage.py backfill_orchestrations --limit 100 --apply --queue

# Retry exactly one failed or OCR-ready workflow.
docker compose exec api python manage.py retry_orchestration <orchestration-uuid>
```

Historical backfill is never automatic. This prevents deployment from converting old changed-byte
observations into a burst of comparisons without an operator-selected temporal order.

Administrator-only, read-only workflow records and nested step attempts are available at
`/api/v1/change-orchestrations/` and in Django Admin. Reviews remain writable only through the
existing controlled review paths.

## Verification

The Phase 3D.3 suite covers artifact integrity failure and retry, raw-byte/normalized-text
suppression, quality gating before graph projection, baseline completion, deterministic comparison,
terminal replay, recovery deduplication, review-complete summary dispatch, and the read-only API.
The complete repository suite passes 93 tests with external requests mocked, plus Django system and
migration checks and Ruff formatting/lint.

The controlled 2026-08-06 deployment applied the initial orchestration migration and restarted the
self-hosted API, general worker, OCR worker, and beat services healthy. A read-only audit found 158
historical changed-byte observations predating this phase and zero workflows; the backfill preview
listed three examples but intentionally wrote nothing. The selected live JPDP detail page first
recorded a resource-level link-snapshot change with no accepted artifact, so it created no artifact
observation or workflow. An immediate second HTTP 200 check had identical content and link-set
hashes and recorded `unchanged`. Artifact observations remained 264, workflows remained zero,
versions remained 70, and comparisons remained zero. This demonstrates both deployment safety and
the boundary that resource-page churn alone cannot create a regulatory comparison.
