import logging
import re
from contextvars import ContextVar
from uuid import uuid4

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
correlation_id: ContextVar[str] = ContextVar("aria_correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        request = getattr(record, "request", None)
        record.correlation_id = getattr(request, "correlation_id", correlation_id.get())
        return True


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
        request.correlation_id = request_id
        token = correlation_id.set(request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            return response
        finally:
            correlation_id.reset(token)
