import ipaddress
import math
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import certifi
import httpx
from django.conf import settings
from redis import Redis


class FetchError(RuntimeError):
    pass


class UnsafeTargetError(FetchError):
    pass


class RetryableFetchError(FetchError):
    pass


class PermanentFetchError(FetchError):
    pass


class ResponseTooLargeError(PermanentFetchError):
    pass


class UnexpectedContentTypeError(PermanentFetchError):
    pass


@dataclass(frozen=True)
class FetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    redirect_chain: list[dict[str, str | int]]
    resolved_addresses: list[str]
    content: bytes


class RateLimiter:
    def acquire(self, hostname: str) -> None:
        return None


class RedisDomainRateLimiter(RateLimiter):
    def __init__(self, redis_url: str, interval_seconds: float):
        self.redis = Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        self.interval_seconds = max(interval_seconds, 0)

    def acquire(self, hostname: str) -> None:
        if not self.interval_seconds:
            return
        key = f"aria:http-rate:{hostname}"
        expiry_ms = max(1, math.ceil(self.interval_seconds * 1000))
        while not self.redis.set(key, "1", nx=True, px=expiry_ms):
            time.sleep(min(0.1, self.interval_seconds))


Resolver = Callable[[str, int], Iterable[str]]


def build_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    supplemental_bundle = settings.HTTP_SUPPLEMENTAL_CA_BUNDLE
    if supplemental_bundle:
        context.load_verify_locations(cafile=supplemental_bundle)
    return context


def system_resolver(hostname: str, port: int) -> list[str]:
    addresses = {
        result[4][0] for result in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    }
    return sorted(addresses)


def hostname_is_allowed(hostname: str, allowed_domains: Iterable[str]) -> bool:
    hostname = hostname.rstrip(".").lower()
    for allowed_domain in allowed_domains:
        allowed = allowed_domain.rstrip(".").lower()
        if hostname == allowed or hostname.endswith(f".{allowed}"):
            return True
    return False


def build_pinned_url(url: str, address: str) -> str:
    parsed = urlsplit(url)
    network_location = f"[{address}]" if ":" in address else address
    return urlunsplit((parsed.scheme, network_location, parsed.path or "/", parsed.query, ""))


def validate_target_url(
    url: str,
    *,
    allowed_domains: Iterable[str],
    resolver: Resolver = system_resolver,
) -> tuple[str, list[str]]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise UnsafeTargetError("Only HTTPS targets are allowed.")
    if parsed.username or parsed.password:
        raise UnsafeTargetError("Target URLs cannot contain credentials.")
    if not parsed.hostname:
        raise UnsafeTargetError("Target URL does not contain a hostname.")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise UnsafeTargetError("Target URL contains an invalid port.") from error
    if port != 443:
        raise UnsafeTargetError("Only the standard HTTPS port is allowed.")

    hostname = parsed.hostname.encode("idna").decode("ascii").rstrip(".").lower()
    if not hostname_is_allowed(hostname, allowed_domains):
        raise UnsafeTargetError(f"Target hostname '{hostname}' is not allowlisted.")
    try:
        addresses = list(resolver(hostname, port))
    except OSError as error:
        raise RetryableFetchError(f"Unable to resolve target hostname '{hostname}'.") from error
    if not addresses:
        raise RetryableFetchError(f"Target hostname '{hostname}' did not resolve.")
    for address in addresses:
        try:
            ip_address = ipaddress.ip_address(address)
        except ValueError as error:
            raise UnsafeTargetError(f"Resolver returned an invalid address: {address}") from error
        if not ip_address.is_global:
            raise UnsafeTargetError(
                f"Target hostname '{hostname}' resolves to a non-public address."
            )

    normalized = urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))
    return normalized, addresses


class SafeHttpClient:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int | None = None,
        max_redirects: int | None = None,
    ):
        self.resolver = resolver
        self.rate_limiter = rate_limiter or RedisDomainRateLimiter(
            settings.REDIS_URL,
            settings.HTTP_MIN_DOMAIN_INTERVAL_SECONDS,
        )
        self.max_response_bytes = max_response_bytes or settings.HTTP_MAX_RESPONSE_BYTES
        self.max_redirects = settings.HTTP_MAX_REDIRECTS if max_redirects is None else max_redirects
        timeout = httpx.Timeout(
            settings.HTTP_READ_TIMEOUT_SECONDS,
            connect=settings.HTTP_CONNECT_TIMEOUT_SECONDS,
        )
        self.client = httpx.Client(
            headers={"User-Agent": settings.HTTP_USER_AGENT, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            verify=build_tls_context(),
        )

    def close(self) -> None:
        self.client.close()

    def fetch(
        self,
        url: str,
        *,
        allowed_domains: Iterable[str],
        headers: dict[str, str] | None = None,
    ) -> FetchResponse:
        requested_url = url
        current_url = url
        redirect_chain: list[dict[str, str | int]] = []
        all_addresses: set[str] = set()

        for redirect_number in range(self.max_redirects + 1):
            current_url, addresses = validate_target_url(
                current_url,
                allowed_domains=allowed_domains,
                resolver=self.resolver,
            )
            all_addresses.update(addresses)
            hostname = urlsplit(current_url).hostname
            assert hostname is not None
            self.rate_limiter.acquire(hostname)
            request_headers = dict(headers or {})
            request_headers["Host"] = hostname
            pinned_url = build_pinned_url(current_url, addresses[0])
            try:
                with self.client.stream(
                    "GET",
                    pinned_url,
                    headers=request_headers,
                    extensions={"sni_hostname": hostname.encode("ascii")},
                ) as response:
                    if response.status_code == 304:
                        return FetchResponse(
                            requested_url=requested_url,
                            final_url=current_url,
                            status_code=response.status_code,
                            headers=dict(response.headers),
                            redirect_chain=redirect_chain,
                            resolved_addresses=sorted(all_addresses),
                            content=b"",
                        )
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise PermanentFetchError(
                                f"Redirect response {response.status_code} has no Location header."
                            )
                        if redirect_number >= self.max_redirects:
                            raise PermanentFetchError("Maximum redirect count exceeded.")
                        next_url = urljoin(current_url, location)
                        redirect_chain.append(
                            {
                                "url": current_url,
                                "status": response.status_code,
                                "location": next_url,
                            }
                        )
                        current_url = next_url
                        continue

                    if response.status_code in {408, 425, 429} or response.status_code >= 500:
                        raise RetryableFetchError(
                            f"Remote server returned retryable status {response.status_code}."
                        )
                    if response.status_code >= 400:
                        raise PermanentFetchError(
                            f"Remote server returned status {response.status_code}."
                        )
                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            parsed_size = int(declared_size)
                        except ValueError as error:
                            raise PermanentFetchError(
                                "Response has an invalid Content-Length header."
                            ) from error
                        if parsed_size > self.max_response_bytes:
                            raise ResponseTooLargeError(
                                f"Response declares {declared_size} bytes; limit is "
                                f"{self.max_response_bytes}."
                            )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            raise ResponseTooLargeError(
                                f"Response exceeded {self.max_response_bytes} bytes."
                            )
                    content = bytes(body)
                    return FetchResponse(
                        requested_url=requested_url,
                        final_url=current_url,
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        redirect_chain=redirect_chain,
                        resolved_addresses=sorted(all_addresses),
                        content=content,
                    )
            except httpx.TimeoutException as error:
                raise RetryableFetchError("HTTP request timed out.") from error
            except httpx.NetworkError as error:
                raise RetryableFetchError("HTTP network request failed.") from error

        raise PermanentFetchError("Maximum redirect count exceeded.")


def get_default_http_client() -> SafeHttpClient:
    return SafeHttpClient()
