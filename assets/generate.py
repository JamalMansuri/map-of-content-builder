#!/usr/bin/env python3
"""Generate the three README SVGs with enforced layout arithmetic.

The previous pass estimated monospace advance at 0.60*font-size and overflowed on
all three images. Measured from a real render, the fallback mono face here advances
at ~0.66. We design at 0.63 and ASSERT at 0.68, so nothing ships that can clip.
"""
from __future__ import annotations
import pathlib, sys

W = 1000
MARGIN = 32
DESIGN_ADV = 0.63
ASSERT_ADV = 0.68          # worst-case face; everything must still fit at this

BG      = "#0b0f14"
PANEL   = "#11161d"
STROKE  = "#232c38"
FG      = "#e6edf3"
MUTED   = "#8b949e"
DIM     = "#6e7681"
RED     = "#ff7b72"
GREEN   = "#56d364"
REDBG   = "#1a1113"
GREENBG = "#0e1a12"
MONO    = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

problems: list[str] = []


def fits(label: str, x: float, text: str, fs: float, limit: float) -> None:
    """Assert a monospace run starting at x stays left of `limit` at worst case."""
    end = x + len(text) * ASSERT_ADV * fs
    if end > limit:
        problems.append(
            f"{label}: '{text[:38]}...' ends {end:.0f} > limit {limit:.0f} (over by {end-limit:.0f})")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def txt(x, y, s, fs=17, fill=FG, weight="normal", anchor="start", family=MONO, extra=""):
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{fs}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>')


def head(w, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img">\n'
            f'<rect x="0" y="0" width="{w}" height="{h}" rx="16" fill="{BG}"/>\n')


def check(cx, cy, col, s=1.0):
    return (f'<path d="M{cx-7*s} {cy} l{5*s} {5*s} l{9*s} {-11*s}" fill="none" stroke="{col}" '
            f'stroke-width="{2.6*s}" stroke-linecap="round" stroke-linejoin="round"/>')


def cross(cx, cy, col, s=1.0):
    return (f'<path d="M{cx-6*s} {cy-6*s} l{12*s} {12*s} M{cx+6*s} {cy-6*s} l{-12*s} {12*s}" '
            f'fill="none" stroke="{col}" stroke-width="{2.6*s}" stroke-linecap="round"/>')


# ---------------------------------------------------------------- logo
def logo() -> str:
    H = 200
    o = [head(W, H)]
    o.append(f'<rect x="{MARGIN+24}" y="52" width="4" height="96" rx="2" fill="{GREEN}"/>')
    name, name_fs, name_x = "map-of-content-builder", 36, MARGIN + 44
    fits("logo/name", name_x, name, name_fs, 668)
    o.append(txt(name_x, 108, name, name_fs, FG, "600"))
    tag, tag_fs = "verify every quote your AI cited", 16
    fits("logo/tagline", name_x, tag, tag_fs, 668)
    o.append(txt(name_x + 2, 140, tag, tag_fs, MUTED))

    # right-hand verdict pair, right-aligned inside the margin
    right = W - MARGIN - 24
    o.append(f'<line x1="700" y1="56" x2="700" y2="144" stroke="{STROKE}" stroke-width="1"/>')
    for i, (mark, label, col) in enumerate(
            [(check, "verbatim", GREEN), (cross, "paraphrase", RED)]):
        y = 86 + i * 34
        o.append(mark(738, y - 5, col))
        lab_fs = 16
        fits(f"logo/verdict{i}", 758, label, lab_fs, right)
        o.append(txt(758, y, label, lab_fs, col))
    o.append("</svg>\n")
    return "".join(o)


# ---------------------------------------------------------------- catch
def catch() -> str:
    H = 470
    PW = 452
    LX, RX = MARGIN, MARGIN + PW + 32          # 32 and 516
    assert RX + PW + MARGIN == W, RX + PW + MARGIN
    INSET = 24
    BODY_FS = 16
    o = [head(W, H)]

    o.append(txt(MARGIN + 8, 44, "the note put this in quotation marks.", 16, MUTED))
    o.append(txt(MARGIN + 8 + 37 * 0.63 * 16 + 10, 44, "the file it cites says something else.", 16, FG, "600"))

    note = ['"the dead-letter threshold of', 'five attempts was too generous',
            'for an outage shaped like this one"']
    src = ['The dead-letter threshold of', 'five attempts was too high',
           'for an outage of this shape.']
    # word-level divergence: index of the coloured run in each line
    note_hi = {1: "generous", 2: "shaped like this one"}
    src_hi = {1: "high", 2: "of this shape."}

    for side, (px, lines, hi, col, bgc, mark, title, foot) in enumerate([
        (LX, note, note_hi, RED, REDBG, cross, "the note says",
         "hold-window-inherits-incident-lag.md:55"),
        (RX, src, src_hi, GREEN, GREENBG, check, "the source says",
         "incident-checkout-timeouts.md"),
    ]):
        tx = px + INSET
        lim = px + PW - INSET
        o.append(f'<rect x="{px}" y="70" width="{PW}" height="270" rx="12" fill="{bgc}" '
                 f'stroke="{col}" stroke-opacity="0.35" stroke-width="1"/>')
        o.append(mark(tx + 8, 102, col))
        fits(f"catch/title{side}", tx + 26, title, 16, lim)
        o.append(txt(tx + 26, 108, title, 16, col))
        o.append(f'<line x1="{tx}" y1="126" x2="{lim}" y2="126" stroke="{col}" stroke-opacity="0.2"/>')
        for i, line in enumerate(lines):
            y = 168 + i * 34
            fits(f"catch/body{side}.{i}", tx, line, BODY_FS, lim)
            word = hi.get(i)
            if word and word in line:
                pre, post = line.split(word, 1)
                o.append(
                    f'<text x="{tx}" y="{y}" font-family="{MONO}" font-size="{BODY_FS}" '
                    f'fill="{MUTED}" xml:space="preserve">{esc(pre)}'
                    f'<tspan fill="{col}" font-weight="600">{esc(word)}</tspan>{esc(post)}</text>')
            else:
                o.append(txt(tx, y, line, BODY_FS, MUTED))
        fits(f"catch/foot{side}", tx, foot, 12, lim)
        o.append(txt(tx, 316, foot, 12, DIM))

    o.append(txt(MARGIN + 8, 366, "grey = identical in both  ·  colour = where they diverge", 13, DIM))

    o.append(f'<rect x="{MARGIN}" y="384" width="{W-2*MARGIN}" height="62" rx="10" '
             f'fill="{PANEL}" stroke="{STROKE}"/>')
    o.append(txt(MARGIN + 22, 412, "TRACE_ABSENT", 16, FG, "700"))
    o.append(txt(MARGIN + 22 + 13 * 0.63 * 16 + 12, 412, "error", 16, RED, "600"))
    o.append(txt(MARGIN + 22 + 13 * 0.63 * 16 + 12 + 6 * 0.63 * 16 + 18, 412,
                 "hold-window-inherits-incident-lag.md:55", 15, MUTED))
    o.append(txt(MARGIN + 22, 434, "traces 4 · verified 3 · errors 1 · warnings 1 · --gate exits 1", 13, DIM))
    o.append("</svg>\n")
    return "".join(o)


# ---------------------------------------------------------------- flow
def flow() -> str:
    H = 190
    o = [head(W, H)]
    labels = ["your documents", "records.jsonl", "spine", "note with quotes"]
    fs = 15
    pad = 22
    widths = [len(l) * 0.63 * fs + 2 * pad for l in labels]
    gap = 30
    total = sum(widths) + gap * len(labels)          # trailing gap before chips
    x = MARGIN + 8
    cy = 74
    for i, (lab, bw) in enumerate(zip(labels, widths)):
        o.append(f'<rect x="{x:.0f}" y="{cy-26}" width="{bw:.0f}" height="52" rx="10" '
                 f'fill="{PANEL}" stroke="{STROKE}"/>')
        fits(f"flow/box{i}", x + pad, lab, fs, x + bw - 6)
        o.append(txt(x + bw / 2, cy + 6, lab, fs, FG, anchor="middle"))
        ax0, ax1 = x + bw + 7, x + bw + gap - 7
        o.append(f'<line x1="{ax0:.0f}" y1="{cy}" x2="{ax1:.0f}" y2="{cy}" stroke="{DIM}" stroke-width="1.6"/>')
        o.append(f'<path d="M{ax1:.0f} {cy} l-7 -4.5 l0 9 z" fill="{DIM}"/>')
        x += bw + gap

    # the two outcomes, right-aligned block
    chip_x = x + 6
    for i, (mark, lab, col) in enumerate([(check, "verified", GREEN), (cross, "not in source", RED)]):
        y = cy - 16 + i * 32
        fits(f"flow/chip{i}", chip_x + 22, lab, 14, W - MARGIN)
        o.append(mark(chip_x + 8, y - 4, col, 0.85))
        o.append(txt(chip_x + 24, y, lab, 14, col))

    o.append(txt(MARGIN + 8, 152, "nothing here calls a model  ·  exact string containment, on disk", 13, DIM))
    o.append("</svg>\n")
    return "".join(o)


out = pathlib.Path(__file__).resolve().parent
out.mkdir(parents=True, exist_ok=True)
for name, fn in (("logo", logo), ("catch", catch), ("flow", flow)):
    svg = fn()
    (out / f"{name}.svg").write_text(svg)

if problems:
    print("LAYOUT PROBLEMS:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("all three generated; every text run fits at worst-case advance %.2f" % ASSERT_ADV)
