import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register

PLACEHOLDERS = ("change-me", "unsafe-development", "django-insecure")
LOCAL_HOSTS = {"api", "localhost", "127.0.0.1", "[::1]"}
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")


def _strong_secret(value: str, *, minimum: int = 32) -> bool:
    lowered = value.lower()
    return (
        len(value) >= minimum
        and len(set(value)) >= 8
        and not any(placeholder in lowered for placeholder in PLACEHOLDERS)
    )


def _private_redis_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "redis"
        and parsed.hostname == "redis"
        and bool(parsed.password)
        and _strong_secret(parsed.password, minimum=16)
    )


def _secure_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None


@register(Tags.security, deploy=True)
def aria_deployment_checks(app_configs, **kwargs) -> list[Error]:
    del app_configs, kwargs
    errors: list[Error] = []

    if settings.DEBUG:
        errors.append(Error("Production debug mode is enabled.", id="aria.E001"))
    if not _strong_secret(settings.SECRET_KEY, minimum=50):
        errors.append(
            Error(
                "ARIA_SECRET_KEY is missing, predictable, or too short.",
                hint="Use a unique random value of at least 50 characters.",
                id="aria.E002",
            )
        )

    transport_ready = all(
        (
            settings.SECURE_SSL_REDIRECT,
            settings.SESSION_COOKIE_SECURE,
            settings.CSRF_COOKIE_SECURE,
            getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
            == ("HTTP_X_FORWARDED_PROTO", "https"),
            settings.SECURE_HSTS_SECONDS >= 300,
        )
    )
    if not transport_ready:
        errors.append(
            Error(
                "Production HTTPS, proxy, secure-cookie, and HSTS controls are incomplete.",
                hint="Use compose.production.yaml behind the private Cloudflare ingress.",
                id="aria.E003",
            )
        )

    public_hosts = set(settings.ALLOWED_HOSTS) - LOCAL_HOSTS
    if not public_hosts or "*" in settings.ALLOWED_HOSTS:
        errors.append(
            Error(
                "ARIA_ALLOWED_HOSTS needs an explicit non-local hostname and must not contain '*'.",
                id="aria.E004",
            )
        )

    if not settings.ENFORCE_OPERATOR_ROLES or not settings.REQUIRE_SEPARATE_PUBLISHER:
        errors.append(
            Error(
                "Least-privilege roles and separate publication approval must both be enforced.",
                id="aria.E005",
            )
        )

    database = settings.DATABASES["default"]
    cache = settings.CACHES["default"]
    infrastructure_ready = all(
        (
            database.get("ENGINE") == "django.db.backends.postgresql",
            database.get("HOST") == "postgres",
            _strong_secret(str(database.get("PASSWORD", "")), minimum=16),
            cache.get("BACKEND") == "django.core.cache.backends.redis.RedisCache",
            _private_redis_url(str(cache.get("LOCATION", ""))),
            _private_redis_url(settings.CELERY_BROKER_URL),
        )
    )
    if not infrastructure_ready:
        errors.append(
            Error(
                "Production PostgreSQL, shared cache, or broker isolation is incomplete.",
                hint="Use password-protected Compose services, not local-memory fallbacks.",
                id="aria.E006",
            )
        )

    storage_ready = all(
        (
            settings.OBJECT_STORAGE_BACKEND == "s3",
            _secure_https_url(settings.OBJECT_STORAGE_ENDPOINT),
            bool(settings.OBJECT_STORAGE_BUCKET),
            _strong_secret(settings.OBJECT_STORAGE_ACCESS_KEY, minimum=16),
            _strong_secret(settings.OBJECT_STORAGE_SECRET_KEY, minimum=32),
        )
    )
    if not storage_ready:
        errors.append(
            Error(
                "Private S3-compatible evidence storage is not production-ready.",
                hint=(
                    "Configure the private Cloudflare R2 endpoint, bucket, "
                    "and scoped credentials."
                ),
                id="aria.E007",
            )
        )

    backup_ready = (
        settings.BACKUP_UPLOAD_TO_R2
        and settings.BACKUP_AGE_RECIPIENT.startswith("age1")
        and len(settings.BACKUP_AGE_RECIPIENT) >= 50
    )
    if not backup_ready:
        errors.append(
            Error(
                "Encrypted off-host backups are not enabled.",
                hint="Configure an age public recipient and R2 backup upload.",
                id="aria.E008",
            )
        )

    if not _strong_secret(settings.METRICS_TOKEN, minimum=32):
        errors.append(
            Error(
                "The private metrics token is missing or predictable.",
                id="aria.E009",
            )
        )
    if not REVISION_PATTERN.fullmatch(settings.RELEASE_REVISION):
        errors.append(
            Error(
                "ARIA_RELEASE_REVISION is not an immutable commit digest.",
                id="aria.E010",
            )
        )

    return errors
