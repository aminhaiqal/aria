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
CSRF_TRUSTED_ORIGINS = env_list("ARIA_CSRF_TRUSTED_ORIGINS")
if env_bool("ARIA_TRUST_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("ARIA_SSL_REDIRECT", False)
SECURE_REDIRECT_EXEMPT = [r"^health/"]
SECURE_HSTS_SECONDS = max(0, int(os.getenv("ARIA_SECURE_HSTS_SECONDS", "0")))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("ARIA_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("ARIA_SECURE_HSTS_PRELOAD", False)

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
    "aria.comparisons",
    "aria.impacts",
    "aria.quality",
    "aria.orchestration",
    "aria.browser",
    "aria.reliability",
    "aria.reader",
    "aria.console",
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
    "aria.reader.middleware.ReaderSecurityHeadersMiddleware",
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
LOGIN_URL = "/console/login/"
LOGIN_REDIRECT_URL = "/console/"
LOGOUT_REDIRECT_URL = "/console/login/"
SESSION_COOKIE_SECURE = env_bool("ARIA_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "reader_search": os.getenv("ARIA_READER_SEARCH_RATE", "60/min"),
        "reader_document": os.getenv("ARIA_READER_DOCUMENT_RATE", "120/min"),
    },
}

REDIS_URL = os.getenv("ARIA_REDIS_URL", "redis://localhost:6379/0")
OBJECT_STORAGE_BACKEND = os.getenv("ARIA_OBJECT_STORAGE_BACKEND", "filesystem")
OBJECT_STORAGE_ROOT = Path(os.getenv("ARIA_OBJECT_STORAGE_ROOT", "/var/lib/aria/artifacts"))
OBJECT_STORAGE_ENDPOINT = os.getenv("ARIA_OBJECT_STORAGE_ENDPOINT", "")
OBJECT_STORAGE_BUCKET = os.getenv("ARIA_OBJECT_STORAGE_BUCKET", "aria-artifacts")
OBJECT_STORAGE_ACCESS_KEY = os.getenv("ARIA_OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.getenv("ARIA_OBJECT_STORAGE_SECRET_KEY", "")
OBJECT_STORAGE_REGION = os.getenv("ARIA_OBJECT_STORAGE_REGION", "auto")
BACKUP_ROOT = Path(os.getenv("ARIA_BACKUP_ROOT", "/var/lib/aria/backups"))
BACKUP_AGE_RECIPIENT = os.getenv("ARIA_BACKUP_AGE_RECIPIENT", "").strip()
BACKUP_UPLOAD_TO_R2 = env_bool("ARIA_BACKUP_UPLOAD_TO_R2", False)
BACKUP_R2_PREFIX = os.getenv("ARIA_BACKUP_R2_PREFIX", "backups/database")

HTTP_USER_AGENT = os.getenv(
    "ARIA_HTTP_USER_AGENT",
    "ARIA-Core/0.1 (+https://github.com/aminhaiqal/aria)",
)
HTTP_CONNECT_TIMEOUT_SECONDS = float(os.getenv("ARIA_HTTP_CONNECT_TIMEOUT_SECONDS", "10"))
HTTP_READ_TIMEOUT_SECONDS = float(os.getenv("ARIA_HTTP_READ_TIMEOUT_SECONDS", "30"))
HTTP_MAX_RESPONSE_BYTES = int(os.getenv("ARIA_HTTP_MAX_RESPONSE_BYTES", str(25 * 1024 * 1024)))
HTTP_MAX_REDIRECTS = int(os.getenv("ARIA_HTTP_MAX_REDIRECTS", "5"))
HTTP_MIN_DOMAIN_INTERVAL_SECONDS = float(os.getenv("ARIA_HTTP_MIN_DOMAIN_INTERVAL_SECONDS", "1"))
HTTP_SUPPLEMENTAL_CA_BUNDLE = os.getenv("ARIA_HTTP_SUPPLEMENTAL_CA_BUNDLE", "")
IMPACT_WEBHOOK_URL = os.getenv("ARIA_IMPACT_WEBHOOK_URL", "").strip()
IMPACT_WEBHOOK_ALLOWED_DOMAINS = env_list("ARIA_IMPACT_WEBHOOK_ALLOWED_DOMAINS")
IMPACT_WEBHOOK_SECRET = os.getenv("ARIA_IMPACT_WEBHOOK_SECRET", "")
IMPACT_WEBHOOK_MAX_ATTEMPTS = min(
    10,
    max(1, int(os.getenv("ARIA_IMPACT_WEBHOOK_MAX_ATTEMPTS", "5"))),
)
IMPACT_WEBHOOK_BATCH_SIZE = min(
    100,
    max(1, int(os.getenv("ARIA_IMPACT_WEBHOOK_BATCH_SIZE", "10"))),
)
IMPACT_WEBHOOK_STALE_MINUTES = min(
    60,
    max(5, int(os.getenv("ARIA_IMPACT_WEBHOOK_STALE_MINUTES", "15"))),
)
BROWSER_PROFILE_NAME = os.getenv("ARIA_BROWSER_PROFILE_NAME", "bounded-chromium")
BROWSER_PROFILE_VERSION = os.getenv("ARIA_BROWSER_PROFILE_VERSION", "1")
BROWSER_NAVIGATION_TIMEOUT_SECONDS = max(
    5,
    min(60, int(os.getenv("ARIA_BROWSER_NAVIGATION_TIMEOUT_SECONDS", "30"))),
)
BROWSER_RENDER_WAIT_MILLISECONDS = min(
    5000,
    max(0, int(os.getenv("ARIA_BROWSER_RENDER_WAIT_MILLISECONDS", "750"))),
)
BROWSER_MAX_RENDER_WAIT_MILLISECONDS = 5000
BROWSER_CAPTURE_PROFILE = f"{BROWSER_PROFILE_NAME}-v{BROWSER_PROFILE_VERSION}"
BROWSER_MAX_REQUESTS = min(200, max(1, int(os.getenv("ARIA_BROWSER_MAX_REQUESTS", "80"))))
BROWSER_MAX_RESPONSE_BYTES = min(
    100 * 1024 * 1024,
    max(
        1024,
        int(os.getenv("ARIA_BROWSER_MAX_RESPONSE_BYTES", str(25 * 1024 * 1024))),
    ),
)
BROWSER_MAX_DOM_BYTES = min(
    20 * 1024 * 1024,
    max(
        1024,
        int(os.getenv("ARIA_BROWSER_MAX_DOM_BYTES", str(5 * 1024 * 1024))),
    ),
)
BROWSER_MAX_CAPTURE_BODY_BYTES = min(
    5 * 1024 * 1024,
    max(
        1024,
        int(os.getenv("ARIA_BROWSER_MAX_CAPTURE_BODY_BYTES", str(2 * 1024 * 1024))),
    ),
)
BROWSER_MAX_REQUEST_BODY_BYTES = min(
    64 * 1024,
    max(
        1024,
        int(os.getenv("ARIA_BROWSER_MAX_REQUEST_BODY_BYTES", str(64 * 1024))),
    ),
)
BROWSER_MAX_REDIRECTS = min(
    10,
    max(0, int(os.getenv("ARIA_BROWSER_MAX_REDIRECTS", "5"))),
)
BROWSER_PLAYWRIGHT_VERSION = "1.61.0"
MONITOR_UNHEALTHY_AFTER_FAILURES = max(
    1,
    int(os.getenv("ARIA_MONITOR_UNHEALTHY_AFTER_FAILURES", "3")),
)
MONITOR_RESOURCE_BATCH_SIZE = max(
    1,
    int(os.getenv("ARIA_MONITOR_RESOURCE_BATCH_SIZE", "5")),
)
MONITOR_RESOURCE_BATCH_PER_ENDPOINT = max(
    1,
    int(os.getenv("ARIA_MONITOR_RESOURCE_BATCH_PER_ENDPOINT", "3")),
)
MONITOR_MAX_ENABLED_RESOURCES_PER_ENDPOINT = max(
    1,
    int(os.getenv("ARIA_MONITOR_MAX_ENABLED_RESOURCES_PER_ENDPOINT", "50")),
)
SOURCE_FRESHNESS_GRACE_MINUTES = max(
    5,
    int(os.getenv("ARIA_SOURCE_FRESHNESS_GRACE_MINUTES", "60")),
)
SOURCE_PIPELINE_GRACE_MINUTES = max(
    5,
    int(os.getenv("ARIA_SOURCE_PIPELINE_GRACE_MINUTES", "30")),
)
EMBEDDING_PROVIDER = os.getenv("ARIA_EMBEDDING_PROVIDER", "local_hash")
LOCAL_EMBEDDING_MODEL = os.getenv(
    "ARIA_LOCAL_EMBEDDING_MODEL",
    os.getenv("ARIA_EMBEDDING_MODEL", "aria-token-hash-v1"),
)
EMBEDDING_DIMENSIONS = int(os.getenv("ARIA_EMBEDDING_DIMENSIONS", "384"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_EMBEDDING_MODEL = os.getenv("ARIA_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_EMBEDDING_BATCH_SIZE = int(os.getenv("ARIA_OPENAI_EMBEDDING_BATCH_SIZE", "64"))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("ARIA_OPENAI_TIMEOUT_SECONDS", "60"))
OPENAI_MAX_RETRIES = int(os.getenv("ARIA_OPENAI_MAX_RETRIES", "3"))
OPENAI_SUMMARY_MODEL = os.getenv("ARIA_OPENAI_SUMMARY_MODEL", "gpt-5.6-sol")
OPENAI_SUMMARY_REASONING_EFFORT = os.getenv("ARIA_OPENAI_SUMMARY_REASONING_EFFORT", "low")
OPENAI_SUMMARY_MAX_OUTPUT_TOKENS = int(os.getenv("ARIA_OPENAI_SUMMARY_MAX_OUTPUT_TOKENS", "2000"))
OPENAI_SUMMARY_MAX_ITEMS = int(os.getenv("ARIA_OPENAI_SUMMARY_MAX_ITEMS", "50"))
OPENAI_SUMMARY_MAX_CHARS_PER_ANCHOR = int(
    os.getenv("ARIA_OPENAI_SUMMARY_MAX_CHARS_PER_ANCHOR", "12000")
)
OPENAI_IMPACT_MODEL = os.getenv("ARIA_OPENAI_IMPACT_MODEL", OPENAI_SUMMARY_MODEL)
OPENAI_IMPACT_REASONING_EFFORT = os.getenv("ARIA_OPENAI_IMPACT_REASONING_EFFORT", "low")
OPENAI_IMPACT_MAX_OUTPUT_TOKENS = int(os.getenv("ARIA_OPENAI_IMPACT_MAX_OUTPUT_TOKENS", "3000"))
OPENAI_IMPACT_MAX_CHARS_PER_ANCHOR = int(
    os.getenv("ARIA_OPENAI_IMPACT_MAX_CHARS_PER_ANCHOR", "12000")
)
ORCHESTRATION_AUTO_GPT_SUMMARIES = env_bool(
    "ARIA_AUTO_GPT_SUMMARIES",
    bool(OPENAI_API_KEY),
)
READER_EMBEDDING_PROVIDER = os.getenv("ARIA_READER_EMBEDDING_PROVIDER", EMBEDDING_PROVIDER)
READER_FRONTEND = os.getenv("ARIA_READER_FRONTEND", "react").strip().lower()
if READER_FRONTEND not in {"react", "server"}:
    raise ImproperlyConfigured("ARIA_READER_FRONTEND must be react or server.")
READER_FRONTEND_DIST = Path(
    os.getenv("ARIA_READER_FRONTEND_DIST", str(BASE_DIR / "frontend" / "reader" / "dist"))
)
READER_MAX_QUERY_CHARACTERS = min(
    1000,
    max(50, int(os.getenv("ARIA_READER_MAX_QUERY_CHARACTERS", "500"))),
)
READER_PAGE_SIZE = min(20, max(5, int(os.getenv("ARIA_READER_PAGE_SIZE", "10"))))
READER_MAX_PAGE_SIZE = 20
READER_MAX_SEARCH_PAGES = 10
READER_MAX_SEARCH_RESULTS = READER_MAX_PAGE_SIZE * READER_MAX_SEARCH_PAGES
READER_SEARCH_CANDIDATE_LIMIT = min(
    500,
    max(50, int(os.getenv("ARIA_READER_SEARCH_CANDIDATE_LIMIT", "300"))),
)
READER_MAX_PASSAGES_PER_DOCUMENT = 3
READER_MAX_DOCUMENT_SECTIONS = min(
    5000,
    max(100, int(os.getenv("ARIA_READER_MAX_DOCUMENT_SECTIONS", "2000"))),
)
ORCHESTRATION_STALE_AFTER_MINUTES = max(
    5,
    int(os.getenv("ARIA_ORCHESTRATION_STALE_AFTER_MINUTES", "30")),
)
ORCHESTRATION_RECOVERY_BATCH_SIZE = max(
    1,
    int(os.getenv("ARIA_ORCHESTRATION_RECOVERY_BATCH_SIZE", "5")),
)
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
MONITOR_STRUCTURE_DRIFT_PAUSE_MINUTES = max(
    60,
    int(os.getenv("ARIA_MONITOR_STRUCTURE_DRIFT_PAUSE_MINUTES", "1440")),
)

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
    "aria.browser.tasks.*": {"queue": "browser_fetch", "routing_key": "browser_fetch"},
    "aria.reliability.tasks.*": {"queue": "discovery", "routing_key": "discovery"},
    "aria.discovery.tasks.*": {"queue": "discovery", "routing_key": "discovery"},
    "aria.fetching.tasks.*": {"queue": "http_fetch", "routing_key": "http_fetch"},
    "aria.extraction.tasks.*": {"queue": "extraction", "routing_key": "extraction"},
    "aria.orchestration.tasks.*": {"queue": "extraction", "routing_key": "extraction"},
    "aria.ocr.tasks.*": {"queue": "ocr", "routing_key": "ocr"},
    "aria.knowledge.tasks.*": {"queue": "normalization", "routing_key": "normalization"},
    "aria.comparisons.tasks.*": {"queue": "diff", "routing_key": "diff"},
    "aria.impacts.tasks.*": {"queue": "diff", "routing_key": "diff"},
    "aria.events.tasks.*": {"queue": "event_publish", "routing_key": "event_publish"},
}
CELERY_BEAT_SCHEDULE = {
    "schedule-due-source-endpoints": {
        "task": "aria.discovery.tasks.schedule_due_endpoints",
        "schedule": crontab(minute="*"),
    },
    "schedule-due-monitored-resources": {
        "task": "aria.discovery.tasks.schedule_due_resources",
        "schedule": crontab(minute="*"),
    },
    "recover-change-orchestrations": {
        "task": "aria.orchestration.tasks.recover_change_orchestrations",
        "schedule": crontab(minute="*/5"),
    },
    "assess-source-reliability": {
        "task": "aria.reliability.tasks.assess_enabled_sources",
        "schedule": crontab(minute="*/15"),
    },
    "deliver-reviewed-impact-webhooks": {
        "task": "aria.events.tasks.deliver_reviewed_impact_webhooks",
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

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
