---
workstream: <<<WORKSTREAM>>>
workstreams: [<<<WORKSTREAM>>>]
sources: [<<<SOURCE_BUCKETS>>>]
status: draft
ai-generated: false
scaffolded_at: <<<GENERATED_AT>>>
scaffolded_by: map-of-content-builder
records: []
---

# <<<WORKSTREAM_TITLE>>> — current brief

**Human-owned.** The builder creates this file once, empty, and never overwrites it.
The living state of this workstream is yours to write; the system's job is to keep the
records underneath it honest.

## What this workstream is

<<<HUMAN>>>

## Current state

<<<HUMAN>>>

## Open questions

<<<HUMAN>>>

## Records in this workstream

`records:` in the frontmatter above is the machine-readable membership list, and it is
the only thing that assigns a record to a workstream. `build_spine.py` reads it to
group `MAP.md`. Add ids exactly as they appear in
`01_sources/_spines/project_records.jsonl`:

```yaml
records:
  - "design_docs:services/auth-gateway.md"
  - "design_docs:decisions/adopt-queue-backpressure.md"
```

A record listed in no brief appears under `(unassigned)` in `MAP.md`. That is a review
queue, not an error — nothing is force-fitted into a workstream it does not belong to.
If a record belongs to several areas, list it in each brief and put every area in
`workstreams:` above, primary first.

## Exceptions

Where the real workflow deviated from the contract, and why.

<<<HUMAN>>>
