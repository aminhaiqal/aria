import select
import socket
import socketserver
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from aria.browser.contracts import CapturedNetworkExchange
from aria.fetching.client import Resolver, UnsafeTargetError, system_resolver, validate_target_url

ALLOWED_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_RESOURCE_TYPES = frozenset(
    {"document", "stylesheet", "script", "xhr", "fetch", "other"}
)


@dataclass(frozen=True)
class BrowserRequestDecision:
    allowed: bool
    sequence: int
    reason: str = ""
    resolved_addresses: tuple[str, ...] = ()


class BrowserNetworkPolicy:
    def __init__(
        self,
        *,
        allowed_domains: Iterable[str],
        max_requests: int,
        max_redirects: int,
        resolver: Resolver = system_resolver,
    ):
        self.allowed_domains = tuple(allowed_domains)
        self.max_requests = max_requests
        self.max_redirects = max_redirects
        self.resolver = resolver
        self.exchanges: list[CapturedNetworkExchange] = []
        self.fatal_reason = ""

    @property
    def request_count(self) -> int:
        return len(self.exchanges)

    @property
    def blocked_request_count(self) -> int:
        return sum(exchange.disposition == "blocked" for exchange in self.exchanges)

    @property
    def resolved_addresses(self) -> list[str]:
        return sorted(
            {
                address
                for exchange in self.exchanges
                for address in exchange.resolved_addresses
            }
        )

    def inspect_request(
        self,
        *,
        url: str,
        method: str,
        resource_type: str,
        redirect_count: int = 0,
    ) -> BrowserRequestDecision:
        sequence = len(self.exchanges) + 1
        normalized_method = method.upper()
        reason = ""
        addresses: list[str] = []
        if sequence > self.max_requests:
            reason = "request_limit_exceeded"
            self.fatal_reason = reason
        elif redirect_count > self.max_redirects:
            reason = "redirect_limit_exceeded"
            self.fatal_reason = reason
        elif normalized_method not in ALLOWED_METHODS:
            reason = "unsafe_http_method"
        elif resource_type not in ALLOWED_RESOURCE_TYPES:
            reason = "blocked_resource_type"
        else:
            try:
                _, addresses = validate_target_url(
                    url,
                    allowed_domains=self.allowed_domains,
                    resolver=self.resolver,
                )
            except UnsafeTargetError as error:
                reason = str(error)[:255]
                if resource_type == "document":
                    self.fatal_reason = self.fatal_reason or reason
            except Exception as error:
                reason = str(error)[:255]
                if resource_type == "document":
                    self.fatal_reason = self.fatal_reason or reason

        self.exchanges.append(
            CapturedNetworkExchange(
                sequence=sequence,
                requested_url=url,
                method=normalized_method,
                resource_type=resource_type,
                disposition="blocked" if reason else "allowed",
                block_reason=reason,
                resolved_addresses=addresses,
            )
        )
        return BrowserRequestDecision(
            allowed=not reason,
            sequence=sequence,
            reason=reason,
            resolved_addresses=tuple(addresses),
        )

    def attach_response(
        self,
        *,
        url: str,
        method: str,
        resource_type: str,
        status: int,
        content_type: str,
        body: bytes | None,
    ) -> None:
        for exchange in self.exchanges:
            if (
                exchange.disposition == "allowed"
                and exchange.response_status is None
                and exchange.requested_url == url
                and exchange.method == method.upper()
                and exchange.resource_type == resource_type
            ):
                exchange.response_status = status
                exchange.content_type = content_type[:255]
                exchange.body = body
                return


class _ProxyBudget:
    def __init__(
        self,
        *,
        allowed_domains: tuple[str, ...],
        max_response_bytes: int,
        resolver: Resolver,
    ):
        self.allowed_domains = allowed_domains
        self.max_response_bytes = max_response_bytes
        self.resolver = resolver
        self.response_bytes = 0
        self.resolved_addresses: set[str] = set()
        self.limit_exceeded = False
        self.unsafe_reason = ""
        self.lock = threading.Lock()

    def resolve(self, hostname: str, port: int) -> list[str]:
        try:
            _, addresses = validate_target_url(
                f"https://{hostname}:{port}/",
                allowed_domains=self.allowed_domains,
                resolver=self.resolver,
            )
        except UnsafeTargetError as error:
            with self.lock:
                self.unsafe_reason = str(error)
            raise
        with self.lock:
            self.resolved_addresses.update(addresses)
        return addresses

    def add_response_bytes(self, count: int) -> bool:
        with self.lock:
            self.response_bytes += count
            if self.response_bytes > self.max_response_bytes:
                self.limit_exceeded = True
                return False
            return True


class _ThreadingProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _PinnedConnectHandler(socketserver.StreamRequestHandler):
    timeout = 35

    def handle(self) -> None:
        budget: _ProxyBudget = self.server.budget  # type: ignore[attr-defined]
        try:
            request_line = self.rfile.readline(8193)
            if len(request_line) > 8192:
                return
            parts = request_line.decode("iso-8859-1").strip().split()
            if len(parts) != 3 or parts[0].upper() != "CONNECT":
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                return
            target = parts[1]
            hostname, separator, port_text = target.rpartition(":")
            if not separator or not hostname:
                raise UnsafeTargetError("Proxy CONNECT target is invalid.")
            port = int(port_text)
            self._consume_headers()
            addresses = budget.resolve(hostname, port)
            upstream = socket.create_connection((addresses[0], port), timeout=self.timeout)
        except Exception:
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return

        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            client = self.connection
            client.setblocking(False)
            upstream.setblocking(False)
            while True:
                readable, _, exceptional = select.select(
                    (client, upstream), (), (client, upstream), self.timeout
                )
                if exceptional or not readable:
                    break
                for source in readable:
                    try:
                        chunk = source.recv(65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        return
                    if source is upstream:
                        if not budget.add_response_bytes(len(chunk)):
                            return
                        destination = client
                    else:
                        destination = upstream
                    if not self._send_all(destination, chunk):
                        return
        finally:
            upstream.close()

    def _send_all(self, destination: socket.socket, chunk: bytes) -> bool:
        remaining = memoryview(chunk)
        while remaining:
            try:
                sent = destination.send(remaining)
            except BlockingIOError:
                _, writable, exceptional = select.select(
                    (), (destination,), (destination,), self.timeout
                )
                if exceptional or not writable:
                    return False
                continue
            if sent <= 0:
                return False
            remaining = remaining[sent:]
        return True

    def _consume_headers(self) -> None:
        total = 0
        while True:
            line = self.rfile.readline(8193)
            total += len(line)
            if total > 65536 or len(line) > 8192:
                raise UnsafeTargetError("Proxy request headers are too large.")
            if line in {b"\r\n", b"\n", b""}:
                return


class PinnedHTTPSProxy:
    def __init__(
        self,
        *,
        allowed_domains: Iterable[str],
        max_response_bytes: int,
        resolver: Resolver = system_resolver,
    ):
        self.budget = _ProxyBudget(
            allowed_domains=tuple(allowed_domains),
            max_response_bytes=max_response_bytes,
            resolver=resolver,
        )
        self.server: _ThreadingProxyServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self.server is None:
            raise RuntimeError("Browser proxy has not started.")
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def response_bytes(self) -> int:
        return self.budget.response_bytes

    @property
    def limit_exceeded(self) -> bool:
        return self.budget.limit_exceeded

    @property
    def resolved_addresses(self) -> list[str]:
        return sorted(self.budget.resolved_addresses)

    @property
    def unsafe_reason(self) -> str:
        return self.budget.unsafe_reason

    def __enter__(self):
        self.server = _ThreadingProxyServer(("127.0.0.1", 0), _PinnedConnectHandler)
        self.server.budget = self.budget  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
