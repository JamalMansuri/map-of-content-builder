#!/usr/bin/env python3
"""lint_corpus.py — the corpus auditor. Reports drift and unverifiable provenance.

CONTRACT, stated once and enforced structurally: **this script never edits a file it
audits.** Zero exceptions. Every write goes through `_guard_write()`, which raises if the
target sits inside the audited corpus root. The one sanctioned in-corpus write is the
semantic queue, and only when the profile explicitly sets `report.queue_in_corpus: true`
(a new file in the corpus's own system area — never an edit to an audited page).

Two modes, ONE code path (an earlier build carried a separate `verify_traces.py`; it was
deleted precisely so that the write-time gate and the audit could not drift apart):

    lint_corpus.py --corpus ROOT --profile P.yaml           full audit; exit 0 unless it crashed
    lint_corpus.py --corpus ROOT --profile P.yaml --gate    write-time; relationship checks
                                                            only; exit 1 on any error finding

Everything that could be tuned — severities, tier lists, token bounds, staleness
thresholds, cluster overlaps — lives in the `*.profile.yaml`, never here. The mechanical
proof is a grep for bare numeric literals over `scripts/*.py`; every tunable in this file
arrives through a `profile.…get(...)` call, never as a literal. (The grep pattern itself
is deliberately NOT written out here: a file that quotes the pattern makes the check fail
on its own documentation.)

Semantic and cross-corpus checks are INTERFACE ONLY. Earlier research found LLM
contradiction and duplicate detection unreliable without a fixed taxonomy, so this script
emits a queue of page pairs plus the taxonomy to prompt with and makes no judgement. The
report says so in plain words; nothing here claims those checks work.

Python 3.9+ (`from __future__` is load-bearing: the system interpreter on many machines
is still 3.9.x).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import corpuslib as C  # noqa: E402

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_BAD = 2

_RANK = {C.SEV_ADVISORY: 0, C.SEV_WARNING: 1, C.SEV_ERROR: 2}


# --------------------------------------------------------------------------
# the check catalogue — 19 checks, hard cap 20
#
# Every entry names (a) what in this build PRODUCES the data it reads and (b) how its
# false-positive rate is measured. A check that cannot answer both does not ship. The
# `default_severity` is also the CEILING: a profile may lower a severity, never raise it.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    id: str
    group: str          # mechanical | semantic-interface | cross-corpus
    default_severity: str
    title: str
    detects: str
    producer: str
    fp_basis: str
    gate: bool = False  # part of the --gate (write-time relationship) set


CHECKS: Tuple[Check, ...] = (
    # ---- mechanical: frontmatter shape -----------------------------------
    Check("FM_ABSENT", "mechanical", C.SEV_ERROR, "No parseable frontmatter",
          "The page has no YAML frontmatter block, or the block is present but does not parse.",
          "templates/*.tpl and the page-authoring step that fills them in.",
          "A page deliberately kept frontmatter-free. Counted by inspecting every finding: "
          "a hit is a false positive only if the file is not meant to be a page, which "
          "means the profile's discovery rules are wrong, not the check."),
    Check("FM_TYPE", "mechanical", C.SEV_ERROR, "Unknown or missing page type",
          "`type` is absent, or is outside the vocabulary the profile declares under `types:`.",
          "The `type` field written by the page author; vocabulary declared per corpus profile.",
          "Fires on every page when the profile's vocabulary is wrong for the corpus — the "
          "exact 39/39 failure measured when a linter was pointed at a corpus whose type "
          "vocabulary it did not share. Measure by checking whether the hit rate is ~100%: "
          "that indicts the profile, not the pages."),
    Check("FM_REQUIRED", "mechanical", C.SEV_ERROR, "Required frontmatter field missing",
          "A field the profile requires for this page's type is absent. SUPPRESSED entirely "
          "when FM_TYPE fired on the same page — that suppression is what stops the cascade.",
          "The per-type `required:` list in the profile; fields written by the page author.",
          "Low by construction: the required list is measured from the corpus's own pages "
          "before the profile is written."),
    Check("FM_STATUS", "mechanical", C.SEV_ERROR, "Status outside the allowed set",
          "`status` is not one of the profile's `status_values`.",
          "The `status` field; allowed set declared per corpus profile.",
          "Fires wrongly only when the profile's status vocabulary is stale. Case-insensitive "
          "and bool-safe comparison, so YAML coercion is not a false-positive source."),
    Check("FM_PLACEHOLDER", "mechanical", C.SEV_ERROR, "Unfilled template placeholder",
          "A literal placeholder marker declared by the profile survives in the page.",
          "templates/*.tpl, which ship with human-fill markers.",
          "A page that legitimately quotes a marker. Measure by reading each hit; expected "
          "to be rare enough to enumerate by hand."),

    # ---- mechanical: citation resolution ---------------------------------
    Check("CITE_UNRESOLVED", "mechanical", C.SEV_ERROR, "Inline citation resolves to nothing",
          "An inline citation's `source_id` does not resolve to a file through the profile's "
          "substrate declaration (the spine for `record_field`, the registry for "
          "`id_registry_file`).",
          "build_spine.py writes the spine; the profile declares the registry join.",
          "Zero by construction when the spine is current: resolution is a dict lookup, not "
          "a heuristic. A non-zero rate means the spine is stale, which is the finding.",
          gate=True),

    # ---- mechanical: trace verification ----------------------------------
    Check("TRACE_ABSENT", "mechanical", C.SEV_ERROR, "Quoted snippet is not in the cited source",
          "corpuslib.verify_trace() returned UNVERIFIED: the snippet occurs nowhere in the "
          "cited source under either VERIFIED-capable tier (exact, normalized).",
          "The `<!-- trace: ... -->` lines written at page-authoring time; verification by "
          "corpuslib.verify_trace(), the single implementation.",
          "MEASURED 0/5 on a hand-checked reference page: all 15 of its traces were compared "
          "against the cited sources by hand, and all 5 flagged snippets are genuinely "
          "non-verbatim. Note what the finding does NOT assert: all 5 are close paraphrases "
          "(longest contiguous verbatim runs 9/10, 6/9, 6/10, 4/7, 4/7 words) and all 5 "
          "underlying claims proved substantively TRUE. The cause is "
          "paraphrase-presented-as-quote, which is why this is never called 'hallucination' "
          "and must not be read as 'the claim is false'.",
          gate=True),
    Check("TRACE_LOCATION", "mechanical", C.SEV_WARNING, "Right source, wrong locator",
          "Snippet verified in the cited source but NOT inside the window the locator points "
          "at. A weaker claim than absence, and deliberately a different severity: collapsing "
          "the two turns 5 real problems into 9 and trains the reader to mute the report.",
          "The locator half of each trace line; window definition from the profile's "
          "`block_anchor_re`.",
          "Window-dependent by nature. On the reference page all 4 hits provably sit in the "
          "ADJACENT block of the same source, so they are true findings about citation "
          "precision, not false positives — but the COUNT moves with the window definition, "
          "so it is reported, never asserted.",
          gate=True),
    Check("TRACE_DRIFT", "mechanical", C.SEV_WARNING, "Cited source changed since capture",
          "The record's stored `content_hash` no longer matches the bytes of the source the "
          "trace cites. The page may or may not still be accurate; this check cannot tell.",
          "build_records.py writes `content_hash` into each record.",
          "Any whitespace- or encoding-normalising touch of a source file fires this. That is "
          "why it is a warning whose text says the source changed, not that the page is wrong.",
          gate=True),
    Check("TRACE_MALFORMED", "mechanical", C.SEV_WARNING, "Trace line is not verifiable by construction",
          "The snippet carries an ellipsis, or falls outside the profile's token bounds, or "
          "the locator does not parse. Such a trace can never be checked, so it is reported "
          "rather than silently passed.",
          "Page-authoring; bounds and locator grammar declared in the profile.",
          "Zero when the profile declares no bounds — only locator-syntax faults remain "
          "detectable, and those are unambiguous.",
          gate=True),
    Check("TRACE_SOURCE_MISSING", "mechanical", C.SEV_ERROR, "Trace cites an unresolvable source",
          "The trace's `source_id` resolves to no file at all — a broken citation, distinct "
          "from a quote that cannot be found inside a file that does exist.",
          "The same substrate declaration CITE_UNRESOLVED uses.",
          "Zero by construction (dict lookup / glob). Non-zero means a stale spine or a "
          "renamed source, both of which are real.",
          gate=True),

    # ---- mechanical: relationship-page shape -----------------------------
    Check("SRC_MIN", "mechanical", C.SEV_ERROR, "Too few distinct sources",
          "The page cites fewer DISTINCT source ids than its type's `min_sources`. For a "
          "relationship page that floor is 2: a 'relationship' grounded in one source is a "
          "summary wearing a relationship's frontmatter.",
          "propose_relations.py proposes the pair; the drafting step writes `relates:`; "
          "the floor is declared per type in the profile.",
          "Zero: it is a set-cardinality comparison against a declared floor.",
          gate=True),
    Check("CLAIM_UNCITED", "mechanical", C.SEV_ERROR, "Claim-bearing line carries no citation",
          "On a type whose profile spec sets `cite_every_claim: true`, a body line long "
          "enough to assert something carries no inline citation. Uncited page == "
          "hallucinated page, applied per line.",
          "The drafting step that authors the page; the length floor and the exempt "
          "sections are declared in the profile's `claims:` block.",
          "Long non-assertive lines (setup sentences, list preambles) are the false-positive "
          "mode. Tuned by raising `claims.min_words` in the profile, never in code.",
          gate=True),

    # ---- mechanical: index vs disk ---------------------------------------
    Check("INDEX_MISSING_FILE", "mechanical", C.SEV_ERROR, "Record points at a file that is gone",
          "A record's evidence path does not resolve to a file on disk.",
          "build_records.py writes `evidence_paths`; build_spine.py aggregates them.",
          "Zero by construction — a filesystem existence test."),
    Check("INDEX_ORPHAN_FILE", "mechanical", C.SEV_WARNING, "Source file with no record",
          "A file under the profile's declared source directories appears in no record's "
          "`evidence_paths`. Usually means: rebuild the index before trusting this report.",
          "build_records.py; the source directories are declared in the profile's `index:` block.",
          "Fires on any incidental file inside a source directory (notes, .DS_Store-likes). "
          "Bounded by the profile's `index.source_glob`; measure by listing the hits."),

    # ---- mechanical: safety and time -------------------------------------
    Check("NAME_LEAK", "mechanical", C.SEV_ERROR, "Name-bearing path in an artifact",
          "Runs LAST, over every artifact the corpus contains. Three finding kinds. "
          "`name_leak_filename` (error): a filename from a `path_is_sensitive` root appears "
          "verbatim in an artifact — reported REDACTED, because printing the token would "
          "commit the leak. `name_leak_path` (error): corpuslib.scan_for_sensitive() hit a "
          "sensitive root's directory name as a WHOLE path component. `name_leak_substring` "
          "(advisory): the same hit as ordinary prose.",
          "Every writer in this build; the sensitive roots are declared in the profile's "
          "`substrate.roots`.",
          "MEASURED on a 39-page corpus, and the measurement is why the check is split "
          "three ways: that corpus's sensitive root is named `safe`, an ordinary English "
          "word, so the library's raw substring scan returns 8 line hits and ALL 8 are "
          "'safe'/'safety' in prose — 100% false positives, which is why that form is "
          "advisory. Whole-component path matching: 0/0 (a substring test scored 1/1 false "
          "positives on the prose `capability/safety`, which is what forced whole-component "
          "matching). Filename form: 0 hits across all 39 pages, 0 false positives.",
          gate=True),
    Check("STALE", "mechanical", C.SEV_ADVISORY, "Page not updated recently",
          "`updated` is older than the profile's `temporal.staleness_days`.",
          "The `updated` field written by the page author; the threshold is declared per corpus.",
          "An evergreen page that is correct and simply old. Advisory forever — never promote. "
          "On a machine-generated 39-page corpus every page shared one `updated` date, so "
          "this check fires 0/39 or 39/39 depending on the threshold and is useless without "
          "per-corpus tuning. That is why the threshold is not in code and the check is "
          "skipped when the profile declines to set one."),

    # ---- semantic interface: LLM-side, NOT implemented here ---------------
    Check("SEM_DUPLICATE", "semantic-interface", C.SEV_ADVISORY, "Possible duplicate pages (queued)",
          "INTERFACE ONLY. Emits candidate page pairs and a fixed taxonomy for a model (or a "
          "human) to judge. This script makes no duplicate judgement and reports no findings.",
          "This script emits the queue; the consumer is the model-side step that reads it.",
          "Not applicable — no verdict is produced, so no verdict can be wrong. The pair "
          "SELECTION rate is measurable (pairs queued / pairs possible) and is reported."),
    Check("SEM_CONTRADICTION", "semantic-interface", C.SEV_ADVISORY, "Possible contradictions (queued)",
          "INTERFACE ONLY. Same pair list, prompted with the fixed five-kind taxonomy "
          "(polarity, quantity, ordering, attribution, status). Anything outside the taxonomy "
          "is dropped, not softened. No verdict is ever produced here.",
          "This script emits the queue; the consumer is the model-side step that reads it.",
          "Not applicable — see SEM_DUPLICATE. Earlier research found unbounded LLM "
          "contradiction detection unreliable, which is why the taxonomy is fixed and the "
          "ceiling is advisory."),
)

CHECKS_BY_ID: Dict[str, Check] = {c.id: c for c in CHECKS}
assert len(CHECKS) <= 20, "check catalogue is capped at 20"

# The fixed taxonomies handed to the LLM. Fixed is the point: open-ended contradiction
# detection measured unreliable, and an open taxonomy is how it becomes open-ended.
SEMANTIC_TAXONOMY: Dict[str, List[str]] = {
    "SEM_DUPLICATE": [
        "same_claim_same_subject",
        "subsumption",
        "supersession",
        "complement_not_duplicate",
    ],
    "SEM_CONTRADICTION": [
        "polarity",
        "quantity",
        "ordering",
        "attribution",
        "status",
    ],
}


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

@dataclass
class Finding:
    check: str
    finding: str
    severity: str
    file: str
    line: int
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check, "finding": self.finding, "severity": self.severity,
            "file": self.file, "line": self.line, "message": self.message,
            "detail": self.detail,
        }


class Ctx:
    """Everything the checks read. Built once, never mutated by a check."""

    def __init__(self, root: pathlib.Path, profile: C.Profile) -> None:
        self.root = root
        self.profile = profile
        self.pages: List[pathlib.Path] = []
        self.notes: Dict[pathlib.Path, Tuple[Dict[str, Any], str, str]] = {}  # fm, body, text
        self.gated_out: List[pathlib.Path] = []
        self.index: Optional[Dict[str, Dict[str, Any]]] = None
        self.spine_path: Optional[pathlib.Path] = None
        self.findings: List[Finding] = []
        self.skipped: Dict[str, str] = {}
        self.notes_out: List[str] = []
        self.stats: Dict[str, Any] = {}
        self._substrate_cache: Dict[str, bool] = {}
        self._bad_type_pages = set()

    # -- helpers ---------------------------------------------------------
    def rel(self, p: pathlib.Path) -> str:
        try:
            return str(pathlib.Path(p).relative_to(self.root))
        except ValueError:
            return str(p)

    def add(self, check: str, finding: str, path: pathlib.Path, line: int, message: str,
            severity: Optional[str] = None, **detail: Any) -> None:
        spec = CHECKS_BY_ID[check]
        sev = severity or effective_severity(self.profile, spec)
        self.findings.append(Finding(check, finding, sev, self.rel(path), line, message, detail))

    def skip(self, check: str, reason: str) -> None:
        self.skipped[check] = reason

    def resolves(self, source_id: str) -> bool:
        """Memoised substrate resolution. A cache, not a second resolver: the answer
        still comes from corpuslib.load_substrate()."""
        if source_id not in self._substrate_cache:
            try:
                path, _ = C.load_substrate(self.profile, source_id, self.index)
            except (C.CorpusError, OSError, ValueError):
                path = None
            self._substrate_cache[source_id] = path is not None
        return self._substrate_cache[source_id]


def effective_severity(profile: C.Profile, spec: Check) -> str:
    """Profile severity, clamped at the catalogue ceiling. A profile may lower a
    severity; it may never raise one. This is what keeps an advisory LLM-side check
    from being promoted to an error by a profile edit."""
    want = profile.check_severity(spec.id, spec.default_severity)
    if want not in _RANK:
        want = spec.default_severity
    return want if _RANK[want] <= _RANK[spec.default_severity] else spec.default_severity


# --------------------------------------------------------------------------
# small local helpers
#
# None of these duplicate a corpuslib facility. `_scalar_key` is a comparison
# canonicaliser, not a parser; `_key_line` locates a line for reporting and does not
# parse frontmatter (corpuslib.parse_frontmatter does that).
# --------------------------------------------------------------------------

def _scalar_key(v: Any) -> str:
    """Canonical comparison form for a YAML scalar.

    THE COERCION TRAP: yaml.safe_load returns Python True for `ai-generated: true`, so
    `str(v) == "true"` is False and a naive discovery gate silently matches nothing —
    zero pages discovered, a vacuous pass. Compare parsed values in this canonical form
    instead, so bool True and the string "true" agree.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return str(v).strip().casefold()


def _key_line(text: str, key: str) -> int:
    """1-based line of `key:` near the top of a file, for reporting. Best effort."""
    for i, ln in enumerate(text.splitlines()[:400], 1):
        if ln.startswith(key + ":"):
            return i
    return 1


def _as_date(v: Any) -> Optional[datetime.date]:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(v or "").strip())
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _source_ids(value: Any) -> List[str]:
    """Distinct citation ids out of a frontmatter list that may hold strings or mappings."""
    out: List[str] = []
    if isinstance(value, str):
        value = [value]
    for item in (value or []):
        if isinstance(item, dict):
            sid = item.get("record_id") or item.get("id") or item.get("source_id")
        else:
            sid = item
        if sid and str(sid) not in out:
            out.append(str(sid))
    return out


_PATHISH = re.compile(r"[^\s`'\"()\[\]<>,;]*[/\\][^\s`'\"()\[\]<>,;]*"
                      r"|[^\s`'\"()\[\]<>,;]+\.[A-Za-z0-9]{1,5}\b")


def _path_shaped_tokens(line: str, segment: str) -> List[str]:
    """Tokens on `line` where `segment` is a WHOLE path or filename component.

    Implemented locally because corpuslib.scan_for_sensitive() answers a different
    question — 'does this directory NAME appear anywhere in the bytes' — and on a root
    whose basename is a common English word ('safe') that is a 100%-false-positive signal
    in prose. This post-filter does not re-implement the scan; it classifies the scan's
    hits so error severity lands only on genuinely path-shaped occurrences.

    Whole-component matching is load-bearing. A substring test promotes the prose
    `capability/safety` to an error — measured, 1/1 false positives on a real corpus.
    Splitting on `/`, `\\` and `.` and demanding an exact component match keeps
    `~/x/safe`, `safe/whoever.txt` and `source.cleaned.safe.txt` while dropping it.
    """
    out: List[str] = []
    for t in _PATHISH.findall(line):
        if segment not in t:
            continue
        if segment in re.split(r"[/\\.]+", t):
            out.append(t)
    return out


def _sensitive_stems(profile: C.Profile) -> List[str]:
    """Filename stems living under a `path_is_sensitive` root.

    Held in memory and NEVER serialized — no caller may write one into any artifact
    (the sensitive-source invariant). This is a new capability, not a second copy of a
    library scan:
    corpuslib.scan_for_sensitive() searches for the root DIRECTORY's name, which cannot
    detect the hazard the invariant actually exists to prevent — a name-bearing FILENAME
    copied into a page. Whole-stem matching, so no length floor and no threshold is
    needed here.
    """
    stems: List[str] = []
    for root in profile.sensitive_roots:
        d = pathlib.Path(os.path.expanduser(root))
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                stem = f.name.split(".")[0]
                if stem and stem not in stems:
                    stems.append(stem)
    return stems


def _inline_regex(profile: C.Profile) -> Optional[str]:
    return profile.trace.get("inline_re") or (profile.raw.get("citation") or {}).get("inline_re")


def _iter_body_lines(text: str) -> Iterable[Tuple[int, str, str]]:
    """Yield (line_no, section_heading, line) for body lines, skipping frontmatter,
    fenced code and trace comments."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    fenced = False
    section = ""
    for n in range(i, len(lines)):
        ln = lines[n]
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if s.startswith("#"):
            section = s
            continue
        if s.startswith("<!--") or not s:
            continue
        yield n + 1, section, ln


# --------------------------------------------------------------------------
# context construction
# --------------------------------------------------------------------------

def build_context(root: pathlib.Path, profile: C.Profile) -> Ctx:
    ctx = Ctx(root, profile)
    gate_fields = (profile.discovery.get("gate_fields") or {})
    gate_norm = {str(k): _scalar_key(v) for k, v in gate_fields.items()}

    for p in C.iter_corpus_files(root, profile):
        text = C.read_text(p)
        fm, body = C.parse_frontmatter(text)
        # The gate applies only to pages that HAVE frontmatter. A page with none can
        # never satisfy a gate field, so gating it would let the discovery rules swallow
        # exactly the case FM_ABSENT exists to catch — silently, which is the worst
        # possible failure for an auditor.
        if gate_norm and fm:
            if any(_scalar_key(fm.get(k)) != want for k, want in gate_norm.items()):
                ctx.gated_out.append(p)
                continue
        ctx.pages.append(p)
        ctx.notes[p] = (fm, body, text)

    idx_cfg = profile.raw.get("index") or {}
    idx_rel = idx_cfg.get("path")
    if idx_rel:
        sp = pathlib.Path(idx_rel)
        if not sp.is_absolute():
            sp = root / sp
        ctx.spine_path = sp
        if sp.exists():
            ctx.index = C.load_index(sp)
            # corpuslib.load_substrate() resolves `evidence_paths[0]` with a bare
            # Path(), i.e. relative to the PROCESS cwd, not to the corpus. Rather than
            # forcing every producer to serialize absolute paths, anchor them here, in
            # memory, using the same resolver check_index uses. Nothing is written back:
            # the on-disk spine keeps whatever convention its writer chose.
            for rec in ctx.index.values():
                anchored = []
                for ev in (rec.get("evidence_paths") or []):
                    hit = _resolve_evidence(root, str(ev))
                    anchored.append(str(hit) if hit else str(ev))
                if anchored:
                    rec["evidence_paths"] = anchored

    ctx.stats["pages"] = len(ctx.pages)
    ctx.stats["pages_gated_out"] = len(ctx.gated_out)
    ctx.stats["records"] = len(ctx.index or {})
    return ctx


# --------------------------------------------------------------------------
# checks — mechanical
# --------------------------------------------------------------------------

def check_frontmatter(ctx: Ctx) -> None:
    prof = ctx.profile
    types = prof.types
    status_values = prof.raw.get("status_values")
    placeholders = prof.raw.get("placeholders") or []

    if not types:
        ctx.skip("FM_TYPE", "profile declares no `types:` vocabulary")
        ctx.skip("FM_REQUIRED", "profile declares no `types:` vocabulary")
    if not status_values:
        ctx.skip("FM_STATUS", "profile declares no `status_values:`")
    if not placeholders:
        ctx.skip("FM_PLACEHOLDER", "profile declares no `placeholders:` markers")

    allowed_status = {_scalar_key(s) for s in (status_values or [])}

    for p in ctx.pages:
        fm, body, text = ctx.notes[p]

        if not fm:
            kind = ("frontmatter_unparseable" if text.lstrip().startswith("---")
                    else "frontmatter_absent")
            ctx.add("FM_ABSENT", kind, p, 1,
                    "no parseable YAML frontmatter block")
            ctx._bad_type_pages.add(p)
            continue

        ptype = fm.get("type")
        if types:
            if ptype is None:
                ctx.add("FM_TYPE", "type_absent", p, 1, "frontmatter has no `type` field")
                ctx._bad_type_pages.add(p)
            elif str(ptype) not in types:
                ctx.add("FM_TYPE", "type_unknown", p, _key_line(text, "type"),
                        "type %r is outside this profile's vocabulary (%s)"
                        % (str(ptype), ", ".join(sorted(types))), type=str(ptype))
                ctx._bad_type_pages.add(p)
            else:
                spec = types.get(str(ptype)) or {}
                for f in (spec.get("required") or []):
                    if f not in fm:
                        ctx.add("FM_REQUIRED", "required_field_missing", p, 1,
                                "type `%s` requires `%s`, which is absent" % (ptype, f),
                                field=f, type=str(ptype))

        if allowed_status:
            got = _scalar_key(fm.get("status"))
            if got not in allowed_status:
                ctx.add("FM_STATUS", "status_not_allowed", p, _key_line(text, "status"),
                        "status %r is not one of %s"
                        % (fm.get("status"), ", ".join(sorted(allowed_status))),
                        status=str(fm.get("status")))

        for marker in placeholders:
            if marker in text:
                ln = next((i for i, l in enumerate(text.splitlines(), 1) if marker in l), 1)
                ctx.add("FM_PLACEHOLDER", "placeholder_unfilled", p, ln,
                        "unfilled placeholder %r survives in the page" % marker,
                        marker=marker)


def check_citations(ctx: Ctx) -> None:
    pattern = _inline_regex(ctx.profile)
    if not pattern:
        ctx.skip("CITE_UNRESOLVED", "profile declares no `trace.inline_re`")
        return
    total = 0
    for p in ctx.pages:
        _, _, text = ctx.notes[p]
        for m in re.finditer(pattern, text):
            total += 1
            sid = (m.groupdict().get("source_id") or "").strip()
            if not sid or not ctx.resolves(sid):
                ln = text.count("\n", 0, m.start()) + 1
                ctx.add("CITE_UNRESOLVED", "citation_unresolved", p, ln,
                        "inline citation %r resolves to no file on disk" % sid,
                        source_id=sid)
    ctx.stats["inline_citations"] = total


def check_traces(ctx: Ctx) -> None:
    prof = ctx.profile
    have_hash = bool(ctx.index) and any(r.get("content_hash") for r in (ctx.index or {}).values())
    if not have_hash:
        ctx.skip("TRACE_DRIFT",
                 "no record index with `content_hash` is available for this corpus")

    total = verified = 0
    for p in ctx.pages:
        _, _, text = ctx.notes[p]
        for tr in C.parse_traces(text, prof):
            total += 1
            declared = None
            if have_hash and ctx.index and tr.source_id in ctx.index:
                declared = ctx.index[tr.source_id].get("content_hash") or None
            try:
                res = C.verify_trace(prof, tr, ctx.index, declared)
            except (C.CorpusError, OSError, ValueError) as exc:
                ctx.add("TRACE_SOURCE_MISSING", "trace_resolution_failed", p, tr.line_no,
                        "resolving %r raised: %s" % (tr.source_id, exc),
                        source_id=tr.source_id)
                continue

            if res.status == C.TRACE_UNRESOLVED:
                ctx.add("TRACE_SOURCE_MISSING", "trace_source_missing", p, tr.line_no,
                        "trace cites %r, which resolves to no file" % tr.source_id,
                        source_id=tr.source_id, locator=tr.locator)
            elif res.status == C.TRACE_MALFORMED:
                ctx.add("TRACE_MALFORMED", "trace_malformed", p, tr.line_no,
                        res.detail, source_id=tr.source_id, locator=tr.locator,
                        raw=tr.raw)
            elif res.status == C.TRACE_UNVERIFIED:
                ctx.add("TRACE_ABSENT", "trace_absent", p, tr.line_no,
                        "quoted snippet does not occur in the cited source "
                        "(this means the QUOTATION could not be confirmed, not that the "
                        "claim is false)",
                        source_id=tr.source_id, locator=tr.locator, raw=tr.raw,
                        hint=res.hint, detail_text=res.detail)
            elif res.status == C.TRACE_VERIFIED:
                verified += 1
                if res.at_locator is False:
                    ctx.add("TRACE_LOCATION", "trace_wrong_locator", p, tr.line_no,
                            "snippet is in the cited source but not at locator %r"
                            % tr.locator,
                            source_id=tr.source_id, locator=tr.locator, raw=tr.raw,
                            tier=res.tier)
            if res.drift and have_hash:
                ctx.add("TRACE_DRIFT", "source_drifted", p, tr.line_no,
                        "the cited source changed since the record was captured; the page "
                        "may or may not still be accurate",
                        source_id=tr.source_id)

    ctx.stats["traces"] = total
    ctx.stats["traces_verified"] = verified


def check_relationships(ctx: Ctx) -> None:
    prof = ctx.profile
    types = prof.types
    typed_min = {t: spec for t, spec in types.items() if (spec or {}).get("min_sources")}
    cite_types = {t for t, spec in types.items() if (spec or {}).get("cite_every_claim")}
    claims_cfg = prof.raw.get("claims") or {}
    pattern = _inline_regex(prof)

    if not typed_min:
        ctx.skip("SRC_MIN", "no type in this profile declares `min_sources`")
    if not cite_types or not claims_cfg.get("min_words") or not pattern:
        ctx.skip("CLAIM_UNCITED",
                 "profile declares no `claims.min_words` / `cite_every_claim` type / "
                 "`trace.inline_re`")

    min_words = claims_cfg.get("min_words")
    exempt = set(claims_cfg.get("exempt_sections") or [])

    for p in ctx.pages:
        fm, body, text = ctx.notes[p]
        ptype = str(fm.get("type") or "")
        spec = types.get(ptype) or {}

        if typed_min and ptype in typed_min:
            field_name = spec.get("sources_field") or "record_ids"
            ids = _source_ids(fm.get(field_name))
            floor = spec.get("min_sources")
            if len(ids) < floor:
                ctx.add("SRC_MIN", "too_few_sources", p, _key_line(text, field_name),
                        "type `%s` requires %d distinct sources in `%s`; found %d"
                        % (ptype, floor, field_name, len(ids)),
                        type=ptype, field=field_name, found=len(ids), required=floor)

        if pattern and min_words and ptype in cite_types:
            for line_no, section, line in _iter_body_lines(text):
                if section in exempt:
                    continue
                stripped = re.sub(r"^\s*[-*+]\s+|^\s*\d+\.\s+|^\s*>\s?", "", line).strip()
                if len(stripped.split()) < min_words:
                    continue
                if re.search(pattern, line):
                    continue
                ctx.add("CLAIM_UNCITED", "claim_uncited", p, line_no,
                        "claim-bearing line carries no inline citation",
                        section=section, text=stripped[:160])


def check_index(ctx: Ctx) -> None:
    prof = ctx.profile
    idx_cfg = prof.raw.get("index") or {}
    if not idx_cfg.get("path"):
        ctx.skip("INDEX_MISSING_FILE", "profile declares no `index.path`")
        ctx.skip("INDEX_ORPHAN_FILE", "profile declares no `index.path`")
        return
    if ctx.spine_path is None or not ctx.spine_path.exists():
        reason = "declared index %s does not exist" % idx_cfg.get("path")
        ctx.skip("INDEX_MISSING_FILE", reason)
        ctx.skip("INDEX_ORPHAN_FILE", reason)
        return

    seen: set = set()
    for rid, rec in sorted((ctx.index or {}).items()):
        paths = rec.get("evidence_paths") or []
        if not paths:
            ctx.add("INDEX_MISSING_FILE", "record_without_evidence", ctx.spine_path, 1,
                    "record %s carries no evidence_paths" % rid, record_id=rid)
            continue
        for ev in paths:
            resolved = _resolve_evidence(ctx.root, str(ev))
            if resolved is None:
                ctx.add("INDEX_MISSING_FILE", "evidence_path_missing", ctx.spine_path, 1,
                        "record %s points at %r, which does not exist" % (rid, ev),
                        record_id=rid, evidence_path=str(ev))
            else:
                seen.add(resolved.resolve())

    src_dirs = idx_cfg.get("source_dirs") or []
    if not src_dirs:
        ctx.skip("INDEX_ORPHAN_FILE", "profile declares no `index.source_dirs`")
        return
    glob = idx_cfg.get("source_glob") or "**/*"
    for d in src_dirs:
        base = pathlib.Path(d)
        if not base.is_absolute():
            base = ctx.root / base
        if not base.exists():
            continue
        for f in sorted(base.glob(glob)):
            if not f.is_file():
                continue
            if f.resolve() not in seen:
                ctx.add("INDEX_ORPHAN_FILE", "source_without_record", f, 1,
                        "file is under a declared source directory but appears in no "
                        "record's evidence_paths; rebuild the index before trusting this")


def _resolve_evidence(root: pathlib.Path, ev: str) -> Optional[pathlib.Path]:
    cand = pathlib.Path(os.path.expanduser(ev))
    tries = [cand] if cand.is_absolute() else [root / cand, root / "01_sources" / cand]
    for t in tries:
        if t.exists():
            return t
    return None


def check_temporal(ctx: Ctx) -> None:
    temporal = ctx.profile.raw.get("temporal") or {}
    days = temporal.get("staleness_days")
    if not days:
        ctx.skip("STALE", "profile declares no `temporal.staleness_days` threshold")
        return
    field_name = temporal.get("updated_field") or "updated"
    cutoff = datetime.date.today() - datetime.timedelta(days=int(days))
    for p in ctx.pages:
        fm, _, text = ctx.notes[p]
        d = _as_date(fm.get(field_name))
        if d is not None and d < cutoff:
            ctx.add("STALE", "page_stale", p, _key_line(text, field_name),
                    "`%s` is %s, older than the profile's %s-day threshold"
                    % (field_name, d.isoformat(), days),
                    updated=d.isoformat())


def check_name_leak(ctx: Ctx) -> None:
    """The sensitive-source invariant. Runs LAST, over every artifact in the corpus."""
    prof = ctx.profile
    if not prof.sensitive_roots:
        ctx.skip("NAME_LEAK", "profile declares no `path_is_sensitive` substrate root")
        return

    artifacts: List[pathlib.Path] = list(ctx.pages)
    if ctx.spine_path is not None and ctx.spine_path.exists():
        artifacts.append(ctx.spine_path)

    hits = C.scan_for_sensitive(prof, artifacts)
    raw_hits = 0
    for path, segment in hits:
        text = C.read_text(path)
        for n, line in enumerate(text.splitlines(), 1):
            if segment not in line:
                continue
            raw_hits += 1
            tokens = _path_shaped_tokens(line, segment)
            if tokens:
                # NEVER print the token. corpuslib.assert_not_sensitive() is the
                # authority on whether a string may be serialized; if it refuses, so do
                # we. A lint that echoes the leaked path into its own report has
                # committed the leak a second time — the file and line are enough for a
                # human to go and look.
                safe_tokens: List[str] = []
                for t in tokens:
                    try:
                        C.assert_not_sensitive(prof, t)
                        safe_tokens.append(t)
                    except C.SensitivePathError:
                        pass
                # NEVER put the raw segment in the finding. It was sitting here beside
                # the carefully-redacted `tokens`, and _detail_line prints it
                # unconditionally -- so the check that exists to catch a leaked name was
                # printing leaked names into every report.
                detail: Dict[str, Any] = {"segment": C.redact(segment),
                                          "token_count": len(tokens)}
                if len(safe_tokens) < len(tokens):
                    detail["redacted"] = True
                else:
                    detail["tokens"] = safe_tokens
                ctx.add("NAME_LEAK", "name_leak_path", path, n,
                        "a path-shaped token contains a whole component of a sensitive "
                        "root — serialize the corpus's opaque id instead and resolve at "
                        "read time (the token is REDACTED here on purpose)",
                        **detail)
            else:
                ctx.add("NAME_LEAK", "name_leak_substring", path, n,
                        "a sensitive root's directory name appears as ordinary prose; "
                        "reported as advisory because the segment is also an ordinary word",
                        severity=C.SEV_ADVISORY, segment=C.redact(segment))

    # The stronger half: a name-bearing FILENAME copied into an artifact. Findings are
    # reported REDACTED — the file and line, never the name. Writing the name into the
    # report would commit the very leak the check exists to catch.
    stems = _sensitive_stems(prof)
    ctx.stats["sensitive_stems_known"] = len(stems)
    leaked = 0
    for p in artifacts:
        try:
            text = C.read_text(p)
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for stem in stems:
                if stem in line:
                    leaked += 1
                    ctx.add("NAME_LEAK", "name_leak_filename", p, n,
                            "a filename from a `path_is_sensitive` root appears verbatim "
                            "in this artifact (the offending token is REDACTED here on "
                            "purpose — printing it would commit the leak). Replace it with "
                            "the corpus's opaque id and resolve at read time.",
                            redacted=True, token_length=len(stem))
                    break
    ctx.stats["name_leak_filename_hits"] = leaked
    ctx.stats["name_leak_raw_hits"] = raw_hits


# --------------------------------------------------------------------------
# semantic interface — queue only, no judgement
# --------------------------------------------------------------------------

def build_semantic_queue(ctx: Ctx, run_id: str) -> Optional[Dict[str, Any]]:
    sem = ctx.profile.raw.get("semantic") or {}
    cluster_on = sem.get("cluster_on")
    min_overlap = sem.get("min_overlap")
    max_pairs = sem.get("max_pairs")
    if not cluster_on or not min_overlap or not max_pairs:
        reason = ("profile declares no `semantic:` block (cluster_on / min_overlap / "
                  "max_pairs); no pairs queued")
        ctx.skip("SEM_DUPLICATE", reason)
        ctx.skip("SEM_CONTRADICTION", reason)
        return None

    keys: Dict[pathlib.Path, set] = {}
    for p in ctx.pages:
        fm, _, _ = ctx.notes[p]
        k = set()
        for f in cluster_on:
            v = fm.get(f)
            if isinstance(v, list):
                for item in v:
                    k.add("%s:%s" % (f, _scalar_key(item)))
            elif v is not None:
                k.add("%s:%s" % (f, _scalar_key(v)))
        keys[p] = k

    scored: List[Tuple[int, pathlib.Path, pathlib.Path, List[str]]] = []
    ordered = sorted(ctx.pages)
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            shared = sorted(keys[ordered[i]] & keys[ordered[j]])
            if len(shared) >= int(min_overlap):
                scored.append((len(shared), ordered[i], ordered[j], shared))
    scored.sort(key=lambda t: (-t[0], str(t[1]), str(t[2])))
    possible = len(ordered) * (len(ordered) - 1) // 2
    selected = scored[: int(max_pairs)]

    ctx.stats["semantic_pairs_possible"] = possible
    ctx.stats["semantic_pairs_over_threshold"] = len(scored)
    ctx.stats["semantic_pairs_queued"] = len(selected)

    return {
        "run_id": run_id,
        "corpus_id": corpus_id_of(ctx.profile),
        "generated_by": "lint_corpus.py",
        "advisory": True,
        "machine_verified": False,
        "statement": (
            "These pairs were selected MECHANICALLY by shared frontmatter keys. No "
            "duplicate or contradiction judgement has been made. Nothing in this file is "
            "a finding."
        ),
        "instructions": (
            "Read each pair. Classify ONLY with the kinds listed under `taxonomies`; "
            "anything that does not fit a listed kind is dropped, not softened. For "
            "SEM_CONTRADICTION quote both sides verbatim, require both sides to carry a "
            "citation that passed CITE_UNRESOLVED, and offer NO verdict about which side "
            "is right. Every finding you emit is advisory and may not be raised to error."
        ),
        "taxonomies": SEMANTIC_TAXONOMY,
        "output_contract": {
            "check": "SEM_DUPLICATE | SEM_CONTRADICTION",
            "finding": "one kind from the matching taxonomy",
            "severity": "advisory",
            "file": "corpus-relative path of side A",
            "line": "1-based line of the quoted span in side A",
            "message": "one sentence, no verdict",
            "detail": {"pair": ["A", "B"], "quote_a": "verbatim", "quote_b": "verbatim"},
        },
        "selection": {
            "cluster_on": list(cluster_on),
            "min_overlap": int(min_overlap),
            "max_pairs": int(max_pairs),
            "pairs_possible": possible,
            "pairs_over_threshold": len(scored),
            "pairs_queued": len(selected),
        },
        "pairs": [
            {"a": ctx.rel(a), "b": ctx.rel(b), "shared_keys": shared, "score": score}
            for score, a, b, shared in selected
        ],
    }


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def corpus_id_of(profile: C.Profile) -> str:
    return str(profile.raw.get("corpus_id") or profile.name)


SEV_TITLE = {C.SEV_ERROR: "Errors", C.SEV_WARNING: "Warnings", C.SEV_ADVISORY: "Advisories"}


def render_report(ctx: Ctx, run_id: str, mode: str, queue_path: Optional[pathlib.Path],
                  ran: List[str]) -> str:
    prof = ctx.profile
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    counts = {s: 0 for s in SEV_TITLE}
    for f in ctx.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    cap = (prof.raw.get("report") or {}).get("max_findings_per_check")

    out: List[str] = []
    a = out.append
    a("---")
    a("created: %s" % today)
    a("updated: %s" % today)
    a("type: lint-report")
    a("corpus_id: %s" % corpus_id_of(prof))
    a("run_id: %s" % run_id)
    a("status: draft")
    a("ai-generated: true")
    a("---")
    a("")
    a("# Corpus lint — %s — %s" % (corpus_id_of(prof), now.strftime("%Y-%m-%d %H:%M")))
    a("")
    a("**Corpus:** `%s`" % ctx.root)
    a("**Profile:** `%s`" % (prof.path or "(none)"))
    a("**Mode:** %s" % mode)
    a("**Mutations to the audited corpus:** none. This tool never edits a file it audits.")
    a("")
    a("| Measured | Count |")
    a("|---|---|")
    a("| Pages discovered | %d |" % ctx.stats.get("pages", 0))
    a("| Pages excluded by the discovery gate | %d |" % ctx.stats.get("pages_gated_out", 0))
    a("| Records in the index | %d |" % ctx.stats.get("records", 0))
    a("| Traces parsed | %d |" % ctx.stats.get("traces", 0))
    a("| Traces verified | %d |" % ctx.stats.get("traces_verified", 0))
    a("| Inline citations | %d |" % ctx.stats.get("inline_citations", 0))
    a("")
    a("## Summary")
    a("")
    a("| Severity | Count |")
    a("|---|---|")
    for s in (C.SEV_ERROR, C.SEV_WARNING, C.SEV_ADVISORY):
        a("| %s | %d |" % (SEV_TITLE[s], counts.get(s, 0)))
    a("")
    a("| Check | Finding | Severity | Count |")
    a("|---|---|---|---|")
    tally: Dict[Tuple[str, str, str], int] = {}
    for f in ctx.findings:
        tally[(f.check, f.finding, f.severity)] = tally.get((f.check, f.finding, f.severity), 0) + 1
    if tally:
        for (cid, fid, sev), n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
            a("| %s | %s | %s | %d |" % (cid, fid, sev, n))
    else:
        a("| *(none)* | | | 0 |")
    a("")

    for sev in (C.SEV_ERROR, C.SEV_WARNING, C.SEV_ADVISORY):
        a("## %s" % SEV_TITLE[sev])
        a("")
        ids = [c.id for c in CHECKS if any(f.check == c.id and f.severity == sev
                                           for f in ctx.findings)]
        if not ids:
            a("*(none)*")
            a("")
            continue
        for cid in ids:
            spec = CHECKS_BY_ID[cid]
            rows = [f for f in ctx.findings if f.check == cid and f.severity == sev]
            a("### %s — %s (%d)" % (cid, spec.title, len(rows)))
            a("")
            a("%s" % spec.detects)
            a("")
            a("- **Producer:** %s" % spec.producer)
            a("- **False positives:** %s" % _fp_text(prof, spec))
            a("")
            shown = rows[: int(cap)] if cap else rows
            for f in shown:
                extra = _detail_line(f)
                a("- `%s`:%d — %s%s" % (f.file, f.line, f.message, extra))
            if len(shown) < len(rows):
                a("- *(+%d more; raise `report.max_findings_per_check` in the profile to "
                  "list them)*" % (len(rows) - len(shown)))
            a("")

    a("## Checks not run")
    a("")
    if ctx.skipped:
        a("| Check | Reason |")
        a("|---|---|")
        for cid, reason in sorted(ctx.skipped.items()):
            a("| %s | %s |" % (cid, reason))
    else:
        a("*(none — every catalogue check ran)*")
    a("")

    a("## Checks that ran with zero findings")
    a("")
    silent = [c.id for c in CHECKS
              if c.id in ran and c.id not in ctx.skipped
              and not any(f.check == c.id for f in ctx.findings)]
    a(", ".join("`%s`" % c for c in silent) if silent else "*(none)*")
    a("")

    a("## Semantic and cross-corpus checks")
    a("")
    a("**Nothing in this section was machine-verified.** These are LLM-side checks and this "
      "script implements the INTERFACE only: it selects candidate page pairs mechanically "
      "and hands them to a model with a FIXED taxonomy. Open-ended LLM contradiction and "
      "duplicate detection measured unreliable, so no Python here makes that judgement and "
      "no result here should be read as one.")
    a("")
    if queue_path is not None:
        a("- **Queue written:** `%s`" % queue_path)
        a("- **Pairs possible:** %d · **over threshold:** %d · **queued:** %d"
          % (ctx.stats.get("semantic_pairs_possible", 0),
             ctx.stats.get("semantic_pairs_over_threshold", 0),
             ctx.stats.get("semantic_pairs_queued", 0)))
        a("- **SEM_DUPLICATE taxonomy:** %s" % ", ".join(SEMANTIC_TAXONOMY["SEM_DUPLICATE"]))
        a("- **SEM_CONTRADICTION taxonomy:** %s"
          % ", ".join(SEMANTIC_TAXONOMY["SEM_CONTRADICTION"]))
    else:
        a("- No queue emitted: %s" % ctx.skipped.get("SEM_DUPLICATE", "not configured"))
    a("")
    a("**Cross-corpus checks do not ship.** Cross-corpus subject overlap and bridge-entity "
      "naming both need substrate that may not exist yet (a second registered profile, a "
      "populated entity glossary). Shipping a disabled interface would be a "
      "reserved-for-future field, which is its own anti-pattern.")
    a("")

    if ctx.notes_out:
        a("## Notes")
        a("")
        for n in ctx.notes_out:
            a("- %s" % n)
        a("")

    a("## What this run proves, and what it does not")
    a("")
    a("**Machine-verified, deterministic, reproducible from this profile and this corpus:**")
    a("")
    a("- Frontmatter shape against the profile's vocabulary (`FM_*`).")
    a("- That every citation and every trace `source_id` resolves to a real file "
      "(`CITE_UNRESOLVED`, `TRACE_SOURCE_MISSING`).")
    a("- That each quoted snippet does or does not occur in the cited source, under exactly "
      "two tiers — byte-exact and normalized. No fuzzy, prefix or trigram tier can return "
      "VERIFIED here; a near-miss is reported as a hint on a failing finding and never "
      "changes a verdict (`TRACE_ABSENT`, `TRACE_LOCATION`).")
    a("- Index-to-disk consistency and sensitive-path scanning (`INDEX_*`, `NAME_LEAK`).")
    a("")
    a("**NOT proven by any of the above:**")
    a("")
    a("- **That a verified quote supports the claim it is attached to.** A passing "
      "`TRACE_ABSENT` check proves only that a real span of the cited source contains those "
      "words. If one model wrote both the claim and its trace, this converts an unbounded "
      "hallucination risk into a bounded quote-mining risk. It does not eliminate it.")
    a("- **That a failing trace means the claim is false.** The measured dominant cause is "
      "paraphrase presented as a quote. The defect is the provenance line; the assertion may "
      "well be sound. This report never uses the word 'hallucination' for that reason.")
    a("- **Anything semantic.** No duplicate, contradiction, voice or support judgement was "
      "made by this script. See the section above.")
    a("- **That the corpus is correct.** This is a shape-and-provenance audit, not a review.")
    a("")
    a("Run id `%s` · report is additive; `latest.md` is the only file overwritten." % run_id)
    return "\n".join(out) + "\n"


def _fp_text(profile: C.Profile, spec: Check) -> str:
    declared = (profile.checks.get(spec.id) or {}).get("fp_rate")
    if declared:
        return "declared in profile — %s. How it is measured: %s" % (declared, spec.fp_basis)
    return "%s (this profile declares no `checks.%s.fp_rate`; add one once measured)" % (
        spec.fp_basis, spec.id)


def _detail_line(f: Finding) -> str:
    bits = []
    for k in ("source_id", "locator", "type", "field", "marker", "segment", "status"):
        if f.detail.get(k):
            bits.append("%s=`%s`" % (k, f.detail[k]))
    if f.detail.get("raw"):
        bits.append('raw="%s"' % str(f.detail["raw"])[:90])
    if f.detail.get("tokens"):
        bits.append("tokens=%s" % f.detail["tokens"])
    if f.detail.get("text"):
        bits.append('line="%s"' % str(f.detail["text"])[:90])
    if f.detail.get("hint"):
        bits.append("hint=%s" % f.detail["hint"])
    return (" — " + " · ".join(bits)) if bits else ""


def render_latest(ctx: Ctx, run_id: str, report_path: pathlib.Path) -> str:
    counts = {s: 0 for s in SEV_TITLE}
    for f in ctx.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    lines = [
        "# Latest corpus lint — %s" % corpus_id_of(ctx.profile),
        "",
        "Pointer page. The report itself is `%s` and is never overwritten." % report_path.name,
        "",
        "| Field | Value |",
        "|---|---|",
        "| Run | `%s` |" % run_id,
        "| Report | [%s](%s) |" % (report_path.name, report_path.name),
        "| Pages | %d |" % ctx.stats.get("pages", 0),
        "| Traces | %d (verified %d) |" % (ctx.stats.get("traces", 0),
                                           ctx.stats.get("traces_verified", 0)),
        "| Errors | %d |" % counts.get(C.SEV_ERROR, 0),
        "| Warnings | %d |" % counts.get(C.SEV_WARNING, 0),
        "| Advisories | %d |" % counts.get(C.SEV_ADVISORY, 0),
        "",
        "Mutations to the audited corpus: none.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# write guard
# --------------------------------------------------------------------------

def _guard_write(target: pathlib.Path, root: pathlib.Path, allow_in_corpus: bool) -> None:
    """Structural enforcement of 'never edits an audited file'."""
    t = pathlib.Path(target).resolve()
    r = pathlib.Path(root).resolve()
    inside = (t == r) or (r in t.parents)
    if inside and not allow_in_corpus:
        raise C.CorpusError(
            "refusing to write %s: it is inside the audited corpus %s. This tool never "
            "edits what it reads." % (t, r))
    if inside and t.exists():
        raise C.CorpusError(
            "refusing to overwrite an existing file inside the audited corpus: %s" % t)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _run_checks(ctx: Ctx, only: Optional[set], gate: bool) -> List[str]:
    """Returns the ids of checks that were attempted."""
    prof = ctx.profile
    wanted: List[str] = []
    for c in CHECKS:
        if only is not None and c.id not in only:
            continue
        if gate and not c.gate:
            continue
        if not prof.check_enabled(c.id):
            reason = (prof.checks.get(c.id) or {}).get("reason") or "disabled in profile"
            ctx.skip(c.id, reason)
            continue
        wanted.append(c.id)
    want = set(wanted)

    # order matters only in that NAME_LEAK runs last (it sweeps the final state)
    if want & {"FM_ABSENT", "FM_TYPE", "FM_REQUIRED", "FM_STATUS", "FM_PLACEHOLDER"}:
        check_frontmatter(ctx)
    if "CITE_UNRESOLVED" in want:
        check_citations(ctx)
    if want & {"TRACE_ABSENT", "TRACE_LOCATION", "TRACE_DRIFT", "TRACE_MALFORMED",
               "TRACE_SOURCE_MISSING"}:
        check_traces(ctx)
    if want & {"SRC_MIN", "CLAIM_UNCITED"}:
        check_relationships(ctx)
    if want & {"INDEX_MISSING_FILE", "INDEX_ORPHAN_FILE"}:
        check_index(ctx)
    if "STALE" in want:
        check_temporal(ctx)
    if "NAME_LEAK" in want:
        check_name_leak(ctx)

    # Drop findings for checks the caller filtered out but whose producer ran anyway.
    ctx.findings = [f for f in ctx.findings if f.check in want]
    for cid in (set(CHECKS_BY_ID) - want):
        ctx.skipped.setdefault(
            cid, "not selected for this run (--only / --gate scope)")
    return wanted


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lint_corpus.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Audit a map-of-content corpus for drift and unverifiable provenance.\n\n"
            "CONTRACT: this script NEVER edits a file it audits. Zero exceptions. Every "
            "write is checked against the corpus root first, and the only file it may "
            "place inside a corpus is a semantic queue, and only when the profile sets "
            "`report.queue_in_corpus: true`.\n\n"
            "Two modes on one code path:\n"
            "  (default)  full audit; exit 0 unless the run itself failed\n"
            "  --gate     write-time; relationship/provenance checks only; exit 1 on any "
            "error-severity finding\n\n"
            "Exit codes: 0 ok · 1 gate failed · 2 bad invocation, unreadable profile, "
            "missing corpus, or ZERO PAGES DISCOVERED (a lint that reports 0 pages and 0 "
            "errors is a lie, so it is never a pass).\n\n"
            "Severities, tier lists, token bounds and every other tunable live in the "
            "*.profile.yaml, never in this file."),
    )
    p.add_argument("--corpus", help="corpus root (default: the profile's `root:`)")
    p.add_argument("--profile", help="path to a *.profile.yaml (required unless --list-checks)")
    p.add_argument("--gate", action="store_true",
                   help="write-time mode: relationship/provenance checks only, exit 1 on error")
    p.add_argument("--only", help="comma-separated check ids to run (e.g. TRACE_ABSENT,NAME_LEAK)")
    p.add_argument("--report", help="report path (default: <repo>/reports/<corpus_id>/<ts>.md)")
    p.add_argument("--json", action="store_true", help="emit the machine summary on stdout")
    p.add_argument("--list-checks", action="store_true",
                   help="print the check catalogue and exit")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_checks:
        for c in CHECKS:
            print("%-22s %-18s %-9s %s" % (c.id, c.group, c.default_severity, c.title))
        print("\n%d checks (cap 20)." % len(CHECKS))
        return EXIT_OK

    if not args.profile:
        print("--profile is required (or use --list-checks)", file=sys.stderr)
        return EXIT_BAD

    try:
        profile = C.load_profile(pathlib.Path(args.profile))
    except C.CorpusError as exc:
        print("profile error: %s" % exc, file=sys.stderr)
        return EXIT_BAD

    root_str = args.corpus or profile.raw.get("root")
    if not root_str:
        print("no corpus root: pass --corpus or set `root:` in the profile", file=sys.stderr)
        return EXIT_BAD
    root = pathlib.Path(os.path.expanduser(str(root_str))).resolve()
    if not root.is_dir():
        print("corpus root is not a directory: %s" % root, file=sys.stderr)
        return EXIT_BAD

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M")

    try:
        ctx = build_context(root, profile)
    except (C.CorpusError, OSError) as exc:
        print("could not read the corpus: %s" % exc, file=sys.stderr)
        return EXIT_BAD

    # HARD GUARD: zero pages is never a pass. A lint that reports
    # {"pages": 0, "errors": 0, "pass": true} is a lie one level up.
    if not ctx.pages:
        msg = ("discovered 0 pages under %s — refusing to report a vacuous pass. Check the "
               "profile's `discovery:` rules, and remember that yaml.safe_load returns "
               "Python True for `ai-generated: true`, so a gate comparing against the "
               "string \"true\" matches nothing." % root)
        if ctx.gated_out:
            msg += " (%d file(s) were excluded by `discovery.gate_fields`.)" % len(ctx.gated_out)
        print(msg, file=sys.stderr)
        return EXIT_BAD

    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    if only:
        unknown = only - set(CHECKS_BY_ID)
        if unknown:
            print("unknown check id(s): %s" % ", ".join(sorted(unknown)), file=sys.stderr)
            return EXIT_BAD

    try:
        ran = _run_checks(ctx, only, args.gate)
    except (C.CorpusError, OSError) as exc:
        print("check run failed: %s" % exc, file=sys.stderr)
        return EXIT_BAD

    # profile drift: settings for checks this build does not implement
    unknown_cfg = sorted(set(profile.checks) - set(CHECKS_BY_ID))
    if unknown_cfg:
        ctx.notes_out.append(
            "This profile carries settings for check id(s) this build does not implement: "
            "%s. They were ignored." % ", ".join("`%s`" % u for u in unknown_cfg))
    if ctx.gated_out:
        ctx.notes_out.append(
            "%d file(s) were excluded from the audit by `discovery.gate_fields`: %s"
            % (len(ctx.gated_out), ", ".join(sorted(ctx.rel(p) for p in ctx.gated_out))))

    # ---- semantic queue (interface only) -------------------------------
    queue_path: Optional[pathlib.Path] = None
    queue: Optional[Dict[str, Any]] = None
    if not args.gate and (only is None or (only & {"SEM_DUPLICATE", "SEM_CONTRADICTION"})):
        queue = build_semantic_queue(ctx, run_id)
    elif args.gate:
        ctx.skip("SEM_DUPLICATE", "not selected for this run (--only / --gate scope)")
        ctx.skip("SEM_CONTRADICTION", "not selected for this run (--only / --gate scope)")

    # ---- report path ---------------------------------------------------
    if args.report:
        report_path = pathlib.Path(os.path.expanduser(args.report)).resolve()
    else:
        report_dir_cfg = (profile.raw.get("report") or {}).get("dir")
        base = (pathlib.Path(os.path.expanduser(str(report_dir_cfg))) if report_dir_cfg
                else REPO_DIR / "reports" / corpus_id_of(profile))
        report_path = (base / ("%s.md" % run_id)).resolve()

    write_report = (not args.gate) or bool(args.report)

    if queue is not None:
        rep_cfg = profile.raw.get("report") or {}
        if rep_cfg.get("queue_in_corpus"):
            qdir = root / str(rep_cfg.get("queue_dir") or "90_system/state")
            allow = True
        else:
            qdir = report_path.parent
            allow = False
        queue_path = qdir / ("%s_semantic_queue.json" % run_id)
        try:
            _guard_write(queue_path, root, allow_in_corpus=allow)
            C.dump_json(queue_path, queue)
        except (C.CorpusError, OSError) as exc:
            ctx.notes_out.append("semantic queue not written: %s" % exc)
            queue_path = None

    if write_report:
        try:
            _guard_write(report_path, root, allow_in_corpus=False)
            C.atomic_write(report_path, render_report(ctx, run_id, "gate" if args.gate
                                                      else "full audit", queue_path, ran))
            latest = report_path.parent / "latest.md"
            _guard_write(latest, root, allow_in_corpus=False)
            C.atomic_write(latest, render_latest(ctx, run_id, report_path))
        except (C.CorpusError, OSError) as exc:
            print("could not write the report: %s" % exc, file=sys.stderr)
            return EXIT_BAD

    # ---- output --------------------------------------------------------
    counts = {C.SEV_ERROR: 0, C.SEV_WARNING: 0, C.SEV_ADVISORY: 0}
    for f in ctx.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    summary = {
        "run_id": run_id,
        "corpus_id": corpus_id_of(profile),
        "corpus": str(root),
        "profile": str(profile.path),
        "mode": "gate" if args.gate else "audit",
        "pages": ctx.stats.get("pages", 0),
        "pages_gated_out": ctx.stats.get("pages_gated_out", 0),
        "records": ctx.stats.get("records", 0),
        "traces": ctx.stats.get("traces", 0),
        "traces_verified": ctx.stats.get("traces_verified", 0),
        "inline_citations": ctx.stats.get("inline_citations", 0),
        "errors": counts[C.SEV_ERROR],
        "warnings": counts[C.SEV_WARNING],
        "advisories": counts[C.SEV_ADVISORY],
        "checks_run": ran,
        "checks_skipped": ctx.skipped,
        "report": str(report_path) if write_report else None,
        "semantic_queue": str(queue_path) if queue_path else None,
        "semantic_pairs_queued": ctx.stats.get("semantic_pairs_queued", 0),
        "machine_verified": "mechanical checks only; semantic checks are queued, not judged",
    }

    if args.json:
        print(json.dumps({"summary": summary,
                          "findings": [f.as_dict() for f in ctx.findings]},
                         indent=2, ensure_ascii=False))
    else:
        print(json.dumps(summary, ensure_ascii=False))

    if args.gate and counts[C.SEV_ERROR] > 0:
        return EXIT_ERRORS
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_BAD)
