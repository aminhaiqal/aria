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
- the endpoint's current HTTP health and consecutive-failure state; and
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

The operator console shows current source reliability alerts and the latest assessment per source.
The source list adds a reliability state beside HTTP health. Source detail displays freshness,
candidate stability, R2/extraction/graph coverage, both embedding projections, and findings. The
provisioned `ARIA Source Reliability & Freshness` Grafana dashboard provides the time-series view:
per-source freshness headroom, scheduled polling, last-success age, stage coverage, failure counts,
and stable finding codes. Source slugs and endpoint UUIDs are its only source identifiers.

Assessment evidence is also available through the staff-only, read-only endpoint:

```text
/api/v1/source-reliability/
```

Phase 3D.10 adds `/console/confidence/` and `make source-confidence`. They combine the current
source-pack, admission, promotion, schedule, and dynamically collected pipeline coverage for every
source without writing a new assessment. The reported percentage is the mean completion ratio for
artifact preservation, extraction, graph projection, local embeddings, and the configured
embedding provider. It is operational evidence coverage, not legal or predictive confidence.

Phase 4A adds `make source-soak` and displays the same read-only acceptance state on the confidence
page. The gate observes the first post-admission scheduled run for both the legacy AGC browser
source and durably promoted sources such as Parliament. It checks scheduled completion, candidate
and artifact observation coverage, bounded fetch outcomes, next-poll advancement, and the exact
run's downstream reliability assessment. A missing future run is `pending`; it becomes `failed`
only after the configured freshness grace. Manual runs never satisfy this gate.

## Change rehearsal and verification

The isolated rehearsal exercises two safety boundaries: byte-level changes that normalize to the
same text stop before comparison/GPT work, while a material change must complete deterministic
comparison and human review before a GPT summary can be queued.

```bash
make rehearse-change
docker compose exec -T api python manage.py test tests.test_reliability
docker compose exec -T api python manage.py test
```

The next operational acceptance points are AGC's and Parliament's first autonomous daily cycles.
They must retain full coverage, avoid duplicate versions or embeddings, and return to `healthy`
within the pipeline grace period.

The post-deployment manual equivalent on 2026-08-06 completed with 20/20 HTTP 304 PDF checks and
left the corpus at 20 versions, 604 sections, 604 local embeddings, and 604 OpenAI embeddings.
JPDP's existing OCR derivative lineage was also recognized correctly, and its 133 missing OpenAI
projections were repaired; its warning then produced one explicit recovery event. The first
autonomous scheduled AGC cycle remains the soak boundary and is not claimed as observed yet.

Parliament's controlled acceptance on 2026-08-07 completed two matching connector-v2 runs with
25/25 artifacts, extractions, and graph projections plus 351/351 local and OpenAI embeddings. It
was promoted from exact durable gate evidence and now reports `healthy`. Its first autonomous daily
cycle is likewise not claimed until the scheduler actually executes it.

On 2026-09-02 AGC changed its rendered download links from direct PDF URLs to signed
`processFile.php` wrappers. Source-pack version 2 and connector version 2 add a narrowly configured
base64 URL/SHA-256 decoder. The decoded destination still has to pass the existing HTTPS, official
domain, exact path-prefix, and PDF-extension checks. A bounded verification run completed with
20 candidates and 20/20 artifact observations, after which the repair planner restored 1208/1208
local and OpenAI embeddings. JPDP's remaining projection was also repaired to 534/534. Fresh
append-only assessments recorded JPDP, AGC, and Parliament as healthy with complete current-run
coverage.
