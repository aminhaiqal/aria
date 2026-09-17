# Phase 3D.9–3D.10 governed source admission

Phase 3D.9 turns one-off seeding into a reviewable source-onboarding contract. Source definitions
live in `src/aria/sources/packs/` as versioned JSON. Applying a pack records its canonical SHA-256
definition in an append-only `SourcePackSnapshot`; connector configuration evidence cannot change
under an installed version.

## Source-pack safety contract

Validation happens before database writes. A pack must:

- declare an HTTPS discovery URL and resources inside an authority-owned official-domain allowlist;
- use public fully qualified hostnames rather than IP addresses or local targets;
- select supported authority, collection, connector, pagination, and resource types;
- bound polling intervals and candidates, and use exact safe paths for document and browser policy;
- keep credentials out of repository definitions; and
- provide exactly one active, version-matched connector configuration.

The default command is a read-only deterministic plan. Applying requires both explicit flags:

```bash
make list-source-packs
make plan-source-pack PACK=parliament-dewan-rakyat-bills
make apply-source-pack PACK=parliament-dewan-rakyat-bills
```

Reapplying a pack is idempotent. It reconciles registry metadata and static resources but preserves
an existing endpoint's enabled state, health, and next scheduled poll. A connector definition that
differs from installed evidence is rejected until both connector and source-pack versions advance.

## Durable browser admission

JavaScript sources are admitted at `/console/admissions/` through three separate actions:

1. queue a bounded capture while the endpoint remains disabled;
2. record an immutable assessment over two to five exact captures; and
3. type `PROMOTE` to activate the exact ready assessment.

The assessment checks controlled activation, an installed immutable source pack, capture count,
original/rendered artifact lineage, bounded allowlisted network behavior, candidate lift over the
original response, official-path precision, repeatability, and downstream
artifact/extraction/knowledge-graph coverage. Promotion locks the endpoint and recomputes those
gates. New evidence or a changed source-pack checksum makes an older assessment stale rather than
silently widening approval.

Append-only evidence is also exposed through staff-only read APIs:

```text
/api/v1/source-pack-snapshots/
/api/v1/source-admissions/
/api/v1/source-promotions/
```

## Parliament disabled pilot

The next official source pack is the Parliament of Malaysia's
[Dewan Rakyat Bills](https://www.parlimen.gov.my/portal/bills-dewan-rakyat.html?lang=en&uweb=dr)
listing. Live inspection on 2026-08-07 established that its official PDF paths are present in the
server-rendered `onclick` attributes. ARIA therefore uses a deterministic bounded attribute
extractor instead of Chromium or evaluating the page's JavaScript.

The pack permits only HTTPS documents below `/files/billindex/pdf/`, accepts PDFs only, caps a run
at 25 candidates, and installs with `is_enabled=false` and no next poll. Its collection explicitly
labels the records as parliamentary bills rather than enacted law. Applying the pack registers the
contract; it does not retrieve documents or activate a schedule.

## Guarded static-source admission

Phase 3D.10 gives deterministic HTML listing sources the same durable admission boundary as a
browser source without pretending their evidence is identical. A disabled pilot run requires an
installed source-pack snapshot, leaves `is_enabled=false` and `next_poll_at=null`, rejects overlap,
records its actor and exact pack/configuration evidence, and uses the ordinary discovery, fetch,
extraction, graph, and embedding pipeline.

Two to five completed runs may then be assessed. Static admission checks:

- controlled disabled activation and the immutable source-pack/configuration versions;
- successful official-domain endpoint observations;
- non-empty bounded candidates confined to approved paths, extensions, and content types;
- repeatable candidate sets with no unexplained disappearance;
- latest-run R2 artifact and extraction coverage; and
- normalized document, knowledge-graph, local-vector, and configured-vector coverage.

Assessment records include the exact source-run IDs, candidate-set hash, pack checksum, every gate,
and a deterministic report fingerprint. Promotion accepts only an exact `PROMOTE` confirmation,
locks the endpoint, and recomputes the current evidence. A newer run, changed candidate set, changed
pack, or failed pipeline gate makes the submitted assessment stale and cannot enable scheduling.

```bash
make pilot-source SOURCE=parliament-dewan-rakyat-bills
# Wait for downstream work, then repeat the pilot.
make audit-static SOURCE=parliament-dewan-rakyat-bills
make promote-static ASSESSMENT_ID=<ready-assessment-uuid>
```

The same guarded actions are available on the static source's `/console/sources/<uuid>/` page.

The Parliament server currently omits its public Sectigo DV R36 intermediate certificate from the
TLS handshake. ARIA vendors that non-secret public intermediate at its reviewed SHA-256 fingerprint
`8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0` and loads it alongside the
normal Mozilla roots. Hostname, chain, expiry, DNS, allowlist, redirect, and public-address checks
remain enabled; ARIA never sets `verify=false`.

Its file endpoint also requires the short-lived session established by the official listing page.
Connector v2 performs one bounded listing bootstrap before each candidate fetch, adds the official
listing as `Referer`, and keeps the received cookie only in that in-memory HTTP client. The cookie
is neither persisted nor logged; all PDF bytes still come directly from the allowlisted Parliament
host and pass content detection before R2 preservation.

## Multi-source confidence

`/console/confidence/` and `make source-confidence` provide one read-only report across registered,
pilot, admission-ready, processing, operational, and attention states. The evidence-coverage
percentage is a deterministic completion measure across preservation, extraction, graph, local
embedding, and configured embedding records; it is not a prediction or legal-confidence score.

## Parliament live acceptance

On 2026-08-07, connector v2 completed two controlled 25-candidate runs with an identical candidate
set and no disappearances. The first v2 run preserved and processed all 25 PDFs; the repeat run
created 25 unchanged artifact observations without duplicate versions. Admission passed 15/15
gates with 25/25 R2 artifacts, extractions, and graph projections, plus 351/351 local and 351/351
OpenAI section embeddings. Exact assessment `76149eea-8bb2-4156-ab9a-b7fcfb60efbf` promoted the
source to the configured schedule. Source-pack v4 reconciles that cadence to every three hours while retaining
the admission evidence. The resulting source reliability state is `healthy` with no findings.

The earlier connector-v1 attempt remains immutable failed evidence: the listing completed, but its
stateless PDF requests received HTML error pages. The content-type gate prevented those responses
from reaching R2. Connector v2 and source-pack v2 were installed rather than rewriting v1, and the
ready assessment used only the two matching v2 runs.

## Verification

Focused tests cover pack discovery and strict rejection, dual confirmation, immutable connector
versions, idempotent application, preservation of promoted schedules, the bounded non-`href`
extractor, durable assessment deduplication, stale-evidence rejection, read-only APIs, staff access,
CSRF, POST-only actions, exact promotion confirmation, static run evidence, pipeline coverage,
source confidence, and audit actors.

```bash
docker compose exec -T api python manage.py test \
  tests.test_source_packs \
  tests.test_browser.BrowserAdmissionGateTestCase \
  tests.test_static_admission \
  tests.test_console.ConsoleAdmissionWorkbenchTestCase
```
