#!/usr/bin/env python3
"""propose_relations.py — the cheap deterministic first pass over a built corpus.

Answers exactly one question: *which pairs of records share enough that a human
should look at them?* It proposes. It never writes a page. Drafting a relationship
page is a judgement call, made outside this tool against the real record bodies.

WHAT IT IS NOT
--------------
There is no weighted score here, and that is deliberate. The earlier design carried
`3*evidence + 2*entities + 1*tags`; the weights were never validated against an
outcome (no human had ever ranked a proposal), so the number they produced was a
made-up quantity wearing the costume of a measurement. This tool reports the raw
overlap components instead and sorts by them lexicographically:

    (shared_entities, shared_tags, shared_links)   -- descending, ties broken by pair id

A human reads the components and decides. A single number would have hidden which
axis fired, which is the only thing that tells you whether a proposal is worth
opening.

THE SIGNAL YOU ACTUALLY HAVE (measured, not assumed)
----------------------------------------------------
* **Shared entities is the strongest axis, and it is human-gated.** It fires only
  for entities a human wrote into the glossary (`90_system/_entities.md`). With an
  empty glossary it cannot fire at all, so a fresh corpus will produce few or no
  entity-backed proposals. That is not a bug; it is the asymmetric-write boundary
  showing through. The glossary is also the only axis that can ever bridge two
  separate corpora.
* **Tag overlap is nearly worthless on its own.** Measured on the 39-page
  validation corpus: 127 distinct tags, 104 of them (82%) appear on exactly one
  page. A tag on exactly one record can never overlap with anything, so most of the
  vocabulary is inert by construction. This run reports the singleton count so the
  number is in front of you rather than in a design doc.
* **Link overlap is real, and an earlier measurement said otherwise.** The figure
  "0.0% of pairs share a link" was computed from the corpus's `links:` frontmatter
  column, which was `[]` on every page. The links were in the *bodies*, written as
  `[[wikilink]]` targets. This script reads links out of `retrieval_text` for that
  reason, and additionally honours an explicit `links` list if a spine row carries
  one.

A term that appears on *every* record is excluded from every axis before pairing.
That is not a tuned threshold — a term shared by all records separates no pair from
any other, so it carries zero information about which pair to read first. Excluded
terms are named in the run summary rather than silently dropped.

ORDERING CONSTRAINT (non-negotiable)
------------------------------------
The spine must be rebuilt in the same run, *before* proposing. Scoring the
validation corpus from its seven-week-stale index produced no overlap for a pair
that plainly shares sources. Proposing from a stale spine is a silent-wrong-answer
machine, and the wrong answer looks exactly like a correct empty result.

WRITES
------
Exactly one file: `<ROOT>/90_system/state/<run_id>_relation_proposals.json`.
Nothing else, anywhere. Source corpora are read-only.

Python 3.9+ (`from __future__` is load-bearing: the system interpreter on many
machines is still 3.9.x). Stdlib + PyYAML only, via corpuslib. No model calls. No
network.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Set BEFORE the corpuslib import: otherwise the import writes
# scripts/__pycache__/corpuslib.cpython-XX.pyc into the source tree, which makes a
# tool that claims to write exactly one file write two.
sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import corpuslib as C  # noqa: E402

Path = pathlib.Path

# Relative layout inside a built corpus. Literal path segments only -- a glob here
# would silently pick up a sibling directory that merely looks like the corpus.
SPINE_REL = ("01_sources", "_spines", "project_records.jsonl")
ENTITIES_REL = ("90_system", "_entities.md")
STATE_REL = ("90_system", "state")

#: `[[target]]`, `[[target|alias]]`, `[[target#heading]]` -> target.
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)")

#: A glossary row a human has not filled in yet. Same marker the scaffolded templates
#: use for "a human still owes this file a decision".
UNFILLED_MARKER = "<<<HUMAN"


# --------------------------------------------------------------------------
# glossary
# --------------------------------------------------------------------------

@dataclass
class Entity:
    canonical: str
    aliases: List[str] = field(default_factory=list)
    kind: str = ""
    corpora: List[str] = field(default_factory=list)

    def terms(self) -> List[str]:
        return [self.canonical] + [a for a in self.aliases if a]


@dataclass
class Glossary:
    path: Path
    present: bool
    entities: List[Entity] = field(default_factory=list)
    unfilled_rows: int = 0

    @property
    def usable(self) -> bool:
        return bool(self.entities)

    def status_line(self) -> str:
        if not self.present:
            return "entity glossary: %s -- ABSENT" % self.path
        return "entity glossary: %s -- %d usable row(s), %d unfilled placeholder row(s)" % (
            self.path, len(self.entities), self.unfilled_rows)

    def caveat(self) -> str:
        """Goes into every `why` whose entity overlap is empty. Deliberately carries no
        path: the path is on the run summary, and repeating it 25 times buries the point.
        """
        if not self.present:
            return ("entity overlap could not fire -- there is no entity glossary, and shared "
                    "entities is the strongest overlap signal")
        if not self.usable:
            return ("entity overlap could not fire -- the entity glossary has no filled rows, "
                    "and shared entities is the strongest overlap signal")
        return ""


def _split_row(line: str) -> List[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip().strip("`").strip() for c in row.split("|")]


def _split_list_cell(cell: str) -> List[str]:
    return [v.strip().strip("`").strip() for v in re.split(r"[,;]", cell) if v.strip()]


def load_glossary(path: Path) -> Glossary:
    """Parse the human-maintained entity glossary.

    Column positions are read from the header row, not assumed, so a human may add
    or reorder columns without breaking this. Only `canonical` is required.
    READ-ONLY: this script never writes, appends to, or reorders this file.
    """
    p = Path(path)
    if not p.exists():
        return Glossary(path=p, present=False)

    header: Optional[List[str]] = None
    cols: Dict[str, int] = {}
    entities: List[Entity] = []
    unfilled = 0

    for line in C.read_text(p).splitlines():
        if "|" not in line:
            continue
        cells = _split_row(line)
        if header is None:
            lowered = [c.lower() for c in cells]
            if "canonical" in lowered:
                header = lowered
                cols = {name: i for i, name in enumerate(lowered)}
            continue
        if all(set(c) <= set("-: ") for c in cells if c):
            continue  # markdown separator row

        def cell(name: str) -> str:
            i = cols.get(name, -1)
            return cells[i] if 0 <= i < len(cells) else ""

        canonical = cell("canonical")
        if not canonical:
            continue
        if UNFILLED_MARKER in line:
            unfilled += 1
            continue
        entities.append(Entity(
            canonical=canonical,
            aliases=_split_list_cell(cell("aliases")),
            kind=cell("kind"),
            corpora=_split_list_cell(cell("corpora")),
        ))

    return Glossary(path=p, present=True, entities=entities, unfilled_rows=unfilled)


# --------------------------------------------------------------------------
# per-record term extraction
# --------------------------------------------------------------------------

def record_text(rec: Dict[str, Any]) -> str:
    """Everything an entity alias could plausibly be written in."""
    parts = [str(rec.get("title", "")), str(rec.get("gist", "")), str(rec.get("retrieval_text", ""))]
    parts.extend(str(t) for t in (rec.get("tags") or []))
    return "\n".join(parts)


def record_tags(rec: Dict[str, Any]) -> List[str]:
    return [str(t).strip() for t in (rec.get("tags") or []) if str(t).strip()]


def record_links(rec: Dict[str, Any]) -> List[str]:
    """Wikilink targets from the body, plus an explicit `links` list if present.

    The body is where the links actually are in every corpus measured so far; a
    `links:` frontmatter column that reads `[]` on every page is why an earlier
    measurement reported this axis as dead.
    """
    out: List[str] = []
    declared = rec.get("links")
    if isinstance(declared, list):
        out.extend(str(v).strip() for v in declared if str(v).strip())
    out.extend(t.strip() for t in WIKILINK_RE.findall(str(rec.get("retrieval_text", ""))) if t.strip())
    return out


# --------------------------------------------------------------------------
# axes
# --------------------------------------------------------------------------

@dataclass
class Axis:
    """One overlap dimension: which records carry which terms."""
    name: str
    postings: Dict[str, Set[str]] = field(default_factory=dict)   # norm term -> record_ids
    display: Dict[str, str] = field(default_factory=dict)         # norm term -> as written
    universal: List[str] = field(default_factory=list)            # dropped: on every record
    singletons: List[str] = field(default_factory=list)           # inert: on exactly one record

    @property
    def distinct(self) -> int:
        return len(self.postings) + len(self.universal) + len(self.singletons)

    def summary(self, n_records: int) -> str:
        bits = ["%s: %d distinct" % (self.name, self.distinct)]
        if self.singletons:
            bits.append("%d on exactly one record (can never overlap)" % len(self.singletons))
        if self.universal:
            bits.append("%d on all %d records, excluded as non-discriminating: %s"
                        % (len(self.universal), n_records,
                           ", ".join(sorted(self.display.get(u, u) for u in self.universal))))
        if not self.postings:
            bits.append("NOTHING LEFT TO PAIR ON")
        return "; ".join(bits)


def build_axis(name: str, per_record: Dict[str, List[Tuple[str, str]]], n_records: int) -> Axis:
    """per_record: record_id -> [(normalized_term, display_term), ...]"""
    axis = Axis(name=name)
    raw: Dict[str, Set[str]] = {}
    for rid, terms in per_record.items():
        for norm, disp in terms:
            if not norm:
                continue
            raw.setdefault(norm, set()).add(rid)
            axis.display.setdefault(norm, disp)
    for norm, rids in raw.items():
        if len(rids) == 1:
            axis.singletons.append(norm)
            continue
        # A term carried by every record separates no pair from any other, so it
        # cannot inform an ordering. This is a definition, not a tuned cutoff.
        # It only applies once there is more than one pair to order: with two
        # records the single pair IS the answer, and dropping the only term they
        # share would report "no overlap" about two records that plainly overlap.
        if n_records > 2 and len(rids) == n_records:
            axis.universal.append(norm)
            continue
        axis.postings[norm] = rids
    return axis


def entity_axis(records: Sequence[Dict[str, Any]], glossary: Glossary) -> Axis:
    per_record: Dict[str, List[Tuple[str, str]]] = {}
    prepared = [(e, [C.normalize(t) for t in e.terms()]) for e in glossary.entities]
    for rec in records:
        rid = rec["record_id"]
        hay = " %s " % C.normalize(record_text(rec))
        hits: List[Tuple[str, str]] = []
        for ent, terms in prepared:
            # Word-boundary containment on the ONE normalizer both sides share.
            if any(t and (" %s " % t) in hay for t in terms):
                hits.append((C.normalize(ent.canonical), ent.canonical))
        per_record[rid] = hits
    return build_axis("entities", per_record, len(records))


def tag_axis(records: Sequence[Dict[str, Any]]) -> Axis:
    per_record = {r["record_id"]: [(C.normalize(t), t) for t in record_tags(r)] for r in records}
    return build_axis("tags", per_record, len(records))


def link_axis(records: Sequence[Dict[str, Any]]) -> Axis:
    per_record = {r["record_id"]: [(C.normalize(t), t) for t in record_links(r)] for r in records}
    return build_axis("links", per_record, len(records))


# --------------------------------------------------------------------------
# pairing
# --------------------------------------------------------------------------

def candidate_pairs(axes: Sequence[Axis]) -> Dict[Tuple[str, str], Dict[str, List[str]]]:
    """Only pairs that share at least one term on some axis are ever formed.

    Enumerating all N*(N-1)/2 pairs and intersecting is equivalent for a floor of 1
    and quadratic for no benefit; posting lists give the same candidate set. Cost is
    driven by the widest term, which is why universal terms are removed first.
    """
    pairs: Dict[Tuple[str, str], Dict[str, List[str]]] = {}
    for axis in axes:
        for norm, rids in axis.postings.items():
            disp = axis.display.get(norm, norm)
            for a, b in combinations(sorted(rids), 2):
                bucket = pairs.setdefault((a, b), {"entities": [], "tags": [], "links": []})
                bucket[axis.name].append(disp)
    for bucket in pairs.values():
        for k in bucket:
            bucket[k] = sorted(set(bucket[k]))
    return pairs


def sort_key(item: Tuple[Tuple[str, str], Dict[str, List[str]]]) -> Tuple[Any, ...]:
    """Lexicographic on the raw components, most overlap first. No weighting.

    Ties break on the pair itself so two runs over the same spine emit the same file.
    """
    (a, b), comp = item
    return (-len(comp["entities"]), -len(comp["tags"]), -len(comp["links"]), a, b)


def _phrase(items: Sequence[str], one: str, many: str) -> str:
    n = len(items)
    if not n:
        return "no shared %s" % many
    return "%d shared %s (%s)" % (n, one if n == 1 else many, ", ".join(items))


def why_sentence(comp: Dict[str, List[str]], glossary: Glossary) -> str:
    """One deterministic sentence naming the overlap. No adjectives, no judgement."""
    bits = [
        _phrase(comp["entities"], "entity", "entities"),
        _phrase(comp["tags"], "tag", "tags"),
        _phrase(comp["links"], "link", "links"),
    ]
    sentence = "; ".join(bits) + "."
    if not comp["entities"]:
        caveat = glossary.caveat()
        if caveat:
            sentence += " " + caveat[0].upper() + caveat[1:] + "."
    return sentence


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------

def guard(profile: C.Profile, values: Iterable[str]) -> None:
    """The sensitive-source invariant -- no name-bearing path may be serialized.

    Every string that reaches the output file passes through here before it is
    written. Raising beats degrading: a silent fallback writes a real person's
    name -- carried by the source filename -- into a published file.
    """
    for v in values:
        C.assert_not_sensitive(profile, str(v))


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

EPILOG = """\
what this proposes on, in order of strength
  entities  a term a HUMAN wrote into 90_system/_entities.md (canonical or alias)
            matched against each record's title, gist, tags and retrieval_text.
            Strongest axis, and the only one that can bridge two corpora. It is
            also the only axis that cannot fire on its own: an empty glossary
            means zero entity overlap and therefore very few proposals. That is
            the expected result for a fresh corpus, not a failure.
  tags      the record's `tags` list. Weak: on the 39-page validation corpus, 104
            of 127 tags (82%) appear on exactly one record and can never overlap.
  links     `[[wikilink]]` targets found in retrieval_text, plus an explicit
            `links` list if the spine carries one.

Terms carried by every record are excluded before pairing (they separate no pair
from any other) and are named in the run summary.

Proposals are sorted by (shared_entities, shared_tags, shared_links), descending,
ties broken by record id. There is no weighted score: read the components.

This tool proposes. It never writes a relationship page -- drafting one is a
judgement about what the records actually say, and happens outside this tool.

The spine must be rebuilt in the same run BEFORE proposing. A stale spine yields a
confidently empty result that is indistinguishable from a correct one.
"""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="propose_relations.py",
        description="Propose record pairs worth a relationship page, by raw overlap only.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", required=True, type=Path,
                    help="corpus root (the built MoC). Read and written under 90_system/state/ only.")
    ap.add_argument("--entities", type=Path, default=None,
                    help="entity glossary (default: <ROOT>/90_system/_entities.md). "
                         "Human-maintained; never written by this tool. Point this at a "
                         "shared glossary one level up to propose across corpora.")
    ap.add_argument("--profile", type=Path, default=None,
                    help="corpus profile *.profile.yaml. Supplies the sensitive-root list "
                         "for the sensitive-source invariant. Without it no root is "
                         "treated as sensitive.")
    # Invocation knobs, not audit parameters: neither is a tier, a match threshold
    # or a token bound, so neither belongs in a profile.
    ap.add_argument("--min-overlap", type=int, default=1, metavar="N",
                    help="minimum TOTAL shared items across all three axes (default: 1). "
                         "Counted, never weighted.")
    ap.add_argument("--limit", type=int, default=25, metavar="N",
                    help="maximum proposals written (default: 25). The number of candidates "
                         "dropped by this cap is always printed.")
    ap.add_argument("--run-id", default=None,
                    help="run id for the output filename (default: YYYYMMDD-HHMMSS).")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.min_overlap < 1:
        ap.error("--min-overlap must be at least 1; a floor of 0 proposes every pair in the corpus")
    if args.limit < 1:
        ap.error("--limit must be at least 1")

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print("ERROR: --root is not a directory: %s" % root, file=sys.stderr)
        return 2
    if "\\" in str(root):
        print("ERROR: --root contains a literal backslash: %r" % str(root), file=sys.stderr)
        return 2
    root = root.resolve(strict=True)

    spine = root.joinpath(*SPINE_REL)
    if not spine.exists():
        print("ERROR: no spine at %s\n"
              "       Build the spine first, IN THIS RUN. Proposing from a missing or stale\n"
              "       spine returns a confidently empty result that looks like a correct one."
              % spine, file=sys.stderr)
        return 2

    profile = C.load_profile(args.profile) if args.profile else C.default_profile()
    glossary = load_glossary(args.entities or root.joinpath(*ENTITIES_REL))

    try:
        rows = C.read_jsonl(spine)
    except C.CorpusError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2

    records = [r for r in rows if r.get("record_id")]
    skipped = len(rows) - len(records)
    n = len(records)

    print("propose_relations: %d record(s) from %s" % (n, spine))
    if skipped:
        print("  %d spine row(s) skipped: no record_id" % skipped)
    print("  " + glossary.status_line())

    if n < 2:
        print("  fewer than two records; nothing can pair. Writing an empty proposal list.")

    axes = [entity_axis(records, glossary), tag_axis(records), link_axis(records)]
    for axis in axes:
        print("  " + axis.summary(n))
    if glossary.caveat():
        print("  NOTE: " + glossary.caveat() + ".")

    pairs = candidate_pairs(axes)
    total_possible = n * (n - 1) // 2
    over_floor = [
        (pair, comp) for pair, comp in pairs.items()
        if len(comp["entities"]) + len(comp["tags"]) + len(comp["links"]) >= args.min_overlap
    ]
    ranked = sorted(over_floor, key=sort_key)
    kept = ranked[:args.limit]
    dropped = len(ranked) - len(kept)

    proposals: List[Dict[str, Any]] = []
    for (a, b), comp in kept:
        guard(profile, [a, b] + comp["entities"] + comp["tags"] + comp["links"])
        proposals.append({
            "pair": [a, b],
            "shared_entities": comp["entities"],
            "shared_tags": comp["tags"],
            "shared_links": comp["links"],
            "why": why_sentence(comp, glossary),
        })

    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = C.ensure_dir(root.joinpath(*STATE_REL))
    out_path = out_dir / ("%s_relation_proposals.json" % run_id)
    C.dump_json(out_path, proposals)

    leaks = C.scan_for_sensitive(profile, [out_path])
    if leaks:
        print("ERROR: sensitive-source violation -- %s contains a sensitive path segment (%s)"
              % (leaks[0][0], C.redact(leaks[0][1])), file=sys.stderr)
        return 2

    print("  pairs possible: %d | pairs sharing >=1 term: %d | over --min-overlap %d: %d"
          % (total_possible, len(pairs), args.min_overlap, len(ranked)))
    print("  proposals written: %d -> %s" % (len(proposals), out_path))
    if dropped:
        msg = ("  DROPPED BY --limit %d: %d further candidate(s) met the overlap floor and are "
               "NOT in that file. It is a truncated queue, not a complete map. Raise --limit "
               "or --min-overlap to see them." % (args.limit, dropped))
        print(msg)
        print(msg.strip(), file=sys.stderr)
    else:
        print("  dropped by --limit: 0 (every candidate over the floor was written)")
    if not proposals:
        print("  no proposals. Zero is a legitimate answer; check the axis summary above for "
              "which axis had nothing to pair on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
