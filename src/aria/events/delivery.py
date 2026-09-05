import hashlib
import hmac
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aria.events.models import OutboxEvent
from aria.fetching.client import (
    Resolver,
    UnsafeTargetError,
    build_pinned_url,
    build_tls_context,
    system_resolver,
    validate_target_url,
)


class ImpactDeliveryError(RuntimeError):
    pass


class ImpactDeliveryNotConfigured(ImpactDeliveryError):
    pass


class RetryableImpactDeliveryError(ImpactDeliveryError):
    pass


class PermanentImpactDeliveryError(ImpactDeliveryError):
    pass


@dataclass(frozen=True)
class ImpactDeliveryReceipt:
    status_code: int
    resolved_addresses: tuple[str, ...]


def impact_delivery_enabled() -> bool:
    return bool(
        settings.IMPACT_WEBHOOK_URL
        and settings.IMPACT_WEBHOOK_ALLOWED_DOMAINS
        and len(settings.IMPACT_WEBHOOK_SECRET) >= 32
    )


def canonical_event_body(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = timestamp.encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


class ImpactWebhookClient:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
    ):
        self.resolver = resolver
        timeout = httpx.Timeout(
            settings.HTTP_READ_TIMEOUT_SECONDS,
            connect=settings.HTTP_CONNECT_TIMEOUT_SECONDS,
        )
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            verify=build_tls_context(),
        )

    def close(self) -> None:
        self.client.close()

    def deliver(
        self,
        outbox_event: OutboxEvent,
        *,
        target_url: str | None = None,
        allowed_domains: Iterable[str] | None = None,
        secret: str | None = None,
    ) -> ImpactDeliveryReceipt:
        selected_url = target_url if target_url is not None else settings.IMPACT_WEBHOOK_URL
        selected_domains = (
            list(allowed_domains)
            if allowed_domains is not None
            else settings.IMPACT_WEBHOOK_ALLOWED_DOMAINS
        )
        selected_secret = secret if secret is not None else settings.IMPACT_WEBHOOK_SECRET
        if not selected_url or not selected_domains or len(selected_secret) < 32:
            raise ImpactDeliveryNotConfigured(
                "Impact webhook delivery requires a URL, allowlist, and 32-character secret."
            )
        normalized_url, addresses = validate_target_url(
            selected_url,
            allowed_domains=selected_domains,
            resolver=self.resolver,
        )
        hostname = urlsplit(normalized_url).hostname
        assert hostname is not None
        body = canonical_event_body(outbox_event.payload)
        timestamp = str(int(timezone.now().timestamp()))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": hostname,
            "User-Agent": settings.HTTP_USER_AGENT,
            "X-ARIA-Event-ID": str(outbox_event.pipeline_event_id),
            "X-ARIA-Event-Topic": outbox_event.topic,
            "X-ARIA-Timestamp": timestamp,
            "X-ARIA-Signature": webhook_signature(selected_secret, timestamp, body),
        }
        pinned_url = build_pinned_url(normalized_url, addresses[0])
        try:
            with self.client.stream(
                "POST",
                pinned_url,
                content=body,
                headers=headers,
                extensions={"sni_hostname": hostname.encode("ascii")},
            ) as response:
                status_code = response.status_code
        except httpx.TimeoutException as error:
            raise RetryableImpactDeliveryError("Impact webhook request timed out.") from error
        except httpx.NetworkError as error:
            raise RetryableImpactDeliveryError("Impact webhook network request failed.") from error
        if 200 <= status_code < 300:
            return ImpactDeliveryReceipt(
                status_code=status_code,
                resolved_addresses=tuple(sorted(addresses)),
            )
        if status_code in {408, 425, 429} or status_code >= 500:
            raise RetryableImpactDeliveryError(
                f"Impact webhook returned retryable status {status_code}."
            )
        raise PermanentImpactDeliveryError(f"Impact webhook returned status {status_code}.")


@transaction.atomic
def _claim_impact_outbox_event(event_id: UUID | None = None) -> OutboxEvent | None:
    now = timezone.now()
    eligible = Q(
        status__in=(OutboxEvent.Status.PENDING, OutboxEvent.Status.FAILED),
        available_at__lte=now,
    ) | Q(status=OutboxEvent.Status.PUBLISHING, available_at__lte=now)
    events = OutboxEvent.objects.select_for_update(skip_locked=True).filter(
        eligible,
        topic="regulatory.impact.confirmed",
        attempts__lt=settings.IMPACT_WEBHOOK_MAX_ATTEMPTS,
        pipeline_event__reviewed_impact_publication__isnull=False,
    )
    if event_id is not None:
        events = events.filter(pk=event_id)
    event = events.order_by("available_at", "id").first()
    if event is None:
        return None
    event.status = OutboxEvent.Status.PUBLISHING
    event.attempts += 1
    event.available_at = now + timedelta(minutes=settings.IMPACT_WEBHOOK_STALE_MINUTES)
    event.last_error = ""
    event.save(update_fields=("status", "attempts", "available_at", "last_error"))
    return event


def _finish_delivery(
    event: OutboxEvent,
    *,
    error: ImpactDeliveryError | UnsafeTargetError | None = None,
) -> None:
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event.pk)
        if error is None:
            event.status = OutboxEvent.Status.PUBLISHED
            event.published_at = timezone.now()
            event.last_error = ""
        else:
            event.status = OutboxEvent.Status.FAILED
            event.published_at = None
            event.last_error = str(error)[:2000]
            if isinstance(
                error,
                (PermanentImpactDeliveryError, ImpactDeliveryNotConfigured, UnsafeTargetError),
            ):
                event.attempts = settings.IMPACT_WEBHOOK_MAX_ATTEMPTS
            else:
                retry_minutes = min(60, 2 ** max(0, event.attempts - 1))
                event.available_at = timezone.now() + timedelta(minutes=retry_minutes)
        event.save(
            update_fields=(
                "status",
                "attempts",
                "published_at",
                "last_error",
                "available_at",
            )
        )


def deliver_impact_outbox_event(
    event_id: UUID | None = None,
    *,
    client: ImpactWebhookClient | None = None,
) -> OutboxEvent | None:
    if not impact_delivery_enabled() and client is None:
        return None
    event = _claim_impact_outbox_event(event_id)
    if event is None:
        return None
    owns_client = client is None
    selected_client = client or ImpactWebhookClient()
    try:
        selected_client.deliver(event)
    except (
        ImpactDeliveryError,
        UnsafeTargetError,
    ) as error:
        _finish_delivery(event, error=error)
    else:
        _finish_delivery(event)
    finally:
        if owns_client:
            selected_client.close()
    event.refresh_from_db()
    return event


def deliver_pending_impact_webhooks(
    *,
    batch_size: int | None = None,
    client: ImpactWebhookClient | None = None,
) -> dict:
    selected_batch_size = batch_size or settings.IMPACT_WEBHOOK_BATCH_SIZE
    if selected_batch_size < 1 or selected_batch_size > 100:
        raise ValueError("Impact webhook batch size must be between 1 and 100.")
    if not impact_delivery_enabled() and client is None:
        return {"configured": False, "processed": 0, "published": 0, "failed": 0}
    owns_client = client is None
    selected_client = client or ImpactWebhookClient()
    published = 0
    failed = 0
    processed = 0
    try:
        for _ in range(selected_batch_size):
            event = deliver_impact_outbox_event(client=selected_client)
            if event is None:
                break
            processed += 1
            if event.status == OutboxEvent.Status.PUBLISHED:
                published += 1
            else:
                failed += 1
    finally:
        if owns_client:
            selected_client.close()
    return {
        "configured": True,
        "processed": processed,
        "published": published,
        "failed": failed,
    }
