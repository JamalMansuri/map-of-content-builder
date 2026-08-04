#!/usr/bin/env bash
#
# regenerate-demo.sh — rebuild the machine-owned half of examples/demo-corpus
# from its own source notes, using the real scripts. Optional: the demo in the
# repo is already the output of this, so you only need this if you edit
# examples/demo-corpus/sources/.
#
# What it rebuilds:   01_sources/design_notes/normalized/records.jsonl
#                     01_sources/_spines/project_records.jsonl
#                     MAP.md
# What it never touches: sources/, PROJECT_HUB.md, current_brief.md, and the
#                     relationship page. Those are authored, not derived.
#
# One thing here is not a plain script call: after build_records.py runs, the
# `evidence_paths` it wrote are absolute paths on THIS machine. The demo has to
# work after being copied anywhere, so they are rewritten to paths relative to
# the corpus root. The resolver anchors relative evidence paths to the corpus
# root, which is what makes the demo portable.
#
# Note: `source_updated_at` in each record comes from the file's modification
# time, so a rebuild after a fresh clone will differ from the committed records
# in that one field. Nothing the linter checks reads it.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
CORPUS="$HERE/demo-corpus"
PY="${PYTHON:-python3}"

cd "$CORPUS"

"$PY" "$REPO/scripts/build_records.py" --root .

"$PY" - <<'RELATIVIZE'
import pathlib
import sys

# cwd is the corpus root; the scripts directory is two levels up.
sys.path.insert(0, str(pathlib.Path("../../scripts").resolve()))
import corpuslib as C

root = pathlib.Path(".").resolve()
records = root / "01_sources" / "design_notes" / "normalized" / "records.jsonl"
rows = C.read_jsonl(records)
for row in rows:
    row["evidence_paths"] = [
        str(pathlib.Path(p).resolve().relative_to(root)) if pathlib.Path(p).is_absolute() else p
        for p in row["evidence_paths"]
    ]
C.write_jsonl(records, rows)
print("relativized %d evidence path(s) against the corpus root" % len(rows))
RELATIVIZE

"$PY" "$REPO/scripts/build_spine.py" --root .

echo
echo "Rebuilt. Now lint it:"
echo "  $PY $REPO/scripts/lint_corpus.py --corpus $CORPUS --profile $REPO/profiles/default.profile.yaml"
