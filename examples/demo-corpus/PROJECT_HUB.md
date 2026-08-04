---
title: Demo Corpus Project Hub
type: project_hub
status: draft
ai-generated: false
scaffolded_by: map-of-content-builder
---

# Demo Corpus Project Hub

Root-level index for this map of content. This file is **human-owned**: the scaffolder
writes it once, at scaffold time, and never touches it again. If a bucket or a
workstream is added later, the run report says so — it does not edit this page.

## Objective

Give a stranger a corpus small enough to read in five minutes and real enough to lint,
where the lint has something true to say. One relationship page connects an incident
writeup to a deploy decision, and two of its four provenance traces are deliberately
defective — one quotation that is a paraphrase, and one that is verbatim but cited to
the wrong section.

## Current Source Layout

- `01_sources/design_notes/`
  Design notes, incident writeups, decision records and runbooks for one small software
  project (`source_type: markdown-note`), rooted at `sources/`.

Each bucket is a **surface** the bytes came from, never a topic.
`normalized/records.jsonl` is the canonical artifact.

Note the deliberate simplification in this demo: the source files live at `sources/`
*inside* the corpus so the whole example is self-contained and can be copied anywhere.
A real corpus normally points at a source lake outside its own root, and the scaffolder
refuses a source root that overlaps the corpus root for exactly that reason.

## Current Workstreams

- `03_workstreams/platform_reliability/`

## Simple Rule For New Files

1. If it came from another system, put it in `01_sources`.
2. If it is code or a plugin, put it in `02_codebases`.
3. If it is your thinking, planning, summaries, or outputs for a project area, put it
   in the matching `03_workstreams` folder.
4. If it is old scaffolding, temp, or infrastructure support material, put it in
   `90_system`.

## Source Spine Automation

Run this from this directory to refresh the spine from the current source files:

```bash
python ../../scripts/build_records.py --root .
python ../../scripts/build_spine.py   --root .
```

The machine-facing spine is:

```text
01_sources/_spines/project_records.jsonl
```

The human-facing index rendered from it is:

```text
MAP.md
```

`build_records.py` never invents a one-sentence `gist`. If a source declares no summary
of its own, the run stops, writes a queue under `90_system/state/`, and waits for a
human to fill the gists in and re-run with `--gists`. A refused run is the design
working, not a failure. Every source note in this demo declares its own `summary:` in
frontmatter, so the demo build never reaches that queue.

`operator_note` on any record is yours alone. No script and no model ever writes it,
and a re-run always carries the existing value forward.

## Important

Keep the root clean. New files should normally not live directly in this folder unless
they are a root-level index like this one.
