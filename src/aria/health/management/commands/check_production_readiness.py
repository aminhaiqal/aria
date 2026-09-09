from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q
from redis import Redis


def _operator_permission_query(*codenames: str) -> Q:
    query = Q(is_superuser=True)
    for relation in ("groups__permissions", "user_permissions"):
        for codename in codenames:
            query |= Q(
                **{
                    f"{relation}__content_type__app_label": "access",
                    f"{relation}__codename": codename,
                }
            )
    return query


def operator_separation_ready() -> bool:
    users = get_user_model().objects.filter(is_active=True, is_staff=True)
    if users.count() < 2:
        return False
    reviewers = set(
        users.filter(
            _operator_permission_query("review_changes", "review_impacts")
        )
        .values_list("pk", flat=True)
        .distinct()
    )
    publishers = set(
        users.filter(
            _operator_permission_query("publish_changes", "publish_impacts")
        )
        .values_list("pk", flat=True)
        .distinct()
    )
    return any(reviewer != publisher for reviewer in reviewers for publisher in publishers)


class Command(BaseCommand):
    help = "Fail unless production policy, database, cache, broker, and migrations are ready."

    def handle(self, *args, **options):
        messages = checks.run_checks(include_deployment_checks=True, databases=["default"])
        errors = [message for message in messages if message.level >= checks.ERROR]
        if errors:
            summary = ", ".join(sorted({message.id or "unknown" for message in errors}))
            raise CommandError(f"Production settings gate failed: {summary}.")
        self.stdout.write("[ok] production settings policy")

        try:
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except Exception as error:
            raise CommandError("Database readiness probe failed.") from error
        if pending:
            raise CommandError(f"Database has {len(pending)} unapplied migration(s).")
        self.stdout.write("[ok] database connectivity and migrations")

        if not operator_separation_ready():
            raise CommandError(
                "No distinct active reviewer and publisher accounts satisfy role separation."
            )
        self.stdout.write("[ok] distinct reviewer and publisher accounts")

        cache_key = f"aria:production-readiness:{uuid4()}"
        cache_value = str(uuid4())
        try:
            cache.set(cache_key, cache_value, timeout=30)
            observed = cache.get(cache_key)
        except Exception as error:
            raise CommandError("Shared cache readiness probe failed.") from error
        finally:
            try:
                cache.delete(cache_key)
            except Exception:
                pass
        if observed != cache_value:
            raise CommandError("Shared cache failed its round-trip integrity probe.")
        self.stdout.write("[ok] shared cache round trip")

        broker = Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        try:
            if broker.ping() is not True:
                raise CommandError("Broker returned an unexpected readiness response.")
        except CommandError:
            raise
        except Exception as error:
            raise CommandError("Broker readiness probe failed.") from error
        finally:
            broker.close()
        self.stdout.write("[ok] broker connectivity")
        self.stdout.write(self.style.SUCCESS("ARIA production readiness gate passed."))
