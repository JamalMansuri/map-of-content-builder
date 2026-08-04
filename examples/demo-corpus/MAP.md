---
title: Demo Corpus — MAP
type: map
spine_fingerprint: 5e9d46e9
generator: map-of-content-builder/build_spine.py
records: 4
ai-generated: true
status: draft
---

# Demo Corpus — MAP

Machine-generated index of every record in this corpus, grouped by source bucket and then by workstream. Regenerated on every `build_spine.py` run — edits made here are lost. Write in `PROJECT_HUB.md` and the workstream briefs instead.

- Spine (machine view): `01_sources/_spines/project_records.jsonl`
- Workstream membership comes from `records:` in each `03_workstreams/<w>/current_brief.md`, which is human-owned.

## design_notes — 4 record(s)

### platform_reliability — 4

- **Decision: blue-green deploys for the order intake service** `sources/decision-blue-green-deploys.md` — Decision record adopting blue-green deploys for the order intake service, with a hold window bounded by the worst consumer lag on record.
- **Incident: checkout timeouts during the evening peak** `sources/incident-checkout-timeouts.md` — Incident writeup for the evening-peak checkout timeouts, tracing the outage to a retry storm that saturated the intake queue's consumer group.
- **Runbook: draining an intake queue backlog** `sources/runbook-queue-backlog.md` — Runbook for draining a backlog on the intake queue, covering when to scale the consumer group and when to park messages by hand.
- **Order intake service — design note** `sources/service-design-order-intake.md` — Design note for the order intake service, covering the queue consumer, its retry policy, and the idempotency key that makes replays safe.
