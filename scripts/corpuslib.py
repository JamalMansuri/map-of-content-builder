"""corpuslib — the one shared module for this toolkit.

Every script here imports this module, and nothing else re-implements what lives in
it: one frontmatter parser, one normalizer, one trace verifier. A codebase that grows
a second parser for the same schema drifts apart silently — the two copies disagree
long before anyone notices. This module exists so that second copy never appears.

Deterministic only. No model calls, no network. Any model-side work (gists, clustering,
relationship prose) happens outside these scripts and reaches them as files on disk.

Python 3.9+ (the `from __future__` import is load-bearing: the system interpreter on
many machines is still 3.9.x, while `python3` may resolve to something much newer).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "corpuslib requires PyYAML (the only non-stdlib dependency). "
        "If this fires, the interpreter changed: pip install pyyaml"
    ) from exc

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

SCHEMA_VERSION = "1"

#: The 12 minimum fields of a normalized record, as fixed by the folder contract this
#: toolkit implements. Order is the serialization order. Do not reorder and do not
#: "improve" the names -- this is a human-facing contract, not an internal struct.
RECORD_FIELDS: Tuple[str, ...] = (
    "record_id",
    "source_type",
    "title",
    "gist",
    "tags",
    "status",
    "retrieval_text",
    "source_updated_at",
    "captured_at",
    "content_hash",
    "evidence_paths",
    "operator_note",
)

#: Fields a machine must never author or overwrite. `operator_note` is the human's
#: own take on a source: the judgement that refuses to automate.
HUMAN_ONLY_FIELDS: frozenset = frozenset({"operator_note"})

#: Locator kinds. T=timestamp, H=heading slug, K=key, P=paragraph index, L=line.
LOCATOR_KINDS: frozenset = frozenset({"", "T", "H", "K", "P", "L"})

TRACE_VERIFIED = "VERIFIED"
TRACE_UNVERIFIED = "UNVERIFIED"
TRACE_UNRESOLVED = "UNRESOLVED"
TRACE_MISSING_SOURCE = "MISSING_SOURCE"
TRACE_MALFORMED = "MALFORMED"

SEV_ERROR = "error"
SEV_WARNING = "warning"
SEV_ADVISORY = "advisory"


class CorpusError(Exception):
    """Any condition that must stop a script rather than degrade silently."""


class SensitivePathError(CorpusError):
    """Raised when a name-bearing path would be serialized into an artifact.

    The sensitive-source invariant: some corpora have source FILENAMES that carry
    personal names (an interview recording named after the interviewee, a document
    named after its author). Those filenames must never be written into a generated
    artifact. Serialize the corpus's opaque id instead and resolve it to a path at
    read time. A profile marks such a directory with `path_is_sensitive: true`.
    """


# --------------------------------------------------------------------------
# frontmatter & text
# --------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split YAML frontmatter from body. Returns ({}, text) when absent.

    A real YAML parse, not a line scanner: a line scanner disagrees with whatever
    wrote the file the moment a value spans two lines or is quoted.
    Note yaml.safe_load returns Python True for `ai-generated: true` -- compare
    against the parsed value, never against the string "true".
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(fm, dict):
        return {}, text[m.end():]
    return fm, text[m.end():]


def read_note(path: Path) -> Tuple[Dict[str, Any], str]:
    return parse_frontmatter(read_text(path))


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def normalize(s: str) -> str:
    """The ONE normalizer used on both sides of every trace comparison.

    Measured policy, not taste: on a hand-checked reference page, case-folding plus
    whitespace collapse lifted trace verification from 9/15 to 10/15; stripping
    punctuation beyond this recovered nothing further and only widened the
    false-positive window. The apostrophe is deliberately KEPT in the allowed class.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-"))
    s = s.casefold()  # not .lower(): handles the Turkish dotted-I and eszett
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s) or "untitled"


def first_sentence(body: str, limit: int = 240) -> str:
    """Best-effort lead sentence. Never used to fabricate a gist -- only to seed
    the queue of gists a human (or a model) must still write."""
    text = re.sub(r"^#.*$", "", body, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"^(.{20,%d}?[.!?])(\s|$)" % limit, text)
    return (m.group(1) if m else text[:limit]).strip()


# --------------------------------------------------------------------------
# hashing & ids
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash raw bytes, not normalized text: this detects any change to the source,
    which is what drift detection needs."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash(path: Path) -> str:
    return "sha256:" + sha256_file(path)


def short_hash(h: str) -> str:
    """The only converter between the record's full hash and a page's fingerprint."""
    return h.split(":", 1)[-1][:8]


def make_record_id(bucket: str, native_id: str) -> str:
    """`<bucket>:<native_id>`. The bucket prefix is load-bearing for resolution."""
    if "|" in bucket or "|" in native_id:
        raise CorpusError("record_id may not contain '|' (reserved as trace delimiter)")
    return "{}:{}".format(bucket, native_id)


def split_record_id(record_id: str) -> Tuple[str, str]:
    bucket, _, native = record_id.partition(":")
    if not native:
        raise CorpusError("malformed record_id (expected <bucket>:<native_id>): %r" % record_id)
    return bucket, native


# --------------------------------------------------------------------------
# records & indexes
# --------------------------------------------------------------------------

def blank_record() -> Dict[str, Any]:
    return {
        "record_id": "", "source_type": "", "title": "", "gist": "",
        "tags": [], "status": "draft", "retrieval_text": "",
        "source_updated_at": "", "captured_at": "", "content_hash": "",
        "evidence_paths": [], "operator_note": "",
    }


def validate_record(rec: Dict[str, Any]) -> List[str]:
    """Return a list of problems. Empty list means valid."""
    problems: List[str] = []
    for f in RECORD_FIELDS:
        if f not in rec:
            problems.append("missing field: %s" % f)
    for f in ("tags", "evidence_paths"):
        if f in rec and not isinstance(rec[f], list):
            problems.append("%s must be a list" % f)
    for f in ("record_id", "source_type", "title", "status"):
        if rec.get(f) in (None, ""):
            problems.append("%s must be non-empty" % f)
    if rec.get("record_id"):
        try:
            split_record_id(rec["record_id"])
        except CorpusError as e:
            problems.append(str(e))
    extra = set(rec) - set(RECORD_FIELDS)
    if extra:
        problems.append("unknown field(s): %s" % ", ".join(sorted(extra)))
    return problems


def order_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize in the canonical field order so diffs stay readable."""
    return {f: rec.get(f, blank_record()[f]) for f in RECORD_FIELDS}


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise CorpusError("%s line %d is not valid JSON: %s" % (p, n, e))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    """Sorted by record_id so a rebuild produces a stable, diffable file."""
    rows = sorted(rows, key=lambda r: r.get("record_id", ""))
    payload = "".join(
        json.dumps(order_record(r), ensure_ascii=False, sort_keys=False) + "\n" for r in rows
    )
    atomic_write(path, payload)
    return len(rows)


def merge_records(
    existing: Sequence[Dict[str, Any]], incoming: Sequence[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Merge a rebuild into what is already on disk WITHOUT destroying human edits.

    Human-only fields (operator_note) always survive from the existing row. This is
    the asymmetric-write principle at the record layer: the system may refresh what
    it derived, never what a human wrote.
    """
    by_id = {r["record_id"]: dict(r) for r in existing if r.get("record_id")}
    stats = {"added": 0, "updated": 0, "unchanged": 0, "preserved_human": 0, "removed": 0}
    out: Dict[str, Dict[str, Any]] = {}

    for new in incoming:
        rid = new.get("record_id")
        if not rid:
            continue
        old = by_id.get(rid)
        if old is None:
            out[rid] = dict(new)
            stats["added"] += 1
            continue
        merged = dict(new)
        for f in HUMAN_ONLY_FIELDS:
            if str(old.get(f, "")).strip():
                merged[f] = old[f]
                stats["preserved_human"] += 1
        # A human may also have moved `status`; never silently revert it. Machines only
        # ever write "draft", so ANY other on-disk value is a human edit -- not just
        # "reviewed". Hardcoding one value silently demoted every other legal state
        # (e.g. "stable") back to draft on the next rebuild, losing a human edit in the
        # exact field pair the asymmetric-write principle is about.
        if merged.get("status") == "draft" and old.get("status") not in ("", None, "draft"):
            merged["status"] = old["status"]
            stats["preserved_human"] += 1
        out[rid] = merged
        if order_record(merged) == order_record(old):
            stats["unchanged"] += 1
        else:
            stats["updated"] += 1

    stats["removed"] = len([r for r in by_id if r not in out])
    return list(out.values()), stats


# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------

@dataclass
class Profile:
    """What 'valid' means for one corpus. The audit contract, and the ONLY place
    a tier list, numeric threshold or token bound may live."""

    name: str
    raw: Dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None

    # --- discovery
    @property
    def discovery(self) -> Dict[str, Any]:
        return self.raw.get("discovery", {}) or {}

    # --- frontmatter rules
    @property
    def types(self) -> Dict[str, Any]:
        return self.raw.get("types", {}) or {}

    # --- trace rules
    @property
    def trace(self) -> Dict[str, Any]:
        return self.raw.get("trace", {}) or {}

    @property
    def tiers(self) -> List[str]:
        # Only `exact` and `normalized` may ever return VERIFIED. Anything else is a
        # hint printed alongside an UNVERIFIED finding, never a pass.
        #
        # No default: a profile that does not declare its tiers is rejected rather
        # than silently given one here. A fallback in the reader is a second place
        # the rule lives, which is how a config drifts away from its schema.
        tiers = self.trace.get("tiers")
        if not tiers:
            raise CorpusError(
                "profile %r declares no trace.tiers -- verification rules live in the "
                "profile, never in code" % self.name)
        return list(tiers)

    @property
    def snippet_tokens(self) -> Optional[Tuple[int, int]]:
        v = self.trace.get("snippet_tokens")
        return (int(v[0]), int(v[1])) if v else None

    @property
    def locator_kind_default(self) -> str:
        return self.trace.get("locator_kind_default", "")

    @property
    def substrate(self) -> Dict[str, Any]:
        return self.raw.get("substrate", {}) or {}

    @property
    def checks(self) -> Dict[str, Any]:
        return self.raw.get("checks", {}) or {}

    def check_enabled(self, cid: str) -> bool:
        c = self.checks.get(cid)
        return True if c is None else bool(c.get("enabled", True))

    def check_severity(self, cid: str, default: str = SEV_WARNING) -> str:
        c = self.checks.get(cid) or {}
        return c.get("severity", default)

    @property
    def sensitive_roots(self) -> List[str]:
        out: List[str] = []
        for root in (self.substrate.get("roots") or []):
            if root.get("path_is_sensitive"):
                out.append(root.get("path", ""))
        return [r for r in out if r]


def load_profile(path: Path) -> Profile:
    p = Path(path)
    if not p.exists():
        raise CorpusError("profile not found: %s" % p)
    raw = yaml.safe_load(read_text(p)) or {}
    if not isinstance(raw, dict):
        raise CorpusError("profile must be a YAML mapping: %s" % p)
    return Profile(name=raw.get("name", p.stem), raw=raw, path=p)


def default_profile() -> Profile:
    """The profile used when a caller supplies none: the shipped strict profile.

    Deliberately loads from YAML rather than returning a literal. No tier list,
    threshold or token bound may appear in a .py file -- they live only in
    `profiles/*.yaml`, and this function existing as a literal was the last
    violation of that rule.
    """
    p = Path(__file__).resolve().parent.parent / "profiles" / "default.profile.yaml"
    if not p.exists():
        raise CorpusError(
            "default profile not found at %s -- pass --profile explicitly" % p)
    return load_profile(p)


# --------------------------------------------------------------------------
# citations, evidence, traces
# --------------------------------------------------------------------------

@dataclass
class Trace:
    source_id: str
    ordinal: str
    locator: str
    raw: str
    line_no: int


@dataclass
class TraceResult:
    trace: Trace
    status: str
    tier: Optional[str] = None
    at_locator: Optional[bool] = None
    drift: bool = False
    occurrences: int = 0
    hint: Optional[str] = None
    detail: str = ""


DEFAULT_TRACE_RE = (
    r'<!--\s*trace:\s*(?P<source_id>[^|]+?)\s*\|\s*(?P<ordinal>[^|]*?)\s*\|\s*'
    r'(?P<locator>[^|]*?)\s*\|\s*raw:\s*"(?P<raw>(?:[^"\\]|\\.)*)"\s*-->'
)


def parse_traces(text: str, profile: Optional[Profile] = None) -> List[Trace]:
    pattern = (profile.trace.get("comment_re") if profile else None) or DEFAULT_TRACE_RE
    out: List[Trace] = []
    for m in re.finditer(pattern, text):
        g = m.groupdict()
        out.append(Trace(
            source_id=(g.get("source_id") or "").strip(),
            ordinal=(g.get("ordinal") or "").strip(),
            locator=(g.get("locator") or "").strip(),
            raw=(g.get("raw") or "").replace('\\"', '"'),
            line_no=text.count("\n", 0, m.start()) + 1,
        ))
    return out


def parse_locator(s: str, default_kind: str = "") -> Tuple[str, str]:
    """'T:00:26:05' -> ('T','00:26:05'). A bare value takes `default_kind`,
    which is how a legacy corpus's bare timestamps parse."""
    s = (s or "").strip()
    if not s:
        return "", ""
    kind, sep, val = s.partition(":")
    if sep and kind in LOCATOR_KINDS and kind != "":
        return kind, val.strip()
    if default_kind and default_kind in LOCATOR_KINDS:
        return default_kind, s
    raise CorpusError("unknown locator kind in %r (expected one of %s)"
                      % (s, sorted(k for k in LOCATOR_KINDS if k)))


def load_substrate(profile: Profile, source_id: str,
                   index: Optional[Dict[str, Dict[str, Any]]] = None,
                   base: Optional[Path] = None) -> Tuple[Optional[Path], str]:
    """Resolve a citation token to (path, text).

    Two substrate kinds:
      record_field    -- the id is a record_id; read evidence_paths[0] from the index.
      id_registry_file-- the id is an opaque token (e.g. `src_21`) joined through a
                         registry JSON to a filename, then globbed in a text dir.
                         This is the indirection that satisfies the sensitive-source
                         invariant: a corpus whose source filenames carry personal
                         names cites the opaque token in its pages, and only this
                         function ever turns that token back into a path on disk.

    Returns (None, "") when unresolvable -- callers report UNRESOLVED.
    """
    sub = profile.substrate
    kind = sub.get("kind", "record_field")

    if kind == "record_field":
        rec = (index or {}).get(source_id)
        if not rec:
            return None, ""
        paths = rec.get("evidence_paths") or []
        if not paths:
            return None, ""
        p = Path(_expand(paths[0], profile))
        # evidence_paths may be corpus-relative. Resolving against the process cwd
        # made every trace UNRESOLVED when the lint ran from anywhere but the corpus
        # root -- a silent, total false-negative. Anchor to `base` when given.
        if not p.is_absolute() and base is not None:
            p = Path(base) / p
        return (p, read_text(p)) if p.exists() else (None, "")

    if kind == "id_registry_file":
        reg_path = Path(_expand(sub.get("path", ""), profile))
        if not reg_path.exists():
            return None, ""
        registry = json.loads(read_text(reg_path))
        key_field = sub.get("key_field", "cite_id")
        val_field = sub.get("value_field", "recording_id")
        rows = registry if isinstance(registry, list) else registry.get("records", [])
        stem = None
        for row in rows:
            if row.get(key_field) == source_id:
                stem = row.get(val_field)
                break
        if not stem:
            return None, ""
        text_dir = Path(_expand(sub.get("text_dir", ""), profile))
        pattern = sub.get("text_glob", "{stem}*").replace("{stem}", stem)
        # One token may glob to several files; concatenate all candidates so a
        # snippet present in any of them resolves. Reporting a false fabrication
        # because we searched only the first match is the worst failure here.
        matches = sorted(text_dir.glob(pattern))
        if not matches:
            return None, ""
        return matches[0], "\n".join(read_text(m) for m in matches)

    raise CorpusError("unknown substrate kind: %r" % kind)


def _expand(s: str, profile: Profile) -> str:
    return os.path.expanduser(str(s).replace("{profile_dir}", str(profile.path.parent) if profile.path else "."))


def locator_window(text: str, kind: str, value: str, profile: Profile) -> Optional[str]:
    """The slice of the source the locator points at, or None if not findable."""
    if kind == "T":
        marker = "[%s]" % value
        i = text.find(marker)
        if i < 0:
            return None
        anchor = profile.trace.get("block_anchor_re", r"\n\[\d{1,2}:\d{2}:\d{2}\]")
        m = re.search(anchor, text[i + len(marker):])
        return text[i: i + len(marker) + (m.start() if m else 1200)]
    if kind == "H":
        for m in re.finditer(r"^(#{1,6})\s+(.*)$", text, re.MULTILINE):
            if slugify(m.group(2)) == value.strip().lower():
                nxt = re.search(r"^#{1,6}\s+", text[m.end():], re.MULTILINE)
                return text[m.start(): m.end() + (nxt.start() if nxt else len(text))]
        return None
    if kind == "P":
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        try:
            return paras[int(value)]
        except (ValueError, IndexError):
            return None
    if kind == "L":
        lines = text.splitlines()
        try:
            n = int(value)
            return "\n".join(lines[max(0, n - 1): n + 2])
        except (ValueError, IndexError):
            return None
    return None


def verify_trace(profile: Profile, trace: Trace,
                 index: Optional[Dict[str, Dict[str, Any]]] = None,
                 declared_hash: Optional[str] = None,
                 base: Optional[Path] = None) -> TraceResult:
    """The single verification implementation. Both the write-time gate and the
    audit call this; nothing else re-implements it.

    Two-tier result, and the tiers carry different meanings:
      * snippet absent from the cited source      -> UNVERIFIED  (error-shaped: fabricated
                                                     or mis-attributed quote)
      * snippet present but not at the locator    -> VERIFIED + at_locator=False
                                                     (warning-shaped: citation imprecision)
    Collapsing these makes the report unusable -- on the reference page it would turn
    5 real problems into 8 and train the reader to ignore all of them.
    """
    path, text = load_substrate(profile, trace.source_id, index, base=base)
    if path is None:
        return TraceResult(trace, TRACE_UNRESOLVED,
                           detail="cannot resolve %r to a file on disk" % trace.source_id)

    drift = False
    if declared_hash:
        observed = short_hash(sha256_file(path))
        drift = observed != short_hash(declared_hash)

    bounds = profile.snippet_tokens
    if bounds:
        if "..." in trace.raw or "…" in trace.raw:
            return TraceResult(trace, TRACE_MALFORMED, drift=drift,
                               detail="snippet contains an ellipsis; quote one contiguous span")
        ntok = len(normalize(trace.raw).split())
        if not (bounds[0] <= ntok <= bounds[1]):
            return TraceResult(trace, TRACE_MALFORMED, drift=drift,
                               detail="snippet is %d normalized tokens; profile requires %d-%d"
                                      % (ntok, bounds[0], bounds[1]))

    kind, value = ("", "")
    if trace.locator:
        try:
            kind, value = parse_locator(trace.locator, profile.locator_kind_default)
        except CorpusError as e:
            return TraceResult(trace, TRACE_MALFORMED, drift=drift, detail=str(e))

    window = locator_window(text, kind, value, profile) if kind else None
    n_text, n_raw = normalize(text), normalize(trace.raw)

    for tier in profile.tiers:
        if tier == "exact":
            hit, hay, needle = trace.raw in text, text, trace.raw
        elif tier == "normalized":
            hit, hay, needle = n_raw in n_text, n_text, n_raw
        else:
            # Only exact/normalized may return VERIFIED. Any other tier name in a
            # profile is ignored here and used solely to build a hint below.
            continue
        if hit:
            at_loc: Optional[bool] = None
            if window is not None:
                at_loc = (needle in (window if tier == "exact" else normalize(window)))
            elif kind:
                at_loc = False
            return TraceResult(trace, TRACE_VERIFIED, tier=tier, at_locator=at_loc,
                               drift=drift, occurrences=hay.count(needle))

    return TraceResult(trace, TRACE_UNVERIFIED, drift=drift, hint=_near_miss(n_raw, n_text),
                       detail="snippet does not occur in %s" % path.name)


def _near_miss(n_raw: str, n_text: str) -> Optional[str]:
    """A locating aid for a human reading the report. Never changes a verdict."""
    words = n_raw.split()
    for k in (8, 5, 3):
        if len(words) >= k:
            probe = " ".join(words[:k])
            i = n_text.find(probe)
            if i >= 0:
                return "first %d words occur at offset %d (%d× in source) -- likely a paraphrase" % (
                    k, i, n_text.count(probe))
    return None


# --------------------------------------------------------------------------
# safety: no name-bearing paths in written artifacts (sensitive-source invariant)
# --------------------------------------------------------------------------

def assert_not_sensitive(profile: Profile, value: str) -> None:
    """Refuse to serialize a path derived from a root marked path_is_sensitive.

    Raising here rather than degrading is deliberate: a silent fallback would write
    a real person's name -- carried by the source FILENAME -- into a published page.
    """
    for root in profile.sensitive_roots:
        # Separator-agnostic: this must hold on Windows too, where the same corpus is
        # addressed with backslashes. Comparing against a hardcoded "/" silently let a
        # sensitive path through on any Windows host.
        r = os.path.expanduser(root).rstrip("/\\")
        norm_val = value.replace("\\", "/")
        norm_r = r.replace("\\", "/")
        if r and (norm_val.startswith(norm_r) or ("/" + Path(r).name + "/") in norm_val):
            raise SensitivePathError(
                "refusing to serialize a path under a sensitive root (%s): %r. "
                "Serialize the corpus's opaque id instead and resolve at read time."
                % (root, value))


def sensitive_stems(profile: Profile) -> List[str]:
    """Filename stems living under a sensitive root.

    These are the actual hazard: a name-bearing FILENAME copied into a page. Held in
    memory, never serialized. Callers must redact before reporting -- a leak check
    that prints the leaked name is itself a leak.
    """
    stems: List[str] = []
    for root in profile.sensitive_roots:
        d = Path(os.path.expanduser(root))
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            stem = f.name
            for _ in range(3):  # strip stacked suffixes: a.diarized.safe.txt -> a
                stem = Path(stem).stem
            if len(stem) >= 4:
                stems.append(stem)
    return sorted(set(stems))


def scan_for_sensitive(profile: Profile, paths: Iterable[Path]) -> List[Tuple[Path, str]]:
    """Post-run sweep for Invariant #0. Returns (file, offending_token); empty is clean.

    Matches whole path COMPONENTS and known filename stems -- never a bare substring.
    A bare substring scan of the root's directory name scored 100% false positives on
    a real corpus whose sensitive root is named `safe`, because "safe" and "safety" are
    ordinary English words. Worse, matching the directory name cannot detect the actual
    hazard (a name-bearing filename in the prose), so it was both noisy and blind.
    """
    comps = [Path(os.path.expanduser(r)).name for r in profile.sensitive_roots]
    stems = sensitive_stems(profile)
    hits: List[Tuple[Path, str]] = []
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        try:
            body = read_text(p)
        except OSError:
            continue
        for stem in stems:
            if stem in body:
                hits.append((p, stem))
        for c in comps:
            # whole path component only: "/safe/" or "\safe\", not the word "safety"
            if c and re.search(r"[/\\]%s[/\\]" % re.escape(c), body):
                hits.append((p, "%s%s%s" % (os.sep, c, os.sep)))
    return hits


def redact(token: str) -> str:
    """Render a sensitive token safe for a report. Never print the token itself."""
    return "<redacted:%d chars>" % len(token)


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def ensure_dir(path: Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write(path: Path, text: str) -> Path:
    """Write via a temp file in the same directory, then replace. A half-written
    index is worse than no index."""
    p = Path(path)
    ensure_dir(p.parent)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".{}.".format(p.name), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def write_if_absent(path: Path, text: str) -> bool:
    """For human-owned files. Returns True if written, False if it already existed.
    Never overwrites: the system does not get to revise what a human wrote."""
    p = Path(path)
    if p.exists():
        return False
    atomic_write(p, text)
    return True


def iter_corpus_files(root: Path, profile: Profile) -> Iterator[Path]:
    """Discovery honouring the profile's include/exclude rules."""
    d = profile.discovery
    includes = d.get("include") or ["**/*.md"]
    ex_dirs = set(d.get("exclude_dirs") or [])
    ex_prefix = tuple(d.get("exclude_prefix") or [])
    ex_glob = d.get("exclude_glob") or []
    root = Path(root)
    seen = set()
    for pat in includes:
        for p in sorted(root.glob(pat)):
            if not p.is_file() or p in seen:
                continue
            rel = p.relative_to(root)
            if any(part in ex_dirs or part.startswith(ex_prefix) for part in rel.parts):
                continue
            if any(rel.match(g) for g in ex_glob):
                continue
            seen.add(p)
            yield p


def load_index(spine_path: Path) -> Dict[str, Dict[str, Any]]:
    return {r["record_id"]: r for r in read_jsonl(spine_path) if r.get("record_id")}


def dump_json(path: Path, obj: Any) -> Path:
    return atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
