from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from aria.health import checks as deployment_checks
from aria.health.management.commands.check_production_readiness import (
    operator_separation_ready,
)

PRODUCTION_DATABASE = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "postgres",
        "PASSWORD": "strong-database-password-0123456789",
    }
}

PRODUCTION_SETTINGS = override_settings(
    DEBUG=False,
    RELEASE_REVISION="a" * 40,
    SECRET_KEY="aria-production-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    ALLOWED_HOSTS=["aria.example.com", "api"],
    SECURE_SSL_REDIRECT=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_HSTS_SECONDS=3600,
    ENFORCE_OPERATOR_ROLES=True,
    REQUIRE_SEPARATE_PUBLISHER=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://:strong-cache-password-0123456789@redis:6379/2",
        }
    },
    CELERY_BROKER_URL="redis://:strong-broker-password-0123456789@redis:6379/1",
    OBJECT_STORAGE_BACKEND="s3",
    OBJECT_STORAGE_ENDPOINT="https://account.r2.cloudflarestorage.com",
    OBJECT_STORAGE_BUCKET="aria-artifacts",
    OBJECT_STORAGE_ACCESS_KEY="ACCESSKEY0123456789",
    OBJECT_STORAGE_SECRET_KEY="secret-storage-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    BACKUP_UPLOAD_TO_R2=True,
    BACKUP_AGE_RECIPIENT="age1" + "q" * 58,
    METRICS_TOKEN="metrics-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)


@PRODUCTION_SETTINGS
class ProductionDeploymentCheckTests(SimpleTestCase):
    def check_ids(self) -> set[str]:
        with patch.object(deployment_checks.settings, "DATABASES", PRODUCTION_DATABASE):
            return {message.id for message in deployment_checks.aria_deployment_checks(None)}

    def test_complete_production_policy_passes(self) -> None:
        self.assertEqual(self.check_ids(), set())

    def test_debug_and_placeholder_secret_fail_closed(self) -> None:
        with self.settings(DEBUG=True, SECRET_KEY="change-me"):
            self.assertTrue({"aria.E001", "aria.E002"}.issubset(self.check_ids()))

    def test_transport_and_public_host_are_required(self) -> None:
        with self.settings(
            SECURE_SSL_REDIRECT=False,
            ALLOWED_HOSTS=["localhost", "api", "*"],
        ):
            self.assertTrue({"aria.E003", "aria.E004"}.issubset(self.check_ids()))

    def test_role_separation_and_shared_infrastructure_are_required(self) -> None:
        with self.settings(
            ENFORCE_OPERATOR_ROLES=False,
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "unsafe-local-cache",
                }
            },
        ):
            self.assertTrue({"aria.E005", "aria.E006"}.issubset(self.check_ids()))

    def test_evidence_backup_metrics_and_revision_controls_are_required(self) -> None:
        with self.settings(
            OBJECT_STORAGE_BACKEND="filesystem",
            BACKUP_UPLOAD_TO_R2=False,
            METRICS_TOKEN="short",
            RELEASE_REVISION="unknown",
        ):
            self.assertTrue(
                {"aria.E007", "aria.E008", "aria.E009", "aria.E010"}.issubset(
                    self.check_ids()
                )
            )


class ProductionReadinessCommandTests(SimpleTestCase):
    @patch("aria.health.management.commands.check_production_readiness.checks.run_checks")
    def test_deployment_error_stops_before_live_probes(self, run_checks: Mock) -> None:
        run_checks.return_value = [checks.Error("unsafe", id="aria.E001")]

        with self.assertRaisesMessage(CommandError, "aria.E001"):
            call_command("check_production_readiness", stdout=StringIO())

    @patch("aria.health.management.commands.check_production_readiness.MigrationExecutor")
    @patch("aria.health.management.commands.check_production_readiness.checks.run_checks")
    def test_pending_migration_fails_readiness(
        self,
        run_checks: Mock,
        executor_class: Mock,
    ) -> None:
        run_checks.return_value = []
        executor_class.return_value.loader.graph.leaf_nodes.return_value = ["latest"]
        executor_class.return_value.migration_plan.return_value = [("migration", False)]

        with self.assertRaisesMessage(CommandError, "1 unapplied migration"):
            call_command("check_production_readiness", stdout=StringIO())

    @patch("aria.health.management.commands.check_production_readiness.Redis.from_url")
    @patch("aria.health.management.commands.check_production_readiness.cache")
    @patch(
        "aria.health.management.commands.check_production_readiness.operator_separation_ready"
    )
    @patch("aria.health.management.commands.check_production_readiness.MigrationExecutor")
    @patch("aria.health.management.commands.check_production_readiness.checks.run_checks")
    def test_all_policy_and_live_probes_pass(
        self,
        run_checks: Mock,
        executor_class: Mock,
        operator_ready: Mock,
        cache: Mock,
        redis_from_url: Mock,
    ) -> None:
        run_checks.return_value = []
        executor_class.return_value.loader.graph.leaf_nodes.return_value = ["latest"]
        executor_class.return_value.migration_plan.return_value = []
        operator_ready.return_value = True
        state = {}
        cache.set.side_effect = lambda key, value, timeout: state.update({key: value})
        cache.get.side_effect = state.get
        redis_from_url.return_value.ping.return_value = True
        output = StringIO()

        call_command("check_production_readiness", stdout=output)

        self.assertIn("ARIA production readiness gate passed", output.getvalue())
        redis_from_url.return_value.close.assert_called_once_with()


class OperatorSeparationReadinessTests(TestCase):
    def test_distinct_reviewer_and_publisher_are_required(self) -> None:
        user_model = get_user_model()
        reviewer = user_model.objects.create_user(
            username="release-reviewer",
            is_active=True,
            is_staff=True,
        )
        publisher = user_model.objects.create_user(
            username="release-publisher",
            is_active=True,
            is_staff=True,
        )
        reviewer.groups.add(Group.objects.get(name="ARIA Change Reviewer"))
        publisher.groups.add(Group.objects.get(name="ARIA Publisher"))

        self.assertTrue(operator_separation_ready())

        publisher.is_active = False
        publisher.save(update_fields=("is_active",))
        self.assertFalse(operator_separation_ready())
