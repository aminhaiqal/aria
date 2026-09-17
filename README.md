# ARIA Core

ARIA is an Autonomous Regulatory Intelligence Agent. This repository contains the evidence-first
foundation for discovering, retrieving, preserving, extracting, and indexing official regulatory
publications before any AI interpretation is introduced.

## What is implemented

- Registry hierarchy: authorities, publication collections, source endpoints, and versioned
  connector configuration
- Repository-backed, checksummed source packs with strict validation, read-only plans, guarded
  application, and immutable installed snapshots
- Scheduled and manual source runs with durable PostgreSQL state
- Idempotent discovered candidates and per-run observations
- A configurable static HTML listing connector, initially seeded for Malaysia's JPDP
- An opt-in JavaScript listing connector with a dedicated, resource-bounded Chromium worker
- Six-hour JPDP monitoring with immutable endpoint observations and conditional artifact retrieval
- Independently scheduled JPDP detail-page and approved RSS monitoring with immutable link snapshots
- An HTTPS client with domain allowlists, public-address validation, redirect revalidation,
  response limits, retries, conditional requests, and per-domain rate limiting
- Immutable, content-addressed raw artifacts with SHA-256 integrity checks
- Append-only provenance observations and durable fetch-attempt history
- Self-hosted filesystem artifact storage plus an optional S3-compatible Cloudflare R2 adapter
- Deterministic offline HTML and PDF extraction from archived bytes, with page and DOM locators
- BetterDocs detail-page routing to archived primary publications with stable landing-page identity
- Stable document identities, immutable content versions, and append-only evidence links
- A structural evidence graph from authority to raw artifact in PostgreSQL
- PostgreSQL full-text search plus pgvector retrieval with local and OpenRouter embedding providers
- Provenance-gated bilingual structural anchors and deterministic immutable-version comparisons
- Append-only human review, optional structured GPT summaries, and reviewed-change outbox events
- Deterministic, corpus-aware extraction quality runs with provenance-backed findings
- Durable changed-artifact orchestration with per-stage attempts, quality/lineage gates, recovery,
  review waits, and post-review GPT-summary dispatch
- A staff-only self-hosted operator console for source health, bounded polling, workflow recovery,
  before/after review, GPT summaries, explicit publication, and audit history
- A staff-only source-admission workbench with bounded pilot captures, immutable gate assessments,
  exact-evidence promotion, network traces, and repair previews
- Guarded disabled-source pilots and stale-safe admission for deterministic static listings
- A consolidated source-confidence view spanning schedules, admission, R2, extraction, graph, and
  embedding coverage
- An authenticated, self-hosted React/TypeScript/Vite reader built from repository-owned shadcn
  components, with responsive search, source filters, exact passage links, immutable evidence
  downloads, quality/version context, and labelled GPT summaries
- A separate active-user reader API and versioned JPDP/AGC/Parliament retrieval benchmark
- Immutable OCR derivatives with source/output hashes, toolchain records, and page metrics
- A dedicated self-hosted OCRmyPDF/Tesseract worker with Malay and English language data
- Explicit pipeline states from discovery through publication and failure handling
- Transactional pipeline events and an outbox ready for a future delivery transport
- Append-only audit records
- Django Admin operations and read-only DRF registry, evidence, graph, and search APIs
- Celery worker queues separated by workload type, including isolated browser and OCR workers
- Liveness and database/Redis readiness probes
- A self-hosted Docker Compose stack using pgvector-enabled PostgreSQL and Redis

Legal-relationship extraction and external outbox delivery remain for later phases. Browser and OCR
outputs are explicit derivatives and never replace or mutate the official source artifact.

## Current milestone

- Phase 1 registry and pipeline foundation: complete
- Phase 2 JPDP retrieval pilot: complete
- Phase 3A deterministic extraction and evidence graph: implemented
- Phase 3B extraction quality and JPDP linked-file remediation: implemented
- Phase 3C evidence-backed structural version comparison: implemented
- Phase 3D.0 durable source monitoring contract: implemented
- Phase 3D.1 scheduled conditional JPDP retrieval: implemented
- Phase 3D.2 independent detail-page/RSS monitoring and safe reconciliation: implemented
- Phase 3D.3 evidence-gated downstream change orchestration: implemented
- Phase 3D.5 self-hosted operator console: implemented
- Phase 3D.6 bounded self-hosted browser retrieval: implemented
- Phase 3D.7 controlled AGC JavaScript-source admission: complete and governed by three-hour polling
- Phase 3D.8 source reliability, transition alerts, and repair planning: implemented
- Phase 3D.9 governed source packs and source-admission workbench: implemented
- Phase 3D.10 guarded static-source admission and multi-source confidence: implemented
- Phase 3F bounded self-hosted OCR completion: implemented
- Phase 3G measured hybrid semantic retrieval: implemented
- Phase 4A authenticated reader search and evidence interface: implemented
- Phase 4B self-hosted React, TypeScript, Vite, and shadcn reader: implemented
- Phase 4C.0 audited monitored-resource retirement: implemented
- Phase 4C.1 evidence-bound regulatory impact records: implemented
- Phase 4C.2 versioned business applicability taxonomy: implemented
- Phase 4C.3 deterministic and GPT impact candidates: implemented
- Phase 4C.4 append-only human impact review console: implemented
- Phase 4C.5 deterministic business-profile matching: implemented
- Phase 4C.6 owner-scoped reader relevance and evidence presentation: implemented
- Phase 4C.7 explicit reviewed-impact publication and signed delivery: implemented
- Phase 4D.1 source-structure drift quarantine and approved selector fallbacks: implemented
- Phase 4D.2 encrypted PostgreSQL backup, R2 copy, and disposable restore drill: implemented
- Phase 4D.3 least-privilege operator roles, login throttling, and two-person release: implemented
- Phase 4D.4 private metrics, self-hosted Grafana scorecard, correlation IDs, heartbeat, and alerts: implemented
- Phase 4D.5 immutable base images, hashed Python locks, production isolation, SBOM, and local
  vulnerability scanning: implemented
- Phase 4D.6 fail-closed production readiness policy and unified release gate: implemented
- Phase 2 source breadth: JPDP, AGC Updated Principal Acts, and Parliament Dewan Rakyat bills are
  operational with complete R2, extraction, graph, local-vector, and hosted-vector coverage
- Operational acceptance still pending: observe the first autonomous AGC and Parliament daily
  cycles through the durable soak gate; manual pilot success is not presented as autonomous proof
- Next implementation step: satisfy the production readiness gate, expose a private Cloudflare
  preview, and collect evidence-reader feedback

## Local setup

Requirements: Docker Engine with Docker Compose.

```bash
make start
make superuser
```

`make start` creates `.env` from `.env.example` when it is absent, then builds and waits for the
lightweight reader stack: PostgreSQL, Redis, migrations, compiled React assets, and the Django API.
It deliberately leaves scheduled monitoring, OCR, and Chromium stopped. Start those only when the
corresponding local workflow is needed:

```bash
make start-workers  # normal ingestion worker and scheduler
make start-ocr      # OCR worker
make start-browser  # bounded Chromium worker
make start-all      # complete stack in one command
```

Open:

- Reader interface: <http://127.0.0.1:8000/reader/>
- Operator console: <http://127.0.0.1:8000/console/>
- Admin: <http://127.0.0.1:8000/admin/>
- Liveness: <http://127.0.0.1:8000/health/live/>
- Readiness: <http://127.0.0.1:8000/health/ready/>
- Read-only API: <http://127.0.0.1:8000/api/v1/>
- Reader API: <http://127.0.0.1:8000/api/reader/v1/>

The registry API requires an administrator session or Basic authentication. The reader and its
separate API accept any active Django account, while the operator console remains staff-only.
PostgreSQL and Redis are reachable only on the private Compose network. Change all `change-me`
values before using this outside local development.

## Common commands

```bash
make start           # build and start the lightweight reader/API stack
make start-workers   # start normal ingestion and scheduled monitoring
make start-ocr       # start the opt-in OCR worker
make start-browser   # start the opt-in bounded Chromium worker
make start-all       # build and start every service (also available as make bootstrap)
make status          # show container and health status
make health          # call Django's database/Redis readiness probe
make logs-core       # follow API, PostgreSQL, and Redis logs
make logs            # follow API and all worker logs
make stop-heavy      # stop OCR and browser workers while leaving the reader running
make stop            # stop the stack without deleting persistent volumes
make lint            # lint Python in the dependency-hashed test image
make test            # run the Django test suite in the dependency-hashed test image
make frontend-test   # install deterministically, lint, test with coverage, and build the reader
make frontend-build  # rebuild and publish the hashed reader bundle into the Compose volume
make reader-e2e      # test login, shadcn interaction, CSP, and logout in Chromium
make release-check   # run the complete local build, test, scan, and SBOM gate
make production-readiness # verify hardened settings, live dependencies, roles, migrations, and R2
make vps-start       # start the hardened VPS deployment on the external edge network
make vps-readiness   # run live policy, dependency, role, migration, and R2 probes
make vps-stop        # stop the VPS deployment without deleting persistent volumes
make makemigrations  # generate model migrations
make migrate         # apply migrations
make list-source-packs # list validated repository source contracts
make plan-source-pack PACK=<slug> # preview a source-pack reconciliation without writes
make apply-source-pack PACK=<slug> # explicitly apply the reviewed plan and snapshot it
make pilot-source SOURCE=<slug> # run one disabled, unscheduled static-source pilot
make audit-static SOURCE=<slug> # record immutable static-source admission gates
make promote-static ASSESSMENT_ID=<uuid> # enable only the exact current ready evidence
make source-confidence # report admission and end-to-end coverage for every source
make source-soak      # report first autonomous AGC/Parliament cycle acceptance
make poll-jpdp       # queue one bounded manual JPDP monitoring cycle
make seed-agc        # register/update the disabled AGC JavaScript pilot
make pilot-agc       # queue one manual AGC run without enabling its schedule
make audit-agc       # record and print the AGC admission gates
make promote-agc     # enable daily AGC polling only after every gate passes
make assess-sources  # record current freshness and end-to-end coverage
make repair-source   # print an idempotent AGC repair plan without changing state
make repair-source-apply # explicitly queue the unchanged repair plan
make rehearse-change # isolated unchanged/change/review/GPT-gate regression drill
make poll-resource RESOURCE_ID=<uuid> # queue one approved detail/feed resource check
make extract         # replay every archived artifact through extraction locally
make route-linked    # route and queue official files linked by archived JPDP pages
make plan-ocr        # list the deterministic OCR input set and current statuses
make ocr             # process OCR synchronously inside the isolated OCR container
make embed-openrouter # populate missing OpenRouter vectors for the current JPDP corpus
make verify-openrouter # make one structured-output and one embedding connectivity probe
make evaluate-embeddings # compare local and OpenRouter vector retrieval on the JPDP benchmark
make evaluate-reader  # require hit@3 across JPDP, AGC, and Parliament reader cases
make quality         # assess the current JPDP extraction corpus without network access
make audit-lineage   # read-only audit of version provenance and representation lineage
make classify-lineage # persist append-only lineage assessments
make anchors         # project current bilingual legal anchors
make compare         # compare every eligible temporal version pair deterministically
make summarize COMPARISON_ID=<uuid> # summarize confirmed deltas with structured GPT output
make publish-reviewed COMPARISON_ID=<uuid> # create idempotent reviewed-change outbox events
make audit-orchestrations # report changed-observation coverage and orchestration state
make retry-orchestration ORCHESTRATION_ID=<uuid> # explicitly retry failed/ready work
make verify-storage  # write/read/delete one temporary R2 probe
make superuser       # create an admin user
make down             # stop services without deleting data
```

Seed and run the first official source:

```bash
docker compose exec api python manage.py seed_jpdp --run
```

The command registers the Personal Data Protection Commissioner, Malaysia (JPDP), its Act 709
publication collection, versioned connector configuration, and explicitly approved English RSS
feed before queueing one manual run. Existing proven detail pages are backfilled into the resource
registry; later listing runs add newly observed detail pages automatically.
Discovery completion means the candidates have been persisted and their fetch tasks have been
queued; inspect fetch attempts or worker logs until every candidate reaches a terminal state.

## Cloudflare boundary

No cloud dependency is required: raw bytes use the `artifact-data` Docker volume by default. To
use the prepared Cloudflare account, set `ARIA_OBJECT_STORAGE_BACKEND=s3` and supply the R2 S3 API
endpoint, bucket, access key, and secret in `.env`. Raw artifact bytes are archived before any
extraction and are never mutated. Normalized records, graph edges, full-text indexes, and vectors
remain in the self-hosted PostgreSQL service.

OpenRouter-hosted embeddings are optional. When selected, ARIA sends normalized section headings
and text, plus hosted-vector search queries, through OpenRouter. Requests require Zero Data
Retention routing and deny provider data collection by default. Source PDFs, R2 credentials,
provenance records, and graph data remain local. The deterministic `local_hash` provider remains
available as an offline fallback.

See [Foundation architecture](docs/foundation.md), [Phase 2 retrieval](docs/retrieval.md),
[Phase 3A extraction and evidence graph](docs/knowledge-graph.md),
[Phase 3B extraction quality](docs/quality.md),
[Phase 3C version comparison](docs/version-comparison.md),
[Phase 3D source monitoring](docs/monitoring.md),
[Phase 3D.3 change orchestration](docs/orchestration.md),
[Phase 3D.5 operator console](docs/operator-console.md),
[Phase 3D.6 browser retrieval](docs/browser-retrieval.md),
[Phase 3D.7 AGC browser pilot](docs/browser-retrieval.md#phase-3d7-first-official-source-pilot),
[Phase 3D.8 source reliability](docs/reliability.md),
[Phase 3D.9 source admission](docs/source-admission.md),
[Phase F self-hosted OCR](docs/ocr.md), and
[Phase G hybrid embeddings](docs/embeddings.md), and
[Phase 4 reader interface](docs/reader-interface.md), and
[Phase 4C regulatory impact intelligence](docs/business-impact.md) for design decisions and operating
instructions.
