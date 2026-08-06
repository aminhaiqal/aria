from dataclasses import dataclass, field


@dataclass
class CapturedNetworkExchange:
    sequence: int
    requested_url: str
    method: str
    resource_type: str
    disposition: str
    block_reason: str = ""
    response_status: int | None = None
    content_type: str = ""
    body: bytes | None = None
    resolved_addresses: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BrowserRenderResult:
    requested_url: str
    final_url: str
    response_status: int
    response_headers: dict[str, str]
    redirect_chain: list[dict[str, str | int]]
    resolved_addresses: list[str]
    original_content: bytes
    rendered_content: bytes
    network_exchanges: tuple[CapturedNetworkExchange, ...]
    request_count: int
    blocked_request_count: int
    response_bytes: int
    toolchain: dict[str, str]


class BrowserCaptureError(RuntimeError):
    pass


class BrowserDependencyError(BrowserCaptureError):
    pass


class BrowserNavigationError(BrowserCaptureError):
    pass


class BrowserRequestLimitError(BrowserCaptureError):
    pass


class BrowserResponseLimitError(BrowserCaptureError):
    pass


class BrowserDOMLimitError(BrowserCaptureError):
    pass
