#!/usr/bin/env python3
"""Generate Excalidraw sources and matching SVG exports for the docs diagrams.

Both artifacts come from one coordinate table so the editable source and the
rendered image cannot drift apart.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "diagrams"

INK = "#1e1e1e"
MUTED = "#5c5f66"

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
    return {
        "id": nid, "label": label, "x": x, "y": y, "w": w, "h": h,
        "fill": FILL[kind],
    }


def edge(src, dst, sx, sy, dx, dy, dashed=False):
    return {"src": src, "dst": dst, "sx": sx, "sy": sy, "dx": dx, "dy": dy,
            "dashed": dashed}


# ---------------------------------------------------------------- architecture
ARCH_TITLE = "certaudio - target architecture"
ARCH_NODES = [
    node("admin",    "Admin browser",                     60,  70, 200, 68, "user"),
    node("listener", "Listener browser",                  60, 185, 200, 68, "user"),
    node("swa",      "Static Web Apps\nEntra sign-in",   330, 118, 250, 78, "web"),
    node("func",     "Functions API\nVNet integrated",   330, 288, 250, 78, "compute"),
    node("queue",    "Storage Queue\ncontent-jobs",      660, 288, 200, 78, "queue"),
    node("gen",      "Queue trigger\nGeneration",        940, 288, 220, 78, "compute"),
    node("ai",       "Azure OpenAI\nSpeech + AI Search", 940, 465, 220, 82, "ai"),
    node("pe",       "Private Endpoints\n+ Private DNS", 420, 465, 330, 78, "network"),
    node("cosmos",   "Cosmos DB",                        350, 620, 180, 64, "data"),
    node("blob",     "Blob Storage",                     570, 620, 180, 64, "data"),
]
ARCH_EDGES = [
    edge("admin",    "swa",    260, 104,  330, 145),
    edge("listener", "swa",    260, 219,  330, 175),
    edge("swa",      "func",   455, 196,  455, 288),
    edge("func",     "queue",  580, 327,  660, 327),
    edge("queue",    "gen",    860, 327,  940, 327),
    edge("gen",      "ai",    1050, 366, 1050, 465),
    edge("func",     "pe",     455, 366,  455, 465),
    edge("gen",      "pe",     940, 366,  750, 470),
    edge("pe",       "cosmos", 490, 543,  440, 620),
    edge("pe",       "blob",   680, 543,  660, 620),
]
ARCH_SIZE = (1240, 745)

# ------------------------------------------------------------- generation flow
FLOW_TITLE = "certaudio - generation job lifecycle"
FLOW_NODES = [
    node("browser", "Admin browser",                     60,  80, 220, 68, "user"),
    node("post",    "POST /api/admin/jobs",             360,  80, 240, 68, "compute"),
    node("accept",  "202 Accepted\njobId returned",     680,  80, 220, 68, "web"),
    node("queue",   "Storage Queue\ncontent-jobs",      360, 215, 240, 78, "queue"),
    node("discover","Discover\nMS Learn",               360, 360, 180, 78, "compute"),
    node("index",   "Index\nAI Search",                 580, 360, 160, 78, "compute"),
    node("generate","Generate episodes\nOpenAI + Speech", 780, 360, 230, 78, "compute"),
    node("publish", "Publish index\nCosmos + Blob",    1050, 360, 190, 78, "data"),
    node("progress","Cosmos jobs container\nstatus + progress", 560, 520, 290, 78, "data"),
    node("poll",    "Admin UI polls\n/api/admin/jobs/{id}",     900, 520, 270, 78, "web"),
]
FLOW_EDGES = [
    edge("browser",  "post",     280, 114,  360, 114),
    edge("post",     "accept",   600, 114,  680, 114),
    edge("post",     "queue",    480, 148,  480, 215),
    edge("queue",    "discover", 450, 293,  450, 360),
    edge("discover", "index",    540, 399,  580, 399),
    edge("index",    "generate", 740, 399,  780, 399),
    edge("generate", "publish", 1010, 399, 1050, 399),
    edge("generate", "progress", 830, 438,  760, 520),
    edge("progress", "poll",     850, 559,  900, 559, dashed=True),
]
FLOW_SIZE = (1320, 660)


# --------------------------------------------------------------- excalidraw out
def _base(nid, seed):
    return {
        "id": nid, "angle": 0, "strokeColor": INK, "fillStyle": "solid",
        "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1, "opacity": 100,
        "groupIds": [], "frameId": None, "seed": seed, "version": 1,
        "versionNonce": seed, "isDeleted": False, "updated": 1, "link": None,
        "locked": False,
    }


def to_excalidraw(title, nodes, edges):
    elements = []
    seed = 1000

    t = _base("title", seed)
    seed += 1
    t.update({
        "type": "text", "x": 60, "y": 20, "width": 640, "height": 30,
        "backgroundColor": "transparent", "roundness": None, "boundElements": [],
        "text": title, "fontSize": 24, "fontFamily": 1, "textAlign": "left",
        "verticalAlign": "top", "containerId": None, "originalText": title,
        "lineHeight": 1.25,
    })
    elements.append(t)

    for n in nodes:
        tid = f"{n['id']}-label"
        rect = _base(n["id"], seed)
        seed += 1
        rect.update({
            "type": "rectangle", "x": n["x"], "y": n["y"],
            "width": n["w"], "height": n["h"],
            "backgroundColor": n["fill"], "roundness": {"type": 3},
            "boundElements": [{"type": "text", "id": tid}],
        })
        elements.append(rect)

        lines = n["label"].split("\n")
        txt = _base(tid, seed)
        seed += 1
        txt.update({
            "type": "text", "x": n["x"] + 12, "y": n["y"] + 14,
            "width": n["w"] - 24, "height": 20 * len(lines),
            "backgroundColor": "transparent", "roundness": None,
            "boundElements": [], "text": n["label"], "fontSize": 16,
            "fontFamily": 1, "textAlign": "center", "verticalAlign": "middle",
            "containerId": n["id"], "originalText": n["label"],
            "lineHeight": 1.25,
        })
        elements.append(txt)

    for e in edges:
        arr = _base(f"arrow-{e['src']}-{e['dst']}", seed)
        seed += 1
        arr.update({
            "type": "arrow", "x": e["sx"], "y": e["sy"],
            "width": abs(e["dx"] - e["sx"]), "height": abs(e["dy"] - e["sy"]),
            "backgroundColor": "transparent", "roundness": {"type": 2},
            "boundElements": [],
            "strokeStyle": "dashed" if e["dashed"] else "solid",
            "points": [[0, 0], [e["dx"] - e["sx"], e["dy"] - e["sy"]]],
            "lastCommittedPoint": None,
            "startBinding": {"elementId": e["src"], "focus": 0, "gap": 4},
            "endBinding": {"elementId": e["dst"], "focus": 0, "gap": 4},
            "startArrowhead": None, "endArrowhead": "arrow",
        })
        elements.append(arr)

    return {
        "type": "excalidraw", "version": 2, "source": "certaudio/scripts/build-diagrams.py",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }


# ---------------------------------------------------------------------- svg out
def to_svg(title, nodes, edges, size):
    w, h = size
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="DejaVu Sans, Segoe UI, sans-serif">',
        '<defs>',
        '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{INK}"/></marker>',
        '</defs>',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="60" y="44" font-size="24" font-weight="600" fill="{INK}">{title}</text>',
    ]

    for e in edges:
        dash = ' stroke-dasharray="7 5"' if e["dashed"] else ""
        out.append(
            f'<line x1="{e["sx"]}" y1="{e["sy"]}" x2="{e["dx"]}" y2="{e["dy"]}" '
            f'stroke="{INK}" stroke-width="2"{dash} marker-end="url(#ah)"/>'
        )

    for n in nodes:
        cx = n["x"] + n["w"] / 2
        out.append(
            f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" '
            f'rx="10" ry="10" fill="{n["fill"]}" stroke="{INK}" stroke-width="2"/>'
        )
        lines = n["label"].split("\n")
        total = len(lines)
        for i, line in enumerate(lines):
            cy = n["y"] + n["h"] / 2 + (i - (total - 1) / 2) * 20 + 6
            weight = "600" if i == 0 else "400"
            fill = INK if i == 0 else MUTED
            out.append(
                f'<text x="{cx}" y="{cy}" font-size="16" font-weight="{weight}" '
                f'fill="{fill}" text-anchor="middle">{line}</text>'
            )

    out.append("</svg>")
    return "\n".join(out)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, title, nodes, edges, size in [
        ("architecture", ARCH_TITLE, ARCH_NODES, ARCH_EDGES, ARCH_SIZE),
        ("generation-flow", FLOW_TITLE, FLOW_NODES, FLOW_EDGES, FLOW_SIZE),
    ]:
        (OUT / f"{name}.excalidraw").write_text(
            json.dumps(to_excalidraw(title, nodes, edges), indent=2) + "\n",
            encoding="utf-8",
        )
        (OUT / f"{name}.svg").write_text(
            to_svg(title, nodes, edges, size) + "\n", encoding="utf-8"
        )
        print(f"wrote {name}.excalidraw and {name}.svg")


if __name__ == "__main__":
    main()
