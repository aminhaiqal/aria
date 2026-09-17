.DEFAULT_GOAL := help
ARIA_SBOM_VERSION ?= $(shell git rev-parse --verify HEAD)
VPS_COMPOSE = docker compose -f compose.yaml -f compose.production.yaml -f compose.vps.yaml

.PHONY: help ensure-env start start-workers start-ocr start-browser start-all start-observability stop stop-heavy status health logs-core bootstrap build up down logs migrate makemigrations test lint check frontend-build frontend-test frontend-format reader-e2e shell superuser list-source-packs plan-source-pack apply-source-pack plan-impact-taxonomy apply-impact-taxonomy extract-impacts publish-impact pilot-source audit-static promote-static source-confidence source-soak poll-jpdp seed-agc pilot-agc audit-agc promote-agc assess-sources repair-source repair-source-apply rehearse-change poll-resource extract route-linked plan-ocr ocr embed-openrouter verify-openrouter evaluate-embeddings evaluate-reader quality audit-lineage classify-lineage anchors compare summarize publish-reviewed audit-orchestrations retry-orchestration verify-storage backup backup-verify restore-drill observability-check supply-chain-check security-scan sbom production-readiness release-check vps-start vps-status vps-readiness vps-stop

help:
	@printf '%s\n' \
		'make start          Start the lightweight local reader' \
		'make start-workers  Start ingestion and scheduled monitoring' \
		'make start-ocr      Start OCR processing' \
		'make start-browser  Start bounded Chromium processing' \
		'make start-all      Start the complete stack' \
		'make start-observability  Start private Prometheus and Grafana' \
		'make backup         Create an encrypted local backup (optionally upload to R2)' \
		'make supply-chain-check  Verify immutable dependencies and production isolation' \
		'make security-scan  Scan source and dependency locks for high-risk findings' \
		'make sbom           Generate build/aria-sbom.spdx.json' \
		'make production-readiness  Probe a hardened deployment before exposure' \
		'make release-check  Run the complete local release gate' \
		'make vps-start      Start the hardened VPS deployment' \
		'make vps-status     Show hardened VPS service status' \
		'make vps-readiness  Run live VPS policy and R2 probes' \
		'make vps-stop       Stop VPS services without deleting data' \
		'make status         Show service health' \
		'make health         Check application readiness' \
		'make stop           Stop everything without deleting data'

ensure-env:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example; review it before non-local use.")

# Lightweight local reader: database, Redis, migrations, frontend build, and API.
start: ensure-env
	docker compose up --build -d --wait --wait-timeout 180 postgres redis api

# Normal ingestion and scheduled monitoring. Start only when those workflows are needed.
start-workers: ensure-env
	docker compose up --build -d --wait --wait-timeout 180 worker beat

# Resource-intensive workers remain opt-in for local development.
start-ocr: ensure-env
	docker compose up --build -d --wait --wait-timeout 180 ocr-worker

start-browser: ensure-env
	docker compose up --build -d --wait --wait-timeout 180 browser-worker

start-all: ensure-env
	docker compose up --build -d --wait --wait-timeout 180

start-observability: ensure-env
	@test -n "$(ARIA_METRICS_TOKEN)" || rg -q '^ARIA_METRICS_TOKEN=.+$$' .env || (echo "Set ARIA_METRICS_TOKEN in the environment or .env" && exit 1)
	@test -n "$(ARIA_GRAFANA_ADMIN_PASSWORD)" || rg -q '^ARIA_GRAFANA_ADMIN_PASSWORD=.+$$' .env || (echo "Set ARIA_GRAFANA_ADMIN_PASSWORD in the environment or .env" && exit 1)
	docker compose --profile observability up -d --wait --wait-timeout 180 prometheus grafana

stop:
	docker compose down

stop-heavy:
	docker compose stop ocr-worker browser-worker

status:
	docker compose ps

health:
	docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready/', timeout=5).read().decode())"

logs-core:
	docker compose logs -f api postgres redis

bootstrap: start-all

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f api worker browser-worker ocr-worker beat

migrate:
	docker compose run --rm migrate

makemigrations:
	docker compose run --rm api python manage.py makemigrations

test:
	docker compose --profile tools run --rm --build test python manage.py test

lint:
	docker compose --profile tools run --rm --build --no-deps test ruff check src tests scripts manage.py

check:
	docker compose run --rm api python manage.py check

frontend-build:
	docker compose build frontend-assets
	docker compose run --rm frontend-assets

frontend-test:
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 npm ci
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 npm run lint
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 npm run test:coverage
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 npm run build

frontend-format:
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 npm run format

reader-e2e:
	docker compose run --rm browser-worker python manage.py test tests.test_reader_browser

shell:
	docker compose exec api python manage.py shell

superuser:
	docker compose exec api python manage.py createsuperuser

list-source-packs:
	docker compose exec -T api python manage.py sync_source_pack --list

plan-source-pack:
	@test -n "$(PACK)" || (echo "Set PACK=<source-pack-slug>" && exit 1)
	docker compose exec -T api python manage.py sync_source_pack $(PACK)

apply-source-pack:
	@test -n "$(PACK)" || (echo "Set PACK=<source-pack-slug>" && exit 1)
	docker compose exec -T api python manage.py sync_source_pack $(PACK) --apply --confirm

plan-impact-taxonomy:
	docker compose exec -T api python manage.py sync_impact_taxonomy

apply-impact-taxonomy:
	docker compose exec -T api python manage.py sync_impact_taxonomy --apply --confirm APPLY

extract-impacts:
	@test -n "$(ITEM_ID)" || (echo "Set ITEM_ID=<confirmed-comparison-item-uuid>" && exit 1)
	docker compose exec -T api python manage.py extract_impact_candidates $(ITEM_ID) --provider $(or $(PROVIDER),deterministic) --sync

publish-impact:
	@test -n "$(REVIEW_ID)" || (echo "Set REVIEW_ID=<approved-impact-review-uuid>" && exit 1)
	@test -n "$(PUBLISHER)" || (echo "Set PUBLISHER=<active-username>" && exit 1)
	docker compose exec -T api python manage.py publish_reviewed_impact $(REVIEW_ID) --publisher $(PUBLISHER) --confirm PUBLISH

pilot-source:
	@test -n "$(SOURCE)" || (echo "Set SOURCE=<source-pack-slug-or-endpoint-uuid>" && exit 1)
	docker compose exec -T api python manage.py run_source_pilot $(SOURCE) --confirm RUN

audit-static:
	@test -n "$(SOURCE)" || (echo "Set SOURCE=<source-pack-slug-or-endpoint-uuid>" && exit 1)
	docker compose exec -T api python manage.py audit_static_admission $(SOURCE)

promote-static:
	@test -n "$(ASSESSMENT_ID)" || (echo "Set ASSESSMENT_ID=<uuid>" && exit 1)
	docker compose exec -T api python manage.py promote_static_source $(ASSESSMENT_ID) --confirm PROMOTE

source-confidence:
	docker compose exec -T api python manage.py source_confidence_report

source-soak:
	docker compose exec -T api python manage.py source_soak_report

poll-jpdp:
	docker compose exec api python manage.py poll_jpdp

seed-agc:
	docker compose exec api python manage.py seed_agc

pilot-agc:
	docker compose exec api python manage.py seed_agc --run

audit-agc:
	docker compose exec api python manage.py audit_browser_admission --allow-incomplete

promote-agc:
	docker compose exec api python manage.py promote_browser_source --confirm

assess-sources:
	docker compose exec api python manage.py assess_source_reliability --all

repair-source:
	docker compose exec api python manage.py repair_source_pipeline

repair-source-apply:
	docker compose exec api python manage.py repair_source_pipeline --apply --confirm

rehearse-change:
	docker compose exec -T api python manage.py test tests.test_phase3d3.Phase3D3OrchestrationTestCase.test_raw_byte_change_with_same_normalized_text_stops_before_quality_and_diff tests.test_phase3d3.Phase3D3OrchestrationTestCase.test_completed_reviews_queue_summary_and_completion_closes_orchestration

poll-resource:
	@test -n "$(RESOURCE_ID)" || (echo "Set RESOURCE_ID=<uuid>" && exit 1)
	docker compose exec api python manage.py poll_resource $(RESOURCE_ID)

extract:
	docker compose exec api python manage.py extract_artifacts --sync

route-linked:
	docker compose exec api python manage.py route_linked_publications --queue

plan-ocr:
	docker compose exec api python manage.py plan_ocr

ocr:
	docker compose exec ocr-worker python manage.py queue_ocr --sync

embed-openrouter:
	docker compose exec api python manage.py embed_sections --provider openrouter --sync

verify-openrouter:
	docker compose exec api python manage.py verify_openrouter

evaluate-embeddings:
	docker compose exec api python manage.py evaluate_embeddings

evaluate-reader:
	docker compose exec -T api python manage.py evaluate_reader_search --require-hit-at-3 1.0

quality:
	docker compose exec api python manage.py assess_extraction_quality

audit-lineage:
	docker compose exec api python manage.py audit_version_lineage

classify-lineage:
	docker compose exec api python manage.py classify_version_lineage

anchors:
	docker compose exec api python manage.py project_structural_anchors

compare:
	docker compose exec api python manage.py compare_versions --sync

summarize:
	@test -n "$(COMPARISON_ID)" || (echo "Set COMPARISON_ID=<uuid>" && exit 1)
	docker compose exec api python manage.py summarize_comparison --comparison $(COMPARISON_ID) --sync

publish-reviewed:
	@test -n "$(COMPARISON_ID)" || (echo "Set COMPARISON_ID=<uuid>" && exit 1)
	@test -n "$(PUBLISHER)" || (echo "Set PUBLISHER=<active-staff-username>" && exit 1)
	docker compose exec api python manage.py publish_reviewed_changes --comparison $(COMPARISON_ID) --publisher $(PUBLISHER)

audit-orchestrations:
	docker compose exec api python manage.py audit_orchestrations

retry-orchestration:
	@test -n "$(ORCHESTRATION_ID)" || (echo "Set ORCHESTRATION_ID=<uuid>" && exit 1)
	docker compose exec api python manage.py retry_orchestration $(ORCHESTRATION_ID)

verify-storage:
	docker compose exec api python manage.py verify_object_storage

backup: ensure-env
	docker compose run --rm --build backup python manage.py create_backup

backup-verify: ensure-env
	@test -n "$(MANIFEST)" || (echo "Set MANIFEST=/var/lib/aria/backups/<name>.manifest.json" && exit 1)
	@test -n "$(IDENTITY_FILE)" || (echo "Set IDENTITY_FILE=<host-path-to-age-identity>" && exit 1)
	docker compose run --rm --build -v "$(abspath $(IDENTITY_FILE)):/run/secrets/aria-backup-age-key:ro" backup python manage.py verify_backup "$(MANIFEST)" --identity-file /run/secrets/aria-backup-age-key

restore-drill: ensure-env
	@test -n "$(MANIFEST)" || (echo "Set MANIFEST=/var/lib/aria/backups/<name>.manifest.json" && exit 1)
	@test -n "$(IDENTITY_FILE)" || (echo "Set IDENTITY_FILE=<host-path-to-age-identity>" && exit 1)
	docker compose run --rm --build -v "$(abspath $(IDENTITY_FILE)):/run/secrets/aria-backup-age-key:ro" backup python manage.py restore_backup_drill "$(MANIFEST)" --identity-file /run/secrets/aria-backup-age-key --confirm RESTORE-DRILL

observability-check:
	docker run --rm --entrypoint /bin/sh -v "$(CURDIR)/config/prometheus:/etc/prometheus:ro" prom/prometheus:v3.5.0@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996 -c 'printf test > /tmp/aria-metrics-token && exec /bin/promtool check config /etc/prometheus/prometheus.yml'
	python3 -m json.tool config/grafana/dashboards/aria-pipeline-performance.json >/dev/null
	docker run --rm -v "$(CURDIR)/deploy/caddy/aria.Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
	docker compose --profile observability config --quiet

supply-chain-check:
	python3 scripts/verify_supply_chain.py

security-scan:
	docker run --rm -v "$(CURDIR):/workspace:ro" -v aria-trivy-cache:/root/.cache/ aquasec/trivy:0.66.0@sha256:086971aaf400beebd94e8300fd8ea623774419597169156cec56eec5b00dfb1e fs --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --exit-code 1 --file-patterns 'pip:requirements.*\.lock' --skip-dirs /workspace/.git --skip-dirs /workspace/.venv --skip-dirs /workspace/build --skip-dirs /workspace/frontend/reader/node_modules --skip-files /workspace/.env /workspace

sbom:
	mkdir -p "$(CURDIR)/build"
	docker run --rm --user "$$(id -u):$$(id -g)" --tmpfs /tmp:rw,mode=1777 -e HOME=/tmp -v "$(CURDIR):/workspace" anchore/syft:v1.33.0@sha256:f94e5d9fce1f2278491a8e3a63bd5f6ddb81fdfdbb8bf7a1637565c1d5344357 dir:/workspace --source-name aria --source-version "$(ARIA_SBOM_VERSION)" --exclude './.git/**' --exclude './.venv/**' --exclude './build/**' --exclude './frontend/reader/node_modules/**' -o spdx-json=/workspace/build/aria-sbom.spdx.json

production-readiness: ensure-env supply-chain-check
	ARIA_RELEASE_REVISION="$$(git rev-parse --verify HEAD)" docker compose --profile operations -f compose.yaml -f compose.production.yaml run --rm api python manage.py check_production_readiness
	ARIA_RELEASE_REVISION="$$(git rev-parse --verify HEAD)" docker compose --profile operations -f compose.yaml -f compose.production.yaml run --rm api python manage.py verify_object_storage

release-check: supply-chain-check
	docker compose --profile tools build api backup browser-worker ocr-worker test
	$(MAKE) lint
	docker compose --profile tools run --rm test python manage.py makemigrations --check --dry-run
	$(MAKE) test
	$(MAKE) reader-e2e
	$(MAKE) frontend-test
	$(MAKE) observability-check
	$(MAKE) security-scan
	$(MAKE) sbom

vps-start: ensure-env supply-chain-check
	ARIA_RELEASE_REVISION="$$(git rev-parse --verify HEAD)" $(VPS_COMPOSE) up --build -d --wait --wait-timeout 600

vps-status:
	$(VPS_COMPOSE) ps

vps-readiness: ensure-env supply-chain-check
	ARIA_RELEASE_REVISION="$$(git rev-parse --verify HEAD)" $(VPS_COMPOSE) --profile operations run --rm api python manage.py check_production_readiness
	ARIA_RELEASE_REVISION="$$(git rev-parse --verify HEAD)" $(VPS_COMPOSE) --profile operations run --rm api python manage.py verify_object_storage

vps-stop:
	$(VPS_COMPOSE) down
