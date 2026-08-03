# Phase 2 retrieval

## First official source

The first approved source is the Personal Data Protection Commissioner, Malaysia (JPDP) Act 709
regulatory library:

<https://www.pdp.gov.my/ppdpv1/en/akta/pdp-act-2010-en/>

Run `python manage.py seed_jpdp --run` inside the API container to register the authority,
collection, endpoint, and connector configuration and then queue a manual source run. The seed is
idempotent. The connector archives at most 20 qualifying links per run during the pilot, with
direct publication files prioritized over publication pages.

Connector configuration v2 follows each allowlisted BetterDocs detail page to its primary official
file. The fetch candidate uses the file URL, while `document_identity_url` retains the official
landing-page URL for stable version projection. Detail expansion still uses the HTTPS/domain/IP
safety boundary and is capped by explicit candidate limits.

## Retrieval boundary

Every request must satisfy all of these rules:

1. Use HTTPS on port 443 without URL credentials.
2. Match the source endpoint's domain allowlist, including explicitly allowed subdomains.
3. Resolve only to globally routable IP addresses. DNS failures are retryable; private, loopback,
   link-local, multicast, and otherwise non-public targets are quarantined. The request socket is
   pinned to the validated address while the official hostname is retained for TLS SNI, the
   `Host` header, and provenance, closing the DNS-rebinding window.
4. Revalidate every redirect before following it.
5. Respect the configured response-size, redirect, connect-timeout, read-timeout, and per-domain
   pacing limits.
6. Disable environment proxy inheritance so a proxy cannot bypass target validation.

Conditional `If-None-Match` and `If-Modified-Since` headers are derived from the previous artifact
observation. A `304 Not Modified` response creates a fresh observation pointing to the previous
artifact rather than writing bytes again.

## Evidence storage

Raw bytes are addressed by their SHA-256 digest using keys of this form:

```text
sha256/ab/cd/abcdef…
```

The filesystem backend performs an exclusive create and verifies any existing file before reuse.
The S3-compatible backend verifies an existing object's digest metadata and length, and is
compatible with Cloudflare R2. Database artifact and observation models are append-only; fetch
attempts retain success and failure history.

The administrator API exposes read-only endpoints for fetch attempts, artifacts, artifact
observations, and authenticated artifact downloads.

| Resource | Endpoint |
| --- | --- |
| Source runs | `/api/v1/source-runs/` |
| Discovered candidates | `/api/v1/candidates/` |
| Fetch attempts | `/api/v1/fetch-attempts/` |
| Raw artifacts | `/api/v1/artifacts/` |
| Artifact observations | `/api/v1/artifact-observations/` |
| Artifact bytes | `/api/v1/artifacts/{id}/content/` |

All API routes require a Django administrator session or Basic authentication.

## Storage configuration

The default configuration is fully self-hosted:

```dotenv
ARIA_OBJECT_STORAGE_BACKEND=filesystem
ARIA_OBJECT_STORAGE_ROOT=/var/lib/aria/artifacts
```

The Compose stack mounts that path from the private `artifact-data` named volume. To use
Cloudflare R2, switch to the S3-compatible backend and provide the prepared account details:

```dotenv
ARIA_OBJECT_STORAGE_BACKEND=s3
ARIA_OBJECT_STORAGE_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
ARIA_OBJECT_STORAGE_BUCKET=aria-artifacts
ARIA_OBJECT_STORAGE_ACCESS_KEY=<access-key-id>
ARIA_OBJECT_STORAGE_SECRET_KEY=<secret-access-key>
ARIA_OBJECT_STORAGE_REGION=auto
```

Never commit the real account ID or credentials. Store them only in the ignored `.env` file or a
deployment secret store.

## Operations

Queue one manual discovery run:

```bash
docker compose exec api python manage.py seed_jpdp --run
```

Watch discovery and artifact retrieval:

```bash
docker compose logs --follow worker
```

To backfill primary links already present in archived HTML without refetching detail pages:

```bash
docker compose exec api python manage.py route_linked_publications --queue
```

The route run is fingerprinted from the current immutable HTML versions. Repeating it against an
unchanged operational corpus reuses the same source run and does not queue completed candidates.

A completed `SourceRun` confirms that discovery persisted its candidates and queued their fetch
tasks. The run is fully archived when its candidates have terminal `FetchAttempt` records and the
successful candidates are in `raw_stored` state.

## Pilot verification

On 2026-08-03, the live JPDP pilot discovered and archived 20 candidates: one PDF and 19 official
HTML publication pages. All 8,428,140 stored bytes were read back and matched their recorded
SHA-256 digests. A replay created 20 new provenance observations while retaining 20 unique raw
artifacts; the JPDP PDF answered with `304 Not Modified` and reused its original artifact.

## Downstream boundary

Extraction consumes only archived artifacts, never a live URL. Phase 3A now adds deterministic
HTML/PDF extraction, OCR routing, stable document identity, immutable versions, and evidence graph
projection while preserving raw artifact and observation links through every derived record. See
[Phase 3A extraction and evidence graph](knowledge-graph.md).

On 2026-08-04, linked-file remediation routed exactly 18 JPDP detail pages. All 18 official PDFs
were archived as content-addressed Cloudflare R2 artifacts. Thirteen produced native text; five
image-only PDFs were retained and marked `ocr_required` before Phase F processing.

Phase F then created five searchable-PDF and five text-sidecar derivatives under the R2
`derived/ocr/sha256/` namespace. The five original source hashes and bytes remain unchanged. Each
derivative has an append-only source relationship, processing profile, configuration hash,
toolchain record, and page metrics. See [Phase F self-hosted OCR](ocr.md).
