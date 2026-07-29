#!/usr/bin/env python3
"""Generate Excalidraw sources and matching SVG exports for the docs diagrams.

Both artifacts come from one coordinate table so the editable source and the
rendered image cannot drift apart.

Layout rules are enforced by ``validate()`` below, so a careless edit fails
loudly instead of shipping an unreadable picture:

* every connector is orthogonal and never crosses a box it does not touch
* boxes never overlap and always keep a visible gap
* label text always fits inside its box
* edge labels never land on top of a box
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "diagrams"

INK = "#1e1e1e"
MUTED = "#5c5f66"
LINE = "#343a40"
PAPER = "#ffffff"

TITLE_SIZE = 24
LABEL_SIZE = 16
SUB_SIZE = 15
EDGE_SIZE = 13
LEGEND_SIZE = 14

BOX_PAD = 28          # horizontal padding reserved inside a box
MIN_GAP = 24          # required clear space between two boxes
CORNER = 12           # elbow corner radius

FILL = {
    "user": "#ffec99",
    "web": "#b2f2bb",
    "compute": "#a5d8ff",
    "queue": "#ffd8a8",
    "network": "#ffc9c9",
    "data": "#d8f5a2",
    "ai": "#d0bfff",
}


def node(nid, label, x, y, w, h, kind):
    return {"id": nid, "label": label, "x": x, "y": y, "w": w, "h": h,
            "kind": kind, "fill": FILL[kind]}


def edge(src, dst, pts, label=None, dashed=False, side=None):
    """``pts`` is a list of absolute waypoints; segments must be axis aligned.

    ``side`` nudges the label off the line: "above"/"below" for a horizontal
    segment, "left"/"right" for a vertical one.
    """
    return {"src": src, "dst": dst, "pts": pts, "label": label,
            "dashed": dashed, "side": side}


def text_w(s, size, bold=False):
    """Conservative advance-width estimate for a sans-serif face."""
    return len(s) * size * (0.62 if bold else 0.56)


# ---------------------------------------------------------------- architecture
ARCH_TITLE = "certaudio - target architecture"
ARCH_NODES = [
    node("admin",    "Admin browser\nadmin.html",              110,  86, 200, 68, "user"),
    node("listener", "Listener browser\nindex.html",           350,  86, 200, 68, "user"),
    node("swa",      "Azure Static Web Apps\nEntra sign-in - only public entry",
                                                               110, 200, 440, 84, "web"),
    node("func",     "Functions API\nVNet integrated",         195, 350, 270, 88, "compute"),
    node("queue",    "Storage Queue\ncontent-jobs",            575, 350, 230, 88, "queue"),
    node("gen",      "Queue trigger\ncontent generation",      915, 350, 250, 88, "compute"),
    node("ai",       "Azure OpenAI + Speech\nAzure AI Search", 905, 190, 270, 84, "ai"),
    node("pe",       "Private Endpoints + Private DNS zones",  195, 520, 970, 76, "network"),
    node("cosmos",   "Cosmos DB\nepisodes + jobs",             400, 660, 240, 76, "data"),
    node("store",    "Storage account\nblob + queue + table",  740, 660, 240, 76, "data"),
]
ARCH_EDGES = [
    edge("admin",    "swa",    [(210, 154), (210, 200)]),
    edge("listener", "swa",    [(450, 154), (450, 200)]),
    edge("swa",      "func",   [(330, 284), (330, 350)], "linked backend", side="right"),
    edge("func",     "queue",  [(465, 394), (575, 394)], "enqueue job", side="above"),
    edge("queue",    "gen",    [(805, 394), (915, 394)], "queue trigger", side="above"),
    edge("gen",      "ai",     [(1040, 350), (1040, 274)], "prompt + TTS", side="right"),
    edge("func",     "pe",     [(330, 438), (330, 520)], "private link", side="right"),
    edge("gen",      "pe",     [(1040, 438), (1040, 520)], "private link", side="right"),
    edge("pe",       "cosmos", [(520, 596), (520, 660)]),
    edge("pe",       "store",  [(860, 596), (860, 660)]),
]
ARCH_LEGEND = [
    ("user", "Browser"),
    ("web", "Static Web Apps"),
    ("compute", "Functions compute"),
    ("queue", "Storage queue"),
    ("ai", "Azure AI services"),
    ("network", "Private networking"),
    ("data", "Data plane"),
]
ARCH_SIZE = (1250, 800)

# ------------------------------------------------------------- generation flow
FLOW_TITLE = "certaudio - generation job lifecycle"
FLOW_NODES = [
    node("browser",  "Admin browser\nstarts a job",                  90,  90, 230, 76, "user"),
    node("post",     "POST /api/portal/jobs\nauthorize + validate",  430,  90, 300, 76, "compute"),
    node("accept",   "202 Accepted\njobId returned",                 860,  90, 250, 76, "web"),
    node("queue",    "Storage Queue\ncontent-jobs",                   90, 250, 220, 88, "queue"),
    node("discover", "Discover\nMicrosoft Learn",                    400, 250, 200, 88, "compute"),
    node("index",    "Index\nAzure AI Search",                       660, 250, 200, 88, "compute"),
    node("generate", "Generate episodes\nOpenAI + Speech",           920, 250, 240, 88, "compute"),
    node("publish",  "Publish\nCosmos + Blob",                      1220, 250, 200, 88, "data"),
    node("progress", "Cosmos jobs container\nstatus + progress",     560, 430, 300, 88, "data"),
    node("poll",     "Admin UI polls\nGET /api/portal/jobs/{id}",    940, 430, 340, 88, "user"),
]
FLOW_EDGES = [
    edge("browser",  "post",     [(320, 116), (430, 116)], "submit", side="above"),
    edge("post",     "accept",   [(730, 128), (860, 128)], "immediately", side="above"),
    edge("post",     "queue",    [(580, 166), (580, 205), (200, 205), (200, 250)],
         "enqueue", side="above"),
    edge("queue",    "discover", [(310, 294), (400, 294)], "trigger", side="above"),
    edge("discover", "index",    [(600, 294), (660, 294)]),
    edge("index",    "generate", [(860, 294), (920, 294)]),
    edge("generate", "publish",  [(1160, 294), (1220, 294)]),
    edge("generate", "progress", [(1040, 338), (1040, 385), (710, 385), (710, 430)],
         "writes progress", side="above"),
    edge("progress", "poll",     [(860, 474), (940, 474)], "status", side="above",
         dashed=True),
]
FLOW_LEGEND = [
    ("user", "Browser"),
    ("compute", "Functions code"),
    ("web", "HTTP response"),
    ("queue", "Storage queue"),
    ("data", "Data plane"),
]
FLOW_SIZE = (1480, 590)


# ---------------------------------------------------------------------- legend
def legend_slot(legend, size, i):
    """Left-to-right legend strip pinned to the bottom margin."""
    _, h = size
    x = 60
    for _, text in legend[:i]:
        x += 24 + text_w(text, LEGEND_SIZE) + 26
    return x, h - 44


def label_anchor(e):
    """Return (x, y, text_anchor) for an edge label, offset clear of the line."""
    pts = e["pts"]
    seg = max(zip(pts, pts[1:]),
              key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    (sx, sy), (ex, ey) = seg
    mx, my = (sx + ex) / 2, (sy + ey) / 2
    horizontal = abs(ex - sx) >= abs(ey - sy)
    side = e["side"] or ("above" if horizontal else "right")
    if horizontal:
        return (mx, my - 9 if side == "above" else my + 20, "middle")
    return ((mx + 10, my + 5, "start") if side == "right" else (mx - 10, my + 5, "end"))


# ------------------------------------------------------------------- validation
def _seg_hits_rect(p0, p1, rect, inset=3):
    """Liang-Barsky clip: does segment p0->p1 pass through the shrunken rect?"""
    x0, y0 = p0
    x1, y1 = p1
    left = rect["x"] + inset
    right = rect["x"] + rect["w"] - inset
    top = rect["y"] + inset
    bottom = rect["y"] + rect["h"] - inset
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - left), (dx, right - x0),
                 (-dy, y0 - top), (dy, bottom - y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t0 <= t1


def _rects_overlap(a, b, gap=0):
    return not (a["x"] + a["w"] + gap <= b["x"] or b["x"] + b["w"] + gap <= a["x"]
                or a["y"] + a["h"] + gap <= b["y"] or b["y"] + b["h"] + gap <= a["y"])


def _on_border(pt, n):
    x, y = pt
    within_x = n["x"] - 1 <= x <= n["x"] + n["w"] + 1
    within_y = n["y"] - 1 <= y <= n["y"] + n["h"] + 1
    on_h = within_x and (abs(y - n["y"]) <= 1 or abs(y - (n["y"] + n["h"])) <= 1)
    on_v = within_y and (abs(x - n["x"]) <= 1 or abs(x - (n["x"] + n["w"])) <= 1)
    return on_h or on_v


def validate(name, nodes, edges, legend, size):
    w, h = size
    by_id = {n["id"]: n for n in nodes}
    problems = []

    for n in nodes:
        for i, line in enumerate(n["label"].split("\n")):
            need = text_w(line, LABEL_SIZE if i == 0 else SUB_SIZE, bold=(i == 0))
            if need > n["w"] - BOX_PAD:
                problems.append(
                    f"text '{line}' needs ~{need:.0f}px but box '{n['id']}' "
                    f"offers {n['w'] - BOX_PAD}px"
                )
        if n["x"] < 0 or n["y"] < 0 or n["x"] + n["w"] > w or n["y"] + n["h"] > h:
            problems.append(f"box '{n['id']}' falls outside the {w}x{h} canvas")

    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if _rects_overlap(a, b, MIN_GAP):
                problems.append(
                    f"boxes '{a['id']}' and '{b['id']}' are closer than {MIN_GAP}px"
                )

    for e in edges:
        pts = e["pts"]
        for p0, p1 in zip(pts, pts[1:]):
            if p0[0] != p1[0] and p0[1] != p1[1]:
                problems.append(
                    f"edge {e['src']}->{e['dst']} has a diagonal segment {p0}->{p1}"
                )
            for n in nodes:
                if n["id"] in (e["src"], e["dst"]):
                    continue
                if _seg_hits_rect(p0, p1, n):
                    problems.append(
                        f"edge {e['src']}->{e['dst']} runs through box '{n['id']}'"
                    )
        for pt, nid in ((pts[0], e["src"]), (pts[-1], e["dst"])):
            if not _on_border(pt, by_id[nid]):
                problems.append(
                    f"edge {e['src']}->{e['dst']} endpoint {pt} is not on the "
                    f"border of '{nid}'"
                )
        if e["label"]:
            lx, ly, anchor = label_anchor(e)
            lw = text_w(e["label"], EDGE_SIZE)
            x0 = lx - lw / 2 if anchor == "middle" else (lx if anchor == "start" else lx - lw)
            box = {"x": x0, "y": ly - EDGE_SIZE, "w": lw, "h": EDGE_SIZE + 6}
            for n in nodes:
                if _rects_overlap(box, n):
                    problems.append(f"label '{e['label']}' overlaps box '{n['id']}'")

    last_x, last_y = legend_slot(legend, size, len(legend))
    if last_x > w - 40:
        problems.append(f"legend is {last_x - (w - 40):.0f}px wider than the canvas")
    for n in nodes:
        strip = {"x": 60, "y": last_y - 6, "w": last_x - 60, "h": 28}
        if _rects_overlap(strip, n):
            problems.append(f"legend strip collides with box '{n['id']}'")

    if problems:
        print(f"{name}: layout check failed", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
    return not problems


# --------------------------------------------------------------- excalidraw out
class _Seed:
    def __init__(self):
        self.n = 1000

    def next(self):
        self.n += 1
        return self.n


def _base(nid, seed):
    return {
        "id": nid, "angle": 0, "strokeColor": LINE, "fillStyle": "solid",
        "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1, "opacity": 100,
        "groupIds": [], "frameId": None, "seed": seed, "version": 1,
        "versionNonce": seed, "isDeleted": False, "updated": 1, "link": None,
        "locked": False,
    }


def _ex_text(seed, tid, text, x, y, w, h, size, align="center", container=None,
             color=INK):
    t = _base(tid, seed.next())
    t.update({
        "type": "text", "x": x, "y": y, "width": w, "height": h,
        "strokeColor": color, "backgroundColor": "transparent", "roundness": None,
        "boundElements": [], "text": text, "fontSize": size, "fontFamily": 2,
        "textAlign": align, "verticalAlign": "middle", "containerId": container,
        "originalText": text, "lineHeight": 1.25, "autoResize": True,
    })
    return t


def to_excalidraw(title, nodes, edges, legend, size):
    seed = _Seed()
    elements = [_ex_text(seed, "title", title, 60, 20, 640, 30, TITLE_SIZE,
                         align="left")]

    for n in nodes:
        tid = f"{n['id']}-label"
        rect = _base(n["id"], seed.next())
        rect.update({
            "type": "rectangle", "x": n["x"], "y": n["y"],
            "width": n["w"], "height": n["h"],
            "backgroundColor": n["fill"], "roundness": {"type": 3},
            "boundElements": [{"type": "text", "id": tid}],
        })
        elements.append(rect)
        lines = n["label"].split("\n")
        elements.append(_ex_text(
            seed, tid, n["label"], n["x"] + 12, n["y"] + 14,
            n["w"] - 24, 20 * len(lines), LABEL_SIZE, container=n["id"],
        ))

    for e in edges:
        aid = f"arrow-{e['src']}-{e['dst']}"
        pts = e["pts"]
        ox, oy = pts[0]
        rel = [[p[0] - ox, p[1] - oy] for p in pts]
        arr = _base(aid, seed.next())
        arr.update({
            "type": "arrow", "x": ox, "y": oy,
            "width": max(abs(p[0]) for p in rel),
            "height": max(abs(p[1]) for p in rel),
            "backgroundColor": "transparent", "roundness": {"type": 2},
            "boundElements": ([{"type": "text", "id": f"{aid}-label"}]
                              if e["label"] else []),
            "strokeStyle": "dashed" if e["dashed"] else "solid",
            "points": rel, "lastCommittedPoint": None,
            "startBinding": {"elementId": e["src"], "focus": 0, "gap": 4},
            "endBinding": {"elementId": e["dst"], "focus": 0, "gap": 4},
            "startArrowhead": None, "endArrowhead": "arrow", "elbowed": False,
        })
        elements.append(arr)
        if e["label"]:
            lx, ly, _ = label_anchor(e)
            lw = text_w(e["label"], EDGE_SIZE)
            elements.append(_ex_text(
                seed, f"{aid}-label", e["label"], lx - lw / 2, ly - EDGE_SIZE,
                lw, EDGE_SIZE + 4, EDGE_SIZE, container=aid, color=MUTED,
            ))

    for i, (kind, text) in enumerate(legend):
        lx, ly = legend_slot(legend, size, i)
        sw = _base(f"legend-{kind}", seed.next())
        sw.update({
            "type": "rectangle", "x": lx, "y": ly, "width": 16, "height": 16,
            "backgroundColor": FILL[kind], "roundness": {"type": 3},
            "boundElements": [],
        })
        elements.append(sw)
        elements.append(_ex_text(
            seed, f"legend-{kind}-label", text, lx + 24, ly,
            text_w(text, LEGEND_SIZE), 18, LEGEND_SIZE, align="left", color=MUTED,
        ))

    return {
        "type": "excalidraw", "version": 2,
        "source": "certaudio/scripts/build-diagrams.py",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": PAPER},
        "files": {},
    }


# ---------------------------------------------------------------------- svg out
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rounded_path(pts, r=CORNER):
    if len(pts) == 2:
        return f"M {pts[0][0]} {pts[0][1]} L {pts[1][0]} {pts[1][1]}"
    d = [f"M {pts[0][0]} {pts[0][1]}"]
    for i in range(1, len(pts) - 1):
        (px, py), (cx, cy), (nx, ny) = pts[i - 1], pts[i], pts[i + 1]
        d0 = max(abs(cx - px), abs(cy - py)) or 1
        d1 = max(abs(nx - cx), abs(ny - cy)) or 1
        rr = min(r, d0 / 2, d1 / 2)
        ax = cx + (px - cx) / d0 * rr
        ay = cy + (py - cy) / d0 * rr
        bx = cx + (nx - cx) / d1 * rr
        by = cy + (ny - cy) / d1 * rr
        d.append(f"L {ax:.1f} {ay:.1f}")
        d.append(f"Q {cx} {cy} {bx:.1f} {by:.1f}")
    d.append(f"L {pts[-1][0]} {pts[-1][1]}")
    return " ".join(d)


def to_svg(title, nodes, edges, legend, size):
    w, h = size
    font = "Segoe UI, Helvetica Neue, Arial, DejaVu Sans, sans-serif"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}" '
        f'font-family="{font}">',
        f"<title>{esc(title)}</title>",
        "<defs>",
        '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/></marker>',
        "</defs>",
        f'<rect width="{w}" height="{h}" fill="{PAPER}"/>',
        f'<text x="60" y="44" font-size="{TITLE_SIZE}" font-weight="600" '
        f'fill="{INK}">{esc(title)}</text>',
    ]

    for e in edges:
        dash = ' stroke-dasharray="7 5"' if e["dashed"] else ""
        out.append(
            f'<path d="{rounded_path(e["pts"])}" fill="none" stroke="{LINE}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
            f'{dash} marker-end="url(#ah)"/>'
        )

    for n in nodes:
        cx = n["x"] + n["w"] / 2
        out.append(
            f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" '
            f'rx="12" ry="12" fill="{n["fill"]}" stroke="{LINE}" stroke-width="2"/>'
        )
        lines = n["label"].split("\n")
        for i, line in enumerate(lines):
            cy = n["y"] + n["h"] / 2 + (i - (len(lines) - 1) / 2) * 21 + 6
            out.append(
                f'<text x="{cx}" y="{cy}" font-size="{LABEL_SIZE if i == 0 else SUB_SIZE}" '
                f'font-weight="{"600" if i == 0 else "400"}" '
                f'fill="{INK if i == 0 else MUTED}" text-anchor="middle">'
                f"{esc(line)}</text>"
            )

    for e in edges:
        if not e["label"]:
            continue
        lx, ly, anchor = label_anchor(e)
        lw = text_w(e["label"], EDGE_SIZE)
        x0 = lx - lw / 2 if anchor == "middle" else (lx if anchor == "start" else lx - lw)
        out.append(
            f'<rect x="{x0 - 4:.1f}" y="{ly - EDGE_SIZE + 1}" width="{lw + 8:.1f}" '
            f'height="{EDGE_SIZE + 5}" fill="{PAPER}"/>'
        )
        out.append(
            f'<text x="{lx}" y="{ly}" font-size="{EDGE_SIZE}" fill="{MUTED}" '
            f'text-anchor="{anchor}">{esc(e["label"])}</text>'
        )

    for i, (kind, text) in enumerate(legend):
        lx, ly = legend_slot(legend, size, i)
        out.append(
            f'<rect x="{lx}" y="{ly}" width="16" height="16" rx="4" ry="4" '
            f'fill="{FILL[kind]}" stroke="{LINE}" stroke-width="1.5"/>'
        )
        out.append(
            f'<text x="{lx + 24}" y="{ly + 13}" font-size="{LEGEND_SIZE}" '
            f'fill="{MUTED}">{esc(text)}</text>'
        )

    out.append("</svg>")
    return "\n".join(out)


DIAGRAMS = [
    ("architecture", ARCH_TITLE, ARCH_NODES, ARCH_EDGES, ARCH_LEGEND, ARCH_SIZE),
    ("generation-flow", FLOW_TITLE, FLOW_NODES, FLOW_EDGES, FLOW_LEGEND, FLOW_SIZE),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    if not all(validate(name, nodes, edges, legend, size)
               for name, _, nodes, edges, legend, size in DIAGRAMS):
        return 1

    for name, title, nodes, edges, legend, size in DIAGRAMS:
        (OUT / f"{name}.excalidraw").write_text(
            json.dumps(to_excalidraw(title, nodes, edges, legend, size), indent=2) + "\n",
            encoding="utf-8",
        )
        (OUT / f"{name}.svg").write_text(
            to_svg(title, nodes, edges, legend, size) + "\n", encoding="utf-8"
        )
        print(f"wrote {name}.excalidraw and {name}.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
