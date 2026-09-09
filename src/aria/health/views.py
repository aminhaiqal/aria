from django.conf import settings
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from redis import Redis

from aria.health.metrics import metrics_token_is_valid, render_operational_metrics


@require_GET
def liveness(request) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "aria-core"})


@require_GET
def readiness(request) -> JsonResponse:
    checks: dict[str, str] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    redis_client = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    finally:
        redis_client.close()

    ready = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if ready else "unavailable", "checks": checks},
        status=200 if ready else 503,
    )


@require_GET
def metrics(request) -> HttpResponse:
    if not settings.METRICS_TOKEN:
        raise Http404
    if not metrics_token_is_valid(request.headers.get("Authorization", "")):
        return HttpResponse("Forbidden\n", status=403, content_type="text/plain")
    return HttpResponse(
        render_operational_metrics(),
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )
