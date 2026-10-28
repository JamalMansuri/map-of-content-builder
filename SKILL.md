---
name: map-of-content-builder
description: Build, grow and audit a map-of-content corpus over any folder of documents. Scaffold the `01_sources/` `02_codebases/` `03_workstreams/` `90_system/` tree, normalise every source into a 12-field JSONL record with a content hash and resolvable evidence paths, build the `project_records.jsonl` spine, propose candidate relationships from shared evidence, draft cited relationship pages that only ship after every trace snippet verifies verbatim against the real source, and audit an existing corpus against a declarative `profile.yaml`. Four modes — scaffold, ingest, relate, lint. Trigger on "build a MoC over", "map of content", "ingest this source", "add this source to the corpus", "lint the MoC", "audit my corpus", "verify the traces", "propose relationships in this corpus". Reads its sources; never writes them.
---

# map-of-content-builder

Turns a pile of sources into a corpus you can navigate, cite, and re-check. Four modes, one shared
library (`scripts/corpuslib.py`). Every deterministic step is a Python script that makes no model
calls and touches no network; every judgement step is a model-side action described in this file
that reaches the scripts as a file on disk. The seam is deliberate: the scripts cannot hallucinate,
and the model cannot quietly relax a threshold.

`$MOC` below is this repository's root. Set it once per session:

```bash
export MOC=/path/to/map-of-content-builder
```

## When to invoke

| Mode | Ask sounds like | What it does |
|---|---|---|
| `scaffold` | "build a MoC over X", "set up a corpus over this folder" | creates the corpus tree and its config |
| `ingest` | "ingest this source", "add this source", "index this into records" | normalises sources into records, rebuilds the spine |
| `relate` | "propose relationships", "build a relationship page for X" | surfaces overlapping pairs, drafts one cited page, gates it |
| `lint` | "lint the MoC", "audit the corpus", "verify the traces" | full audit against a profile; report only, never edits |

If the ask is ambiguous between modes, ask. `ingest` writes records and `lint` writes nothing;
picking wrong is not a small error.

## Pre-flight (every time)

1. Read `docs/how-it-works.md` once per session — the data model and the trace contract.
2. Confirm the environment: `python3 -c "import yaml"`. If that fails, stop and install PyYAML.
   Everything here is stdlib + PyYAML; a missing PyYAML is an environment problem, not something to
   route around by writing a second parser.
3. Identify the governing profile. A profile is **mandatory** for `lint` and for the `relate` gate.
   `profiles/default.profile.yaml` ships as the generic ruleset. Per corpus, **copy it** and edit
   `name` and `corpus_id` — do not edit the shipped default in place.

## Mode 1 — `scaffold`

Creates the corpus tree. Runs once per corpus; re-running is safe (human-owned files are written
only if absent, never overwritten).

1. **Resolve the output root.** It must be a directory you are allowed to write to, and it must sit
   outside every source root you are about to register.
2. **Choose the CHOSEN names — this is the model's whole job in this mode.** Corpus slug
   (kebab-case); bucket names (snake_case, one per source **surface** — `design_docs`, `recordings`,
   `kb_pages` — **never** a topic: a bucket is where the bytes came from, not what they are about);
   workstream names (snake_case, the areas the work actually divides into).

   **FIXED names are never invented and never renamed:** `01_sources/`, `02_codebases/`,
   `03_workstreams/`, `05_exports/`, `90_system/`, `01_sources/_spines/`, `PROJECT_HUB.md`,
   `02_codebases/README.md`, `current_brief.md`, `90_system/config/corpus.config.json`,
   `90_system/_entities.md`. Not on the CHOSEN list, not yours.
3. **Write the config** to a scratch path outside the corpus, then dry-run it:

```bash
python3 "$MOC/scripts/scaffold_corpus.py" \
  --config "<SCRATCH>/corpus.config.json" --output-root "<OUTPUT_ROOT>" --dry-run
```

Config shape — the only free fields are the slug, bucket names and workstream names:

```json
{"corpus_slug": "platform-knowledge",
 "buckets": [{"name": "design_docs", "source_type": "design_doc",
              "root": "/abs/path/to/design-docs",
              "description": "Authored by the platform team; read-only.",
              "include": ["**/*.md"], "exclude_dirs": ["scripts"], "exclude_prefix": ["_"]}],
 "workstreams": ["ingestion", "governance"]}
```

A bucket root inside `<OUTPUT_ROOT>` (or vice versa) is rejected — the corpus would ingest its own
output on the second run. A root whose **paths themselves carry sensitive strings** (person names,
patient or subject ids, unreleased codenames) takes `"path_is_sensitive": true`, and the script then
refuses to build path-addressed records from it at all.

4. **Run for real** (drop `--dry-run`). Read `created` vs `skipped_existing` on stdout. Anything in
   `skipped_existing` that you expected to create means the corpus already exists — stop and say so.
5. **Report** the root, bucket count, workstream count. Do not narrate the tree.

Buckets and workstreams are frozen after this run. Later runs may **add**; never rename or delete.
Propose a rename; let a human do it.

## Mode 2 — `ingest`

Normalises sources into records, then rebuilds the spine. Sources are read, hashed and pointed at.
They are never copied, never symlinked, never edited.

1. **First pass, no gists:**

```bash
python3 "$MOC/scripts/build_records.py" --root "<ROOT>"
```

2. **The gist handshake.** A source that declares its own one-sentence summary has that string
   lifted verbatim. A source that declares none lands in `gists_needed` on stdout and the script
   **refuses the write (exit 1) rather than fabricating a gist**. That refusal is the feature.

   The queue lands at `<ROOT>/90_system/state/<run_id>_gists_needed.json`. Read each entry's source
   and write **one sentence each** into `<ROOT>/90_system/state/<run_id>_gists.json` as
   `{"<record_id>": "<one sentence>"}`. `lead_sentence_SEED_NOT_A_GIST` is the source's opening
   line, offered to save a read — it is **not** a gist and must not be copied as one. Declarative;
   no "This document…"; no first person; no hedging; ≤30 words (over that is a warning, over 60 an
   error). Say what the source establishes, not that it exists.

   **`operator_note` is never filled here, or anywhere, by a model.** It is the human's own take —
   the judgement that refuses to automate. A machine write to it aborts the bucket.

3. **Re-run with the handshake file**, then rebuild the spine — always, in the same run:

```bash
python3 "$MOC/scripts/build_records.py" --root "<ROOT>" \
  --gists "<ROOT>/90_system/state/<run_id>_gists.json"
python3 "$MOC/scripts/build_spine.py" --root "<ROOT>"
```

4. **Report** the real counts the run produced — records total, new, changed, unchanged, missing,
   and every notice. A number that differs from what anyone predicted is a finding to report, not a
   failure to hide.

## Mode 3 — `relate`

Finds candidate relationships, drafts **one** page, and gates it before it is kept.

1. **The spine must be rebuilt in this same run, before proposing.** Proposing from a stale spine is
   a silent-wrong-answer machine, and the wrong answer looks exactly like a correct empty result — a
   pair that plainly shares sources will show no overlap against a weeks-stale index.

```bash
python3 "$MOC/scripts/build_spine.py" --root "<ROOT>"
python3 "$MOC/scripts/propose_relations.py" --root "<ROOT>"
```

2. **Read the proposals** at `<ROOT>/90_system/state/<run_id>_relation_proposals.json`. Each entry
   is a seed pair with its raw overlap components — `shared_entities`, `shared_tags`, `shared_links`
   — and a `why`. **There is no score and no suggested relation kind.** A single number would hide
   which axis fired, and classifying the relationship is judgement. If the run reports candidates
   dropped by `--limit`, the file is a truncated queue, not a complete map — say so.
3. **Draft one page** from `templates/relationship-page.tpl` into
   `<ROOT>/03_workstreams/<workstream>/<kebab-slug>.md`. The title names the **relationship**, not
   the topics. Every assertive sentence in `## The connection`, `## What each source contributes`
   and `## Tension` carries at least one inline `[src:<record_id>|<locator>]`, and at least one
   sentence cites two **distinct** records. Create `## Operator note` as an empty heading and write
   nothing under it, ever.
4. **The deletion test, before you write a word:** delete any one of the related records from the
   corpus. If the thesis is still true, still supported, and still worth writing down, this is a
   list, not a relationship. Drop it and take the next proposal.
5. **Gate it. The gate is not advisory:**

```bash
python3 "$MOC/scripts/lint_corpus.py" --corpus "<ROOT>" --profile "<PROFILE>.yaml" --gate
```

   Exit 0 → keep the page. **Exit 1 → fix the page or delete it. Never ship a page with a failing
   trace.** A page that ships with a broken provenance line is worse than no page, because the
   citation launders the claim.
6. **Report** the verdict string the gate prints and nothing stronger. The gate says
   `mechanically-clean`. It never says `verified` and never says `pass`.

## Mode 4 — `lint`

Full audit. Reports; never edits.

```bash
python3 "$MOC/scripts/lint_corpus.py" --corpus "<ROOT>" --profile "<PROFILE>.yaml"
```

A profile is **mandatory**; there is no profile-less mode. A profile is the only place a tier list,
numeric threshold or token bound may live; one in a `.py` file is a defect, not a shortcut.
`lint_corpus.py` writes the report itself and prints its path — there is no separate renderer, and
it never writes inside the audited corpus.

Then the model-side pass the scripts cannot do:

- **Joint claim, or co-mention?** `Both A [src:A|…] and B [src:B|…] mention plugins` clears every
  mechanical gate and is worthless. Only a reader can tell them apart. Flag suspects; never silently
  accept them.
- **Duplicate and contradiction sweep** across pages sharing records. Quote both sides; pick no side.

**Report severity counts and the report path. Do not narrate the findings.** A human reads the
report. If errors and warnings are both zero, say so explicitly.

## The trace discipline

A trace line is a hidden, verifiable commitment attached to a citation:

```
<!-- trace: <record_id> | <ordinal> | <locator> | raw: "<snippet>" -->
```

**Writing a `raw:` snippet — no exceptions:**

- Copy the span **character-for-character** from the source. Open the source and copy. Never re-type
  from memory.
- Never tidy. Not the casing, not the disfluencies, not the punctuation, not a resolved pseudonym.
- Never merge two spans. No ellipsis, ever — a spliced quote is a fabricated quote.
- One contiguous span, ~8 words, 30–120 characters. Under 30 characters is unverifiable by
  construction; much longer and ordinary transcription variance breaks the match.
- **If no single contiguous span supports the claim, split the claim or drop it.** Do not paraphrase
  into quotation marks. That is the exact failure this tool exists to catch.

**What a passing trace proves:** the quoted words physically exist in the file that was cited, right
now; the record id resolves; and if the source changed, which claims survived.

**What it does not prove:** that the span *supports* the claim it is attached to. String existence is
not entailment. If one model writes both the claim and its trace, a green check converts an unbounded
hallucination risk into a bounded quote-mining risk — nothing more. It also does not prove the
citation is complete: nothing detects a contradicting passage twenty lines below.

`TRACE_ABSENT` means *this quotation is not verbatim*. It does **not** mean *this claim is false*,
and no report from this tool should ever be read that way. The common real-world failure is a close
paraphrase of a true statement — a quote-fidelity defect, not fabricated content. Say it that way.

Two severities, and collapsing them makes the report unusable: snippet **not in the cited source** is
an **error** (fabricated or mis-attributed). Snippet **in the source but not at the cited locator**
is a **warning** (citation imprecision).

## Refusal conditions

- **Corpus path unresolvable** — `--root`/`--corpus` is not a real directory, or its string contains
  a literal backslash. Stop; do not glob for a near match.
- **Output root not writable by contract** — any folder the owning team has declared read-only, and
  any registered source root. Refuse and say where output may go instead.
- **The gate fails on a relationship page** — fix the page or delete it. Never keep it.
- **Zero pages discovered** — exit 2, not exit 0. A lint that passes on an empty set is a lie.
- **A sensitive identifier would be written into an artifact** — hard stop. Where a corpus is
  governed by a pseudonymisation rule, the real↔pseudonym mapping lives outside the corpus and is
  never serialised into a record, a page, or a run-state file.
- **A machine write would change a non-empty `operator_note`** or overwrite a human-owned file — the
  bucket's write aborts. The system does not win that argument; it stops.

## Asymmetric write boundary

**Reads, never writes:** every registered source root; any external registry a profile points at.

**Writes:** only inside the corpus `<ROOT>` — the scaffold tree, `records.jsonl`,
`project_records.jsonl`, `MAP.md`, relationship pages under `03_workstreams/`, and run state under
`90_system/state/` — plus the lint report at the path the linter prints, which is always outside the
audited corpus.

**Writes once, then never again (`write_if_absent`):** `PROJECT_HUB.md`, `current_brief.md`,
`02_codebases/README.md`, `90_system/config/corpus.config.json`. `90_system/_entities.md` is
human-authored and only ever read.

**Never writes, in any mode:** `operator_note`; `status` after its initial `draft`; `manual_edit`
after its initial `false`; the `## Operator note` section of any page; any file inside a source
corpus.

## Output contract

| Mode | Produces |
|---|---|
| `scaffold` | the corpus tree at `<ROOT>`, `PROJECT_HUB.md`, one `current_brief.md` per workstream, `90_system/config/corpus.config.json`; JSON summary on stdout |
| `ingest` | `01_sources/<bucket>/normalized/records.jsonl`, `01_sources/_spines/project_records.jsonl`, `MAP.md`, run state under `90_system/state/`; JSON counts on stdout |
| `relate` | `90_system/state/<run_id>_relation_proposals.json`, and **at most one** relationship page that passed the gate. Zero pages is a legitimate outcome — report it. |
| `lint` | exactly one report file outside the audited corpus, its path on stdout, and zero mutations to anything audited |

Every mode may also produce nothing. Say so plainly when it does.
