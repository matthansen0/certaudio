#!/usr/bin/env python3
"""Generate Excalidraw sources and matching SVG exports for the docs diagrams.

Both artifacts come from one coordinate table so the editable source and the
rendered image cannot drift apart. Every diagram in the docs is built here —
there are no Mermaid blocks and no hand-drawn ASCII boxes.

Layout rules are enforced by ``validate()`` below, so a careless edit fails
loudly instead of shipping an unreadable picture:

* every connector is orthogonal and never crosses a box it does not touch
* boxes never overlap and always keep a visible gap
* label text always fits inside its box
* edge labels never land on top of a box
* group members stay inside their container
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
GROUP_PAD = 12        # required clear space inside a container
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


def group(gid, label, x, y, w, h, members):
    """A dashed container drawn behind its member boxes."""
    return {"id": gid, "label": label, "x": x, "y": y, "w": w, "h": h,
            "members": members}


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


def diagram(name, title, nodes, edges, legend, size, groups=()):
    return {"name": name, "title": title, "nodes": nodes, "edges": edges,
            "legend": legend, "size": size, "groups": list(groups)}


# ---------------------------------------------------------------- architecture
ARCHITECTURE = diagram(
    "architecture",
    "certaudio - target architecture",
    [
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
    ],
    [
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
    ],
    [
        ("user", "Browser"),
        ("web", "Static Web Apps"),
        ("compute", "Functions compute"),
        ("queue", "Storage queue"),
        ("ai", "Azure AI services"),
        ("network", "Private networking"),
        ("data", "Data plane"),
    ],
    (1250, 800),
)

# ------------------------------------------------------------- generation flow
GENERATION_FLOW = diagram(
    "generation-flow",
    "certaudio - generation job lifecycle",
    [
        node("browser",  "Admin browser\nstarts a job",                  90,  90, 230, 76, "user"),
        node("post",     "POST /api/portal/jobs\nauthorize + validate",  430,  90, 300, 76, "compute"),
        node("accept",   "202 Accepted\njobId returned",                 860,  90, 250, 76, "web"),
        node("queue",    "Storage Queue\ncontent-jobs",                   90, 250, 220, 88, "queue"),
        node("discover", "Discover\nMicrosoft Learn",                    400, 250, 200, 88, "compute"),
        node("index",    "Index\nAzure AI Search",                       660, 250, 200, 88, "compute"),
        node("estimate", "Episode count\n+ cost estimate",               920, 250, 260, 88, "compute"),
        node("confirm",  "Admin confirms\nestimated cost",                90, 430, 280, 88, "user"),
        node("generate", "Generate episodes\nOpenAI + Speech",           490, 430, 240, 88, "compute"),
        node("publish",  "Publish\nCosmos + Blob",                       790, 430, 200, 88, "data"),
        node("meter",    "Metered actual\nchars + tokens",              1050, 430, 260, 88, "data"),
        node("progress", "Cosmos jobs container\nstatus + progress",     300, 610, 300, 88, "data"),
        node("poll",     "Admin UI polls\nGET /api/portal/jobs/{id}",    700, 610, 340, 88, "user"),
    ],
    [
        edge("browser",  "post",     [(320, 128), (430, 128)], "submit", side="above"),
        edge("post",     "accept",   [(730, 128), (860, 128)], "immediately", side="above"),
        edge("post",     "queue",    [(580, 166), (580, 205), (200, 205), (200, 250)],
             "enqueue", side="above"),
        edge("queue",    "discover", [(310, 294), (400, 294)], "index job", side="above"),
        edge("discover", "index",    [(600, 294), (660, 294)]),
        edge("index",    "estimate", [(860, 294), (920, 294)]),
        edge("estimate", "confirm",  [(1050, 338), (1050, 384), (230, 384), (230, 430)],
             "cheap half stops here", side="above"),
        edge("confirm",  "generate", [(370, 474), (490, 474)], "generate job", side="above"),
        edge("generate", "publish",  [(730, 474), (790, 474)]),
        edge("publish",  "meter",    [(990, 474), (1050, 474)]),
        edge("generate", "progress", [(610, 518), (610, 564), (450, 564), (450, 610)],
             "writes progress", side="above"),
        edge("progress", "poll",     [(600, 654), (700, 654)], "status", side="above",
             dashed=True),
    ],
    [
        ("user", "Browser"),
        ("compute", "Functions code"),
        ("web", "HTTP response"),
        ("queue", "Storage queue"),
        ("data", "Data plane"),
    ],
    (1480, 760),
)

# ------------------------------------------------------------- content pipeline
CONTENT_PIPELINE = diagram(
    "content-pipeline",
    "certaudio - content pipeline",
    [
        node("learn",    "Microsoft Learn\ncatalog + study guide",  60, 110, 250, 76, "data"),
        node("discover", "Discovery\nmodules + skills",            356, 110, 220, 76, "compute"),
        node("index",    "RAG index\nAzure AI Search",             622, 110, 220, 76, "compute"),
        node("narrate",  "AI narration\nGPT-4o script",             60, 270, 220, 76, "ai"),
        node("tts",      "Text to speech\nAzure AI Speech",        326, 270, 230, 76, "ai"),
        node("player",   "Web player\nStatic Web Apps",            602, 270, 240, 76, "web"),
    ],
    [
        edge("learn",    "discover", [(310, 148), (356, 148)]),
        edge("discover", "index",    [(576, 148), (622, 148)]),
        edge("index",    "narrate",  [(732, 186), (732, 228), (170, 228), (170, 270)],
             "grounded retrieval", side="above"),
        edge("narrate",  "tts",      [(280, 308), (326, 308)]),
        edge("tts",      "player",   [(556, 308), (602, 308)]),
    ],
    [
        ("data", "Source content"),
        ("compute", "Pipeline step"),
        ("ai", "Azure AI"),
        ("web", "Web player"),
    ],
    (900, 440),
)

# ---------------------------------------------------------------- study partner
STUDY_PARTNER = diagram(
    "study-partner",
    "certaudio - Study Partner agent",
    [
        node("user",  "Listener question\nstudy.html",                  60, 165, 230, 76, "user"),
        node("api",   "Functions API\nPOST /api/chat",                 350, 165, 300, 76, "compute"),
        node("agent", "GPT-4o agent\nexam-prep instructions",          745, 165, 350, 76, "ai"),
        node("tool",  "Azure AI Search tool\ncertification-content",   745, 285, 350, 76, "ai"),
    ],
    [
        edge("user",  "api",   [(290, 203), (350, 203)]),
        edge("api",   "agent", [(650, 203), (745, 203)], "run agent", side="above"),
        edge("agent", "tool",  [(920, 241), (920, 285)], "AI Search tool", side="right"),
        edge("tool",  "user",  [(920, 361), (920, 430), (175, 430), (175, 241)],
             "grounded answer", side="above", dashed=True),
    ],
    [
        ("user", "Browser"),
        ("compute", "Functions compute"),
        ("ai", "Azure AI services"),
    ],
    (1190, 520),
    groups=[
        group("foundry", "AI Foundry project (managed identity)",
              710, 120, 420, 266, ["agent", "tool"]),
    ],
)

# -------------------------------------------------------------------- auth flow
AUTH_FLOW = diagram(
    "auth-flow",
    "certaudio - user sign-in and identity",
    [
        node("click",   "Listener clicks\nSign in",                  60, 110, 240, 84, "user"),
        node("login",   "/.auth/login/aad\nStatic Web Apps",        360, 110, 260, 84, "web"),
        node("entra",   "Microsoft Entra ID\nOAuth + consent",      680, 110, 260, 84, "network"),
        node("session", "/.auth/me\nsession cookie",                 60, 290, 240, 84, "web"),
        node("header",  "x-ms-client-principal\ninjected on /api/*", 360, 290, 300, 84, "compute"),
        node("backend", "Functions decodes\nstable userId",          720, 290, 260, 84, "compute"),
        node("cosmos",  "Cosmos DB\nprogress + admin role",          360, 470, 300, 84, "data"),
    ],
    [
        edge("click",   "login",   [(300, 152), (360, 152)]),
        edge("login",   "entra",   [(620, 152), (680, 152)]),
        edge("entra",   "session", [(810, 194), (810, 240), (180, 240), (180, 290)],
             "redirect + cookie", side="above"),
        edge("session", "header",  [(300, 332), (360, 332)]),
        edge("header",  "backend", [(660, 332), (720, 332)]),
        edge("backend", "cosmos",  [(850, 374), (850, 420), (510, 420), (510, 470)],
             "lookup by userId", side="above"),
    ],
    [
        ("user", "Browser"),
        ("web", "Static Web Apps"),
        ("network", "Microsoft Entra ID"),
        ("compute", "Functions compute"),
        ("data", "Data plane"),
    ],
    (1040, 640),
)

# --------------------------------------------------------------- content sources
CONTENT_SOURCES = diagram(
    "content-sources",
    "certaudio - two content sources",
    [
        node("lp",    "Learning path",         110, 145, 320, 66, "web"),
        node("mod",   "Module",                110, 245, 320, 66, "web"),
        node("unit",  "Unit (topic)",          110, 345, 320, 66, "web"),
        node("dom",   "Domain (% weight)",     580, 145, 320, 66, "queue"),
        node("obj",   "Objective",             580, 245, 320, 66, "queue"),
        node("skill", "Skill (testable item)", 580, 345, 320, 66, "queue"),
        node("sweep", "Coverage sweep\nskills checked against content",
                                               330, 540, 350, 76, "compute"),
    ],
    [
        edge("lp",    "mod",   [(270, 211), (270, 245)]),
        edge("mod",   "unit",  [(270, 311), (270, 345)]),
        edge("dom",   "obj",   [(740, 211), (740, 245)]),
        edge("obj",   "skill", [(740, 311), (740, 345)]),
        edge("unit",  "sweep", [(270, 411), (270, 485), (450, 485), (450, 540)],
             "narration source", side="above"),
        edge("skill", "sweep", [(740, 411), (740, 485), (560, 485), (560, 540)],
             "coverage target", side="above"),
    ],
    [
        ("web", "Learning path content"),
        ("queue", "Exam skills outline"),
        ("compute", "Pipeline step"),
    ],
    (1000, 700),
    groups=[
        group("paths", "Learning paths - catalog API",
              70, 95, 400, 350, ["lp", "mod", "unit"]),
        group("skills", "Exam skills - catalog + guide",
              540, 95, 400, 350, ["dom", "obj", "skill"]),
    ],
)

DIAGRAMS = [ARCHITECTURE, GENERATION_FLOW, CONTENT_PIPELINE, STUDY_PARTNER,
            AUTH_FLOW, CONTENT_SOURCES]


# ---------------------------------------------------------------------- helpers
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


def label_box(e):
    lx, ly, anchor = label_anchor(e)
    lw = text_w(e["label"], EDGE_SIZE)
    x0 = lx - lw / 2 if anchor == "middle" else (lx if anchor == "start" else lx - lw)
    return {"x": x0, "y": ly - EDGE_SIZE, "w": lw, "h": EDGE_SIZE + 6}


# ------------------------------------------------------------------- validation
def _seg_hits_rect(p0, p1, rect, inset=3):
    """Liang-Barsky clip: does segment p0->p1 pass through the shrunken rect?"""
    x0, y0 = p0
    x1, y1 = p1
    left, right = rect["x"] + inset, rect["x"] + rect["w"] - inset
    top, bottom = rect["y"] + inset, rect["y"] + rect["h"] - inset
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


def _contains(outer, inner, pad):
    return (inner["x"] >= outer["x"] + pad
            and inner["y"] >= outer["y"] + pad
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] - pad
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] - pad)


def _on_border(pt, n):
    x, y = pt
    within_x = n["x"] - 1 <= x <= n["x"] + n["w"] + 1
    within_y = n["y"] - 1 <= y <= n["y"] + n["h"] + 1
    on_h = within_x and (abs(y - n["y"]) <= 1 or abs(y - (n["y"] + n["h"])) <= 1)
    on_v = within_y and (abs(x - n["x"]) <= 1 or abs(x - (n["x"] + n["w"])) <= 1)
    return on_h or on_v


def validate(spec):
    w, h = spec["size"]
    nodes, edges, groups = spec["nodes"], spec["edges"], spec["groups"]
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

    for i, g in enumerate(groups):
        if text_w(g["label"], LEGEND_SIZE) > g["w"] - 24:
            problems.append(f"group label '{g['label']}' is wider than its container")
        if g["x"] < 0 or g["y"] < 0 or g["x"] + g["w"] > w or g["y"] + g["h"] > h:
            problems.append(f"group '{g['id']}' falls outside the {w}x{h} canvas")
        for other in groups[i + 1:]:
            if _rects_overlap(g, other, MIN_GAP):
                problems.append(f"groups '{g['id']}' and '{other['id']}' overlap")
        for mid in g["members"]:
            if mid not in by_id:
                problems.append(f"group '{g['id']}' lists unknown member '{mid}'")
            elif not _contains(g, by_id[mid], GROUP_PAD):
                problems.append(f"'{mid}' does not fit inside group '{g['id']}'")
    grouped = {m for g in groups for m in g["members"]}
    for g in groups:
        for n in nodes:
            if n["id"] not in grouped and _rects_overlap(g, n):
                problems.append(f"box '{n['id']}' sits on group '{g['id']}'")

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
            if nid not in by_id:
                problems.append(f"edge {e['src']}->{e['dst']} references unknown '{nid}'")
            elif not _on_border(pt, by_id[nid]):
                problems.append(
                    f"edge {e['src']}->{e['dst']} endpoint {pt} is not on the "
                    f"border of '{nid}'"
                )
        if e["label"]:
            box = label_box(e)
            for n in nodes:
                if _rects_overlap(box, n):
                    problems.append(f"label '{e['label']}' overlaps box '{n['id']}'")

    last_x, last_y = legend_slot(spec["legend"], spec["size"], len(spec["legend"]))
    if last_x > w - 40:
        problems.append(f"legend is {last_x - (w - 40):.0f}px wider than the canvas")
    strip = {"x": 60, "y": last_y - 6, "w": last_x - 60, "h": 28}
    for n in nodes:
        if _rects_overlap(strip, n):
            problems.append(f"legend strip collides with box '{n['id']}'")
    for g in groups:
        if _rects_overlap(strip, g):
            problems.append(f"legend strip collides with group '{g['id']}'")

    if problems:
        print(f"{spec['name']}: layout check failed", file=sys.stderr)
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


def to_excalidraw(spec):
    seed = _Seed()
    elements = [_ex_text(seed, "title", spec["title"], 60, 20, 640, 30,
                         TITLE_SIZE, align="left")]

    for g in spec["groups"]:
        rect = _base(g["id"], seed.next())
        rect.update({
            "type": "rectangle", "x": g["x"], "y": g["y"],
            "width": g["w"], "height": g["h"], "strokeColor": MUTED,
            "backgroundColor": "transparent", "strokeStyle": "dashed",
            "roundness": {"type": 3}, "boundElements": [],
        })
        elements.append(rect)
        elements.append(_ex_text(
            seed, f"{g['id']}-label", g["label"], g["x"] + 16, g["y"] + 14,
            text_w(g["label"], LEGEND_SIZE), 18, LEGEND_SIZE, align="left",
            color=MUTED,
        ))

    for n in spec["nodes"]:
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

    for e in spec["edges"]:
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

    for i, (kind, text) in enumerate(spec["legend"]):
        lx, ly = legend_slot(spec["legend"], spec["size"], i)
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
        d.append(f"L {cx + (px - cx) / d0 * rr:.1f} {cy + (py - cy) / d0 * rr:.1f}")
        d.append(f"Q {cx} {cy} "
                 f"{cx + (nx - cx) / d1 * rr:.1f} {cy + (ny - cy) / d1 * rr:.1f}")
    d.append(f"L {pts[-1][0]} {pts[-1][1]}")
    return " ".join(d)


def to_svg(spec):
    w, h = spec["size"]
    font = "Segoe UI, Helvetica Neue, Arial, DejaVu Sans, sans-serif"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(spec["title"])}" '
        f'font-family="{font}">',
        f"<title>{esc(spec['title'])}</title>",
        "<defs>",
        '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/></marker>',
        "</defs>",
        f'<rect width="{w}" height="{h}" fill="{PAPER}"/>',
        f'<text x="60" y="44" font-size="{TITLE_SIZE}" font-weight="600" '
        f'fill="{INK}">{esc(spec["title"])}</text>',
    ]

    for g in spec["groups"]:
        out.append(
            f'<rect x="{g["x"]}" y="{g["y"]}" width="{g["w"]}" height="{g["h"]}" '
            f'rx="16" ry="16" fill="none" stroke="{MUTED}" stroke-width="2" '
            f'stroke-dasharray="8 6"/>'
        )
        out.append(
            f'<text x="{g["x"] + 16}" y="{g["y"] + 27}" font-size="{LEGEND_SIZE}" '
            f'font-weight="600" fill="{MUTED}">{esc(g["label"])}</text>'
        )

    for e in spec["edges"]:
        dash = ' stroke-dasharray="7 5"' if e["dashed"] else ""
        out.append(
            f'<path d="{rounded_path(e["pts"])}" fill="none" stroke="{LINE}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
            f'{dash} marker-end="url(#ah)"/>'
        )

    for n in spec["nodes"]:
        cx = n["x"] + n["w"] / 2
        out.append(
            f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" '
            f'rx="12" ry="12" fill="{n["fill"]}" stroke="{LINE}" stroke-width="2"/>'
        )
        lines = n["label"].split("\n")
        for i, line in enumerate(lines):
            cy = n["y"] + n["h"] / 2 + (i - (len(lines) - 1) / 2) * 21 + 6
            out.append(
                f'<text x="{cx}" y="{cy}" '
                f'font-size="{LABEL_SIZE if i == 0 else SUB_SIZE}" '
                f'font-weight="{"600" if i == 0 else "400"}" '
                f'fill="{INK if i == 0 else MUTED}" text-anchor="middle">'
                f"{esc(line)}</text>"
            )

    for e in spec["edges"]:
        if not e["label"]:
            continue
        lx, ly, anchor = label_anchor(e)
        box = label_box(e)
        out.append(
            f'<rect x="{box["x"] - 4:.1f}" y="{box["y"] + 1}" '
            f'width="{box["w"] + 8:.1f}" height="{box["h"]}" fill="{PAPER}"/>'
        )
        out.append(
            f'<text x="{lx}" y="{ly}" font-size="{EDGE_SIZE}" fill="{MUTED}" '
            f'text-anchor="{anchor}">{esc(e["label"])}</text>'
        )

    for i, (kind, text) in enumerate(spec["legend"]):
        lx, ly = legend_slot(spec["legend"], spec["size"], i)
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    if not all([validate(spec) for spec in DIAGRAMS]):
        return 1

    for spec in DIAGRAMS:
        (OUT / f"{spec['name']}.excalidraw").write_text(
            json.dumps(to_excalidraw(spec), indent=2) + "\n", encoding="utf-8",
        )
        (OUT / f"{spec['name']}.svg").write_text(
            to_svg(spec) + "\n", encoding="utf-8",
        )
        print(f"wrote {spec['name']}.excalidraw and {spec['name']}.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
