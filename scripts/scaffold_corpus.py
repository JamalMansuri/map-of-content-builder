#!/usr/bin/env python3
"""scaffold_corpus.py -- materialise the folder contract for one corpus.

FIXED names (this script always creates them, never asks):
    01_sources/<bucket>/{raw,normalized}/   01_sources/_spines/
    02_codebases/   03_workstreams/   05_exports/
    90_system/config/   90_system/state/
    PROJECT_HUB.md   02_codebases/README.md   03_workstreams/<w>/current_brief.md
    90_system/config/corpus.config.json

CHOSEN names (corpus slug, bucket names, workstream names) come from the corpus config
the operator supplies. This script never invents one. That split is the whole point: the
folder contract is a human contract, and the vocabulary inside it is a human choice.

Human-owned files are written with corpuslib.write_if_absent() and are never
overwritten. PROJECT_HUB.md and current_brief.md carry the substance a human has to
supply; a machine that rewrites them every run destroys exactly the part that mattered.
A later run that finds a new bucket or workstream REPORTS it instead of editing the hub.

Deterministic. No model calls, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import sys, pathlib
sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import corpuslib as C

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# corpuslib deliberately exposes no slug validators -- these are scaffold-local naming
# rules from the folder contract's "Naming Rules", not shared parsing logic.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")       # kebab-case: corpus slug
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")               # snake_case: buckets, workstreams

#: Directories the skill always creates, relative to <ROOT>. Bucket and workstream
#: subdirectories are appended from the config.
FIXED_DIRS: Tuple[str, ...] = (
    "01_sources",
    "01_sources/_spines",
    "02_codebases",
    "03_workstreams",
    "05_exports",
    "90_system",
    "90_system/config",
    "90_system/state",
)

CODEBASES_README = """\
# 02_codebases/

Empty on purpose.

Codebase ingestion is **deferred**. This toolkit ships no extractor that turns a repo or
a packaged plugin into records: the reference corpus it was built against contained no
code, so there was nothing to validate an extractor on -- and a graph extractor written
with nothing to run it on is a guess wearing a schema.

The folder exists because it is part of the folder contract this corpus was scaffolded
from, and because rule 2 ("if it is code or a plugin, put it in `02_codebases`") stays
true whether or not a script implements it. Drop repos, repo snapshots and plugin
packages here by hand; nothing automated reads them yet.
"""


class PlanItem(NamedTuple):
    rel: str
    kind: str                  # "dir" | "file"
    content: Optional[str]     # None for dirs
    human_owned: bool


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def load_config(path: Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise C.CorpusError("corpus config not found: %s" % p)
    try:
        cfg = json.loads(C.read_text(p))
    except json.JSONDecodeError as e:
        raise C.CorpusError("corpus config is not valid JSON (%s): %s" % (p, e))
    if not isinstance(cfg, dict):
        raise C.CorpusError("corpus config must be a JSON object: %s" % p)
    return cfg


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(str(p)))


def validate_config(cfg: Dict[str, Any]) -> List[str]:
    """Return a list of problems. Empty list means the config may be materialised."""
    problems: List[str] = []

    slug = cfg.get("corpus_slug", "")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        problems.append("corpus_slug must be kebab-case ([a-z0-9-]): %r" % (slug,))

    buckets = cfg.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        problems.append("buckets must be a non-empty list")
        buckets = []

    seen_bucket: set = set()
    for i, b in enumerate(buckets):
        where = "buckets[%d]" % i
        if not isinstance(b, dict):
            problems.append("%s must be an object" % where)
            continue
        name = b.get("name", "")
        if not isinstance(name, str) or not SNAKE_RE.match(name):
            problems.append("%s.name must be snake_case: %r" % (where, name))
        elif name in seen_bucket:
            problems.append("%s.name duplicated: %r" % (where, name))
        else:
            seen_bucket.add(name)
        if not b.get("source_type"):
            problems.append("%s.source_type is required (it is the record's source_type)" % where)
        root = b.get("root", "")
        if not isinstance(root, str) or not root:
            problems.append("%s.root is required (absolute path to the source surface)" % where)
            continue
        if "\\" in root:
            # Source roots whose names contain spaces invite shell-quoting (`my\ notes`).
            # A literal backslash in a path string means someone pasted a shell-escaped
            # path into JSON; it will not resolve.
            problems.append("%s.root contains a literal backslash; use a plain path: %r" % (where, root))
            continue
        rp = _expand(root)
        if not rp.is_dir():
            problems.append("%s.root is not an existing directory: %s" % (where, rp))

    workstreams = cfg.get("workstreams", [])
    if not isinstance(workstreams, list):
        problems.append("workstreams must be a list (may be empty)")
        workstreams = []
    seen_ws: set = set()
    for w in workstreams:
        if not isinstance(w, str) or not SNAKE_RE.match(w):
            problems.append("workstream names must be snake_case: %r" % (w,))
        elif w in seen_ws:
            problems.append("workstream duplicated: %r" % (w,))
        else:
            seen_ws.add(w)

    return problems


def check_containment(cfg: Dict[str, Any], root: Path) -> List[str]:
    """A source root inside <ROOT> (or vice versa) makes the corpus ingest its own
    output on the second run. Catch it here, not after it has happened."""
    problems: List[str] = []
    r = root.resolve()
    for b in cfg.get("buckets", []):
        if not isinstance(b, dict) or not b.get("root"):
            continue
        try:
            sr = _expand(b["root"]).resolve()
        except OSError:
            continue
        if sr == r or r in sr.parents or sr in r.parents:
            problems.append(
                "bucket %r root %s overlaps the corpus root %s; the corpus would ingest "
                "its own output" % (b.get("name"), sr, r))
    return problems


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------

def render(template_name: str, ctx: Dict[str, str]) -> str:
    """Substitute <<<KEY>>> markers. Keys absent from ctx are left in place -- that is
    how <<<HUMAN>>> survives into the scaffolded file, where it belongs."""
    path = TEMPLATE_DIR / template_name
    if not path.exists():
        raise C.CorpusError("template not found: %s" % path)
    text = C.read_text(path)
    for key, value in ctx.items():
        text = text.replace("<<<%s>>>" % key, value)
    return text


def _title(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

def plan_tree(cfg: Dict[str, Any], root: Path, generated_at: str,
              scripts_dir: Path) -> List[PlanItem]:
    buckets = [b["name"] for b in cfg["buckets"]]
    workstreams = list(cfg.get("workstreams", []))

    items: List[PlanItem] = [PlanItem(d, "dir", None, False) for d in FIXED_DIRS]
    for b in buckets:
        items.append(PlanItem("01_sources/%s" % b, "dir", None, False))
        items.append(PlanItem("01_sources/%s/raw" % b, "dir", None, False))
        items.append(PlanItem("01_sources/%s/normalized" % b, "dir", None, False))
    for w in workstreams:
        items.append(PlanItem("03_workstreams/%s" % w, "dir", None, False))

    source_layout = "\n".join(
        "- `01_sources/%s/`\n  %s (`source_type: %s`), rooted at `%s`."
        % (b["name"], b.get("description") or "Registered source surface",
           b["source_type"], _expand(b["root"]))
        for b in cfg["buckets"]
    ) or "_No source buckets registered yet._"

    codebase_layout = (
        "- `02_codebases/`\n"
        "  Empty. Codebase ingestion is deferred -- see `02_codebases/README.md`."
    )

    workstream_layout = "\n".join(
        "- `03_workstreams/%s/`" % w for w in workstreams
    ) or "_No workstreams registered yet._"

    hub = render("PROJECT_HUB.tpl", {
        "PROJECT_TITLE": _title(cfg["corpus_slug"]),
        "GENERATED_AT": generated_at,
        "SOURCE_LAYOUT": source_layout,
        "CODEBASE_LAYOUT": codebase_layout,
        "WORKSTREAM_LAYOUT": workstream_layout,
        "SCRIPTS_DIR": str(scripts_dir),
        "ROOT": str(root),
        "EXAMPLE_WORKSTREAM": workstreams[0] if workstreams else "your_workstream",
        "EXAMPLE_SOURCES": ", ".join(buckets),
    })
    items.append(PlanItem("PROJECT_HUB.md", "file", hub, True))
    items.append(PlanItem("02_codebases/README.md", "file", CODEBASES_README, True))

    for w in workstreams:
        brief = render("current_brief.tpl", {
            "WORKSTREAM": w,
            "WORKSTREAM_TITLE": _title(w),
            "SOURCE_BUCKETS": ", ".join(buckets),
            "GENERATED_AT": generated_at,
        })
        items.append(PlanItem("03_workstreams/%s/current_brief.md" % w, "file", brief, True))

    # The entity glossary. Entity overlap is the relationship proposer's strongest axis,
    # but nothing rendered this file at first -- so every freshly scaffolded corpus started
    # with that axis dead and no table for the human to fill in. Human-owned once it
    # exists (write_if_absent), never rewritten.
    items.append(PlanItem("90_system/_entities.md", "file",
                          render("_entities.tpl", {
                              "CORPUS_TITLE": _title(cfg["corpus_slug"]),
                              "CORPUS_SLUG": cfg["corpus_slug"],
                              "GENERATED_AT": generated_at,
                          }), True))

    items.append(PlanItem("90_system/config/corpus.config.json", "file",
                          json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", True))
    return items


def materialize(items: List[PlanItem], root: Path) -> Dict[str, List[str]]:
    created: List[str] = []
    skipped: List[str] = []
    for it in items:
        target = root / it.rel
        if it.kind == "dir":
            if target.is_dir():
                skipped.append(it.rel + "/")
            else:
                C.ensure_dir(target)
                created.append(it.rel + "/")
            continue
        if C.write_if_absent(target, it.content or ""):
            created.append(it.rel)
        else:
            skipped.append(it.rel)
    return {"created": created, "skipped_existing": skipped}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _run_id() -> str:
    # corpuslib exposes no clock helper; a run id is a scaffold/build-local concern.
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a map-of-content corpus tree.")
    ap.add_argument("--config", required=True,
                    help="corpus config JSON (corpus_slug, buckets, workstreams)")
    ap.add_argument("--output-root", required=True,
                    help="parent directory; <corpus_slug>/ is created inside it")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the tree that would be created and write nothing")
    args = ap.parse_args()

    try:
        cfg = load_config(Path(args.config))
    except C.CorpusError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 1

    problems = validate_config(cfg)
    if problems:
        print(json.dumps({"error": "invalid corpus config", "problems": problems}, indent=2))
        return 1

    out_root = _expand(args.output_root)
    root = out_root / cfg["corpus_slug"]
    problems = check_containment(cfg, root)
    if problems:
        print(json.dumps({"error": "invalid corpus config", "problems": problems}, indent=2))
        return 1

    notes: List[str] = []

    # The on-disk config is authoritative once it exists: bucket and workstream names
    # are CHOSEN once, then frozen. A later run may propose a change; it may not make
    # one. Unless the corpus is under version control, a machine rename is unrecoverable.
    frozen_path = root / "90_system" / "config" / "corpus.config.json"
    effective = cfg
    if frozen_path.exists():
        try:
            frozen = load_config(frozen_path)
        except C.CorpusError as e:
            print(json.dumps({"error": str(e)}, indent=2))
            return 1
        effective = frozen
        old_b = {b["name"] for b in frozen.get("buckets", []) if isinstance(b, dict)}
        new_b = {b["name"] for b in cfg.get("buckets", []) if isinstance(b, dict)}
        old_w = set(frozen.get("workstreams", []))
        new_w = set(cfg.get("workstreams", []))
        for label, gone, added in (("bucket", old_b - new_b, new_b - old_b),
                                   ("workstream", old_w - new_w, new_w - old_w)):
            if gone:
                notes.append("PROPOSAL ONLY (not applied): %s(s) present on disk but absent "
                             "from --config: %s. Remove them by hand if that is intended."
                             % (label, ", ".join(sorted(gone))))
            if added:
                # New names are additive and safe, so they are merged into the effective
                # config -- creating is allowed, renaming and deleting are not.
                notes.append("new %s(s) from --config will be created: %s"
                             % (label, ", ".join(sorted(added))))
                if label == "bucket":
                    known = {b["name"] for b in effective["buckets"]}
                    effective["buckets"].extend(
                        b for b in cfg["buckets"] if b["name"] not in known)
                else:
                    effective.setdefault("workstreams", []).extend(sorted(added))
                notes.append(
                    "ACTION REQUIRED: 90_system/config/corpus.config.json is human-owned and "
                    "was NOT rewritten, so it still lists the old %ss. Add the new one(s) there "
                    "by hand -- build_records.py reads that file, not this --config, and will "
                    "ignore a bucket missing from it." % label)
                notes.append("PROJECT_HUB.md is human-owned and was NOT updated; its layout "
                             "sections may now be stale. Edit it yourself if you care.")

    items = plan_tree(effective, root, _run_id(), Path(__file__).resolve().parent)

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "root": str(root),
            "tree": sorted(it.rel + ("/" if it.kind == "dir" else "") for it in items),
            "buckets": len(effective["buckets"]),
            "workstreams": len(effective.get("workstreams", [])),
            "human_owned_files": sorted(it.rel for it in items if it.human_owned),
            "notes": notes,
        }, indent=2))
        return 0

    result = materialize(items, root)
    print(json.dumps({
        "root": str(root),
        "created": sorted(result["created"]),
        "skipped_existing": sorted(result["skipped_existing"]),
        "buckets": len(effective["buckets"]),
        "workstreams": len(effective.get("workstreams", [])),
        "notes": notes,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
