import os
from pathlib import Path

from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from kombu import Exchange, Queue

BASE_DIR = Path(__file__).resolve().parents[2]


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEBUG = env_bool("ARIA_DEBUG", False)
SECRET_KEY = os.getenv("ARIA_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("ARIA_SECRET_KEY must be set when ARIA_DEBUG is false.")
    SECRET_KEY = "unsafe-development-key"
ALLOWED_HOSTS = env_list("ARIA_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "rest_framework",
    "aria.authorities",
    "aria.collections",
    "aria.sources",
    "aria.discovery",
    "aria.fetching",
    "aria.artifacts",
    "aria.extraction",
    "aria.ocr",
    "aria.documents",
    "aria.knowledge",
    "aria.quality",
    "aria.events",
    "aria.health",
    "aria.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "aria.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "aria.wsgi.application"
ASGI_APPLICATION = "aria.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "aria"),
        "USER": os.getenv("POSTGRES_USER", "aria"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "aria"),
        "HOST": os.getenv("ARIA_DATABASE_HOST", "localhost"),
        "PORT": os.getenv("ARIA_DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 5},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("ARIA_TIME_ZONE", "Asia/Kuala_Lumpur")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

REDIS_URL = os.getenv("ARIA_REDIS_URL", "redis://localhost:6379/0")
OBJECT_STORAGE_BACKEND = os.getenv("ARIA_OBJECT_STORAGE_BACKEND", "filesystem")
OBJECT_STORAGE_ROOT = Path(os.getenv("ARIA_OBJECT_STORAGE_ROOT", "/var/lib/aria/artifacts"))
OBJECT_STORAGE_ENDPOINT = os.getenv("ARIA_OBJECT_STORAGE_ENDPOINT", "")
OBJECT_STORAGE_BUCKET = os.getenv("ARIA_OBJECT_STORAGE_BUCKET", "aria-artifacts")
OBJECT_STORAGE_ACCESS_KEY = os.getenv("ARIA_OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.getenv("ARIA_OBJECT_STORAGE_SECRET_KEY", "")
OBJECT_STORAGE_REGION = os.getenv("ARIA_OBJECT_STORAGE_REGION", "auto")

HTTP_USER_AGENT = os.getenv(
    "ARIA_HTTP_USER_AGENT",
    "ARIA-Core/0.1 (+https://github.com/aminhaiqal/aria)",
)
HTTP_CONNECT_TIMEOUT_SECONDS = float(os.getenv("ARIA_HTTP_CONNECT_TIMEOUT_SECONDS", "10"))
HTTP_READ_TIMEOUT_SECONDS = float(os.getenv("ARIA_HTTP_READ_TIMEOUT_SECONDS", "30"))
HTTP_MAX_RESPONSE_BYTES = int(os.getenv("ARIA_HTTP_MAX_RESPONSE_BYTES", str(25 * 1024 * 1024)))
HTTP_MAX_REDIRECTS = int(os.getenv("ARIA_HTTP_MAX_REDIRECTS", "5"))
HTTP_MIN_DOMAIN_INTERVAL_SECONDS = float(os.getenv("ARIA_HTTP_MIN_DOMAIN_INTERVAL_SECONDS", "1"))
EMBEDDING_PROVIDER = os.getenv("ARIA_EMBEDDING_PROVIDER", "local_hash")
EMBEDDING_MODEL = os.getenv("ARIA_EMBEDDING_MODEL", "aria-token-hash-v1")
EMBEDDING_DIMENSIONS = int(os.getenv("ARIA_EMBEDDING_DIMENSIONS", "384"))
PDF_OCR_MIN_CHARACTERS_PER_PAGE = int(os.getenv("ARIA_PDF_OCR_MIN_CHARACTERS_PER_PAGE", "40"))
OCR_PROFILE_NAME = os.getenv("ARIA_OCR_PROFILE_NAME", "jpdp-msa-eng")
OCR_PROFILE_VERSION = os.getenv("ARIA_OCR_PROFILE_VERSION", "1")
OCR_LANGUAGES = os.getenv("ARIA_OCR_LANGUAGES", "msa+eng")
OCR_BINARY = os.getenv("ARIA_OCR_BINARY", "ocrmypdf")
OCR_TESSERACT_BINARY = os.getenv("ARIA_OCR_TESSERACT_BINARY", "tesseract")
OCR_DECLARED_TOOLCHAIN = os.getenv("ARIA_OCR_DECLARED_TOOLCHAIN", "aria-ocr-bookworm-v1")
OCR_PROCESS_TIMEOUT_SECONDS = int(os.getenv("ARIA_OCR_PROCESS_TIMEOUT_SECONDS", "1800"))
OCR_TESSERACT_TIMEOUT_SECONDS = int(os.getenv("ARIA_OCR_TESSERACT_TIMEOUT_SECONDS", "300"))
OCR_MAX_INPUT_BYTES = int(os.getenv("ARIA_OCR_MAX_INPUT_BYTES", str(50 * 1024 * 1024)))
OCR_STALE_AFTER_MINUTES = int(os.getenv("ARIA_OCR_STALE_AFTER_MINUTES", "45"))

CELERY_BROKER_URL = os.getenv("ARIA_CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "discovery"
CELERY_TASK_QUEUES = tuple(
    Queue(name, Exchange(name, type="direct"), routing_key=name)
    for name in (
        "discovery",
        "http_fetch",
        "browser_fetch",
        "extraction",
        "ocr",
        "normalization",
        "diff",
        "event_publish",
    )
)
CELERY_TASK_ROUTES = {
    "aria.discovery.tasks.*": {"queue": "discovery", "routing_key": "discovery"},
    "aria.fetching.tasks.*": {"queue": "http_fetch", "routing_key": "http_fetch"},
    "aria.extraction.tasks.*": {"queue": "extraction", "routing_key": "extraction"},
    "aria.ocr.tasks.*": {"queue": "ocr", "routing_key": "ocr"},
    "aria.events.tasks.*": {"queue": "event_publish", "routing_key": "event_publish"},
}
CELERY_BEAT_SCHEDULE = {
    "schedule-due-source-endpoints": {
        "task": "aria.discovery.tasks.schedule_due_endpoints",
        "schedule": crontab(minute="*"),
    },
}

LOG_LEVEL = os.getenv("ARIA_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
