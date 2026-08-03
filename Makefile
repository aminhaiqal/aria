.PHONY: bootstrap build up down logs migrate makemigrations test check shell superuser extract quality verify-storage

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
	docker compose logs -f api worker beat

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

extract:
	docker compose exec api python manage.py extract_artifacts --sync

quality:
	docker compose exec api python manage.py assess_extraction_quality

verify-storage:
	docker compose exec api python manage.py verify_object_storage
