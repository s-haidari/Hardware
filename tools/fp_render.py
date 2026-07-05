#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fp_render.py — render KiCad footprints (and symbols) to images, plus extract a
machine-readable summary, with no external CAD dependency. Used for the Contents
preview pane and the one-file library catalog.

A footprint .kicad_mod is parsed as an S-expression and drawn with QPainter:
pads (copper), courtyard, fab, and silkscreen each in their own layer colour.
QImage rendering works headlessly (no display needed) so the catalog can be
generated in the background.
"""
import re
import math
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt5.QtGui import QImage, QPainter, QColor, QPen, QBrush, QPolygonF, QFont
from PyQt5.QtCore import QPointF, QRectF, Qt

# Layer colours on the dark viewport background
BG = QColor("#14161a")
COL_COPPER = QColor("#c2913e")
COL_HOLE = QColor("#14161a")
COL_SILK = QColor("#d9dee5")
COL_CRTYD = QColor("#7f8aa0")
COL_FAB = QColor("#5b6673")
COL_OTHER = QColor("#8a93a3")


def _tokenize(s: str):
    return re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+', s)


def parse_sexpr(text: str):
    """Parse the first top-level (…) into a nested list. Quoted strings are
    unquoted; everything else stays a token string."""
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


def _stroke_width(node, default: float = 0.1) -> float:
    """Graphic line width from a flat ``(width X)`` or a nested
    ``(stroke (width X))``. KiCad 7/8/9 moved the width into a ``(stroke …)``
    sub-list; older files keep it flat. Returns ``default`` when neither carries
    a numeric width (so a malformed flat width still falls through to stroke)."""
    flat = _find(node, "width")
    if flat is not None and len(flat) > 1:
        try:
            return float(flat[1])
        except (TypeError, ValueError):
            pass
    stroke = _find(node, "stroke")
    if stroke is not None:
        sw = _find(stroke, "width")
        if sw is not None and len(sw) > 1:
            try:
                return float(sw[1])
            except (TypeError, ValueError):
                pass
    return default


def _arc_polyline(start, mid, end, segs: int = 24):
    """Sample the circular arc through the three model-space points
    ``start → mid → end`` into ``segs``+1 polyline points. Falls back to the
    chord ``[start, mid, end]`` when the points are collinear (no finite
    circle). Used for both footprint ``fp_arc`` and symbol ``arc`` curves so
    silk/body arcs render as real curves instead of straight chords."""
    (x1, y1), (xm, ym), (x2, y2) = start, mid, end
    d = 2.0 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
    if abs(d) < 1e-12:
        return [start, mid, end]
    s1 = x1 * x1 + y1 * y1
    sm = xm * xm + ym * ym
    s2 = x2 * x2 + y2 * y2
    cx = (s1 * (ym - y2) + sm * (y2 - y1) + s2 * (y1 - ym)) / d
    cy = (s1 * (x2 - xm) + sm * (x1 - x2) + s2 * (xm - x1)) / d
    r = math.hypot(x1 - cx, y1 - cy)
    a1 = math.atan2(y1 - cy, x1 - cx)
    am = math.atan2(ym - cy, xm - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    two_pi = 2.0 * math.pi
    total = (a2 - a1) % two_pi            # CCW span start → end
    mid_ccw = (am - a1) % two_pi
    sweep = total if mid_ccw <= total + 1e-9 else total - two_pi
    return [(cx + r * math.cos(a1 + sweep * i / segs),
             cy + r * math.sin(a1 + sweep * i / segs)) for i in range(segs + 1)]


def _arc_polyline_center(center, start, angle_deg: float, segs: int = 24):
    """Legacy KiCad footprint arc: ``start`` is the centre, ``end`` the arc's
    first point, ``angle_deg`` the swept angle. Sample into ``segs``+1 points."""
    cx, cy = center
    sx, sy = start
    r = math.hypot(sx - cx, sy - cy)
    a1 = math.atan2(sy - cy, sx - cx)
    sweep = math.radians(angle_deg)
    return [(cx + r * math.cos(a1 + sweep * i / segs),
             cy + r * math.sin(a1 + sweep * i / segs)) for i in range(segs + 1)]


def _layer_color(layer: str) -> QColor:
    if layer.endswith(".Cu"):
        return COL_COPPER
    if "CrtYd" in layer:
        return COL_CRTYD
    if "Fab" in layer:
        return COL_FAB
    if "SilkS" in layer:
        return COL_SILK
    return COL_OTHER


class _Footprint:
    def __init__(self, root):
        self.root = root
        self.name = root[1] if len(root) > 1 and isinstance(root[1], str) else "footprint"
        self.pads = []          # (shape, x, y, w, h, rot, drill, ptype)
        self.lines = []         # (x1, y1, x2, y2, layer, width)
        self.circles = []       # (cx, cy, r, layer, width)
        self.rects = []         # (x1, y1, x2, y2, layer, width)
        self.polys = []         # (points[list of (x,y)], layer, width)
        self.arcs = []          # (points[list of (x,y)], layer, width)
        self._parse()

    def _parse(self):
        r = self.root
        for pad in _findall(r, "pad"):
            try:
                num = str(pad[1]) if len(pad) > 1 else ""
                ptype = pad[2]
                shape = pad[3]
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
                self.pads.append((shape, x, y, w, h, rot, dr, ptype, num))
            except Exception:
                continue
        for ln in _findall(r, "fp_line") + _findall(r, "gr_line"):
            s, e = _find(ln, "start"), _find(ln, "end")
            lay = _find(ln, "layer")
            if s and e:
                self.lines.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2]),
                                   lay[1] if lay else "", _stroke_width(ln)))
        for c in _findall(r, "fp_circle") + _findall(r, "gr_circle"):
            ctr, end = _find(c, "center"), _find(c, "end")
            lay = _find(c, "layer")
            if ctr and end:
                cx, cy = _f(ctr[1]), _f(ctr[2])
                rad = math.hypot(_f(end[1]) - cx, _f(end[2]) - cy)
                self.circles.append((cx, cy, rad, lay[1] if lay else "", _stroke_width(c)))
        for rc in _findall(r, "fp_rect") + _findall(r, "gr_rect"):
            s, e = _find(rc, "start"), _find(rc, "end")
            lay = _find(rc, "layer")
            if s and e:
                self.rects.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2]),
                                   lay[1] if lay else "", _stroke_width(rc)))
        for pol in _findall(r, "fp_poly") + _findall(r, "gr_poly"):
            pts = _find(pol, "pts")
            lay = _find(pol, "layer")
            if pts:
                pp = [(_f(xy[1]), _f(xy[2])) for xy in _findall(pts, "xy")]
                if pp:
                    self.polys.append((pp, lay[1] if lay else "", _stroke_width(pol)))
        for ar in _findall(r, "fp_arc") + _findall(r, "gr_arc"):
            lay = _find(ar, "layer")
            layer = lay[1] if lay else ""
            s, m, e = _find(ar, "start"), _find(ar, "mid"), _find(ar, "end")
            ang = _find(ar, "angle")
            pts = None
            if s and m and e:                    # KiCad 6+ three-point arc
                pts = _arc_polyline((_f(s[1]), _f(s[2])), (_f(m[1]), _f(m[2])),
                                    (_f(e[1]), _f(e[2])))
            elif s and e and ang:                # legacy centre + swept angle
                pts = _arc_polyline_center((_f(s[1]), _f(s[2])),
                                           (_f(e[1]), _f(e[2])), _f(ang[1]))
            if pts:
                self.arcs.append((pts, layer, _stroke_width(ar)))

    def bbox(self) -> Tuple[float, float, float, float]:
        xs, ys = [], []
        for (_s, x, y, w, h, _r, _d, _t, _n) in self.pads:
            xs += [x - w / 2, x + w / 2]
            ys += [y - h / 2, y + h / 2]
        for (x1, y1, x2, y2, _l, _w) in self.lines + self.rects:
            xs += [x1, x2]; ys += [y1, y2]
        for (cx, cy, rr, _l, _w) in self.circles:
            xs += [cx - rr, cx + rr]; ys += [cy - rr, cy + rr]
        for (pp, _l, _w) in self.polys + self.arcs:
            xs += [p[0] for p in pp]; ys += [p[1] for p in pp]
        if not xs:
            return (-1, -1, 1, 1)
        return (min(xs), min(ys), max(xs), max(ys))

    def body_bbox(self) -> Tuple[float, float, float, float]:
        """Bounds of the actual component body — pads + courtyard — ignoring
        stray silk/fab markers (pin-1 dots, reference outlines) that sit far
        from the body and would otherwise skew the framing."""
        xs, ys = [], []
        for (_s, x, y, w, h, _r, _d, _t, _n) in self.pads:
            xs += [x - w / 2, x + w / 2]
            ys += [y - h / 2, y + h / 2]
        for (x1, y1, x2, y2, lay, _w) in self.lines:
            if "CrtYd" in lay:
                xs += [x1, x2]; ys += [y1, y2]
        for (pp, lay, _w) in self.arcs:
            if "CrtYd" in lay:
                xs += [p[0] for p in pp]; ys += [p[1] for p in pp]
        if not xs:
            return self.bbox()
        return (min(xs), min(ys), max(xs), max(ys))

    def summary(self) -> dict:
        x0, y0, x1, y1 = self.body_bbox()
        layers = set()
        for coll in (self.lines, self.rects):
            for item in coll:
                layers.add(item[4])
        for (_pp, lay, _w) in self.arcs:
            layers.add(lay)
        smd = sum(1 for p in self.pads if p[7] == "smd")
        tht = len(self.pads) - smd
        return {
            "name": self.name,
            "pads": len(self.pads),
            "smd_pads": smd,
            "tht_pads": tht,
            "width_mm": round(x1 - x0, 3),
            "height_mm": round(y1 - y0, 3),
            "layers": sorted(l for l in layers if l),
        }

    def render(self, px: int = 420) -> QImage:
        img = QImage(px, px, QImage.Format_ARGB32)
        img.fill(BG)
        x0, y0, x1, y1 = self.body_bbox()
        span = max(x1 - x0, y1 - y0, 0.5)
        margin = px * 0.12
        scale = (px - 2 * margin) / span
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

        # Cap the on-screen STROKE width. `scale` is huge for tiny parts, so a real
        # 0.12 mm silk line would render as a ~40 px pen and the outlines would blob
        # over the pads. Pad FILLS below keep the true scale — only line weight is
        # capped (~8 px at 640). (Review fix: small-SMD footprints rendered as blobs.)
        def stroke_px(w):
            return min(max(w * scale, 1.0), px * 0.012)

        # outlier-rejection window: drop graphics far outside the body so stray
        # silk/fab markers don't appear as floating dots
        ex = (x1 - x0) * 0.30 + 0.3
        ey = (y1 - y0) * 0.30 + 0.3
        wx0, wy0, wx1, wy1 = x0 - ex, y0 - ey, x1 + ex, y1 + ey

        def _in(mx, my):
            return wx0 <= mx <= wx1 and wy0 <= my <= wy1

        def T(mx, my):
            return QPointF((mx - cx) * scale + px / 2, (my - cy) * scale + px / 2)

        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)

        def draw_lines(coll):
            for (x1_, y1_, x2_, y2_, lay, w) in coll:
                if not (_in(x1_, y1_) or _in(x2_, y2_)):
                    continue
                pen = QPen(_layer_color(lay)); pen.setWidthF(stroke_px(w))
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(T(x1_, y1_), T(x2_, y2_))

        def draw_arcs(coll):
            for (pp, lay, w) in coll:
                if not any(_in(x, y) for (x, y) in pp):
                    continue
                pen = QPen(_layer_color(lay)); pen.setWidthF(stroke_px(w))
                pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen); p.setBrush(Qt.NoBrush)
                p.drawPolyline(QPolygonF([T(x, y) for (x, y) in pp]))

        def draw_rects(coll):
            for (a, b, c2, d, lay, w) in coll:
                if not (_in(a, b) or _in(c2, d)):
                    continue
                pen = QPen(_layer_color(lay)); pen.setWidthF(stroke_px(w)); p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRectF(T(a, b), T(c2, d)))

        def draw_circles(coll):
            for (pcx, pcy, rr, lay, w) in coll:
                if not _in(pcx, pcy):
                    continue
                pen = QPen(_layer_color(lay)); pen.setWidthF(stroke_px(w)); p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(T(pcx, pcy), rr * scale, rr * scale)

        def draw_polys(coll):
            for (pp, lay, w) in coll:
                if not any(_in(x, y) for (x, y) in pp):
                    continue
                col = _layer_color(lay)
                p.setPen(QPen(col, stroke_px(w)))
                p.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 60)))
                p.drawPolygon(QPolygonF([T(x, y) for (x, y) in pp]))

        def _silk(lay):
            return "SilkS" in lay

        # courtyard + fab + NON-silk rect/circle/poly graphics go UNDER the pads.
        draw_lines([l for l in self.lines if "CrtYd" in l[4]])
        draw_arcs([a for a in self.arcs if "CrtYd" in a[1]])
        draw_lines([l for l in self.lines if "Fab" in l[4]])
        draw_arcs([a for a in self.arcs if "Fab" in a[1]])
        draw_rects([r for r in self.rects if not _silk(r[4])])
        draw_circles([c for c in self.circles if not _silk(c[3])])
        draw_polys([pl for pl in self.polys if not _silk(pl[1])])

        # pads (copper) on top
        label_font = QFont("Arial")
        for (shape, x, y, w, h, rot, dr, ptype, num) in self.pads:
            p.save()
            p.translate(T(x, y))
            if rot:
                p.rotate(-rot)
            p.setPen(QPen(COL_COPPER.darker(130), 1))
            p.setBrush(QBrush(COL_COPPER))
            pw, ph = w * scale, h * scale
            rect = QRectF(-pw / 2, -ph / 2, pw, ph)
            if shape in ("circle",) or (shape == "oval" and abs(w - h) < 1e-6):
                p.drawEllipse(rect)
            elif shape == "oval":
                p.drawRoundedRect(rect, min(pw, ph) / 2, min(pw, ph) / 2)
            elif shape == "roundrect":
                p.drawRoundedRect(rect, min(pw, ph) * 0.25, min(pw, ph) * 0.25)
            else:  # rect / trapezoid / custom fallback
                p.drawRect(rect)
            if dr > 0:  # through-hole
                p.setBrush(QBrush(COL_HOLE))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(0, 0), dr * scale / 2, dr * scale / 2)
            p.restore()
            # pad number, centred and upright (sized to fit even thin pads)
            fs = int(min(max(pw, ph) * 0.42, min(pw, ph) * 0.95))
            if num and fs >= 7:
                label_font.setPixelSize(min(fs, 20))
                p.setFont(label_font)
                p.setPen(QPen(QColor("#1c1407")))
                p.drawText(QRectF(T(x, y).x() - max(pw, ph) / 2, T(x, y).y() - max(pw, ph) / 2,
                                  max(pw, ph), max(pw, ph)),
                           Qt.AlignCenter, num)

        # silk last (most visible) — incl. silk rects/circles/polys so pin-1 markers
        # (dots/triangles drawn on F.SilkS) sit ON TOP of copper, not hidden under it.
        draw_lines([l for l in self.lines if "SilkS" in l[4]])
        draw_arcs([a for a in self.arcs if "SilkS" in a[1]])
        draw_rects([r for r in self.rects if _silk(r[4])])
        draw_circles([c for c in self.circles if _silk(c[3])])
        draw_polys([pl for pl in self.polys if _silk(pl[1])])
        p.end()
        return img


def load_footprint(path: Path) -> Optional[_Footprint]:
    try:
        root = parse_sexpr(Path(path).read_text(encoding="utf-8", errors="replace"))
        if not root or root[0] not in ("footprint", "module"):
            return None
        return _Footprint(root)
    except Exception:
        return None


_IMG_CACHE: dict = {}           # render cache; keyed per-kind below (LRU)
_IMG_CACHE_MAX = 48


def _img_cache_get(key):
    if key in _IMG_CACHE:
        _IMG_CACHE[key] = _IMG_CACHE.pop(key)
        return _IMG_CACHE[key]
    return None


def _img_cache_put(key, img):
    if img is None:
        return
    _IMG_CACHE[key] = img
    while len(_IMG_CACHE) > _IMG_CACHE_MAX:
        _IMG_CACHE.pop(next(iter(_IMG_CACHE)))


def render_footprint_image(path: Path, px: int = 420) -> Optional[QImage]:
    try:
        key = ("fp", str(path), Path(path).stat().st_mtime, px)
    except OSError:
        key = None
    if key is not None:
        hit = _img_cache_get(key)
        if hit is not None:
            return hit
    fp = load_footprint(path)
    img = fp.render(px) if fp else None
    if key is not None:
        _img_cache_put(key, img)
    return img


def footprint_summary(path: Path) -> Optional[dict]:
    fp = load_footprint(path)
    return fp.summary() if fp else None


# ---------------------------------------------------------------------------
# Symbol rendering — parse a .kicad_sym (symbol …) block and draw the body
# graphics + pins, the way the schematic editor shows it. Y is up in symbols,
# so it is flipped for display.
# ---------------------------------------------------------------------------
COL_SYMBODY = QColor("#c9a063")
COL_SYMPIN = QColor("#9fb0c8")


def render_symbol_image(block_text: str, px: int = 280) -> Optional[QImage]:
    key = ("sym", hash(block_text), px)
    hit = _img_cache_get(key)
    if hit is not None:
        return hit
    img = _render_symbol_image_uncached(block_text, px)
    _img_cache_put(key, img)
    return img


def _render_symbol_image_uncached(block_text: str, px: int = 280) -> Optional[QImage]:
    try:
        root = parse_sexpr(block_text)
        if not root or root[0] != "symbol":
            return None
        rects, polys, circs, arcs, pins = [], [], [], [], []

        def walk(node):
            for c in node:
                if not (isinstance(c, list) and c):
                    continue
                h = c[0]
                if h == "rectangle":
                    s, e = _find(c, "start"), _find(c, "end")
                    if s and e:
                        rects.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2])))
                elif h == "polyline":
                    pts = _find(c, "pts")
                    if pts:
                        polys.append([(_f(xy[1]), _f(xy[2])) for xy in _findall(pts, "xy")])
                elif h == "circle":
                    ctr, rad = _find(c, "center"), _find(c, "radius")
                    if ctr and rad:
                        circs.append((_f(ctr[1]), _f(ctr[2]), _f(rad[1])))
                elif h == "arc":
                    s, m, e = _find(c, "start"), _find(c, "mid"), _find(c, "end")
                    if s and e:
                        start = (_f(s[1]), _f(s[2]))
                        end = (_f(e[1]), _f(e[2]))
                        mid = (_f(m[1]), _f(m[2])) if m else None
                        pts = _arc_polyline(start, mid, end) if mid else [start, end]
                        arcs.append(pts)
                elif h == "pin":
                    at, ln = _find(c, "at"), _find(c, "length")
                    numf = _find(c, "number")
                    if at:
                        ang = _f(at[3]) if len(at) > 3 else 0.0
                        num = str(numf[1]) if numf and len(numf) > 1 else ""
                        pins.append((_f(at[1]), _f(at[2]), ang, _f(ln[1]) if ln else 2.54, num))
                walk(c)

        walk(root)
        xs, ys = [], []
        for (a, b, c2, d) in rects:
            xs += [a, c2]; ys += [b, d]
        for pp in polys:
            xs += [p[0] for p in pp]; ys += [p[1] for p in pp]
        for (cx, cy, r) in circs:
            xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
        for pp in arcs:
            xs += [pt[0] for pt in pp]; ys += [pt[1] for pt in pp]
        for (x, y, ang, ln, num) in pins:
            ex = x + ln * math.cos(math.radians(ang))
            ey = y + ln * math.sin(math.radians(ang))
            xs += [x, ex]; ys += [y, ey]
        if not xs:
            return None
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        span = max(x1 - x0, y1 - y0, 2.54)
        margin = px * 0.14
        scale = (px - 2 * margin) / span
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

        def T(mx, my):                          # flip Y (schematic Y is up)
            return QPointF((mx - cx) * scale + px / 2, (cy - my) * scale + px / 2)

        img = QImage(px, px, QImage.Format_ARGB32)
        img.fill(BG)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        pin_font = QFont("Arial")
        pin_font.setPixelSize(max(int(min(px, px) * 0.035), 8))
        show_nums = len(pins) <= 40
        for (x, y, ang, ln, num) in pins:       # pins first (under body)
            ex = x + ln * math.cos(math.radians(ang))
            ey = y + ln * math.sin(math.radians(ang))
            p.setPen(QPen(COL_SYMPIN, 1.6)); p.drawLine(T(x, y), T(ex, ey))
            p.setPen(Qt.NoPen); p.setBrush(QBrush(COL_SYMPIN))
            p.drawEllipse(T(x, y), 2.2, 2.2)
            if num and show_nums:               # number near the body end
                mid = T(x + ln * 0.62 * math.cos(math.radians(ang)),
                        y + ln * 0.62 * math.sin(math.radians(ang)))
                p.setPen(QPen(QColor("#d9dee5"))); p.setFont(pin_font)
                p.drawText(QRectF(mid.x() - 14, mid.y() - 9, 28, 18), Qt.AlignCenter, num)
        p.setBrush(QBrush(QColor(201, 160, 99, 28)))
        for (a, b, c2, d) in rects:
            p.setPen(QPen(COL_SYMBODY, 2)); p.drawRect(QRectF(T(a, b), T(c2, d)))
        p.setBrush(Qt.NoBrush)
        for pp in polys:
            p.setPen(QPen(COL_SYMBODY, 2)); p.drawPolyline(QPolygonF([T(x, y) for (x, y) in pp]))
        for (ccx, ccy, r) in circs:
            p.setPen(QPen(COL_SYMBODY, 2)); p.drawEllipse(T(ccx, ccy), r * scale, r * scale)
        p.setBrush(Qt.NoBrush)
        for pp in arcs:
            p.setPen(QPen(COL_SYMBODY, 2))
            p.drawPolyline(QPolygonF([T(x, y) for (x, y) in pp]))
        p.end()
        return img
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3D model rendering — dispatched by file suffix:
#   * STEP/STP  -> mesh via cascadio (OpenCASCADE, the SnapMagic approach)
#   * WRL/VRML  -> mesh via the built-in VRML IndexedFaceSet reader below
# then a small software rasteriser draws a shaded thumbnail. All local, no
# display required. Degrades gracefully if a backend / format isn't available.
#
# KiCad's own 3D library ships .wrl by default, so feeding those into cascadio's
# STEP reader (as the previous STEP-only path did) raised and returned None —
# most models rendered blank. trimesh has no VRML loader and VTK isn't a project
# dependency, so WRL is parsed here with a tiny dependency-free reader (numpy
# only) that pulls each Shape's IndexedFaceSet geometry.
# ---------------------------------------------------------------------------
import logging

_log = logging.getLogger(__name__)

STEP_SUFFIXES = (".step", ".stp")
VRML_SUFFIXES = (".wrl", ".vrml")


def model_format(path) -> str:
    """Classify a 3D model path by suffix: 'step', 'vrml', or 'unsupported'."""
    suf = Path(path).suffix.lower()
    if suf in STEP_SUFFIXES:
        return "step"
    if suf in VRML_SUFFIXES:
        return "vrml"
    return "unsupported"


def _have_numpy() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def have_3d() -> bool:
    """STEP backend (cascadio + trimesh + numpy) availability. VRML needs only
    numpy — see :func:`_have_numpy`."""
    try:
        import cascadio  # noqa: F401
        import trimesh   # noqa: F401
        import numpy     # noqa: F401
        return True
    except Exception:
        return False


# --- VRML / .wrl reader ----------------------------------------------------
_VRML_NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _vrml_indexed_face_sets(text: str):
    """Yield (point_str, coord_index_str) for every IndexedFaceSet block in a
    VRML2 (.wrl) document, using brace matching to bound each block so the
    coordIndex and its coord/point stay paired per-Shape."""
    n = len(text)
    for m in re.finditer(r"IndexedFaceSet", text):
        b = text.find("{", m.end())
        if b < 0:
            continue
        depth = 0
        end = -1
        i = b
        while i < n:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end < 0:
            continue
        block = text[b:end + 1]
        pm = re.search(r"\bpoint\s*\[", block)
        cm = re.search(r"\bcoordIndex\s*\[", block)
        if not pm or not cm:
            continue
        pe = block.find("]", pm.end())
        ce = block.find("]", cm.end())
        if pe < 0 or ce < 0:
            continue
        yield block[pm.end():pe], block[cm.end():ce]


def parse_vrml(text: str):
    """Parse VRML2/.wrl IndexedFaceSet geometry into (verts Nx3, faces Mx3)
    numpy arrays. Each Shape carries a flat ``point [x y z, …]`` list and a
    ``coordIndex [i j k -1, …]`` list of 0-based indices, ``-1`` terminating a
    face; polygons are fan-triangulated and per-Shape index bases are offset so
    multiple Shapes concatenate correctly. Returns (None, None) if empty."""
    import numpy as np
    all_v = []
    all_f = []
    offset = 0
    for point_str, index_str in _vrml_indexed_face_sets(text):
        coords = [float(x) for x in _VRML_NUM.findall(point_str)]
        n = (len(coords) // 3) * 3
        if n < 9:                                   # need >= 3 vertices
            continue
        pv = np.asarray(coords[:n], float).reshape(-1, 3)
        idx = [int(x) for x in re.findall(r"-?\d+", index_str)]
        face = []

        def _flush(face):
            if len(face) >= 3:
                for k in range(1, len(face) - 1):
                    all_f.append((face[0] + offset,
                                  face[k] + offset,
                                  face[k + 1] + offset))

        for vi in idx:
            if vi < 0:                              # -1 ends the current polygon
                _flush(face)
                face = []
            elif 0 <= vi < len(pv):
                face.append(vi)
        _flush(face)                                # trailing face without -1
        all_v.append(pv)
        offset += len(pv)
    if not all_v or not all_f:
        return None, None
    return np.vstack(all_v), np.asarray(all_f, int)


def load_vrml_mesh(path):
    """Load a .wrl/.vrml model to (verts, faces). Pure-Python VRML reader (needs
    only numpy) so it works without cascadio/OpenCASCADE. (None, None) on error."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return parse_vrml(text)
    except Exception:
        return None, None


import contextlib


@contextlib.contextmanager
def _suppress_native_stderr():
    """Silence OpenCASCADE's C-level chatter (it writes skipped-node messages
    to stdout). Redirects both fd 1 and fd 2 for the duration."""
    import os
    import sys
    saved = []
    devnull = None
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        for stream in (sys.stdout, sys.stderr):
            try:
                fd = stream.fileno()
            except Exception:
                continue
            saved.append((fd, os.dup(fd)))
            os.dup2(devnull, fd)
        yield
    finally:
        for fd, dup in saved:
            try:
                os.dup2(dup, fd)
                os.close(dup)
            except Exception:
                pass
        if devnull is not None:
            os.close(devnull)


def load_step_mesh(step_path: Path):
    """Return (vertices Nx3, faces Mx3) numpy arrays, or (None, None).

    Dispatches on the file suffix: STEP/STP go through cascadio+OpenCASCADE,
    WRL/VRML through the built-in VRML reader. Unsupported suffixes are logged
    (not silently swallowed) and return (None, None) — so a .wrl model is no
    longer fed into the STEP reader (which raised → blank preview)."""
    # mesh cache: STEP -> glb conversion takes seconds; re-selecting the same part
    # should not re-run OpenCASCADE. Keyed by (path, mtime) so edits invalidate.
    try:
        key = (str(step_path), Path(step_path).stat().st_mtime)
    except OSError:
        key = None
    if key is not None and key in _MESH_CACHE:
        _MESH_CACHE[key] = _MESH_CACHE.pop(key)      # LRU refresh
        return _MESH_CACHE[key]

    fmt = model_format(step_path)
    if fmt == "vrml":
        vf = load_vrml_mesh(step_path)
        _mesh_cache_put(key, vf)
        return vf
    if fmt != "step":
        _log.warning("fp_render: unsupported 3D model format %r (%s)",
                     Path(step_path).suffix, step_path)
        return None, None
    import os
    import tempfile
    import cascadio
    import trimesh
    import numpy as np
    fd, glb = tempfile.mkstemp(suffix=".glb")        # own the handle; close before use
    os.close(fd)
    try:
        with _suppress_native_stderr():
            cascadio.step_to_glb(str(step_path), glb, tol_linear=0.05, tol_angular=0.3)
        scene = trimesh.load(glb)
        if hasattr(scene, "to_geometry"):
            mesh = scene.to_geometry()
        elif hasattr(scene, "dump"):
            mesh = scene.dump(concatenate=True)
        else:
            mesh = scene
        vf = np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int)
        _mesh_cache_put(key, vf)
        return vf
    finally:
        try:
            os.unlink(glb)
        except Exception:
            pass


_MESH_CACHE: dict = {}          # (path, mtime) -> (verts, faces); insertion-ordered LRU
_MESH_CACHE_MAX = 6             # meshes can be large; keep the working set small


def _mesh_cache_put(key, vf):
    if key is None or vf[0] is None:
        return
    _MESH_CACHE[key] = vf
    while len(_MESH_CACHE) > _MESH_CACHE_MAX:
        _MESH_CACHE.pop(next(iter(_MESH_CACHE)))


_load_step_mesh = load_step_mesh   # backward-compatible alias


def paint_mesh(painter, w: int, h: int, verts, faces,
               rot_x: float = -60.0, rot_y: float = -35.0, zoom: float = 1.0):
    """Software-rasterise a shaded mesh onto `painter` filling a w×h area. Used
    both for the static thumbnail and the interactive viewer (re-called on drag)."""
    import numpy as np
    v = np.asarray(verts, float)
    v = v - (v.max(0) + v.min(0)) / 2.0
    ax, ay = math.radians(rot_x), math.radians(rot_y)
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(ax), -math.sin(ax)],
                   [0, math.sin(ax), math.cos(ax)]])
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)],
                   [0, 1, 0],
                   [-math.sin(ay), 0, math.cos(ay)]])
    vr = v @ (Rx @ Ry).T

    proj = vr[:, :2]
    pmin, pmax = proj.min(0), proj.max(0)
    ctr = (pmin + pmax) / 2.0
    side = min(w, h)
    margin = side * 0.12
    s = (side - 2 * margin) / max(float((pmax - pmin).max()), 1e-6) * zoom

    tris = vr[faces]
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    nlen = np.linalg.norm(normals, axis=1)
    nlen[nlen == 0] = 1.0
    normals = normals / nlen[:, None]
    light = np.array([0.35, 0.45, 0.82]); light /= np.linalg.norm(light)
    ndotl = normals @ light
    shade = np.clip(0.26 + 0.74 * np.clip(ndotl, 0.0, 1.0), 0.0, 1.0)
    depth = tris[:, :, 2].mean(1)
    order = np.argsort(depth)
    front = normals[:, 2] > 0
    order = order[front[order]]
    if len(order) < 4:
        order = np.argsort(depth)
        shade = np.clip(0.26 + 0.74 * np.abs(ndotl), 0.0, 1.0)

    cxpx, cypx = w / 2.0, h / 2.0

    def to2d(pt):
        return QPointF((pt[0] - ctr[0]) * s + cxpx, (pt[1] - ctr[1]) * s + cypx)

    base = (198, 204, 212)
    for i in order:
        sh = shade[i]
        col = QColor(int(base[0] * sh), int(base[1] * sh), int(base[2] * sh))
        poly = QPolygonF([to2d(tris[i][0]), to2d(tris[i][1]), to2d(tris[i][2])])
        pen = QPen(col); pen.setWidthF(0.7)
        painter.setPen(pen); painter.setBrush(QBrush(col))
        painter.drawPolygon(poly)


def _model_backend_ready(fmt: str) -> bool:
    """True when the backend for this format is importable: STEP needs the full
    cascadio stack, VRML needs only numpy."""
    if fmt == "step":
        return have_3d()
    if fmt == "vrml":
        return _have_numpy()
    return False


def step_summary(step_path: Path) -> Optional[dict]:
    """Size/triangle summary for a STEP or WRL model (None if unavailable or
    unsupported)."""
    fmt = model_format(step_path)
    if not _model_backend_ready(fmt):
        return None
    try:
        v, f = _load_step_mesh(step_path)
        if v is None or f is None or len(v) == 0:
            return None
        dims = v.max(0) - v.min(0)
        # glTF/GLB from cascadio is in metres; convert to mm for display. VRML
        # geometry is already in model units, so it's left untouched.
        if fmt == "step" and float(dims.max()) < 1.0:
            dims = dims * 1000.0
        return {"triangles": int(len(f)),
                "size_mm": [round(float(d), 2) for d in dims]}
    except Exception:
        return None


def render_step_image(step_path: Path, px: int = 420) -> Optional[QImage]:
    """Render a static shaded 3D thumbnail of a STEP or WRL model (None if the
    format is unsupported or its backend is unavailable)."""
    fmt = model_format(step_path)
    if not _model_backend_ready(fmt):
        return None
    try:
        v, faces = load_step_mesh(step_path)
        if v is None or faces is None or len(faces) == 0:
            return None
        img = QImage(px, px, QImage.Format_ARGB32)
        img.fill(BG)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        paint_mesh(p, px, px, v, faces, rot_x=-60.0, rot_y=-35.0, zoom=1.0)
        p.end()
        return img
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Board-level render — export a WHOLE .kicad_pcb to an image via kicad-cli.
#
# The footprint/symbol/STEP/WRL paths above render a single part. There was no
# way to preview an assembled board. This shells out to the installed KiCad's
# ``kicad-cli`` (located through the shared kicad_paths locator) and turns the
# board into a QImage — with zero new pip dependencies.
#
# Two strategies, tried in order by ``render_board_image(method="auto")``:
#   * "render" — ``kicad-cli pcb render`` writes a 3D PNG directly. Preferred:
#                the PNG loads with QImage alone (no QtSvg, no QApplication) and
#                mirrors the existing STEP/WRL 3D thumbnails.
#   * "svg"    — ``kicad-cli pcb export svg`` writes a 2D board plot which is
#                rasterised with PyQt5.QtSvg (used only as a fallback, and only
#                when QtSvg is importable). Older kicad-cli builds without the
#                ``pcb render`` subcommand fall through to this automatically.
#
# Everything is defensive: missing CLI, subprocess timeout, non-zero exit, empty
# / unreadable output each return a BoardRenderResult carrying an explicit
# ``reason`` instead of raising. Windows console flashes are suppressed with
# CREATE_NO_WINDOW (0 on other platforms).
# ---------------------------------------------------------------------------
from dataclasses import dataclass

_BOARD_RENDER_TIMEOUT = 120           # seconds; board renders are usually <1 s
_BOARD_SVG_LAYERS = "F.Cu,F.SilkS,F.Mask,Edge.Cuts"


@dataclass
class BoardRenderResult:
    """Outcome of :func:`render_board_image`.

    On success ``image`` is a non-null QImage (and the object is truthy);
    ``png_bytes`` carries the same picture as PNG for callers that want raw
    bytes, and ``method`` is "render" or "svg". On failure ``image`` is None,
    the object is falsy, and ``reason`` explains why (CLI missing, timeout,
    bad file, …) so the UI can show a message instead of a blank pane."""
    image: Optional[QImage] = None
    png_bytes: Optional[bytes] = None
    method: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.image is not None and not self.image.isNull()

    def __bool__(self) -> bool:
        return self.ok


def _no_window_flag() -> int:
    """CREATE_NO_WINDOW on Windows (suppresses the kicad-cli console flash); 0
    elsewhere, where the flag doesn't exist and 0 means 'no special flags'."""
    import subprocess
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_board_render_cli() -> Optional[str]:
    """Locate ``kicad-cli`` for board rendering via the shared kicad_paths
    locator. Delegates to :func:`kicad_paths.find_kicad_cli` (which honours
    KICAD_BIN / PATH), then falls back to :func:`kicad_paths.find_kicad_bin`.
    Returns None when KiCad isn't installed, so callers/tests can probe
    availability without launching a subprocess."""
    try:
        from kicad_paths import find_kicad_cli, find_kicad_bin
    except Exception:
        return None
    try:
        cli = find_kicad_cli()
        if cli and Path(cli).exists():
            return cli
    except Exception:
        pass
    try:
        bin_dir = find_kicad_bin()
        if bin_dir:
            for name in ("kicad-cli.exe", "kicad-cli"):
                exe = Path(bin_dir) / name
                if exe.exists():
                    return str(exe)
    except Exception:
        pass
    return None


def have_board_render() -> bool:
    """True when a board render is possible right now (kicad-cli is on disk).
    Convenience for the UI to enable/disable a 'Render board' action."""
    return find_board_render_cli() is not None


def _run_kicad_cli(args, timeout):
    """Run kicad-cli, hidden-window and time-bounded. Returns
    ``(returncode, combined_output_text)``; on timeout/spawn failure returns
    ``(None, message)`` so callers can branch on ``rc is None``."""
    import subprocess
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,            # avoid WinError 6 under pythonw/pytest
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, creationflags=_no_window_flag(),
        )
        return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return None, "timed out after %ss" % timeout
    except Exception as e:                       # FileNotFoundError, OSError, …
        return None, str(e)


def _board_extents(pcb_path) -> Optional[Tuple[float, float, float, float]]:
    """(min_x, min_y, max_x, max_y) of the board outline in mm, reusing this
    module's S-expr parser. Prefers Edge.Cuts geometry; falls back to the bbox
    of all top-level graphics when the outline is empty. None if unparseable."""
    try:
        root = parse_sexpr(Path(pcb_path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not root or root[0] != "kicad_pcb":
        return None
    xs, ys, exs, eys = [], [], [], []

    def _is_edge(node) -> bool:
        lay = _find(node, "layer")
        return bool(lay and len(lay) > 1 and lay[1] == "Edge.Cuts")

    def _add(node, pts):
        xs.extend(p[0] for p in pts)
        ys.extend(p[1] for p in pts)
        if _is_edge(node):
            exs.extend(p[0] for p in pts)
            eys.extend(p[1] for p in pts)

    for g in _findall(root, "gr_line") + _findall(root, "gr_rect"):
        s, e = _find(g, "start"), _find(g, "end")
        if s and e:
            _add(g, [(_f(s[1]), _f(s[2])), (_f(e[1]), _f(e[2]))])
    for g in _findall(root, "gr_circle"):
        ctr, end = _find(g, "center"), _find(g, "end")
        if ctr and end:
            cx, cy = _f(ctr[1]), _f(ctr[2])
            r = math.hypot(_f(end[1]) - cx, _f(end[2]) - cy)
            _add(g, [(cx - r, cy - r), (cx + r, cy + r)])
    for g in _findall(root, "gr_arc"):
        pts = [(_f(pt[1]), _f(pt[2])) for pt in
               (_find(g, "start"), _find(g, "mid"), _find(g, "end"))
               if pt and len(pt) > 2]
        if pts:
            _add(g, pts)

    use_x, use_y = (exs, eys) if exs else (xs, ys)
    if not use_x:
        return None
    return (min(use_x), min(use_y), max(use_x), max(use_y))


def _board_canvas(pcb_path, max_px: int) -> Tuple[int, int]:
    """Pick a (width, height) that fits the board's aspect ratio inside a
    ``max_px`` square. Falls back to a square canvas when extents are unknown."""
    ext = _board_extents(pcb_path)
    if not ext:
        return max_px, max_px
    bw = max(ext[2] - ext[0], 0.1)
    bh = max(ext[3] - ext[1], 0.1)
    if bw >= bh:
        return max_px, max(int(round(max_px * bh / bw)), 64)
    return max(int(round(max_px * bw / bh)), 64), max_px


def _render_board_png(cli, pcb_path, max_px, side, timeout):
    """``kicad-cli pcb render`` → (QImage, png_bytes, "") or (None, None, reason)."""
    import os
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        w, h = _board_canvas(pcb_path, max_px)
        rc, log = _run_kicad_cli(
            [cli, "pcb", "render", "--output", tmp.name,
             "--width", str(w), "--height", str(h), "--side", str(side),
             pcb_path], timeout)
        if rc is None:
            return None, None, "pcb render: %s" % log
        if rc != 0:
            return None, None, "pcb render exit %s: %s" % (rc, (log or "").strip()[-160:])
        data = Path(tmp.name).read_bytes()
        if not data:
            return None, None, "pcb render produced an empty file"
        img = QImage()
        if not img.loadFromData(data, "PNG") or img.isNull():
            return None, None, "pcb render output was not a readable PNG"
        return img, data, ""
    except Exception as e:
        return None, None, "pcb render error: %s" % e
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _render_board_svg(cli, pcb_path, max_px, timeout):
    """``kicad-cli pcb export svg`` rasterised via PyQt5.QtSvg →
    (QImage, png_bytes, "") or (None, None, reason). Needs QtSvg (optional);
    returns a clear reason when it isn't importable."""
    try:
        from PyQt5.QtSvg import QSvgRenderer
    except Exception as e:
        return None, None, "svg rasteriser unavailable (PyQt5.QtSvg): %s" % e
    import os
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
    tmp.close()
    try:
        rc, log = _run_kicad_cli(
            [cli, "pcb", "export", "svg", "--output", tmp.name,
             "--layers", _BOARD_SVG_LAYERS, "--page-size-mode", "2",
             "--exclude-drawing-sheet", "--mode-single", pcb_path], timeout)
        if rc is None:
            return None, None, "pcb export svg: %s" % log
        if rc != 0:
            return None, None, "pcb export svg exit %s: %s" % (rc, (log or "").strip()[-160:])
        if not Path(tmp.name).exists() or Path(tmp.name).stat().st_size == 0:
            return None, None, "pcb export svg produced an empty file"
        renderer = QSvgRenderer(tmp.name)
        if not renderer.isValid():
            return None, None, "pcb export svg output was not renderable"
        default = renderer.defaultSize()
        vw, vh = default.width(), default.height()
        if vw <= 0 or vh <= 0:
            vw = vh = max_px
        if vw >= vh:
            tw, th = max_px, max(int(round(max_px * vh / vw)), 1)
        else:
            tw, th = max(int(round(max_px * vw / vh)), 1), max_px
        img = QImage(tw, th, QImage.Format_ARGB32)
        img.fill(BG)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(p)
        p.end()
        if img.isNull():
            return None, None, "svg rasterisation produced a null image"
        return img, _qimage_to_png(img), ""
    except Exception as e:
        return None, None, "pcb export svg error: %s" % e
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _qimage_to_png(img: QImage) -> Optional[bytes]:
    """Serialise a QImage to PNG bytes (None if the write handler fails)."""
    try:
        from PyQt5.QtCore import QBuffer, QByteArray
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        ok = img.save(buf, "PNG")
        buf.close()
        return bytes(ba) if ok else None
    except Exception:
        return None


def render_board_image(kicad_pcb_path, max_px: int = 1600, side: str = "top",
                       method: str = "auto",
                       timeout: Optional[float] = None) -> "BoardRenderResult":
    """Render a whole ``.kicad_pcb`` to an image via the installed kicad-cli.

    Returns a :class:`BoardRenderResult`: truthy with ``.image`` (a QImage) on
    success, falsy with an explicit ``.reason`` on failure (CLI missing, bad
    file, timeout, non-zero exit). Never raises for the ordinary failure modes.

    ``method`` selects the strategy: "auto" (default) tries the 3D ``pcb
    render`` first and falls back to the 2D ``pcb export svg`` rasteriser;
    "render" or "svg" force one. ``max_px`` caps the longest image side (the
    other side follows the board aspect ratio). ``side`` is passed to
    ``pcb render`` (top/bottom/left/right/front/back)."""
    if timeout is None:
        timeout = _BOARD_RENDER_TIMEOUT
    pcb = Path(kicad_pcb_path)
    if not pcb.exists():
        return BoardRenderResult(reason="board file not found: %s" % pcb)
    if pcb.suffix.lower() != ".kicad_pcb":
        return BoardRenderResult(reason="not a .kicad_pcb file: %s" % pcb)
    cli = find_board_render_cli()
    if not cli:
        return BoardRenderResult(
            reason="kicad-cli not found (install KiCad or set KICAD_BIN)")
    try:
        px = max(64, min(int(max_px), 8192))
    except (TypeError, ValueError):
        px = 1600
    order = {"render": ("render",), "svg": ("svg",)}.get(
        method, ("render", "svg"))
    reasons = []
    for m in order:
        if m == "render":
            img, data, why = _render_board_png(cli, str(pcb), px, side, timeout)
        else:
            img, data, why = _render_board_svg(cli, str(pcb), px, timeout)
        if img is not None and not img.isNull():
            return BoardRenderResult(image=img, png_bytes=data, method=m)
        reasons.append("%s (%s)" % (why, m))
    return BoardRenderResult(reason="; ".join(reasons) or "board render failed")
