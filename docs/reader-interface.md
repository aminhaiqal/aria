# Phase 4 authenticated reader interface

## Scope

Phase 4A made ARIA usable without exposing its operator controls. Phase 4B moves the authenticated
search and document experience to a self-hosted React and TypeScript application built by Vite from
repository-owned shadcn components. Django remains the authentication, authorization, API, CSRF,
and evidence-download boundary. The frontend does not create a second index, copy evidence into a
hosted search service, or grant reader accounts access to source polling, reviews, summaries,
publication, or repair actions.

The private preview provides:

- bounded hybrid, exact-text, and vector search across JPDP, AGC, and Parliament;
- authority, collection, and ARIA-version-date filters;
- document-grouped results with up to three exact, bounded passage excerpts;
- a current-version document page with all configured normalized sections and stable anchors;
- official-source links plus authenticated downloads of immutable reader-eligible artifacts;
- SHA-256, retrieval, extractor, quality, version, and comparison context; and
- completed GPT change summaries only when their structured output explicitly preserves the
  `legal_effect_not_assessed` boundary.

ARIA search ranks textual similarity. It does not determine legal authority, applicability,
commencement, legal effect, or the correct answer to a legal question. ARIA-version timestamps are
labelled as evidence-record dates rather than legal effective dates.

## Access boundary

Any active Django account can use `/reader/` and `/api/reader/v1/`. Staff status is not required.
The existing `/api/v1/`, `/console/`, and Django Admin permissions are unchanged. Reader artifact
downloads accept only artifacts linked through `VersionEvidence` or `NormalizedSection`; knowing
the UUID of another raw capture is insufficient.

Reader HTML and API responses receive a restrictive Content Security Policy, same-origin referrer
policy, disabled camera/geolocation/microphone permissions, `noindex`, and private no-store cache
headers. Scripts, fonts, and styles are compiled locally and served from hashed same-origin paths;
remote scripts and `eval` are not allowed. The style policy permits inline declarations because
Radix positions accessible popovers with runtime style values. Forms use Django sessions and CSRF,
and reader API requests use active authenticated sessions or Basic authentication. Search and
document API views have separate per-user throttle scopes.

Create the first account and open the reader:

```bash
docker compose exec api python manage.py createsuperuser
open http://127.0.0.1:8000/reader/
```

For a non-staff preview account, use Django Admin or `python manage.py shell` to create an active
user without assigning `is_staff`.

## Frontend architecture

The TypeScript workspace is `frontend/reader/`. Vite emits a hashed manifest, JavaScript, CSS, and
locally bundled Geist font files. The Docker `reader-ui` build stage creates that exact production
bundle; the one-shot `frontend-assets` service publishes it into the read-only `reader-ui` volume
mounted by Django and the browser-test worker. No Node process runs in production.

The React application uses Django-rendered bootstrap attributes for same-origin routes, the current
active user, and a CSRF token. It then reads only the versioned reader APIs. Search URL parameters
remain bookmarkable, document and passage URLs remain stable, and external links accept only HTTP
or HTTPS schemes. Django continues to verify artifact byte count and SHA-256 before every download.

The original templates remain a rollback path. Set this and restart the API:

```dotenv
ARIA_READER_FRONTEND=server
```

The normal default is `ARIA_READER_FRONTEND=react`. Rebuild or validate the frontend with:

```bash
make frontend-build
make frontend-test
make reader-e2e
```

`frontend-test` runs ESLint, four Vitest tests, axe-core accessibility scans, enforced coverage
floors, TypeScript compilation, and a production Vite build. `reader-e2e` runs the actual Django
login, React mount, shadcn select, CSP console-error gate, and logout flow in isolated Chromium.

## Retrieval contract

Search uses only the latest version of every non-superseded identity in an enabled,
evidence-eligible collection. PostgreSQL full-text search weights the document title first, then
section headings and normalized text. Semantic search uses the selected stored pgvector projection.
Hybrid mode combines their stable ranks with reciprocal-rank fusion, groups passages by document,
and uses UUIDs as deterministic tie-breakers.

Resource bounds are configured with:

```dotenv
ARIA_READER_EMBEDDING_PROVIDER=local_hash
ARIA_READER_PAGE_SIZE=10
ARIA_READER_SEARCH_RATE=60/min
ARIA_READER_DOCUMENT_RATE=120/min
```

The repository defaults to the self-hosted `local_hash` provider. A deployment that already has
complete OpenAI vectors can set `ARIA_READER_EMBEDDING_PROVIDER=openai`. In that mode only the query
text is sent to the OpenAI embeddings endpoint to create its vector; source PDFs, credentials,
provenance, and stored vectors remain local. The interface discloses this per search. If semantic
ranking fails, hybrid mode visibly falls back to full text; vector-only mode returns HTTP 503
instead of disguising a different ranking method.

The separate read-only contract is:

```text
GET /api/reader/v1/search/?q=...&mode=hybrid&embedding_provider=openai
GET /api/reader/v1/options/
GET /api/reader/v1/documents/<identity-uuid>/
GET /reader/artifacts/<artifact-uuid>/content/
```

Queries are capped at 500 characters, page size at 20, pages at 10, ranked document results at 200,
candidate sections at 300 by default, passages per document at three, and document sections at
2,000. Date filters describe the ARIA version record, not legal dates.

## Quality gates

The versioned `aria-reader-multisource-v1` benchmark covers one known-answer query for each current
authority:

- JPDP — appointment of a data protection officer;
- AGC — Government Procurement Act 2026; and
- Parliament — Cybercrimes Bill 2026.

Run the live OpenAI hybrid acceptance threshold with:

```bash
make evaluate-reader
```

On 2026-08-07 all three expected documents ranked first: MRR `1.0`, hit@1 `1.0`, hit@3 `1.0`, and
hit@5 `1.0`. This is a small regression benchmark, not a universal statement about legal-search
quality. The test suite separately covers authentication, reader/operator permission separation,
input bounds, filter validation, semantic fallback, XSS escaping, evidence-only downloads, security
headers, exact document rendering, React bundle integrity, filter metadata, accessibility, browser
interaction, and autonomous source acceptance.

## Autonomous-source acceptance

Phase 4A is usable locally, but the source-operation soak remains deliberately independent. Run:

```bash
make source-soak
```

The report remains `pending` until the first genuinely scheduled AGC and Parliament cycles execute.
It never treats successful manual pilots as autonomous evidence. Once a scheduled run completes, it
must have complete candidate/artifact observations, no failed or quarantined fetches, an advanced
next poll, and a healthy run-specific pipeline assessment before the source passes.

## Cloudflare preview

Keep the Compose web port bound to `127.0.0.1`, place Cloudflare Tunnel and Cloudflare Access in
front of it, and retain Django authentication as the application boundary. Before exposure, set a
strong secret, disable debug, allow only the chosen hostname, trust forwarded HTTPS only from the
controlled proxy path, and enable secure cookies. The complete production values are documented in
[the operator console guide](operator-console.md#access-and-cloudflare-boundary).
