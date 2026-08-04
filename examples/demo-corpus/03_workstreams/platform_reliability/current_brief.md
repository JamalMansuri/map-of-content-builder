---
workstream: platform_reliability
workstreams: [platform_reliability]
sources: [design_notes]
status: draft
ai-generated: false
scaffolded_by: map-of-content-builder
records:
  - "design_notes:service-design-order-intake.md"
  - "design_notes:incident-checkout-timeouts.md"
  - "design_notes:decision-blue-green-deploys.md"
  - "design_notes:runbook-queue-backlog.md"
---

# Platform Reliability — current brief

**Human-owned.** The scaffolder creates this file once, empty, and never overwrites it.
The living state of this workstream is yours to write; the system's job is to keep the
records underneath it honest.

It carries `ai-generated: false`, so the default profile's discovery gate excludes it
from the lint. That exclusion is reported, not silent.

## What this workstream is

Everything that decides whether the order intake path stays up: the queue design, the
incidents it produced, the deploy policy that resulted, and the runbook on-call uses.

## Current state

One relationship page is drafted. It connects the incident writeup to the deploy
decision and is deliberately shipped with two defective trace lines so that a first
lint run has something real to report.

## Open questions

Whether the hold window in the deploy decision should be re-derived once the circuit
breaker has a quarter of production behind it.

## Records in this workstream

`records:` in the frontmatter above is the machine-readable membership list, and it is
the only thing that assigns a record to a workstream. `build_spine.py` reads it to
group `MAP.md`. Add ids exactly as they appear in
`01_sources/_spines/project_records.jsonl`:

```yaml
records:
  - "design_notes:incident-checkout-timeouts.md"
  - "design_notes:decision-blue-green-deploys.md"
```

A record listed in no brief appears under `(unassigned)` in `MAP.md`. That is a review
queue, not an error — nothing is force-fitted into a workstream it does not belong to.
If a record belongs to several areas, list it in each brief and put every area in
`workstreams:` above, primary first.

## Exceptions

Where the real workflow deviated from the contract, and why.

The source notes for this demo live at `sources/` inside the corpus root rather than at
an external location, so that the whole example is self-contained and relocatable. Real
corpora normally point at a source lake that lives outside the corpus.
