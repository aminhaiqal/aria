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

- permits only HTTPS on port 443 and only the endpoint's explicit official domains plus any
  source-reviewed script/style dependency domains;
- resolves every target and rejects non-public, invalid, or empty DNS results;
- re-resolves and pins the proxy connection to a validated public IP to constrain DNS rebinding;
- permits `GET`, `HEAD`, and `OPTIONS` by default; a JavaScript source may additionally declare at
  most eight exact, query-free paths for bounded read-only `POST` requests on its primary official
  domain;
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
- append-only `BrowserNetworkExchange` rows, including bounded document, script, stylesheet,
  XHR, and fetch response bodies when eligible, and hash/byte-size evidence for request bodies
  without retaining their raw data.

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
  "render_wait_milliseconds": 750,
  "browser_dependency_domains": ["cdn.datatables.net"],
  "browser_read_only_post_paths": ["/api/publications"]
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

## Phase 3D.7 first official source pilot

The first pilot is the Attorney General's Chambers of Malaysia (AGC) Federal Legislation portal's
[Updated Principal Acts](https://lom.agc.gov.my/principal.php?type=updated) listing. The original
HTML contains the table structure but no publication rows. Its official JavaScript uses
DataTables to load rows from `POST /json-updated-2024.php`; the source also imports DataTables
assets from `cdn.datatables.net` and `cdnjs.cloudflare.com`.

`seed_agc` registers this exact source with:

- candidate scope restricted to official HTTPS PDFs below
  `/ilims/upload/portal/akta/outputaktap/`;
- two network-only CDN dependency hosts, which are never added to the publication candidate
  allowlist;
- one exact, query-free read-only POST path on `lom.agc.gov.my` with a 64 KiB request-body cap;
- a maximum of 20 candidates per capture; and
- `is_enabled=false`, `next_poll_at=null`, so the scheduler cannot run it during admission.

AGC later changed the rendered link value to a signed `processFile.php` wrapper. Connector version
2 decodes only the explicitly configured query parameter and requires a UTF-8 payload shaped as an
HTTPS target plus a 64-character SHA-256 value. Decoding is not admission: every target continues
through the official-domain, path-prefix, and PDF-extension checks, and direct official PDF links
remain supported if AGC changes back.

Run the controlled workflow with:

```bash
make seed-agc
make pilot-agc
make pilot-agc
make audit-agc
make promote-agc
```

The admission audit is durable through a `browser.admission.evaluated` pipeline event and never
enables the endpoint. Promotion requires every gate to pass: two completed captures, immutable
original/rendered evidence, bounded approved network behavior, no qualifying links in either raw
HTML response, official-path PDF precision, identical candidate URL sets, and artifact →
successful extraction → knowledge-graph lineage for every candidate in the latest run.

If the official listing changes between the two captures, repeatability correctly fails. Review
the change and take two new close-together captures rather than weakening the gate. After all
checks pass, `promote_browser_source --confirm` re-runs the gates inside a database transaction,
records audit and pipeline events, and schedules the first configured hourly check. It cannot bypass a failed
gate.

The initial admission completed on 2026-08-06 with two matching 20-candidate captures and 20/20
artifact, extraction, and knowledge-graph lineage checks. The source was promoted through the
guarded command and now polls at the governed hourly cadence; reseeding preserves that promoted state. Connector version 2
was verified on 2026-09-02 with a fresh bounded 20-candidate capture and 20/20 artifact checks.
