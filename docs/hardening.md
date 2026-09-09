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
