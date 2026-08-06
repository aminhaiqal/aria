# Phase 3D.9 governed source admission

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

## Verification

Focused tests cover pack discovery and strict rejection, dual confirmation, immutable connector
versions, idempotent application, preservation of promoted schedules, the bounded non-`href`
extractor, durable assessment deduplication, stale-evidence rejection, read-only APIs, staff access,
CSRF, POST-only actions, exact promotion confirmation, and audit actors.

```bash
docker compose exec -T api python manage.py test \
  tests.test_source_packs \
  tests.test_browser.BrowserAdmissionGateTestCase \
  tests.test_console.ConsoleAdmissionWorkbenchTestCase
```
