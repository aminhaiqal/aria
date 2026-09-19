# ARIA product roadmap

Last updated: 2026-09-20

## Purpose

ARIA's product goal is to become the most defensible regulatory change-to-action system for
Malaysian regulated businesses, beginning with fintech. It should help a compliance team answer:

1. What changed in an official source?
2. What obligation or regulatory expectation follows from that change?
3. Does it apply to this company, and why?
4. Which policy, system, process, or control is affected?
5. Who must act, by when, and what evidence proves completion?
6. Can every conclusion and decision be reconstructed later?

The roadmap uses five benchmark dimensions supplied by the product owner. They are product design
tests, not claims of feature parity with, or verified descriptions of, another vendor's current
implementation.

| Benchmark dimension | Question ARIA must answer |
|---|---|
| CUBE — product architecture | Can ARIA preserve an unbroken source → change → obligation → control → action → audit trail? |
| RegASK — AI-native workflow | Can AI shorten interpretation and impact work while humans retain explicit review and publication authority? |
| Bloomberg Regology — relevance engine | Can ARIA determine and explain whether a development applies to a specific company's actual business? |
| Thomson Reuters Regulatory Intelligence — trust | Can a reviewer defend every result using authoritative sources, provenance, history, citations, and versioned decisions? |
| Otonoco Nakhoda — Malaysian competitive benchmark | Can ARIA provide deep Malaysian monitoring, obligation analysis, impact analysis, a regulatory library, and an operational compliance workflow? |

## North-star product architecture

ARIA should mature toward this evidence chain:

```text
Official source
  → immutable document and provenance
  → detected version change
  → cited, human-reviewed obligation
  → versioned company applicability decision
  → policy/system/control mapping and gap
  → owned action, approval, and completion evidence
  → reconstructable audit package
```

AI may propose interpretation, applicability, mappings, and actions. It must never erase source
evidence, silently change a reviewed decision, or present an unreviewed legal conclusion as fact.
Human decisions, model and prompt versions, inputs, outputs, edits, and publication events remain
attributable and versioned.

## Where ARIA stands today

ARIA already has a production evidence foundation: governed source packs, scheduled monitoring,
immutable artifacts, deterministic extraction, document versions, structural comparisons, local
and hosted retrieval, human review, business profiles, exact profile matching, reviewed-impact
publication, signed delivery, audit records, reliability dashboards, and production-readiness
gates.

Four Malaysian source channels are operational: JPDP, AGC Updated Principal Acts, Parliament Dewan
Rakyat bills, and BNM Payment Systems. The BNM fintech channel currently contains 52 official
documents and 2,309 searchable sections with complete pipeline coverage.

The current product chain is uneven:

| Capability | Current state | Main gap |
|---|---|---|
| Official source and provenance | Strong production foundation | Expand only from measured customer demand and keep source SLOs visible. |
| Change detection and evidence | Implemented foundation | Validate recall, noise, timeliness, and reviewer usability on real changes. |
| AI interpretation and impact drafting | Guarded foundation | Turn isolated generation into a measured analyst workflow with evaluation and review queues. |
| Obligation intelligence | Early proxy through impact candidates | Add a first-class, versioned obligation model with legal context and exact citations. |
| Company relevance | Exact controlled-term matching | Model products, licenses, activities, entities, policies, systems, controls, and explicit unknowns. |
| Control mapping | Missing | Link reviewed obligations to existing controls and show coverage gaps. |
| Action management | Missing | Add owners, due dates, state transitions, approvals, reminders, and completion evidence. |
| Audit trail | Strong technical foundation | Produce a human-readable audit package spanning the entire product chain. |
| Malaysian regulatory depth | Four operational channels; first fintech channel live | Build a coherent Malaysia fintech library and bilingual domain model based on pilot priorities. |
| Product validation | Phase 5 in progress | Complete a real change journey and prove recurring value with active pilot users. |

## Product principles

1. **Evidence before interpretation.** Every derived claim links to an exact official artifact,
   version, section, locator, and hash.
2. **Unknown is a valid result.** Missing profile or legal context produces `insufficient_context`,
   never a guessed applicable or not-applicable answer.
3. **AI proposes; governed roles decide.** Material conclusions and external publication require
   explicit, attributable human decisions.
4. **Append, do not rewrite history.** New source versions, interpretations, profile changes,
   reviews, mappings, and actions supersede earlier records without deleting them.
5. **Malaysia depth before uncontrolled breadth.** New sources follow pilot demand, admission gates,
   and measurable operational capacity.
6. **Outcomes over feature count.** A phase exits only with usage and quality evidence, not because
   screens or database tables exist.
7. **No unsupported competitive claims.** ARIA may claim an advantage only after a repeatable
   benchmark measures it.

## Delivery sequence

The phases are outcome-gated rather than date-gated. Each phase creates evidence needed to make the
next investment. Work may be researched early, but a later phase should not displace the current
phase's exit criteria.

### Phase 5 — Prove the product loop (now)

**Objective:** prove that ARIA creates useful, trusted work for real users, not only a sound
technical pipeline.

Deliver:

- complete one genuine regulatory change journey, or a clearly labelled production-safe rehearsal
  while waiting for a genuine change;
- take the change through comparison, review, impact generation, impact review, profile matching,
  publication, and signed delivery;
- onboard three to five compliance, legal-operations, or risk users with owner-scoped business
  profiles;
- observe users searching, opening exact evidence, evaluating relevance, and acting on a reviewed
  impact;
- collect structured feedback and measure detection-to-publication time, search success, evidence
  use, relevance usefulness, and delivery acceptance; and
- select the next milestone from recorded pilot evidence.

Exit gate:

- all enabled sources remain healthy with complete evidence coverage;
- one complete governed change journey is recorded;
- at least three eligible pilot users complete the defined reader and evidence tasks;
- product metrics and structured feedback exist; and
- one evidence-backed next-milestone decision is recorded.

Benchmark movement: validates the existing Thomson Reuters-style trust foundation and tests the
first usable slice of the RegASK, Regology, and Nakhoda dimensions.

### Phase 6 — Make obligations a first-class system of record

**Objective:** turn a reviewed change into a durable, reviewable statement of what an affected
party must, must not, or may do.

Deliver:

- a versioned `RegulatoryObligation` record linked to a reviewed source change and exact evidence;
- structured fields for regulated actor, action, modality, condition, exception, timing or
  frequency, jurisdiction, legal status, effective date, and source citations;
- explicit obligation states such as proposed, reviewed, effective, superseded, withdrawn, and
  needs-context;
- relationships between new, amended, replaced, and unchanged obligations across document
  versions;
- deterministic and AI-assisted obligation proposals under one evidence contract;
- a reviewer workbench for approve, amend, reject, merge, split, and request-context decisions;
- an obligation library with source, topic, status, date, and authority filters; and
- an obligation history view that reconstructs why the current wording exists.

Exit gate:

- every approved obligation has at least one exact official citation and a current human review;
- no unreviewed AI proposal appears as an operative obligation;
- a labelled evaluation set covers the pilot's highest-value document and obligation types;
- extraction quality, reviewer amendment rate, missed-obligation rate, and review time are measured;
- supersession tests prove that historical obligations and decisions remain reconstructable; and
- pilot reviewers confirm that the obligation library is more useful than reading change summaries
  alone.

Benchmark movement: closes the largest CUBE architecture gap, adds the core Nakhoda-style
obligation layer, strengthens trust, and gives AI a bounded interpretation task.

### Phase 7 — Build the company relevance engine

**Objective:** explain whether each reviewed obligation applies to a particular company without
collapsing uncertainty into a false answer.

Deliver:

- versioned organization profiles covering legal entities, jurisdictions, regulatory licenses,
  regulated roles, products and services, customer types, activities, channels, data and payment
  flows, outsourced functions, policies, systems, and controls;
- effective dates and profile history so an old applicability decision can be reproduced;
- applicability rules that combine controlled facts with obligation requirements;
- `applies`, `does_not_apply`, and `insufficient_context` outcomes with matched, excluded, unmet,
  and missing facts;
- a triage inbox ordered by applicability, deadline, materiality, confidence, and review status;
- a clear “why this applies” explanation with exact obligation and company-profile evidence;
- reviewer corrections captured as versioned rule or profile changes, never silent model learning;
  and
- a company-specific relevance evaluation set with independently reviewed expected outcomes.

Exit gate:

- at least three real pilot profiles contain the facts required by their priority obligations;
- relevance precision and recall are reported by regulator, obligation type, and profile dimension;
- target release thresholds are at least 90% precision and 85% recall on the reviewed pilot set;
- every false negative receives a root-cause classification and blocks a broad applicability claim;
- every result can be reproduced from the exact obligation, profile version, taxonomy, and rules or
  model version; and
- pilot users report a material reduction in irrelevant regulatory items.

Benchmark movement: directly targets the Regology benchmark while extending ARIA's existing exact,
explainable profile matching instead of replacing it with opaque similarity.

### Phase 8 — Connect obligations to controls and actions

**Objective:** make a relevant obligation operational inside the company's compliance program.

Deliver:

- versioned policy, process, system, and control inventories;
- evidence-backed obligation-to-control mappings with covered, partial, uncovered, and unknown
  assessments;
- AI-assisted mapping suggestions with confidence, rationale, and human approval;
- gap records that preserve the obligation, company context, affected control, and reviewer;
- actions with an owner, priority, due date, workflow state, approver, comments, dependencies, and
  reminders;
- completion evidence such as policy versions, test results, approvals, and implementation records;
- separation of duties for assignment, approval, closure, and publication;
- signed webhooks and stable APIs for ticketing, governance, risk, and compliance systems; and
- an exportable audit package covering source through action closure.

Exit gate:

- one real regulatory change completes the full source → change → obligation → relevance → control
  → action → closure chain;
- every state transition is actor-attributed and time-stamped;
- every closed action contains approved completion evidence;
- control coverage and outstanding gaps can be reproduced for a selected date;
- overdue, reopened, rejected, and superseded paths pass workflow tests; and
- a pilot compliance owner can produce the full audit package without engineering assistance.

Benchmark movement: completes the CUBE architecture chain and turns RegASK-style interpretation
into governed operational work.

### Phase 9 — Establish Malaysia fintech depth

**Objective:** become the most useful evidence-backed regulatory workspace for the selected
Malaysia fintech customer segment.

Deliver:

- a pilot-ranked source expansion plan across the Malaysian authorities and official publication
  families that govern the target firms;
- domain packs for the selected fintech activities, beginning with the payment-system evidence
  already onboarded and expanding only where pilot profiles show demand;
- bilingual Malay and English terminology, retrieval, obligation extraction, and reviewer aids;
- cross-document relationships for enabling laws, subsidiary instruments, policies, exposure
  drafts, FAQs, amendments, effective dates, and superseded guidance;
- a curated Malaysia regulatory library organized by authority, topic, regulated role, product,
  obligation status, and effective date;
- source-specific freshness objectives and visible coverage for every promoted channel; and
- repeatable onboarding playbooks for each new source and regulatory domain.

Exit gate:

- the source portfolio covers the pilot-approved priority list rather than an arbitrary document
  count;
- every source meets its freshness, provenance, extraction, graph, and embedding objectives;
- Malay and English benchmark cases meet the same evidence and citation requirements;
- effective and superseded status is independently reviewed for the priority obligation library;
- pilot teams use the regulatory library and workflow repeatedly across more than one regulatory
  topic; and
- ARIA can show measured Malaysia-specific coverage and workflow outcomes without claiming complete
  legal coverage.

Benchmark movement: addresses the Nakhoda benchmark through depth, local context, and workflow,
while applying ARIA's stronger evidence contract to every added channel.

### Phase 10 — Scale governed AI and prove differentiation

**Objective:** use AI to reduce analyst effort across the entire journey while preserving the
trust, review, and audit boundaries established in earlier phases.

Deliver:

- an analyst work queue that clusters related changes and assembles the relevant evidence context;
- governed AI proposals for change explanation, obligation extraction, applicability questions,
  control mapping, gap summaries, and action plans;
- task-specific model routing, prompt and schema versions, confidence calibration, fallbacks, cost
  budgets, and latency budgets;
- regression evaluations for citations, factual consistency, obligation recall, applicability,
  control mapping, unsafe certainty, and bilingual behavior;
- side-by-side reviewer diffs showing the proposal, edits, evidence, and final decision;
- feedback analytics based on accepted, amended, rejected, and escalated proposals;
- organization tenancy, enterprise role policy, retention controls, API governance, and operational
  service objectives; and
- a repeatable comparative evaluation that measures ARIA against the five benchmark dimensions.

Exit gate:

- every AI-assisted task has a versioned evaluation set, minimum quality threshold, and safe
  fallback;
- AI reduces median analyst handling time without lowering citation, recall, or review quality;
- material conclusions still require the configured human approval boundary;
- model or prompt changes cannot reach production without regression evidence;
- enterprise isolation, authorization, recovery, and audit tests pass; and
- differentiation claims are supported by reproducible results from real customer workflows.

Benchmark movement: targets the RegASK AI-native workflow while using traceability, explicit
uncertainty, Malaysian depth, and end-to-end action evidence as ARIA's differentiators.

## Cross-phase scorecard

The product team should review this scorecard at every milestone. A metric without a retained
evidence trail does not count.

| Outcome | Core measures |
|---|---|
| Source reliability | On-time poll rate, freshness lag, evidence coverage, failed candidates, drift incidents, and recovery time |
| Change quality | Detection precision and recall on labelled versions, duplicate rate, time to detection, and reviewer disposition |
| Obligation quality | Citation coverage, field accuracy, missed obligations, reviewer amendment and rejection rates, and review time |
| Relevance quality | Company-specific precision and recall, false-negative causes, unknown rate, explanation completeness, and irrelevant-alert reduction |
| Workflow value | Time from detection to reviewed decision, control-mapping coverage, open gaps, overdue actions, closure time, and reopened actions |
| Trust | Percentage of conclusions reproducible from artifact, locator, version, actor, rule/model, and decision history |
| Malaysian depth | Coverage of pilot-prioritized sources and topics, Malay/English quality, effective-status review, and repeat user activity |
| User value | Weekly active pilot users, successful searches, evidence use, accepted relevance results, completed actions, and qualitative feedback |

## What “surpass” means

ARIA should not define success as having more screens or more AI-generated text. It can credibly
claim leadership only when customer evidence shows that it:

- carries a regulatory development from official source to verified action without breaking the
  evidence chain;
- produces fewer irrelevant alerts and fewer missed applicable obligations for a defined company;
- reaches a reviewed, actionable result faster while requiring less analyst effort;
- gives every material conclusion an exact citation, history, responsible actor, and reproducible
  reasoning path;
- handles Malaysian regulatory sources, terminology, and workflows with demonstrable depth; and
- produces an audit package that compliance teams and independent reviewers can use without help
  from ARIA's engineers.

These outcomes must be measured on declared datasets and real workflows. Until then, the five
vendors remain design benchmarks rather than competitors ARIA can claim to outperform.

## Immediate priorities

Until Phase 5 exits, the order of work is:

1. keep all four production sources healthy and complete;
2. complete and retain one governed change-to-publication-and-delivery journey;
3. onboard at least three pilot users with real company profiles and tasks;
4. collect the required product metrics and structured feedback;
5. decide whether the pilot evidence makes Phase 6 obligation intelligence the next milestone; and
6. begin Phase 6 only after that decision is recorded.

New source requests should enter a ranked backlog and must not replace the product-proof work unless
pilot evidence shows that the missing source prevents validation.

## Roadmap governance

- Review this roadmap after every completed phase and at least monthly during an active pilot.
- Record scope changes with the customer or operational evidence that caused them.
- Keep completed phase evidence immutable and link it from this document or the relevant runbook.
- Treat target metrics as release gates; if evidence shows a target is poorly chosen, version the
  target and record why it changed.
- Maintain one active product milestone. Research may run ahead, but delivery focus stays on the
  current exit gate.
