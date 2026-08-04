---
title: <<<PROJECT_TITLE>>> Project Hub
type: project_hub
status: draft
ai-generated: false
scaffolded_at: <<<GENERATED_AT>>>
scaffolded_by: map-of-content-builder
---

# <<<PROJECT_TITLE>>> Project Hub

Root-level index for this map of content. This file is **human-owned**: the builder
writes it once, at scaffold time, and never touches it again. If a bucket or a
workstream is added later, the run report says so — it does not edit this page.

## Objective

<<<HUMAN>>>

Write, in your own words: what this map is for, the question it has to answer, and
how you will know it worked. Nothing in this system fills this in for you, and the
map is worth very little until you do.

## Current Source Layout

<<<SOURCE_LAYOUT>>>

Each bucket is a **surface** the bytes came from, never a topic. `raw/` holds bytes
only for sources with no durable home of their own; when a source already lives at a
durable read-only location, that location *is* the source lake and nothing is copied
or symlinked. `normalized/records.jsonl` is the canonical artifact.

## Current Codebase Layout

<<<CODEBASE_LAYOUT>>>

## Current Workstreams

<<<WORKSTREAM_LAYOUT>>>

## Simple Rule For New Files

1. If it came from another system, put it in `01_sources`.
2. If it is code or a plugin, put it in `02_codebases`.
3. If it is your thinking, planning, summaries, or outputs for a project area, put it
   in the matching `03_workstreams` folder.
4. If it is old scaffolding, temp, or infrastructure support material, put it in
   `90_system`.

## Source Spine Automation

Run this to refresh the JSONL spine from the current local source files:

```bash
python "<<<SCRIPTS_DIR>>>/build_records.py" --root "<<<ROOT>>>"
python "<<<SCRIPTS_DIR>>>/build_spine.py"   --root "<<<ROOT>>>"
```

The combined LLM-facing spine is:

```text
01_sources/_spines/project_records.jsonl
```

The human-facing index rendered from it is:

```text
MAP.md
```

`build_records.py` never invents a one-sentence `gist`. If a source declares no summary
of its own, the run stops, writes `90_system/state/<run_id>_gists_needed.json`, and
waits for a human (or a model) to fill them in and re-run with `--gists`. A refused run
is the design working, not a failure.

`operator_note` on any record is yours alone. No script and no model ever writes it,
and a re-run always carries the existing value forward.

## Suggested Note Convention

For any new markdown or JSON summary that matters, add the workstream near the top:

```md
Workstream: <<<EXAMPLE_WORKSTREAM>>>
Sources: <<<EXAMPLE_SOURCES>>>
Status: draft
```

If a note applies to multiple areas, list more than one workstream.

## Important

Keep the root clean. New files should normally not live directly in this folder unless
they are a root-level index like this one.
