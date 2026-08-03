# ARIA Core

ARIA is an Autonomous Regulatory Intelligence Agent. This repository currently contains the
Layer 1 foundation: evidence-first infrastructure for discovering and tracking official
regulatory publications before any AI interpretation is introduced.

## What is implemented

- Registry hierarchy: authorities, publication collections, source endpoints, and versioned
  connector configuration
- Scheduled and manual source runs with durable PostgreSQL state
- Idempotent discovered candidates and per-run observations
- Explicit pipeline states from discovery through publication and failure handling
- Transactional pipeline events and an outbox ready for a future delivery transport
- Append-only audit records
- Django Admin operations and read-only DRF registry APIs
- Celery worker queues separated by workload type
- Liveness and database/Redis readiness probes
- A self-hosted Docker Compose stack using PostgreSQL and Redis

Actual source connectors, artifact storage, extraction, OCR, normalization, document identity,
and versioning are intentionally reserved for the next phases.

## Local setup

Requirements: Docker Engine with Docker Compose.

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api python manage.py createsuperuser
```

Open:

- Admin: <http://127.0.0.1:8000/admin/>
- Liveness: <http://127.0.0.1:8000/health/live/>
- Readiness: <http://127.0.0.1:8000/health/ready/>
- Read-only API: <http://127.0.0.1:8000/api/v1/>

The API requires an administrator session or Basic authentication. PostgreSQL and Redis are only
reachable on the private Compose network. Change all `change-me` values before using this outside
local development.

## Common commands

```bash
make bootstrap       # create .env if absent, build, and start
make logs            # follow API and worker logs
make test            # run the Django test suite in Compose
make makemigrations  # generate model migrations
make migrate         # apply migrations
make superuser       # create an admin user
make down             # stop services without deleting data
```

## Cloudflare boundary

No cloud dependency is required for the Phase 1 stack. Object-storage settings are reserved in
`.env.example`; Phase 2 can point the S3-compatible artifact adapter at Cloudflare R2. Raw artifact
bytes must be archived before extraction and must never be mutated.

See [Foundation architecture](docs/foundation.md) for design decisions and the next implementation
slice.
