from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from aria.access.roles import OPERATOR_ROLES
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import SourceRun
from aria.events.models import AuditEvent
from aria.sources.models import SourceEndpoint

STRICT_ACCESS = override_settings(
    ENFORCE_OPERATOR_ROLES=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "aria-access-tests",
        }
    },
)


@STRICT_ACCESS
class OperatorAccessTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.staff = get_user_model().objects.create_user(
            username="bounded-operator",
            password="test-password",
            is_staff=True,
        )
        authority = Authority.objects.create(
            name="Access regulator",
            slug="access-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        collection = PublicationCollection.objects.create(
            authority=authority,
            name="Access publications",
            slug="access-publications",
            document_family=PublicationCollection.DocumentFamily.GUIDELINE,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=collection,
            name="Access-controlled source",
            discovery_url="https://example.com/publications/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html"],
        )

    def grant(self, *codenames: str) -> None:
        permissions = Permission.objects.filter(
            content_type__app_label="access", codename__in=codenames
        )
        self.staff.user_permissions.add(*permissions)
        for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            if hasattr(self.staff, cache_name):
                delattr(self.staff, cache_name)

    def test_migration_provisions_named_roles_with_exact_permissions(self) -> None:
        for name, codenames in OPERATOR_ROLES.items():
            with self.subTest(role=name):
                group = Group.objects.get(name=name)
                self.assertEqual(
                    set(group.permissions.values_list("codename", flat=True)),
                    set(codenames),
                )

    def test_staff_without_console_role_cannot_sign_in(self) -> None:
        response = self.client.post(
            reverse("console:login"),
            {"username": self.staff.username, "password": "test-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has no ARIA console role")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_viewer_can_read_but_cannot_operate_or_see_mutation_form(self) -> None:
        self.grant("access_console")
        self.client.force_login(self.staff)

        detail = self.client.get(reverse("console:source-detail", args=[self.endpoint.id]))
        denied = self.client.post(reverse("console:source-poll", args=[self.endpoint.id]))

        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Poll endpoint now")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(SourceRun.objects.count(), 0)
        audit = AuditEvent.objects.get(action="console.permission_denied")
        self.assertEqual(audit.actor_identifier, str(self.staff.pk))
        self.assertEqual(audit.target_id, self.endpoint.id)
        self.assertEqual(audit.details["required_permission"], "access.operate_sources")

    def test_source_operator_can_queue_the_bounded_poll(self) -> None:
        self.grant("access_console", "operate_sources")
        self.client.force_login(self.staff)

        with (
            patch("aria.console.operations.execute_source_run.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("console:source-poll", args=[self.endpoint.id])
            )

        self.assertEqual(response.status_code, 302)
        run = SourceRun.objects.get(endpoint=self.endpoint)
        delay.assert_called_once_with(str(run.id))

    def test_audit_log_requires_the_independent_auditor_permission(self) -> None:
        self.grant("access_console")
        self.client.force_login(self.staff)

        denied = self.client.get(reverse("console:audit-list"))
        self.assertEqual(denied.status_code, 403)

        self.grant("view_audit_log")
        allowed = self.client.get(reverse("console:audit-list"))
        self.assertEqual(allowed.status_code, 200)


@override_settings(
    ENFORCE_OPERATOR_ROLES=False,
    LOGIN_RATE_LIMIT_ATTEMPTS=2,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "aria-login-rate-tests",
        }
    },
)
class OperatorLoginRateLimitTests(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_repeated_invalid_credentials_are_rate_limited_without_echoing_passwords(self) -> None:
        route = reverse("console:login")
        payload = {"username": "unknown-operator", "password": "never-echo-this-password"}

        first = self.client.post(route, payload, REMOTE_ADDR="192.0.2.10")
        blocked = self.client.post(route, payload, REMOTE_ADDR="192.0.2.10")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], "300")
        self.assertNotContains(blocked, payload["password"], status_code=429)
