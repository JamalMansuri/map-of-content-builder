#!/usr/bin/env python3
"""build_records.py -- turn source files into `01_sources/<bucket>/normalized/records.jsonl`.

Every bucket produces records. There is no evidence-only mode: if a thing can be cited,
it has a record, so citation resolution is one dict lookup and never a mode branch.

Two rules do most of the work here:

1. **The script never invents a `gist`.** If the source declares its own one-sentence
   summary in frontmatter, that string is lifted verbatim. Otherwise the record joins a
   queue written to `90_system/state/<run_id>_gists_needed.json`, the run exits 1, and
   nothing is written. A human (or a model) fills the queue into a JSON map and the run
   is repeated with `--gists`. That file handshake is how a deterministic script gets
   model output without making a model call.

2. **`operator_note` is never machine-authored and never lost.** New records get `""`;
   re-runs carry the existing value forward via `corpuslib.merge_records()`.

The sensitive-source invariant: no name-bearing filename is ever serialized. Some corpora
have source filenames carrying personal names; every path is passed through
`corpuslib.assert_not_sensitive()` before it can reach a record, and the written files
are swept again afterwards.

Deterministic. No model calls, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys, pathlib
sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import corpuslib as C

#: v1 normalizes text formats only. Anything else gets a record (so it stays citable)
#: with an EMPTY retrieval_text and a notice -- never a fabricated one.
TEXT_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".csv"})
MAX_TEXT_BYTES = 2 * 1024 * 1024

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# --------------------------------------------------------------------------
# local helpers (things corpuslib deliberately does not expose)
# --------------------------------------------------------------------------

def normalize_body(body: str) -> str:
    """The `retrieval_text` normalizer.

    Implemented here rather than in corpuslib on purpose: corpuslib.normalize() is the
    ONE trace-matching normalizer (case-folded, punctuation-flattened) and must not be
    reused for retrieval text -- these two jobs want opposite things. This one KEEPS
    markdown structure and inline citations (stripping citations is how a grounded
    corpus quietly becomes ungrounded) and REMOVES html comments (those are trace lines,
    already represented by evidence_paths). Steps, in order:
        b) CRLF/CR -> LF   c) frontmatter already split by corpuslib.read_note
        d) drop <!-- ... -->   e) right-strip each line
        f) collapse 3+ newlines to 2   g) strip
    """
    t = body.replace("\r\n", "\n").replace("\r", "\n")
    t = _HTML_COMMENT_RE.sub("", t)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _run_id() -> str:
    # corpuslib exposes no clock helper; run ids are a build-script concern.
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(str(p)))


def _as_str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _as_tags(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(t).strip() for t in v if str(t).strip()]
    if isinstance(v, str):
        return [t.strip() for t in v.split(",") if t.strip()]
    return []


def title_for(fm: Dict[str, Any], body: str, path: Path) -> str:
    """frontmatter `title:` -> first H1 -> filename stem."""
    t = _as_str(fm.get("title"))
    if t:
        return t[:200]
    m = _H1_RE.search(body)
    if m:
        return m.group(1).strip()[:200]
    return re.sub(r"[-_]+", " ", path.stem).strip()[:200]


# --------------------------------------------------------------------------
# profiles built from the corpus config
# --------------------------------------------------------------------------

def sensitivity_profile(cfg: Dict[str, Any], profile_path: Optional[Path]) -> C.Profile:
    """A corpuslib Profile carrying every root this run must refuse to serialize.

    Roots come from two places, unioned: `path_is_sensitive: true` on a bucket in the
    corpus config, and `substrate.roots[*].path_is_sensitive` in an audit profile passed
    with --profile. Raising beats degrading: a silent fallback writes a real person's
    name -- carried by the source filename -- into a published artifact.
    """
    roots: List[Dict[str, Any]] = []
    for b in cfg.get("buckets", []):
        if isinstance(b, dict) and b.get("path_is_sensitive"):
            roots.append({"path": b.get("root", ""), "path_is_sensitive": True})
    if profile_path is not None:
        for r in C.load_profile(profile_path).sensitive_roots:
            roots.append({"path": r, "path_is_sensitive": True})
    return C.Profile(name="%s-sensitivity" % cfg.get("corpus_slug", "corpus"),
                     raw={"substrate": {"roots": roots}})


def discovery_profile(bucket: Dict[str, Any]) -> C.Profile:
    """A corpuslib Profile whose only job is to drive iter_corpus_files()."""
    d = {k: bucket[k] for k in ("include", "exclude_dirs", "exclude_prefix", "exclude_glob")
         if k in bucket}
    return C.Profile(name=bucket["name"], raw={"discovery": d})


# --------------------------------------------------------------------------
# record construction
# --------------------------------------------------------------------------

def build_candidate(path: Path, source_root: Path, bucket: Dict[str, Any],
                    sens: C.Profile, captured_at: str) -> Tuple[Dict[str, Any], List[Any]]:
    """One source file -> one 12-field record (gist still empty). Returns (record, notices)."""
    notices: List[Any] = []

    # Sensitive-source gate, on the real path, BEFORE anything derived from it is built.
    C.assert_not_sensitive(sens, str(path))

    rel = path.relative_to(source_root).as_posix()
    record_id = C.make_record_id(bucket["name"], rel)
    # The evidence path must RESOLVE, or the trace verifier -- the whole point of this
    # tool -- can never run on a corpus we build. A bucket-relative "<bucket>/<rel>"
    # discards the source root, and since the folder contract LINKS to the source lake
    # rather than copying it, no path under the corpus root can ever exist. Store the
    # resolved absolute path.
    #
    # Safe by construction: the assert above already refused any sensitive root, so a
    # path only reaches here when serializing it is permitted.
    evidence = path.resolve().as_posix()
    # ...and again on the string that actually gets serialized, because a name can be
    # in the filename even when the root itself is innocuous.
    C.assert_not_sensitive(sens, evidence)

    st = path.stat()
    size = st.st_size
    if path.suffix.lower() in TEXT_SUFFIXES and size <= MAX_TEXT_BYTES:
        fm, body = C.read_note(path)
    else:
        fm, body = {}, ""
        notices.append({"code": "not_normalized", "record_id": record_id,
                        "reason": "suffix %r or size %d bytes outside the text budget; "
                                  "retrieval_text left empty rather than fabricated"
                                  % (path.suffix, size)})

    rec = C.blank_record()
    rec["record_id"] = record_id
    rec["source_type"] = bucket["source_type"]
    rec["title"] = title_for(fm, body, path)
    rec["gist"] = _as_str(fm.get("summary"))
    rec["tags"] = _as_tags(fm.get("tags"))
    rec["status"] = "draft"
    rec["retrieval_text"] = normalize_body(body)
    rec["source_updated_at"] = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%d")
    rec["captured_at"] = captured_at
    rec["content_hash"] = C.content_hash(path)
    rec["evidence_paths"] = [evidence]
    rec["operator_note"] = ""          # human-only, always, forever
    return rec, notices


def carry_forward_captured_at(candidates: List[Dict[str, Any]],
                              existing: List[Dict[str, Any]]) -> None:
    """`captured_at` records when THIS content version was first seen, not when the run
    ran. Carrying it forward on an unchanged hash is what makes a re-run byte-identical
    -- and on a corpus that is not under version control, "the file did not change" is
    the only cheap correctness signal there is. Not a re-implementation of merge_records:
    this prepares candidates, merge_records still owns the human-field merge."""
    by_id = {r.get("record_id"): r for r in existing}
    for c in candidates:
        old = by_id.get(c["record_id"])
        if old and old.get("content_hash") == c["content_hash"] and old.get("captured_at"):
            c["captured_at"] = old["captured_at"]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-bucket records.jsonl for a corpus.")
    ap.add_argument("--root", required=True, help="the corpus <ROOT>")
    ap.add_argument("--bucket", action="append", default=None,
                    help="restrict to this bucket (repeatable); default is every bucket")
    ap.add_argument("--gists", default=None,
                    help='JSON {record_id: "one sentence"} answering the queued records')
    ap.add_argument("--profile", default=None,
                    help="audit profile yaml; its sensitive roots are enforced here too")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--dry-run", action="store_true", help="compute and report; write nothing")
    args = ap.parse_args()

    root = _expand(args.root)
    cfg_path = root / "90_system" / "config" / "corpus.config.json"
    if not cfg_path.exists():
        print(json.dumps({"error": "no corpus config at %s; run scaffold_corpus.py first"
                                   % cfg_path}, indent=2))
        return 1
    cfg = json.loads(C.read_text(cfg_path))

    try:
        sens = sensitivity_profile(cfg, Path(args.profile) if args.profile else None)
    except C.CorpusError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 1

    gists: Dict[str, str] = {}
    if args.gists:
        gp = _expand(args.gists)
        if not gp.exists():
            print(json.dumps({"error": "--gists file not found: %s" % gp}, indent=2))
            return 1
        gists = json.loads(C.read_text(gp))
        if not isinstance(gists, dict):
            print(json.dumps({"error": "--gists must be a JSON object {record_id: gist}"}, indent=2))
            return 1

    run_id = args.run_id or _run_id()
    captured_at = date.today().isoformat()
    wanted = set(args.bucket) if args.bucket else None

    buckets = [b for b in cfg["buckets"] if wanted is None or b["name"] in wanted]
    if not buckets:
        print(json.dumps({"error": "no matching bucket in %s" % cfg_path}, indent=2))
        return 1

    per_bucket: Dict[str, Any] = {}
    pending_writes: List[Tuple[Path, List[Dict[str, Any]]]] = []
    gist_queue: List[Dict[str, str]] = []
    notices: List[Any] = []

    if not sens.sensitive_roots:
        notices.append({"code": "no_sensitive_roots_declared",
                        "reason": "the sensitive-source invariant has nothing to enforce on "
                                  "this run. If any source root has name-bearing filenames, "
                                  "mark it path_is_sensitive in the corpus config or pass "
                                  "--profile."})

    for b in buckets:
        name = b["name"]
        source_root = _expand(b["root"]).resolve()
        candidates: List[Dict[str, Any]] = []
        leads: Dict[str, str] = {}
        try:
            for p in C.iter_corpus_files(source_root, discovery_profile(b)):
                rec, ns = build_candidate(p, source_root, b, sens, captured_at)
                notices.extend(ns)
                if not rec["gist"]:
                    rec["gist"] = _as_str(gists.get(rec["record_id"], ""))
                if not rec["gist"]:
                    leads[rec["record_id"]] = C.first_sentence(rec["retrieval_text"])
                candidates.append(rec)
        except C.SensitivePathError:
            # The exception text quotes the offending path, which is the very thing the
            # sensitive-source invariant forbids putting in front of anyone. Report the
            # bucket and the declared root; never the file.
            print(json.dumps({
                "error": "bucket %r resolves to a path under a root declared "
                         "path_is_sensitive; refusing to build records for it." % name,
                "offending_path": "withheld (sensitive-source invariant)",
                "fix": "a name-bearing surface cannot produce path-addressed records. "
                       "Cite it through an id_registry_file substrate in the profile and "
                       "resolve at read time instead.",
            }, indent=2))
            return 1

        records_path = root / "01_sources" / name / "normalized" / "records.jsonl"
        existing = C.read_jsonl(records_path)
        carry_forward_captured_at(candidates, existing)
        merged, stats = C.merge_records(existing, candidates)

        for rec in merged:
            if not rec.get("gist"):
                gist_queue.append({
                    "record_id": rec["record_id"],
                    "title": rec["title"],
                    "evidence_path": rec["evidence_paths"][0] if rec["evidence_paths"] else "",
                    "lead_sentence_SEED_NOT_A_GIST": leads.get(rec["record_id"], ""),
                })

        per_bucket[name] = {
            "discovered": len(candidates),
            "records": len(merged),
            "added": stats["added"], "updated": stats["updated"],
            "unchanged": stats["unchanged"], "preserved_human": stats["preserved_human"],
            "dropped_source_missing": stats["removed"],
        }
        if stats["removed"]:
            notices.append({"code": "source_missing", "bucket": name,
                            "count": stats["removed"],
                            "reason": "records whose source file no longer exists are not "
                                      "carried forward by merge_records"})
        pending_writes.append((records_path, merged))

    # Zero discovery is never a green run. The linter already refuses to pass on an empty
    # page set; the ingest path is where a config typo actually happens, and an exit-0
    # "wrote 0 records" reads as "this corpus is empty" when it means "your include
    # globs matched nothing".
    if sum(v["discovered"] for v in per_bucket.values()) == 0:
        print(json.dumps({
            "run_id": run_id, "root": str(root), "buckets": per_bucket,
            "error": "no source files discovered in any bucket -- nothing was written. "
                     "Check each bucket's `root` and `include` globs in "
                     "90_system/config/corpus.config.json.",
        }, indent=2, ensure_ascii=False))
        return 2

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "root": str(root),
        "buckets": per_bucket,
        "records_total": sum(v["records"] for v in per_bucket.values()),
        "added": sum(v["added"] for v in per_bucket.values()),
        "updated": sum(v["updated"] for v in per_bucket.values()),
        "unchanged": sum(v["unchanged"] for v in per_bucket.values()),
        "preserved_human": sum(v["preserved_human"] for v in per_bucket.values()),
        "gist_queue": len(gist_queue),
        "notices": notices,
        "wrote": False,
    }

    if args.dry_run:
        summary["dry_run"] = True
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    if gist_queue:
        queue_path = root / "90_system" / "state" / ("%s_gists_needed.json" % run_id)
        C.dump_json(queue_path, {
            "run_id": run_id,
            "root": str(root),
            "count": len(gist_queue),
            "instruction": (
                "This script does not invent gists. For each entry below, read the source "
                "at evidence_path and write ONE sentence saying what it is -- present tense, "
                "no first person, no 'This document...'. lead_sentence_SEED_NOT_A_GIST is the "
                "source's opening line, offered only to save a read; it is not a gist and "
                "must not be copied as one. Write the answers to "
                "90_system/state/%s_gists.json as {record_id: gist}, then re-run "
                "build_records.py with --gists pointing at that file." % run_id),
            "needed": gist_queue,
        })
        summary["gist_queue_path"] = str(queue_path)
        summary["error"] = ("%d record(s) have no source-declared summary. Nothing was "
                            "written. Fill the queue and re-run with --gists."
                            % len(gist_queue))
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1

    written: List[Path] = []
    for records_path, merged in pending_writes:
        C.write_jsonl(records_path, merged)
        written.append(records_path)

    leaks = C.scan_for_sensitive(sens, written)
    if leaks:
        summary["error"] = "sensitive-source invariant violation: %s" % [
            [str(p), C.redact(seg)] for p, seg in leaks]
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1

    summary["wrote"] = True
    summary["written"] = [str(p) for p in written]
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
