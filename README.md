# map-of-content-builder

Builds a map of content over a folder of documents, and mechanically verifies that every quotation in a generated note appears verbatim in the file it cites.

AI-generated notes cite their sources. Almost nobody checks whether the quoted text is actually in the cited file, and when you check, it usually is not — not exactly. The quotation marks end up wrapped around a paraphrase that reads like the source without being it. This tool does the check: exact string containment against the bytes on disk. No model calls, no network, no embeddings.

## The finding

Measured on a real 39-page AI-generated corpus carrying explicit provenance lines: **448 quoted snippets, 116 of which do not occur verbatim in the source they cite** — under byte-exact matching or after Unicode/case/whitespace normalization. That is 26%.

Every trace on one page was then hand-checked against the underlying sources. All 15 traces, 5 failures, and all 5 failures were close paraphrases rather than inventions: their longest contiguous verbatim runs against the cited source were 9-of-10, 6-of-9, 6-of-10, 4-of-7 and 4-of-7 words. When the claims themselves were checked against the real source windows, all five held up.

So state it precisely, because the loose reading is wrong and would sink the credibility of everything below: **this is a provenance defect, not a truth defect.** The underlying claims were largely true. The quotation marks were not earned. A failing check here means *this quotation is not verbatim*. It never means *this claim is false*, and no report this tool writes should be read that way.

A second pattern showed up in the same corpus: a systematic locator bug, where quotes pointed at a timestamp one speaking turn after the line they actually quoted. The quote was real and in the right file, just filed at the wrong offset. That is a different problem with a different fix, which is why it gets a different severity.

## Quickstart

No config, no API key, no network.

```bash
pip install pyyaml
python3 scripts/lint_corpus.py --corpus examples/demo-corpus --profile profiles/default.profile.yaml
```

The demo corpus ships with a planted defect: one quotation that reads like its source and is not in it. Abridged output —

```jsonc
// stdout: the machine summary
{"run_id": "20260803-1820", "corpus_id": "default", "mode": "audit",
 "pages": 1, "records": 4, "traces": 4, "traces_verified": 3,
 "inline_citations": 10, "errors": 1, "warnings": 1, "advisories": 0,
 "checks_skipped": {"NAME_LEAK": "profile declares no `path_is_sensitive` substrate root"},
 "machine_verified": "mechanical checks only; semantic checks are queued, not judged"}
```

```markdown
<!-- from the report -->
### TRACE_ABSENT — Quoted snippet is not in the cited source (1)

- `03_workstreams/platform_reliability/hold-window-inherits-incident-lag.md`:55
  — source_id=`design_notes:incident-checkout-timeouts.md` · locator=`H:contributing-factors`
  · raw="the dead-letter threshold of five attempts was too generous for an outage shaped like this one"
  · hint=first 8 words occur at offset 830 (1x in source) — likely a paraphrase

### TRACE_LOCATION — Right source, wrong locator (1)

- `03_workstreams/platform_reliability/hold-window-inherits-incident-lag.md`:56
  — source_id=`design_notes:decision-blue-green-deploys.md` · locator=`H:context` · tier=exact
  · raw="The hold period is bounded by the worst consumer lag we have measured"
```

Three of the four traces verify. The paraphrase is an **error**; the verbatim-but-misfiled quote is a **warning**. That split is the whole design — collapsing the two is what makes a checker get muted.

An audit exits 0 unless the run itself failed; findings live in the report. Add `--gate` for a non-zero exit on any error finding — that is the CI-shaped invocation, and it exits 1 on this demo.

## How it works

Four modes over one shared library. Every mode is deterministic Python.

| Mode | What it does |
|---|---|
| `scaffold` | Creates the corpus tree and its config from a JSON file. Human-owned files are written once and never overwritten. |
| `ingest` | Normalizes each source file into a 12-field JSONL record with a content hash and resolvable evidence paths, then rebuilds the spine. Refuses to invent a one-sentence summary. |
| `relate` | Proposes candidate pairs from shared entities, tags and links — raw overlap components, no score, no suggested relation kind. |
| `lint` | Audits a corpus against a declarative profile. Writes one report outside the corpus. Never edits what it audits. |

Verification, step by step:

1. Parse the trace lines out of the page: `<!-- trace: <record_id> | <ordinal> | <locator> | raw: "<span>" -->`.
2. Resolve `<record_id>` through the record spine to a real file on disk. No file, no verdict — that is its own error.
3. Reject snippets that are unverifiable by construction before matching: an ellipsis (a spliced quote is a fabricated quote), or a length outside the profile's token band.
4. **Tier `exact`** — is `raw` a substring of the source text? **Tier `normalized`** — same test after NFKD decomposition, diacritic strip, smart-quote folding, casefold, and collapse of runs of non-alphanumerics. A hit at either tier returns VERIFIED. There is no third tier.
5. No hit at either tier → **ERROR**. A prefix/trigram near-miss search then runs and attaches its result as a `hint` on the failing finding, to help a human find the paraphrase. It cannot change the verdict or the exit code.
6. Hit, but outside the window the locator names → **WARNING**. Right source, wrong offset.

The two severities are deliberately not merged. A snippet absent from its cited source is a broken quote; a snippet present but filed at the wrong offset is citation imprecision. Collapsed into one number, the reference corpus reports 8 failures where its real broken-quote count is 5 — and a report that inflates gets muted, which costs you the 5.

Numeric thresholds, tier lists and token bounds live only in `profiles/*.yaml`, never in a `.py` file. Swapping the rules for a corpus is a config edit.

## What it does not do

- **It cannot tell you whether a verbatim quote supports the claim attached to it.** String existence is not entailment. Quote-mining passes every check here. If one model writes both the claim and its supporting quote, verification converts an unbounded hallucination risk into a bounded quote-mining risk — a real improvement, and not a proof. A page that clears every check is *mechanically clean*, never *verified*.
- **It says nothing about completeness.** A passage twenty lines below the quote that contradicts it is invisible to this tool.
- **It needs the sources on local disk.** There is no URL fetching and no network access anywhere in it. If you cannot point at the bytes, it cannot check the quote.
- **It does not fix anything.** No `--fix`, no suggested-patch field. It reports, and the edit stays a human decision.
- **It is not a RAG evaluator.** Exact string verification against a named file, not LLM-as-judge scoring, not semantic similarity, not a retrieval benchmark. Duplicate and contradiction detection are queued for a human to judge, and the tool is explicit in its own report that it made no such judgement.
- **It is not useful over one document.** A map of content over a single file is a table of contents. Read the file.

## Requirements

Python 3.9+ and PyYAML. Nothing else — the rest is standard library. Runs on macOS, Linux and Windows.

## Design notes

[`docs/how-it-works.md`](docs/how-it-works.md) — the record schema, the folder contract, the trace grammar, why fuzzy matching is never allowed to return a pass, and the limits stated at more length than they are above.

## License

MIT. See [LICENSE](LICENSE).
