# Phase 3D.8 source reliability and repair

Phase 3D.8 evaluates whether an official source is useful end to end, rather than treating a
successful listing request as sufficient health. The evaluator is self-hosted, reads only ARIA's
PostgreSQL evidence, and runs every 15 minutes on the normal discovery worker.

## Durable assessment contract

Each `SourceReliabilityAssessment` is append-only and linked to the latest completed endpoint
`SourceRun`. It records:

- the freshness deadline derived from the scheduled next poll plus a bounded grace period;
- the canonical candidate-set SHA-256 and whether it changed from the prior completed run;
- candidate-level R2 artifact, successful extraction, and knowledge-graph coverage;
- document-version and normalized-section totals;
- current local and configured-provider embedding coverage;
- browser-capture and bounded-network evidence for JavaScript sources; and
- structured findings with stable codes, severities, and operator-facing details.

Statuses are `healthy`, `processing`, `warning`, `critical`, or `disabled`. Missing downstream
records during the configured pipeline grace period are `processing`, not alerts. A transition
into `warning` or `critical` emits `source.reliability.alert`; a later healthy state emits
`source.reliability.recovered`. Identical periodic assessments reuse the existing signature, so
the 15-minute schedule does not create duplicate snapshots or events.

The defaults are:

```text
ARIA_SOURCE_FRESHNESS_GRACE_MINUTES=60
ARIA_SOURCE_PIPELINE_GRACE_MINUTES=30
```

## Repair boundary

`repair_source_pipeline` is read-only by default. It builds a deterministic plan for the latest
completed source run and can propose only four bounded actions:

- retry a candidate that has no artifact and no successful terminal fetch;
- extract an archived artifact that has no successful or active extraction; or
- OCR an artifact whose deterministic extraction classified it as image-only; or
- re-project a known document version whose graph or current embeddings are incomplete.

A terminal fetch with missing artifact evidence or a permanent OCR failure is marked
`manual_review` and cannot be queued. A successful OCR derivative counts toward its original
candidate's extraction, graph, version, section, and embedding coverage.
Applying repairs requires both `--apply` and `--confirm`; the command re-builds the plan under an
endpoint row lock and rejects it if the fingerprint changed. Every accepted repair plan creates
audit and pipeline events.

```bash
make assess-sources
make repair-source
make repair-source-apply
```

For a non-AGC endpoint, pass its UUID directly to either management command.

## Operator surfaces

The dashboard shows current source reliability alerts and the latest assessment per source. The
source list adds a reliability state beside HTTP health. Source detail displays freshness,
candidate stability, R2/extraction/graph coverage, both embedding projections, and findings.

Assessment evidence is also available through the staff-only, read-only endpoint:

```text
/api/v1/source-reliability/
```

## Change rehearsal and verification

The isolated rehearsal exercises two safety boundaries: byte-level changes that normalize to the
same text stop before comparison/GPT work, while a material change must complete deterministic
comparison and human review before a GPT summary can be queued.

```bash
make rehearse-change
docker compose exec -T api python manage.py test tests.test_reliability
docker compose exec -T api python manage.py test
```

The next operational acceptance point is AGC's first scheduled daily cycle. It must retain full
coverage, use conditional PDF retrieval without duplicate versions or embeddings, and return to
`healthy` within the pipeline grace period before another official corpus is admitted.

The post-deployment manual equivalent on 2026-08-06 completed with 20/20 HTTP 304 PDF checks and
left the corpus at 20 versions, 604 sections, 604 local embeddings, and 604 OpenAI embeddings.
JPDP's existing OCR derivative lineage was also recognized correctly, and its 133 missing OpenAI
projections were repaired; its warning then produced one explicit recovery event. The first
autonomous scheduled AGC cycle remains the soak boundary and is not claimed as observed yet.
