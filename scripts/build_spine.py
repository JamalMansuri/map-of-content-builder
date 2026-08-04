#!/usr/bin/env python3
"""build_spine.py -- union every bucket's records into the spine, then render MAP.md.

Two artifacts, and only two:

    01_sources/_spines/project_records.jsonl   the machine view, fixed by the contract
    MAP.md                                     the human view, grouped bucket -> workstream

There is no `_index.json`, no `summary_index.jsonl`, no cluster or root index. Three
projections of a few dozen records, plus a check to keep them agreeing, is the
two-parsers-for-one-schema anti-pattern wearing a different hat.

Workstream membership comes from the `records:` list in each
`03_workstreams/<w>/current_brief.md` -- a human-owned file. Nothing here guesses which
workstream a record belongs to; unclaimed records surface under `(unassigned)`, which is
a review queue, not an error.

Every row is validated with `corpuslib.validate_record()` before anything is written. A
bad row fails the run; a half-valid spine is worse than no spine.

The sensitive-source invariant: a record whose evidence path is sensitive is rendered as
its opaque record_id alone. No such path is ever written into MAP.md.

Deterministic. No model calls, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys, pathlib
sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import corpuslib as C

SPINE_REL = "01_sources/_spines/project_records.jsonl"
UNASSIGNED = "(unassigned)"


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(str(p)))


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _title(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


def _oneline(s: str, limit: int = 400) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def vault_relative(abs_path: Path) -> Optional[str]:
    """Wiki-link target for a path inside a note vault, or None.

    Optional nicety: when the source lake happens to live inside a note-taking vault
    (any ancestor directory containing a `.obsidian/` folder), MAP.md can link to the
    source with a `[[wiki-link]]` instead of a raw path. Detecting the vault by that
    marker beats hardcoding its path, which would be wrong on every other machine.
    Returns None -- and the caller falls back to a plain path -- when there is none.
    """
    for parent in abs_path.parents:
        if (parent / ".obsidian").is_dir():
            rel = abs_path.relative_to(parent).as_posix()
            return rel[:-3] if rel.endswith(".md") else rel
    return None


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------

def load_workstream_membership(root: Path) -> Tuple[Dict[str, List[str]], List[str]]:
    """{record_id: [workstream, ...]} from the human-owned current_brief.md files."""
    membership: Dict[str, List[str]] = {}
    workstreams: List[str] = []
    ws_dir = root / "03_workstreams"
    if not ws_dir.is_dir():
        return membership, workstreams
    for d in sorted(p for p in ws_dir.iterdir() if p.is_dir()):
        workstreams.append(d.name)
        brief = d / "current_brief.md"
        if not brief.exists():
            continue
        fm, _ = C.read_note(brief)
        for rid in (fm.get("records") or []):
            membership.setdefault(str(rid), []).append(d.name)
    return membership, workstreams


def bucket_roots(cfg: Dict[str, Any]) -> Dict[str, Path]:
    return {b["name"]: _expand(b["root"]).resolve() for b in cfg.get("buckets", [])}


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def render_line(rec: Dict[str, Any], roots: Dict[str, Path], sens: C.Profile) -> str:
    """One record, one line: title, gist, and a link only when the path is safe to write."""
    title = _oneline(rec.get("title") or rec["record_id"], 160)
    gist = _oneline(rec.get("gist") or "")
    ev = (rec.get("evidence_paths") or [""])[0]
    bucket, _, native = rec["record_id"].partition(":")

    label = "`%s`" % rec["record_id"]
    if ev:
        try:
            C.assert_not_sensitive(sens, ev)
            abs_path = roots.get(bucket, Path("/")) / native
            C.assert_not_sensitive(sens, str(abs_path))
        except C.SensitivePathError:
            # Opaque id alone. Never degrade to printing the path.
            return "- %s — %s" % (label, gist) if gist else "- %s" % label
        target = vault_relative(abs_path)
        safe_title = title.replace("|", "/").replace("[", "(").replace("]", ")")
        if target:
            label = "[[%s|%s]]" % (target, safe_title)
        else:
            label = "**%s** `%s`" % (safe_title, ev)
    return "- %s — %s" % (label, gist) if gist else "- %s" % label


def render_map(cfg: Dict[str, Any], records: List[Dict[str, Any]],
               membership: Dict[str, List[str]], workstreams: List[str],
               roots: Dict[str, Path], sens: C.Profile, fingerprint: str) -> str:
    slug = cfg.get("corpus_slug", "corpus")
    name = _title(slug)
    out: List[str] = [
        "---",
        "title: %s — MAP" % name,
        "type: map",
        # A fingerprint of the spine, not a wall-clock timestamp: a regenerated file
        # that differs on every run destroys the only change signal an unversioned
        # corpus has. The filesystem mtime already answers "when was this built".
        "spine_fingerprint: %s" % fingerprint,
        "generator: map-of-content-builder/build_spine.py",
        "records: %d" % len(records),
        "ai-generated: true",
        "status: draft",
        "---",
        "",
        "# %s — MAP" % name,
        "",
        "Machine-generated index of every record in this corpus, grouped by source bucket "
        "and then by workstream. Regenerated on every `build_spine.py` run — edits made "
        "here are lost. Write in `PROJECT_HUB.md` and the workstream briefs instead.",
        "",
        "- Spine (machine view): `%s`" % SPINE_REL,
        "- Workstream membership comes from `records:` in each "
        "`03_workstreams/<w>/current_brief.md`, which is human-owned.",
        "",
    ]

    by_bucket: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_bucket.setdefault(rec["record_id"].partition(":")[0], []).append(rec)

    for bucket in sorted(by_bucket):
        rows = by_bucket[bucket]
        out.append("## %s — %d record(s)" % (bucket, len(rows)))
        out.append("")
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for rec in rows:
            for ws in (membership.get(rec["record_id"]) or [UNASSIGNED]):
                groups.setdefault(ws, []).append(rec)
        ordered = [w for w in workstreams if w in groups] + \
                  [w for w in sorted(groups) if w not in workstreams and w != UNASSIGNED]
        if UNASSIGNED in groups:
            ordered.append(UNASSIGNED)
        for ws in ordered:
            out.append("### %s — %d" % (ws, len(groups[ws])))
            out.append("")
            for rec in sorted(groups[ws], key=lambda r: r["record_id"]):
                out.append(render_line(rec, roots, sens))
            out.append("")

    empty = [w for w in workstreams if not any(w in v for v in membership.values())]
    if empty:
        out.append("## Workstreams with no records claimed")
        out.append("")
        out.append("Add record ids to `records:` in each brief to populate these.")
        out.append("")
        for w in empty:
            out.append("- `03_workstreams/%s/current_brief.md`" % w)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Union bucket records into the spine and render MAP.md.")
    ap.add_argument("--root", required=True, help="the corpus <ROOT>")
    ap.add_argument("--profile", default=None,
                    help="audit profile yaml; its sensitive roots are honoured when linking")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    root = _expand(args.root)
    cfg_path = root / "90_system" / "config" / "corpus.config.json"
    if not cfg_path.exists():
        print(json.dumps({"error": "no corpus config at %s; run scaffold_corpus.py first"
                                   % cfg_path}, indent=2))
        return 1
    cfg = json.loads(C.read_text(cfg_path))

    sens_roots: List[Dict[str, Any]] = [
        {"path": b.get("root", ""), "path_is_sensitive": True}
        for b in cfg.get("buckets", []) if isinstance(b, dict) and b.get("path_is_sensitive")
    ]
    if args.profile:
        try:
            for r in C.load_profile(Path(args.profile)).sensitive_roots:
                sens_roots.append({"path": r, "path_is_sensitive": True})
        except C.CorpusError as e:
            print(json.dumps({"error": str(e)}, indent=2))
            return 1
    sens = C.Profile(name="spine-sensitivity", raw={"substrate": {"roots": sens_roots}})

    run_id = args.run_id or _run_id()
    records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    per_bucket: Dict[str, int] = {}
    seen: Dict[str, str] = {}

    for b in cfg.get("buckets", []):
        name = b["name"]
        # A filesystem bucket's record_id IS its relative path, so a name-bearing
        # filename becomes a name-bearing id. Rendering "the record_id alone" only
        # upholds the sensitive-source invariant when the id is genuinely opaque
        # (`src_21`). If the bucket's own root is declared sensitive, records for it
        # should never have been built; fail loudly rather than launder the name
        # into MAP.md.
        try:
            C.assert_not_sensitive(sens, str(_expand(b.get("root", "")).resolve()))
        except C.SensitivePathError:
            if (root / "01_sources" / name / "normalized" / "records.jsonl").exists():
                errors.append({"bucket": name, "problem":
                               "bucket root is declared path_is_sensitive but path-addressed "
                               "records exist for it. Delete "
                               "01_sources/%s/normalized/records.jsonl and re-run "
                               "build_records.py --profile, which refuses to build them."
                               % name})
            continue
        path = root / "01_sources" / name / "normalized" / "records.jsonl"
        if not path.exists():
            errors.append({"bucket": name, "problem": "no records.jsonl at %s; "
                                                      "run build_records.py first" % path})
            continue
        try:
            rows = C.read_jsonl(path)
        except C.CorpusError as e:
            errors.append({"bucket": name, "problem": str(e)})
            continue
        per_bucket[name] = len(rows)
        for i, rec in enumerate(rows, 1):
            problems = C.validate_record(rec)
            if problems:
                errors.append({"bucket": name, "line": i,
                               "record_id": rec.get("record_id", "?"), "problems": problems})
                continue
            rid = rec["record_id"]
            if rid in seen:
                errors.append({"bucket": name, "line": i, "record_id": rid,
                               "problems": ["duplicate record_id (also in bucket %r)" % seen[rid]]})
                continue
            seen[rid] = name
            records.append(rec)

    if errors:
        print(json.dumps({
            "run_id": run_id, "root": str(root),
            "error": "%d row/bucket problem(s); the spine was NOT written" % len(errors),
            "errors": errors,
            "records_valid": len(records),
        }, indent=2, ensure_ascii=False))
        return 1

    membership, workstreams = load_workstream_membership(root)
    unknown = sorted(rid for rid in membership if rid not in seen)
    roots = bucket_roots(cfg)

    spine_path = root / SPINE_REL
    C.write_jsonl(spine_path, records)
    fingerprint = C.short_hash(C.sha256_file(spine_path))
    map_path = root / "MAP.md"
    C.atomic_write(map_path, render_map(cfg, records, membership, workstreams,
                                        roots, sens, fingerprint))

    leaks = C.scan_for_sensitive(sens, [spine_path, map_path])
    if leaks:
        print(json.dumps({
            "run_id": run_id,
            "error": "sensitive-source invariant violation in written artifacts: %s"
                     % [[str(p), C.redact(seg)] for p, seg in leaks],
        }, indent=2))
        return 1

    assigned = sum(1 for rid in seen if membership.get(rid))
    print(json.dumps({
        "run_id": run_id,
        "root": str(root),
        "spine": str(spine_path),
        "map": str(map_path),
        "spine_fingerprint": fingerprint,
        "records": len(records),
        "buckets": per_bucket,
        "workstreams": workstreams,
        "assigned": assigned,
        "unassigned": len(records) - assigned,
        "unknown_record_ids_in_briefs": unknown,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
