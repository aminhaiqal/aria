from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version

from django.conf import settings

from aria.browser.contracts import (
    BrowserDependencyError,
    BrowserDOMLimitError,
    BrowserNavigationError,
    BrowserRenderResult,
    BrowserRequestLimitError,
    BrowserResponseLimitError,
)
from aria.browser.network import BrowserNetworkPolicy, PinnedHTTPSProxy
from aria.fetching.client import (
    PermanentFetchError,
    Resolver,
    RetryableFetchError,
    UnsafeTargetError,
    system_resolver,
)

CAPTURE_CONTENT_TYPES = (
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "text/",
)
SAFE_RESPONSE_HEADERS = frozenset(
    {"cache-control", "content-language", "content-type", "date", "etag", "last-modified"}
)


def _redirect_count(request) -> int:
    count = 0
    current = request.redirected_from
    while current is not None:
        count += 1
        current = current.redirected_from
    return count


def _redirect_chain(request) -> list[dict[str, str | int]]:
    requests = []
    current = request
    while current.redirected_from is not None:
        previous = current.redirected_from
        requests.append((previous, current.url))
        current = previous
    chain = []
    for previous, location in reversed(requests):
        response = previous.response()
        chain.append(
            {
                "url": previous.url,
                "status": response.status if response is not None else 0,
                "location": location,
            }
        )
    return chain


class PlaywrightBrowserRunner:
    def __init__(self, *, resolver: Resolver = system_resolver):
        self.resolver = resolver

    def render(
        self,
        url: str,
        *,
        allowed_domains: Iterable[str],
        ready_selector: str = "",
        render_wait_milliseconds: int | None = None,
    ) -> BrowserRenderResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserDependencyError(
                "The isolated browser worker requires the pinned Playwright package."
            ) from error

        policy = BrowserNetworkPolicy(
            allowed_domains=allowed_domains,
            max_requests=settings.BROWSER_MAX_REQUESTS,
            max_redirects=settings.BROWSER_MAX_REDIRECTS,
            resolver=self.resolver,
        )
        responses = []
        wait_ms = (
            settings.BROWSER_RENDER_WAIT_MILLISECONDS
            if render_wait_milliseconds is None
            else min(5000, max(0, render_wait_milliseconds))
        )
        timeout_ms = settings.BROWSER_NAVIGATION_TIMEOUT_SECONDS * 1000

        with PinnedHTTPSProxy(
            allowed_domains=allowed_domains,
            max_response_bytes=settings.BROWSER_MAX_RESPONSE_BYTES,
            resolver=self.resolver,
        ) as proxy:
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        chromium_sandbox=True,
                        ignore_default_args=["--disable-dev-shm-usage"],
                        proxy={"server": proxy.url},
                        args=[
                            "--disable-background-networking",
                            "--disable-breakpad",
                            "--disable-component-update",
                            "--disable-default-apps",
                            "--disable-domain-reliability",
                            "--disable-extensions",
                            "--disable-features=MediaRouter,OptimizationHints",
                            "--disable-quic",
                            "--disable-sync",
                            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                            "--no-first-run",
                            "--no-pings",
                        ],
                    )
                    toolchain = {
                        "playwright": self._playwright_version(),
                        "chromium": browser.version,
                    }
                    context = browser.new_context(
                        accept_downloads=False,
                        bypass_csp=False,
                        ignore_https_errors=False,
                        java_script_enabled=True,
                        locale="en-MY",
                        service_workers="block",
                        user_agent=settings.HTTP_USER_AGENT,
                    )

                    def route_request(route) -> None:
                        request = route.request
                        decision = policy.inspect_request(
                            url=request.url,
                            method=request.method,
                            resource_type=request.resource_type,
                            redirect_count=_redirect_count(request),
                        )
                        if decision.allowed:
                            route.continue_()
                        else:
                            route.abort("blockedbyclient")

                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", lambda websocket: websocket.close())
                    page = context.new_page()
                    page.set_default_timeout(timeout_ms)
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    page.on("download", lambda download: download.cancel())
                    page.on("popup", lambda popup: popup.close())
                    page.on("response", lambda response: responses.append(response))
                    main_response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    if main_response is None:
                        raise BrowserNavigationError("Navigation produced no document response.")
                    if main_response.status in {408, 425, 429} or main_response.status >= 500:
                        raise RetryableFetchError(
                            f"Browser navigation returned retryable status "
                            f"{main_response.status}."
                        )
                    if main_response.status >= 400:
                        raise PermanentFetchError(
                            f"Browser navigation returned status {main_response.status}."
                        )
                    if ready_selector:
                        page.locator(ready_selector).first.wait_for(
                            state="attached",
                            timeout=timeout_ms,
                        )
                    if wait_ms:
                        page.wait_for_timeout(wait_ms)
                    rendered_content = page.content().encode("utf-8")
                    if len(rendered_content) > settings.BROWSER_MAX_DOM_BYTES:
                        raise BrowserDOMLimitError(
                            f"Rendered DOM exceeded {settings.BROWSER_MAX_DOM_BYTES} bytes."
                        )
                    original_content = main_response.body()
                    self._attach_responses(policy, responses)
                    result = BrowserRenderResult(
                        requested_url=url,
                        final_url=page.url,
                        response_status=main_response.status,
                        response_headers={
                            key.lower(): value
                            for key, value in main_response.headers.items()
                            if key.lower() in SAFE_RESPONSE_HEADERS
                        },
                        redirect_chain=_redirect_chain(main_response.request),
                        resolved_addresses=sorted(
                            set(policy.resolved_addresses) | set(proxy.resolved_addresses)
                        ),
                        original_content=original_content,
                        rendered_content=rendered_content,
                        network_exchanges=tuple(policy.exchanges),
                        request_count=policy.request_count,
                        blocked_request_count=policy.blocked_request_count,
                        response_bytes=proxy.response_bytes,
                        toolchain=toolchain,
                    )
                    context.close()
                    browser.close()
            except PlaywrightTimeoutError as error:
                raise BrowserNavigationError("Browser navigation timed out.") from error
            except PlaywrightError as error:
                if policy.fatal_reason == "request_limit_exceeded":
                    raise BrowserRequestLimitError("Browser request limit exceeded.") from error
                if proxy.limit_exceeded:
                    raise BrowserResponseLimitError(
                        "Browser response byte limit exceeded."
                    ) from error
                if proxy.unsafe_reason:
                    raise UnsafeTargetError(proxy.unsafe_reason) from error
                if policy.fatal_reason:
                    raise UnsafeTargetError(policy.fatal_reason) from error
                raise BrowserNavigationError(str(error)) from error

        if proxy.limit_exceeded:
            raise BrowserResponseLimitError("Browser response byte limit exceeded.")
        if policy.fatal_reason == "request_limit_exceeded":
            raise BrowserRequestLimitError("Browser request limit exceeded.")
        return result

    @staticmethod
    def _attach_responses(policy: BrowserNetworkPolicy, responses) -> None:
        for response in responses:
            request = response.request
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            body = None
            declared_size = response.headers.get("content-length", "")
            within_declared_limit = not declared_size or (
                declared_size.isdigit()
                and int(declared_size) <= settings.BROWSER_MAX_CAPTURE_BODY_BYTES
            )
            if (
                request.resource_type in {"document", "xhr", "fetch"}
                and any(content_type.startswith(prefix) for prefix in CAPTURE_CONTENT_TYPES)
                and within_declared_limit
            ):
                try:
                    candidate = response.body()
                except Exception:
                    candidate = b""
                if len(candidate) <= settings.BROWSER_MAX_CAPTURE_BODY_BYTES:
                    body = candidate
            policy.attach_response(
                url=request.url,
                method=request.method,
                resource_type=request.resource_type,
                status=response.status,
                content_type=content_type,
                body=body,
            )

    @staticmethod
    def _playwright_version() -> str:
        try:
            return version("playwright")
        except PackageNotFoundError:
            return settings.BROWSER_PLAYWRIGHT_VERSION
