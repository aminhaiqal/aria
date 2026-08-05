.PHONY: bootstrap build up down logs migrate makemigrations test check shell superuser poll-jpdp poll-resource extract route-linked plan-ocr ocr embed-openai evaluate-embeddings quality audit-lineage classify-lineage anchors compare summarize publish-reviewed verify-storage

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
	docker compose logs -f api worker ocr-worker beat

migrate:
	docker compose run --rm migrate

makemigrations:
	docker compose run --rm api python manage.py makemigrations

test:
	docker compose run --rm api python manage.py test

check:
	docker compose run --rm api python manage.py check --deploy

shell:
	docker compose exec api python manage.py shell

superuser:
	docker compose exec api python manage.py createsuperuser

poll-jpdp:
	docker compose exec api python manage.py poll_jpdp

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

verify-storage:
	docker compose exec api python manage.py verify_object_storage
