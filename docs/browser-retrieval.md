# Phase 3D.6 bounded self-hosted browser retrieval

Phase 3D.6 adds an explicitly opt-in Chromium path for official listings whose publication links do
not exist in the original HTML response. It is not a general web crawler. Static HTML, RSS, direct
document, and monitored-resource retrieval continue to use the smaller HTTP workers.

## Execution boundary

Only a `javascript_listing` endpoint with `requires_javascript=true` can create a browser capture.
Celery routes that work to the dedicated `browser_fetch` queue. The Compose service runs one
capture at a time as the non-root `aria` user with all Linux capabilities dropped except the
Chromium sandbox's narrow `SYS_CHROOT` requirement, the Playwright 1.61 seccomp profile, a
read-only root filesystem, a bounded temporary filesystem, a 1 GiB shared-memory allocation,
PID/CPU/memory limits, and `no-new-privileges`.

Playwright and Chromium are installed only in the `browser` image target. The ordinary API,
scheduler, HTTP worker, and OCR worker do not contain the browser runtime.
The vendored seccomp profile is byte-for-byte Playwright's `v1.61.0` profile (SHA-256
`cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`) and must be updated with
the pinned runtime rather than independently.

Every browser request passes through both a context-wide route policy and a local CONNECT proxy.
The policy:

- permits only HTTPS on port 443 and only the endpoint's explicit domain allowlist;
- resolves every target and rejects non-public, invalid, or empty DNS results;
- re-resolves and pins the proxy connection to a validated public IP to constrain DNS rebinding;
- permits only `GET`, `HEAD`, and `OPTIONS` and blocks side-effecting methods;
- blocks media, fonts, WebSockets, downloads, popups, service workers, and non-proxied WebRTC;
- caps navigation time, render wait, redirects, requests, encrypted response bytes, captured body
  bytes, and rendered DOM bytes.

The default limits are configurable with the `ARIA_BROWSER_*` variables in `.env.example`. Raising
them expands the retrieval and resource boundary and should be reviewed as a source-specific
change.

## Immutable evidence

Each source run has one `BrowserCapture`, with an attempt counter for retry history. A completed
capture records:

- requested and final URL, response status and selected headers, redirect chain, and resolved IPs;
- the original main-document response as a content-addressed `RawArtifact`;
- the rendered DOM as a second `RawArtifact` and an append-only `browser_rendered_dom` derivative;
- the pinned Playwright/Chromium toolchain and canonical configuration hash;
- request, blocked-request, and transport-byte totals;
- append-only `BrowserNetworkExchange` rows, including bounded document/XHR/fetch response bodies
  when eligible.

Browser artifacts use the existing filesystem or Cloudflare R2 artifact backend. They are stored
under the collection's `sources/<authority>/<collection>/browser/` namespace before candidate
reconciliation. A worker redelivery reuses the completed immutable capture instead of opening the
official site again.

Capture and exchange metadata are available through staff-only, read-only API endpoints:

```text
/api/v1/browser-captures/
/api/v1/browser-network-exchanges/
```

The source detail page in `/console/` shows capture status, request bounds, blocked requests,
transport bytes, rendered DOM hash, and failures.

## Source configuration

The connector uses the existing static listing selectors against the rendered DOM. Browser-only
keys are optional:

```json
{
  "link_selector": "main a.publication[href]",
  "include_path_prefixes": ["/publications/"],
  "document_extensions": [".pdf", ".docx"],
  "max_candidates": 20,
  "ready_selector": "main[data-loaded='true']",
  "render_wait_milliseconds": 750
}
```

`ready_selector` must become attached within the navigation timeout. The render wait is capped at
five seconds. Browser discovery intentionally does not follow detail pages inside the capture;
rendered detail links become independently approved monitored resources through normal
reconciliation.

## Operations and verification

Build and start the isolated worker with:

```bash
docker compose build browser-worker
docker compose up -d migrate api worker browser-worker beat
docker compose logs -f browser-worker
```

For a configured JavaScript endpoint, the existing console “Poll endpoint now” action creates the
source run. The discovery worker hands it to `browser_fetch`; it is not executed by a general
worker.

The focused suite covers the opt-in model gate, append-only evidence, staff-only API, SSRF and DNS
rebinding protection, read-only request policy, resource/request/redirect/byte limits, artifact
lineage, rendered candidate parsing, safe replay, and dedicated-queue handoff:

```bash
docker compose exec -T api python manage.py test tests.test_browser
```

The next operational slice is not to enable browser retrieval globally. It is to select one
explicitly approved JavaScript-only official source, encode its narrow allowlist/selectors, run a
bounded pilot, and evaluate captured evidence and candidate precision before scheduling it.
