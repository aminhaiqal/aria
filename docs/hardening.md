# Phase 4D production hardening

Phase 4D hardens ARIA without weakening its evidence or human-review boundaries. Each slice is
independently testable and committed before the next begins.

## 4D.1 source-structure drift quarantine

Monitored detail pages may disappear, redirect to a generic home page, or move to a new HTML
template. These are deterministic contract failures, so retrying the same response cannot repair
them. ARIA now handles them as source drift:

- a detail connector may try up to five selectors, but every selector must be present in the
  immutable repository source-pack definition;
- an optional `detail_final_path_prefixes` contract rejects soft-404 redirects that leave the
  approved detail-page scope;
- missing approved selectors and final-path violations are terminal for that run and are not sent
  through the transient network retry loop;
- the resource is paused for `ARIA_MONITOR_STRUCTURE_DRIFT_PAUSE_MINUTES` (24 hours by default),
  while unrelated source and resource checks continue;
- an append-only `ResourceStructureIncident` records response provenance, content and HTML-shape
  hashes, redirects, selector expectations, and a bounded tag/class sample;
- page text and raw HTML are deliberately excluded from the diagnostic sample; and
- the operator resource page and Django Admin expose the incident and pause deadline.

The JPDP v4 connector contract also requires final responses to remain under
`/ppdpv1/en/akta/`. The two English links observed redirecting to the JPDP home page are therefore
quarantined as drift instead of being mistaken for valid document pages.

Before enabling a replacement selector, update the repository source pack, increment its connector
and pack versions, run the source-pack test suite, apply the pack through its explicit gate, then
re-run only the affected resource. A broad selector such as `main` must not be introduced as an
unreviewed runtime fallback.

## 4D.2 encrypted backup and restore drills

ARIA backups are designed around the evidence boundary rather than only the relational database.
Every backup contains:

- a PostgreSQL custom-format dump and artifact inventory taken from the same exported,
  repeatable-read snapshot;
- a tar archive of every artifact whose recorded backend is the local filesystem, with every byte
  checked against its database SHA-256 before inclusion;
- an inventory of R2 artifacts with IDs, object keys, content types, sizes, and SHA-256 values;
- one age-encrypted bundle; and
- a small plaintext manifest containing sizes and digests, but no password, private key, access
  key, or artifact content.

R2 remains the durable home of artifacts recorded with the `s3` backend; duplicating those objects
inside every database backup would waste storage. The inventory makes those dependencies explicit
and auditable. Locally stored evidence bytes are included because a database-only backup would be
incomplete.

Generate an age X25519 identity offline with `age-keygen`. Put only the printed public `age1...`
recipient in `ARIA_BACKUP_AGE_RECIPIENT`; keep the identity file outside the repository and Docker
volumes. Set `ARIA_BACKUP_UPLOAD_TO_R2=true` to copy the encrypted bundle and manifest under
`backups/database/YYYY/MM/DD/` in the configured private R2 bucket. ARIA checks the uploaded byte
size and SHA-256 object metadata immediately after each upload.

Operational commands:

```text
make backup
make backup-verify MANIFEST=/var/lib/aria/backups/<name>.manifest.json IDENTITY_FILE=/offline/aria-age-key.txt
make restore-drill MANIFEST=/var/lib/aria/backups/<name>.manifest.json IDENTITY_FILE=/offline/aria-age-key.txt
```

The verification command checks the encrypted envelope before requesting the private identity. A
full verification decrypts into a temporary directory, rejects unexpected or non-regular archive
members, verifies every nested digest, and asks `pg_restore` to inspect the custom dump. The restore
drill goes further: it restores into a randomly named `aria_restore_drill_*` database, requires the
literal `RESTORE-DRILL` acknowledgement, confirms restored public tables exist, and removes that
database in a `finally` boundary. It has no option for naming or overwriting the live database.

For a disaster recovery event, first recover the pair of R2 objects into an isolated host, run the
full verification and restore drill, then stop writers and restore into a new PostgreSQL instance.
Validate source, artifact, evidence, graph, comparison, and review counts before changing any
application database endpoint. Restore the included filesystem artifact archive only into a new
empty artifact volume. Never unpack it over a running evidence store.

Schedule the encrypted backup daily from the host and run a restore drill at least weekly. Retain
multiple generations in R2 so operator error or a late-discovered corruption is not immediately
propagated to every recovery point.
