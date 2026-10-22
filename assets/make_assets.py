"""Builds the README artwork (logo, mark, hero timeline; light + dark) as pure SVG. Every glyph is outlined from
Bricolage Grotesque and IBM Plex Mono (both SIL Open Font License) and defined once, so the images look the same
everywhere with no font dependency.

    python3 -m venv /tmp/v && /tmp/v/bin/pip install fonttools
    # fonts: github.com/google/fonts  ofl/bricolagegrotesque/*.ttf  ofl/ibmplexmono/IBMPlexMono-Regular.ttf
    FONT_DIR=/tmp/fonts /tmp/v/bin/python assets/make_assets.py     # expects bricolage.ttf and plexmono.ttf there
"""
from __future__ import annotations

import os
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONT_DIR = Path(os.environ.get("FONT_DIR", "/tmp/memaso-fonts"))
OUT = Path(__file__).parent
DEFS: dict[str, str] = {}  # glyph id -> outline (font units), reset for every svg


class Face:
    def __init__(self, tag: str, file: str, axes: dict | None = None):
        f = TTFont(FONT_DIR / file)
        if axes:
            instantiateVariableFont(f, axes, inplace=True)
        self.tag, self.gs, self.cmap, self.hmtx = tag, f.getGlyphSet(), f.getBestCmap(), f["hmtx"]
        self.upm = f["head"].unitsPerEm

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        return sum(self.hmtx[self.cmap[ord(c)]][0] * size / self.upm + tracking * size for c in text)

    def glyph(self, ch: str) -> str:
        name = self.cmap[ord(ch)]
        gid = f"{self.tag}-{name}"
        if gid not in DEFS:
            pen = SVGPathPen(self.gs, ntos=lambda v: (f"{v:.0f}"))
            self.gs[name].draw(pen)
            DEFS[gid] = pen.getCommands()
        return gid


DISPLAY = Face("d", "bricolage.ttf", {"wght": 800, "opsz": 72, "wdth": 100})   # wordmark
HEAD = Face("h", "bricolage.ttf", {"wght": 700, "opsz": 28, "wdth": 100})      # titles
BODY = Face("b", "bricolage.ttf", {"wght": 500, "opsz": 14, "wdth": 100})      # labels and text
CODE = Face("c", "plexmono.ttf")                                                # code only

THEMES = {
    "light": dict(bg="#FFFFFF", ink="#1A1630", muted="#6B6785", hair="#E4E1F0", stage="#1A1630", stage_text="#F4F2FF",
                  stage_muted="#9C97BA", teal="#0F8F83", violet="#6A4CFF", pink="#F0397A", pink_text="#F0397A",
                  grey="#B3AFC6", teal_tint="#0F8F83", violet_tint="#6A4CFF"),
    "dark": dict(bg="#0F0B1E", ink="#F4F2FF", muted="#9C97BA", hair="#2A2444", stage="#1B1533", stage_text="#F4F2FF",
                 stage_muted="#9C97BA", teal="#34D3C0", violet="#9B87FF", pink="#FF5C93", pink_text="#FF5C93",
                 grey="#5C5778", teal_tint="#34D3C0", violet_tint="#9B87FF"),
}


def txt(face, text, size, x, y, fill, tracking=0.0, anchor="start", opacity=1.0):
    """Text as <use> references to shared glyph outlines."""
    s = size / face.upm
    w = face.width(text, size, tracking)
    x0 = x - (w if anchor == "end" else w / 2 if anchor == "middle" else 0)
    parts, cursor = [], 0.0
    for ch in text:
        if ch != " ":
            parts.append(f'<use href="#{face.glyph(ch)}" x="{cursor:.0f}"/>')
        cursor += face.hmtx[face.cmap[ord(ch)]][0] + tracking * face.upm
    op = f' fill-opacity="{opacity}"' if opacity != 1 else ""
    return f'<g transform="translate({x0:.1f} {y}) scale({s:.5f} {-s:.5f})" fill="{fill}"{op}>{"".join(parts)}</g>'


def defs_block() -> str:
    return "<defs>" + "".join(f'<path id="{k}" d="{v}"/>' for k, v in sorted(DEFS.items())) + "</defs>"


# ------------------------------------------------------------------ mark: three nested arches, the innermost is "now"
def mark(t, x=0, y=0, s=1.0):
    return f'''<g transform="translate({x} {y}) scale({s})">
    <path d="M3.25 44V24A20.75 20.75 0 0 1 44.75 24V44" fill="none" stroke="{t["teal"]}" stroke-width="6.5"/>
    <path d="M13.75 44V24A10.25 10.25 0 0 1 34.25 24V44" fill="none" stroke="{t["violet"]}" stroke-width="6.5"/>
    <path d="M20.5 44V29.5A3.5 3.5 0 0 1 27.5 29.5V44Z" fill="{t["pink"]}"/>
  </g>'''


def logo(name: str):
    DEFS.clear()
    t = THEMES[name]
    word_x = 62
    w = word_x + DISPLAY.width("memaso", 44, -0.02) + 2
    body = txt(DISPLAY, "memaso", 44, word_x, 44, t["ink"], tracking=-0.02)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -2 {w:.0f} 50" width="{w:.0f}" height="50" role="img" '
            f'aria-label="Memaso"><title>Memaso</title>{defs_block()}{mark(t)}{body}</svg>\n')


def mark_svg(name: str):
    t = THEMES[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 52 48" width="104" height="96" role="img" '
            f'aria-label="Memaso"><title>Memaso</title>{mark(t)}</svg>\n')


# ------------------------------------------------------------------ hero: a timeline with a "now" cursor
def hero(name: str):
    DEFS.clear()
    t = THEMES[name]
    ink, muted, teal, violet, pink, grey = t["ink"], t["muted"], t["teal"], t["violet"], t["pink"], t["grey"]
    o = []
    # --- geometry
    X0, X_JAN, X_FEB, X_NOW, X_END = 190, 214, 520, 806, 806
    o.append(f'<rect width="1200" height="560" fill="{t["bg"]}"/>')

    # lane backgrounds (tinted by layer) and lane names
    o.append(f'<rect x="0" y="196" width="{X_NOW + 30}" height="104" fill="{t["teal_tint"]}" fill-opacity=".08"/>')
    o.append(f'<rect x="0" y="300" width="{X_NOW + 30}" height="176" fill="{t["violet_tint"]}" fill-opacity=".08"/>')
    o.append(txt(HEAD, "verbatim", 17, 28, 238, teal))
    o.append(txt(BODY, "as it was said", 13, 28, 258, muted))
    o.append(txt(HEAD, "facts", 17, 28, 336, violet))
    o.append(txt(BODY, "with dates", 13, 28, 356, muted))

    # time axis
    o.append(f'<path stroke="{t["hair"]}" stroke-width="1.5" d="M{X0} 70H{X_NOW}"/>')
    for x, label in ((X_JAN, "jan 5"), (X_FEB, "feb 1")):
        o += [f'<path stroke="{muted}" stroke-width="1.5" d="M{x} 64V76"/>', txt(BODY, label, 14, x, 54, muted)]

    # lane 0: what was said (two notes)
    o.append(txt(HEAD, "said", 17, 28, 112, ink))
    o.append(txt(BODY, "the conversation", 13, 28, 132, muted))
    note1_w, note2_w = 262, 262
    for x, w, h, lines in ((X_JAN, note1_w, 76, ["Budget is 50k.", "I want weekly reports in INR."]),
                           (X_FEB, note2_w, 50, ["Actually the budget is 80k."])):
        o.append(f'<rect x="{x}" y="86" width="{w}" height="{h}" rx="14" fill="{ink}" fill-opacity=".06"/>')
        for i, line in enumerate(lines):
            o.append(txt(BODY, line, 16.5, x + 18, 114 + i * 26, ink))

    # lane 1: verbatim chunks
    cx = X_JAN
    o.append(f'<rect x="{cx}" y="214" width="{note1_w}" height="54" rx="10" fill="{teal}" fill-opacity=".16" stroke="{teal}" stroke-opacity=".5"/>')
    o.append(txt(CODE, "user: [superseded]", 11.5, cx + 14, 237, muted))
    w_sup = CODE.width("user: ", 11.5)
    o.append(f'<path stroke="{muted}" d="M{cx + 14 + w_sup:.1f} 233.5H{cx + 14 + CODE.width("user: [superseded]", 11.5):.1f}"/>')
    o.append(txt(CODE, "I want weekly reports in INR.", 11.5, cx + 14, 256, ink))
    o.append(f'<rect x="{X_FEB}" y="214" width="{note2_w}" height="54" rx="10" fill="{teal}" fill-opacity=".16" stroke="{teal}" stroke-opacity=".5"/>')
    o.append(txt(CODE, "Actually the budget is 80k.", 11.5, X_FEB + 14, 246, ink))
    o.append(txt(BODY, "50k was replaced by 80k, so its words are redacted", 12.5, cx, 288, muted))

    # lane 2: facts as intervals
    o.append(txt(BODY, "I prefer weekly reports in INR", 15, X_JAN, 338, ink))
    o.append(f'<rect x="{X_JAN}" y="348" width="{X_NOW - X_JAN}" height="7" rx="3.5" fill="{violet}"/>')
    o.append(txt(BODY, "Budget is 50k", 15, X_JAN, 394, muted))
    o.append(f'<rect x="{X_JAN}" y="404" width="{X_FEB - X_JAN}" height="7" rx="3.5" fill="{grey}"/>')
    o.append(f'<path stroke="{ink}" stroke-width="1.8" d="M{X_FEB} 396V420"/>')
    o.append(txt(CODE, "valid_to", 11.5, X_FEB + 8, 438, muted))
    o.append(txt(HEAD, "the budget is 80k", 15, X_FEB + 10, 394, ink))
    o.append(f'<rect x="{X_FEB}" y="404" width="{X_NOW - X_FEB}" height="7" rx="3.5" fill="{pink}"/>')

    # the "now" cursor
    o.append(f'<path stroke="{pink}" stroke-width="2" d="M{X_NOW} 84V492"/>')
    pw = BODY.width("now", 14) + 22
    o.append(f'<rect x="{X_NOW - pw/2:.1f}" y="40" width="{pw:.1f}" height="26" rx="13" fill="{pink}"/>')
    o.append(txt(HEAD, "now", 14, X_NOW, 58, "#FFFFFF", anchor="middle"))
    o.append(f'<circle class="pulse" cx="{X_NOW}" cy="407.5" r="12" fill="{pink}"/>')
    o.append(f'<circle cx="{X_NOW}" cy="407.5" r="7" fill="{pink}"/>')

    # what the prompt gets
    px, pwid = 902, 278
    o.append(f'<rect x="{px}" y="40" width="{pwid}" height="452" rx="20" fill="{t["stage"]}"/>')
    o.append(txt(HEAD, "what the prompt gets", 17, px + 26, 78, t["stage_text"]))
    o.append(txt(BODY, "context(scope, \"budget?\", 1500)", 13, px + 26, 100, t["stage_muted"]))
    code = [("<memory kind=\"fact\">", t["stage_muted"]), ("  the budget is 80k", t["pink_text"] if name == "dark" else "#FF7AA5"),
            ("</memory>", t["stage_muted"]), (None, None),
            ("<memory kind=\"preference\">", t["stage_muted"]), ("  I prefer weekly", t["stage_text"]), ("  reports in INR", t["stage_text"]),
            ("</memory>", t["stage_muted"]), (None, None),
            ("<chunk observed=\"jan 5\">", t["stage_muted"]), ("  [superseded] I want weekly", t["stage_text"]),
            ("  reports in INR.", t["stage_text"]), ("</chunk>", t["stage_muted"])]
    y = 138
    for s, col in code:
        if s:
            o.append(txt(CODE, s, 12.5, px + 26, y, col))
        y += 24
    o.append(f'<rect x="{px + 26}" y="{138 + 24 - 15}" width="3" height="19" fill="{pink}"/>')
    o.append(f'<rect x="{px + 26}" y="452" width="{CODE.width("0 llm tokens", 12) + 26:.0f}" height="26" rx="13" fill="none" stroke="{pink}" stroke-width="1.5"/>')
    o.append(txt(CODE, "0 llm tokens", 12, px + 39, 469, t["stage_text"]))

    # connector: now cursor -> prompt
    o.append(f'<path stroke="{muted}" stroke-width="1.5" fill="none" d="M{X_NOW + 36} 270H{px - 10}"/>')
    o.append(f'<path fill="{muted}" d="M{px - 2} 270l-9 -5v10z"/>')
    o.append(txt(BODY, "search", 13, (X_NOW + 36 + px - 10) / 2, 256, muted, anchor="middle"))

    # legend
    ly, x = 528, 28
    for color, label in ((teal, "verbatim layer"), (violet, "facts layer"), (pink, "current value"), (grey, "superseded")):
        o += [f'<circle cx="{x + 6}" cy="{ly - 5}" r="6" fill="{color}"/>', txt(BODY, label, 14, x + 20, ly, muted)]
        x += 20 + BODY.width(label, 14) + 32

    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 560" width="1200" height="560" role="img" '
            f'aria-label="A conversation is stored twice, as verbatim chunks and as dated facts. A later statement closes the old fact; search returns the current fact and the surviving words of old text.">'
            "<title>How Memaso stores and recalls a conversation</title>"
            "<style>.pulse{transform-box:fill-box;transform-origin:center;animation:p 3.2s ease-in-out infinite}"
            "@keyframes p{0%,100%{opacity:.14;transform:scale(1)}50%{opacity:.4;transform:scale(1.5)}}"
            "@media (prefers-reduced-motion:reduce){.pulse{animation:none}}</style>")
    body = "\n  ".join(x for x in o if x)
    return head + defs_block() + "\n  " + body + "\n</svg>\n"


def main():
    for name in THEMES:
        (OUT / f"logo-{name}.svg").write_text(logo(name))
        (OUT / f"mark-{name}.svg").write_text(mark_svg(name))
        (OUT / f"hero-{name}.svg").write_text(hero(name))
    print("assets written")


if __name__ == "__main__":
    main()
