.PHONY: bootstrap build up down logs migrate makemigrations test check frontend-build frontend-test frontend-format reader-e2e shell superuser list-source-packs plan-source-pack apply-source-pack pilot-source audit-static promote-static source-confidence source-soak poll-jpdp seed-agc pilot-agc audit-agc promote-agc assess-sources repair-source repair-source-apply rehearse-change poll-resource extract route-linked plan-ocr ocr embed-openai evaluate-embeddings evaluate-reader quality audit-lineage classify-lineage anchors compare summarize publish-reviewed audit-orchestrations retry-orchestration verify-storage

bootstrap:
	@test -f .env || cp .env.example .env
	docker compose up --build -d

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
	docker compose run --rm api python manage.py test

check:
	docker compose run --rm api python manage.py check --deploy

frontend-build:
	docker compose build frontend-assets
	docker compose run --rm frontend-assets

frontend-test:
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim npm ci
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim npm run lint
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim npm run test:coverage
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim npm run build

frontend-format:
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(CURDIR)/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim npm run format

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

embed-openai:
	docker compose exec api python manage.py embed_sections --provider openai --sync

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
	docker compose exec api python manage.py publish_reviewed_changes --comparison $(COMPARISON_ID)

audit-orchestrations:
	docker compose exec api python manage.py audit_orchestrations

retry-orchestration:
	@test -n "$(ORCHESTRATION_ID)" || (echo "Set ORCHESTRATION_ID=<uuid>" && exit 1)
	docker compose exec api python manage.py retry_orchestration $(ORCHESTRATION_ID)

verify-storage:
	docker compose exec api python manage.py verify_object_storage
