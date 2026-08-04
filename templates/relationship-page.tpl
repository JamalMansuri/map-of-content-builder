---
type: relationship
title: "{{ the relation stated as a phrase — not the topics, the relation }}"
workstream: "{{ snake_case_workstream }}"
relates:
  - record_id: "{{ record_id_from_the_spine }}"
    content_hash: "{{ short_hash }}"
  - record_id: "{{ record_id_from_the_spine }}"
    content_hash: "{{ short_hash }}"
tags: ["{{ tag }}", "{{ tag }}"]
status: draft
created: "{{ YYYY-MM-DD }}"
updated: "{{ YYYY-MM-DD }}"
ai-generated: true
manual_edit: false
---

<!-- =====================================================================
     DRAFTER BRIEF — DELETE THIS ENTIRE COMMENT BLOCK from the finished page.
     =====================================================================

     WHAT THIS PAGE IS FOR
     ---------------------
     A relationship page is the thing that makes a MoC a map of content rather
     than an index. An index tells you where a record is. This tells you what
     two or more records mean together that neither means alone.

     THE RULE, and it is the only rule that matters:

         EVERY CLAIM ON THIS PAGE MUST BE ABOUT THE RELATION BETWEEN TWO OR
         MORE RECORDS, NOT ABOUT ONE RECORD IN ISOLATION.

     The working test: delete any one record from `relates`. If the thesis is
     still true, still supported, and still worth writing down, you wrote a
     list of links and gave it a title.

     BE HONEST ABOUT THE ENFORCEMENT. That rule is NOT mechanically checkable.
     The lint can only enforce two proxies:
       (a) at least two DISTINCT record_ids are cited in the body, and
       (b) every claim carries a citation.
     Both are satisfied by the sentence "Both A [src:A|H:x] and B [src:B|H:y]
     mention plugins." That sentence is worthless and it passes. A human judges
     whether the page is actually about a connection. Nothing else can.
     A page that clears every mechanical check is `mechanically-clean`. It is
     never `verified` and never `pass`.

     FRONTMATTER — every key, and why it is here
     -------------------------------------------
     type: relationship   Selects the ruleset. The lint keys off this and
                          nothing else; a page without it is not linted as a
                          relationship page at all.
     title                The relation as a phrase. Repeated as the H1 so the
                          frontmatter is readable without opening the body.
     workstream           REQUIRED. This is the mapping key into
                          `03_workstreams/`. The page lives in the directory
                          this names. A page with no workstream has no home and
                          lands in the review queue instead of the map.
     relates              The poles of the relation. At least TWO entries with
                          DISTINCT record_ids. One record listed twice is one
                          record.
       .record_id         The corpus's own opaque id, copied exactly from the
                          spine (`01_sources/_spines/project_records.jsonl`).
                          NEVER a filesystem path and never a person's name —
                          the sensitive-source invariant. Ids resolve to real
                          paths at read time; the path is never written down.
       .content_hash      Short hash of the source bytes at the moment this page
                          was written. This is the key that makes DRIFT
                          distinguishable from FABRICATION: hash still matches
                          and a quote fails => the quote was wrong when written.
                          Hash changed and a quote fails => the source was
                          rewritten under the page. Those are different problems
                          with different fixes, and without this key they look
                          identical.
     tags                 Retrieval only. `relationship` is deliberately NOT a
                          required tag: `type` already indexes the page, and a
                          tag carried by every page makes every pair look
                          related — `propose_relations.py` discards such tags as
                          non-discriminating.
     status: draft        The machine writes `draft`. Promotion to `reviewed` is
                          a human act and is never written or reverted by the
                          system.
     created / updated    ISO dates, written UNQUOTED once filled in
                          (`created: 2026-08-03`). Every placeholder above is
                          quoted so that an unfinished page still parses as YAML
                          and the lint reports "this is a placeholder" instead of
                          "this page has no frontmatter".
     ai-generated: true   A YAML boolean, not the string "true". Compare against
                          the parsed value.
     manual_edit: false   Written `false` at authoring. Once a human sets it
                          `true`, the system must not rewrite this body. The
                          system never resets it.

     Deliberately absent: `confidence` / `strength` / `edge_weight` (nothing
     computes or reads them, and an LLM-authored confidence number is a number
     pretending to be evidence); `discovered_by` / `proposal_score` / `llm_model`
     (run-log facts, not page facts); `sources` (`relates` IS the source list —
     a second copy is a second parser for one schema and will drift).

     CITATION AND TRACE GRAMMAR
     --------------------------
     Inline citation, in the prose:   [src:<record_id>|<locator>]
     Trace line, at the bottom:
         <!-- trace: <record_id> | <ordinal> | <locator> | raw: "<verbatim span>" -->

     <ordinal> is the 1-based index of this citation among the inline citations
     sharing the same (record_id, locator), in document order. It is derivable
     from the body, so it is recomputed on a rebuild rather than guessed.

     Locators — pick the most specific one the source supports:
         T:<HH:MM:SS>     timestamp        (transcripts, recordings)
         H:<heading-slug> heading anchor   (prose, markdown)
         P:<n>            paragraph index  (unstructured text, OCR)
         L:<n>            line             (last resort — least stable)
         K:<dotted.key>   key path         (JSON / YAML payloads)
         (empty)          the whole artifact — only for short files

     Sections below appear in this order. Do not add sections; a heading the
     lint does not know is an error, because free-form sections are where "list
     of links" hides.
     ===================================================================== -->

# {{ title }}

<!-- DRAFTER: one paragraph, no heading. State the connection as a single
     falsifiable claim about the records TOGETHER. If this paragraph is true of
     one record on its own, you have not found a relationship yet. This is the
     only prose exempt from per-sentence citation — everything it asserts must
     be carried, cited, in the sections below. -->

## How they connect

<!-- DRAFTER: the argument. Why these records belong on one page. At least one
     sentence here must cite two DISTINCT record_ids in the same breath — that
     joint sentence is the spine of the page. Name the mechanism, the causal
     link, the contradiction, or the shared constraint explicitly; "both concern
     X" is a co-mention, not a connection. -->

## Evidence

<!-- DRAFTER: the claims that hold the argument up, each with an inline
     citation before its terminating punctuation. Every record in `relates` must
     appear here at least once — a record cited nowhere is padding the relata
     count. Prefer few load-bearing claims over many decorative ones. An absence
     claim ("none of these records states X") must name its scope — "in any of
     the three related records", never "anywhere". -->

## Tension / what does not line up

<!-- DRAFTER: the disconfirming evidence, the unresolved conflict, the limit of
     scope. This section is where the page earns trust; a relationship page with
     no tension is usually a page that stopped looking. If there genuinely is
     none, write the single line `- None found.` and mean it. -->

## Open questions

<!-- DRAFTER: what a human would have to check, decide, or remember. Questions
     for the reviewer, not questions you intend to answer on the next run. The
     system never answers these. -->

## Updates

<!-- append-only, dated. KEEP THIS COMMENT.
     New entries append below as `### YYYY-MM-DD` blocks. Existing entries are
     never edited, reordered, or removed — not by the system, not on a rebuild.
     This section is the page's only change log unless the corpus happens to be
     under version control. -->

## Sources

<!-- DRAFTER: one line per `relates` entry, in the same order: a link to the
     source record and what specifically it grounds. This section's ABSENCE is
     treated as evidence the page was hallucinated — it is the cheapest possible
     signal that the author had real sources in hand. -->

- {{ record_id }} — {{ what this record grounds that no other related record does }}
- {{ record_id }} — {{ what this record grounds that no other related record does }}

<!-- =====================================================================
     QUOTING RULES — KEEP THIS BLOCK. It is re-read every time this page is
     edited, and it is the only thing standing between a trace and a fiction.

     1. COPY the span character-for-character out of the open source file. Do
        not re-type it from memory. Do not reconstruct it from what you recall
        the source saying.
     2. DO NOT TIDY. No fixing case, punctuation, grammar, spelling, or
        transcription errors. A transcript that says "the id and the name" is
        quoted as "the id and the name".
     3. ONE CONTIGUOUS SPAN. Never merge two spans into one quote, even if they
        are adjacent, even if the join reads better.
     4. NO ELLIPSIS. `...` or `…` inside a raw: makes the trace malformed, not
        shortened.
     5. IF NO SINGLE CONTIGUOUS SPAN SUPPORTS THE CLAIM, the claim is
        wrong-shaped. Split it into the claims that each do have a span, or drop
        it. Do not stretch a quote to cover a claim it does not cover.
     6. LENGTH: long enough to be unique inside the source, short enough to
        survive a one-word edit to it. The binding bound is `trace.snippet_tokens`
        in this corpus's profile — read it there; do not restate a number here.

     WHY THIS IS BLUNT — this is measured, not cautionary. On one real 39-page
     machine-drafted corpus, 116 of its 448 quoted snippets could not be found
     verbatim in the source they cited. On the best-formed page in that corpus,
     5 of its 15 provenance snippets appear NOWHERE in their cited source under
     any normalization, and three of those read as summary prose wrapped in
     quotation marks. The underlying claims were mostly sound — the defect was
     in the quotation marks, not in the assertions. An LLM asked for a claim AND
     its supporting quote will paraphrase unless it is forced not to. The
     verifier catches this afterwards; writing it correctly is the only thing
     that prevents it.

     WHAT A PASSING TRACE PROVES: the quoted string physically exists in the
     named source right now, and the citation resolves.
     WHAT IT DOES NOT PROVE: that the span SUPPORTS the claim attached to it,
     that the relationship is real, or that a contradicting passage twenty lines
     further down was not ignored. A trace is a commitment device, not a proof.
     ===================================================================== -->

<!-- trace: {{ record_id }} | 1 | {{ locator }} | raw: "{{ verbatim span, copied }}" -->
<!-- trace: {{ record_id }} | 1 | {{ locator }} | raw: "{{ verbatim span, copied }}" -->
