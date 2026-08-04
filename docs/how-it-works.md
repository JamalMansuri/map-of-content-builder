# How it works

This is the engineering reasoning behind the tool: what the data model is, what the verifier
actually checks, and which of the obvious-looking design choices are wrong. Most of what follows was
contested during the build, and several parts were reversed by measurement rather than argument.

## The record schema

A map of content is only as good as its substrate. The substrate here is one JSONL row per source
file, twelve fields, in a fixed serialization order so that a rebuild produces a diffable file:

| Field | What it is |
|---|---|
| `record_id` | `<bucket>:<native_id>`. The only identifier that ever gets written into a page, a trace line or a map. |
| `source_type` | What kind of thing this is — a note, a transcript, a config, a doc page. |
| `title` | Lifted from the source, not invented. |
| `gist` | One declarative sentence. Never machine-authored; see below. |
| `tags` | Scalar list, lifted from the source's own metadata. |
| `status` | `draft` on creation. The machine writes that value once and never again. |
| `retrieval_text` | Normalized body, structure and inline citations preserved. |
| `source_updated_at` | The source's own timestamp. |
| `captured_at` | When this record was built. |
| `content_hash` | `sha256:` over the raw bytes. Load-bearing; see "Drift versus fabrication". |
| `evidence_paths` | Where the bytes live. Resolved at read time, never copied. |
| `operator_note` | The human's own take. No script and no model ever writes it. |

The schema is flat and every array holds scalars, which means it drops into one `CREATE TABLE` with
an index over `retrieval_text` if you ever want it in a database. That is not incidental — the
format was chosen for a downstream that reads it as rows.

**Why a `gist` is never machine-invented.** The one-sentence summary is the field that decides
whether a human ever opens a source again, so a plausible-sounding wrong one is expensive in a way a
missing one is not. If a source declares its own summary, that string is lifted character-for-character.
If it declares none, the record joins a queue written to disk, the run exits non-zero, and *nothing is
written*. A human or a model fills the queue into a JSON map, and the run is repeated pointing at that
file.

That file handshake is how a deterministic script gets model output without making a model call. The
script stays a script — no API key, no network, reproducible — and the judgement enters as a
reviewable artifact on disk rather than as a side effect inside a black box. The refusal is the
feature. A tool that quietly synthesizes a summary from the first paragraph produces a corpus where
every gist is technically present and none of them are trustworthy, which is worse than one that
stopped and told you what it needed. The queue does offer the source's opening line, labelled
explicitly as a seed and not a gist — there to save a file read, not to be copied into the field.

## The folder contract, and fixed versus chosen

Every corpus gets the same tree:

```
<ROOT>/
├── PROJECT_HUB.md                        written once, human-owned thereafter
├── MAP.md                                the human-navigable view, regenerated each run
├── 01_sources/
│   ├── _spines/project_records.jsonl     the spine
│   └── <bucket>/normalized/records.jsonl the canonical per-bucket artifact
├── 02_codebases/
├── 03_workstreams/<workstream>/          briefs and relationship pages
├── 05_exports/
└── 90_system/
    ├── config/corpus.config.json         written once, frozen
    ├── _entities.md                      the glossary — human-authored, only ever read
    └── state/                            run state, handshake queues, proposals
```

The distinction that matters is **fixed names versus chosen names**. The numbered directories, the
spine path, `PROJECT_HUB.md`, `current_brief.md`, the config path and the glossary path are fixed.
Nothing invents them, nothing renames them, and no run may reinterpret them. Exactly three things are
chosen at scaffold time: the corpus slug, the bucket names, and the workstream names.

A **bucket is a surface, not a topic** — where bytes came from, never what they are about.
`meeting_transcripts`, `wiki_export`, `pdf_reports` are buckets. `security`, `onboarding`,
`q3_planning` are workstreams. The distinction sounds pedantic until a corpus grows, at which point a
topic-named bucket becomes a place where the same document belongs in two buckets and neither is
wrong. Buckets and workstreams freeze after the scaffold run: later runs may add, never rename or
delete, since a machine rename of a name embedded in generated links is not something you can undo by
reading a diff.

Two JSONL artifacts exist — the per-bucket records and the aggregated spine — and no more. Every
additional projection of the same rows is a second reader of one schema plus a synchronization check,
and the second reader always eventually disagrees with the first.

## The trace-line grammar

A page carries two kinds of citation. The visible one, inline in the prose:

```
[src:<record_id>|<locator>]
```

And the hidden one, at the bottom of the page, which is the actual verifiable commitment:

```
<!-- trace: <record_id> | <ordinal> | <locator> | raw: "<verbatim span>" -->
```

Pipe-delimited rather than comma-delimited because a `record_id` legitimately contains `:` and `/`.
`<ordinal>` is the 1-based index of this citation among the citations sharing the same
`(record_id, locator)` in document order; it is derivable from the body, so it is recomputed on a
rebuild rather than trusted.

Locators name the most specific anchor the source supports: `T:HH:MM:SS` for timestamped material,
`H:<heading-slug>` for prose, `P:<n>` for a paragraph index, `L:<n>` for a line, `K:<dotted.key>` for
structured payloads, and empty for "the whole artifact", which is only honest on a short file.

The rules for the `raw:` span are blunt because the failure they prevent is the one this whole tool
exists to catch. Copy the span character-for-character out of the open file. Do not re-type from
memory. Do not tidy the casing, the punctuation, the disfluencies or the transcription errors. One
contiguous span — never merge two, no ellipsis, ever, because a spliced quote is a fabricated quote.
And if no single contiguous span supports the claim, the claim is wrong-shaped: split it into the
claims that each do have a span, or drop it. Do not stretch a quote to cover a claim it does not
cover.

## The verification algorithm

`verify_trace()` is the single implementation. Both the write-time gate and the full audit call it;
nothing else re-implements any part of it.

1. **Resolve.** Look up `record_id` in the spine, read `evidence_paths[0]`, read the file. If the id
   does not resolve, or the path does not exist, the result is UNRESOLVED — a broken citation, which
   is a different finding from a quote that cannot be found inside a file that does exist.
2. **Drift check.** If the trace declares a `content_hash`, hash the file now and compare. The
   result is carried on the finding regardless of what happens next.
3. **Reject the unverifiable up front.** An ellipsis in the snippet is MALFORMED. So is a snippet
   whose normalized token count falls outside the profile's band. Both conditions make a trace
   uncheckable by construction, so they are reported rather than silently passed.
4. **Parse the locator** into a kind and a value, and compute the window of source text it points
   at — the block after a timestamp marker, the section under a heading, the nth paragraph, the line
   plus context. An unparseable locator is MALFORMED.
5. **Tier `exact`.** Is `raw` a substring of the source text as read? If yes: VERIFIED.
6. **Tier `normalized`.** Apply one normalizer to both sides — NFKD decomposition, combining marks
   dropped, curly quotes and dashes folded to ASCII, `casefold()` (not `lower()`, because the Turkish
   dotted I and the German eszett are real), everything outside `[a-z0-9' ]` collapsed to a space,
   whitespace runs collapsed. The apostrophe stays in the allowed set on purpose. Substring test
   again. If yes: VERIFIED.
7. **Neither tier hit:** UNVERIFIED, severity error.
8. **A tier hit, but the snippet is not inside the locator window:** VERIFIED with
   `at_locator=False`, severity warning.

The normalization policy is measured, not assumed. Over the fifteen provenance lines of one
carefully-built reference page: byte-exact matching found 9 of 15, adding case-folding found 10, adding
whitespace collapse found 10, and adding punctuation-stripping still found 10. Case-folding is worth
one line and is kept. Stripping punctuation beyond that buys nothing and only widens the
false-positive window, so it is not done.

## Why fuzzy matching is never a pass

The draft design had a five-rung ladder: exact, normalized, punctuation-stripped, first-five-words
prefix, and a fuzzy ratio threshold. It was cut to two rungs because of one measured failure. Under
that ladder, a trace whose snippet was a sentence occurring **nowhere in any source file in the
corpus** was reported VERIFIED. The fuzzy rung matched it off a three-word trigram — a filler phrase
of the "we're going to" variety — that happened to occur three times elsewhere in the same file. The
verifier did not find the quote. It found three ordinary English words and called that a citation.

A verifier that passes a fabricated quote is worse than having no verifier at all, because it
launders the fabrication behind a green check: before, a reader had a vague unease about a quote;
after, the reader has a machine's assurance that it is fine. The tool's entire value is that its
"pass" means something narrow and exact, and every additional rung trades some of that meaning for a
slightly nicer-looking pass rate.

So: **only `exact` and `normalized` may return VERIFIED.** Prefix and fuzzy matching survive in the
codebase, but only as a `hint` attached to an already-failing finding — "the first five words occur
at offset 1180, likely a paraphrase" — to save a human the search. A hint never changes a verdict and
never touches an exit code. The tier list itself lives in the profile YAML rather than in the Python,
so a corpus can narrow the rules but the code has no third rung to widen them to.

## Drift versus fabrication, and why the severities stay apart

Two failures look identical from inside a single verification run: a quote that was wrong when it was
written, and a quote that was right when it was written but whose source has since been edited
underneath it. They have completely different fixes — the first means rewrite the page, the second
means re-verify against the new source, and possibly nothing is wrong at all.

`content_hash` is what separates them. A page records the short hash of each cited source's bytes at
the moment it was written. Quote fails and the hash still matches: the quote was wrong when written.
Quote fails and the hash changed: the source was rewritten under the page. Without that field the two
are indistinguishable and the report has to hedge on every finding, which is the same as saying
nothing.

The other separation is severity, and it is the difference between a report people read and a report
people mute:

- Snippet **absent from the cited source** — ERROR. The quotation is not a quotation.
- Snippet **present in the source but not at the cited locator** — WARNING. The quotation is real and
  the citation is imprecise.

Merged into one count, the reference page reports 8 failures where its real broken-quote count is 5.
Sixty percent inflation on the headline number is exactly how a linter earns a permanent `# noqa` in
someone's workflow, and once it is muted the 5 real findings are lost along with the 3 noisy ones. The
locator warnings were also not random: in the reference corpus they clustered systematically one
speaking turn late, pointing at the listener's acknowledgement rather than the speaker's statement.
That is an upstream bug worth reporting on its own, and it is only visible because the two severities
were kept apart.

## The asymmetric-write principle

The tool reads its substrate and never writes it. This holds at three layers, and each one had a
tempting shortcut.

**At the corpus layer**, every registered source root is read, hashed and pointed at — never copied,
never symlinked, never edited. Symlinks are banned specifically. A symlink from a corpus into a
source folder gives every byte two valid paths, which breaks content-hash duplicate detection, and
makes any recursive walk of the corpus re-walk the entire source lake.

**At the record layer**, `operator_note` is the human's own judgement and is never machine-authored.
If a merge would alter a non-empty one, the write for that bucket *aborts*. The system does not win
that argument; it stops. `status` is written once as `draft`, and any other value found on disk is
treated as a human edit and carried forward — the earlier version hardcoded one specific "reviewed"
value as the thing to preserve, which silently demoted every other legal status back to draft on the
next rebuild, losing a human edit in precisely the field the principle is about. Human-owned files go
through a write-if-absent path and are never overwritten. The glossary is authored by hand and only
ever read. A later run that discovers a new bucket or workstream *reports* it rather than editing the
hub page.

**At the report layer**, the linter reports and never fixes. There is no auto-fix mode and no
suggested-patch field, and both were cut permanently rather than deferred: a suggested fix in a
machine-readable field is one copy-paste away from being applied unreviewed, which converts a tool
that finds provenance defects into a tool that rewrites your quotes for you. Structurally, every write
in the audit path goes through a guard that raises if the target sits inside the audited corpus root.
The one sanctioned in-corpus write is a queue file in the corpus's own system directory, and only when
the profile explicitly enables it — a new file, never an edit to an audited page.

## Configuration lives in the profile, not the code

A profile is a YAML file declaring what "valid" means for one corpus: which files are discovered,
what page types exist and what frontmatter each requires, which verification tiers are permitted, the
snippet token band, the staleness threshold, and the severity of each check.

The rule is absolute: **no numeric threshold, tier list or token bound may appear in a `.py` file.**
Not as a default, not as a fallback in the profile reader. A profile that fails to declare its
verification tiers is rejected rather than quietly handed a default, because a fallback in the reader
is a second place the rule lives and that is how config drifts away from its schema. Severity works
one direction too: each check's default severity is also a ceiling, so a profile may lower one and
never raise one. That is what stops an advisory check becoming a build-breaking error by config edit.

Each check has to answer two questions before it ships: what in the system *produces* the data it
reads, and how its false-positive rate is measured. A check that cannot answer both does not exist.
One check's history is the argument for that rule — a name-leak scan that matched substrings scored
100% false positives on a real corpus, because the word it scanned for was an ordinary English word
appearing throughout the prose. It was rewritten to match whole path components, with the noisy form
split off as advisory.

## Relationship proposals have no score

The pair proposer surfaces candidates from three overlap axes — shared entities, shared tags, shared
links — and reports the raw components. It emits no score and no suggested relation kind. The draft
ranked pairs as a weighted sum of the three, but those weights had never been validated against a
human's judgement of any proposal, which makes the number they produce a made-up quantity wearing the
costume of a measurement. One number also hides *which axis fired*, and that is the only thing telling
you whether a proposal is worth opening.

What is known about the axes, from the validation corpus: **shared entities is the strongest and it is
human-gated**, firing only for terms a human wrote into the glossary — so a fresh corpus produces few
or no entity-backed proposals, which is the asymmetric-write boundary showing through rather than a
bug. It is also the only axis that can bridge two separate corpora, which is how a bridge entity
spanning two document sets gets noticed at all. **Tag overlap is nearly worthless alone**: of 127
distinct tags in that corpus, 104 appeared on exactly one page and can never overlap with anything. A
term appearing on *every* record is excluded from every axis before pairing — not a tuned threshold,
just the observation that a term shared by all records separates no pair from any other.

## Honest limits

**A passing trace proves one thing only:** the quoted characters physically exist in the file that was
cited, right now, and the citation resolves. It proves nothing about entailment. `Both A [src:A|H:x]
and B [src:B|H:y] mention plugins` clears every mechanical check in this tool and is worthless as a
relationship claim. Only a reader can tell a joint claim from a co-mention, and the tool's own report
says so in those words.

If one model writes both a claim and its supporting quote, verification converts an unbounded
hallucination risk into a bounded quote-mining risk. That is a real improvement. It is not a proof,
which is why a page that clears every check is reported as *mechanically clean* and never as
*verified* or *pass*.

**It cannot detect incompleteness.** Nothing here notices a passage twenty lines below the quoted span
that contradicts it, or a source that was never cited because it disagreed.

**It needs the bytes.** No URL fetching, no network. If the source is behind an API, there is nothing
to compare against.

**Semantic checks are an interface, not an implementation.** Duplicate and contradiction detection
emit a mechanically-selected queue of page pairs plus a fixed taxonomy, and stop. No Python here makes
that judgement, the report states so plainly, and the taxonomy is a closed list because open-ended
contradiction detection proved unreliable in testing.

**It is a shape-and-provenance audit, not a review.** A corpus can pass every check in this tool and
be wrong about everything it says.
