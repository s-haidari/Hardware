"""
render.py — render a KiCad ``.kicad_mod`` footprint to SVG (no Qt, no raster).

The s-expr parser is ported from tools/fp_render.py; the Qt raster rendering is
replaced with SVG generation (resolution-independent and web-native).
"""
from __future__ import annotations

import math
import re

_LAYER_COLOR = {
    "cu": "#c98a3a",       # copper
    "crtyd": "#d24f4f",    # courtyard
    "fab": "#4f9ed2",      # fab
    "silk": "#d8dce3",     # silkscreen
    "other": "#7f8794",
}


def _tokenize(s: str):
    return re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+', s)


def parse_sexpr(text: str):
    tokens = _tokenize(text)
    pos = [0]

    def parse():
        node = []
        while pos[0] < len(tokens):
            t = tokens[pos[0]]
            pos[0] += 1
            if t == "(":
                node.append(parse())
            elif t == ")":
                return node
            else:
                node.append(t[1:-1] if (t.startswith('"') and t.endswith('"')) else t)
        return node

    while pos[0] < len(tokens) and tokens[pos[0]] != "(":
        pos[0] += 1
    if pos[0] >= len(tokens):
        return []
    pos[0] += 1
    return parse()


def _find(node, head):
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None


def _findall(node, head):
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _layer_color(layer: str) -> str:
    if layer.endswith(".Cu"):
        return _LAYER_COLOR["cu"]
    if "CrtYd" in layer:
        return _LAYER_COLOR["crtyd"]
    if "Fab" in layer:
        return _LAYER_COLOR["fab"]
    if "SilkS" in layer:
        return _LAYER_COLOR["silk"]
    return _LAYER_COLOR["other"]


class Footprint:
    def __init__(self, root):
        self.pads: list = []
        self.lines: list = []
        self.circles: list = []
        self.rects: list = []
        self.polys: list = []
        self._parse(root)

    def _parse(self, r):
        for pad in _findall(r, "pad"):
            try:
                ptype, shape = pad[2], pad[3]
                at = _find(pad, "at")
                x, y = _f(at[1]), _f(at[2])
                rot = _f(at[3]) if len(at) > 3 else 0.0
                size = _find(pad, "size")
                w, h = _f(size[1]), _f(size[2])
                drill = _find(pad, "drill")
                dr = 0.0
                if drill:
                    vals = [_f(v) for v in drill[1:] if re.match(r"-?\d", str(v))]
                    dr = max(vals) if vals else 0.0
                self.pads.append((shape, x, y, w, h, rot, dr, ptype))
            except Exception:
                continue
        for ln in _findall(r, "fp_line") + _findall(r, "gr_line"):
            s, e, lay = _find(ln, "start"), _find(ln, "end"), _find(ln, "layer")
            if s and e:
                self.lines.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2]), lay[1] if lay else ""))
        for c in _findall(r, "fp_circle") + _findall(r, "gr_circle"):
            ctr, end, lay = _find(c, "center"), _find(c, "end"), _find(c, "layer")
            if ctr and end:
                cx, cy = _f(ctr[1]), _f(ctr[2])
                self.circles.append((cx, cy, math.hypot(_f(end[1]) - cx, _f(end[2]) - cy), lay[1] if lay else ""))
        for rc in _findall(r, "fp_rect") + _findall(r, "gr_rect"):
            s, e, lay = _find(rc, "start"), _find(rc, "end"), _find(rc, "layer")
            if s and e:
                self.rects.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2]), lay[1] if lay else ""))
        for pol in _findall(r, "fp_poly") + _findall(r, "gr_poly"):
            pts, lay = _find(pol, "pts"), _find(pol, "layer")
            if pts:
                pp = [(_f(xy[1]), _f(xy[2])) for xy in _findall(pts, "xy")]
                if pp:
                    self.polys.append((pp, lay[1] if lay else ""))

    def bbox(self):
        xs, ys = [], []
        for (_s, x, y, w, h, _r, _d, _t) in self.pads:
            xs += [x - w / 2, x + w / 2]; ys += [y - h / 2, y + h / 2]
        for (x1, y1, x2, y2, _l) in self.lines + self.rects:
            xs += [x1, x2]; ys += [y1, y2]
        for (cx, cy, rr, _l) in self.circles:
            xs += [cx - rr, cx + rr]; ys += [cy - rr, cy + rr]
        for (pp, _l) in self.polys:
            xs += [p[0] for p in pp]; ys += [p[1] for p in pp]
        if not xs:
            return (-1.0, -1.0, 1.0, 1.0)
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def pad_count(self) -> int:
        return len(self.pads)


def _e(v) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def footprint_svg(text: str, size: int = 320, pad: float = 0.5) -> str:
    fp = Footprint(parse_sexpr(text))
    x0, y0, x1, y1 = fp.bbox()
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
    w = max(x1 - x0, 0.1); h = max(y1 - y0, 0.1)
    sw = 0.08 * max(w, h) / 10  # stroke ~ scales with size

    el: list[str] = []
    # copper pads first
    for (shape, x, y, pw, ph, rot, dr, _t) in fp.pads:
        tr = f' transform="rotate({_e(rot)} {_e(x)} {_e(y)})"' if rot else ""
        col = _LAYER_COLOR["cu"]
        if shape == "circle":
            el.append(f'<circle cx="{_e(x)}" cy="{_e(y)}" r="{_e(pw/2)}" fill="{col}"{tr}/>')
        else:
            rx = min(pw, ph) * (0.25 if shape == "roundrect" else (0.5 if shape == "oval" else 0))
            el.append(f'<rect x="{_e(x-pw/2)}" y="{_e(y-ph/2)}" width="{_e(pw)}" height="{_e(ph)}" '
                      f'rx="{_e(rx)}" fill="{col}"{tr}/>')
        if dr > 0:
            el.append(f'<circle cx="{_e(x)}" cy="{_e(y)}" r="{_e(dr/2)}" fill="#15171a"/>')
    # graphic layers
    for (a, b, c, d, lay) in fp.lines:
        el.append(f'<line x1="{_e(a)}" y1="{_e(b)}" x2="{_e(c)}" y2="{_e(d)}" '
                  f'stroke="{_layer_color(lay)}" stroke-width="{_e(sw)}"/>')
    for (a, b, c, d, lay) in fp.rects:
        el.append(f'<rect x="{_e(min(a,c))}" y="{_e(min(b,d))}" width="{_e(abs(c-a))}" height="{_e(abs(d-b))}" '
                  f'fill="none" stroke="{_layer_color(lay)}" stroke-width="{_e(sw)}"/>')
    for (cx, cy, rr, lay) in fp.circles:
        el.append(f'<circle cx="{_e(cx)}" cy="{_e(cy)}" r="{_e(rr)}" fill="none" '
                  f'stroke="{_layer_color(lay)}" stroke-width="{_e(sw)}"/>')
    for (pp, lay) in fp.polys:
        pts = " ".join(f"{_e(px)},{_e(py)}" for px, py in pp)
        el.append(f'<polygon points="{pts}" fill="none" stroke="{_layer_color(lay)}" stroke-width="{_e(sw)}"/>')

    height = int(size * h / w)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_e(x0)} {_e(y0)} {_e(w)} {_e(h)}" '
        f'width="{size}" height="{height}">{"".join(el)}</svg>'
    )
