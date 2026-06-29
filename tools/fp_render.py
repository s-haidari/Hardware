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
        self._parse()

    def _parse(self):
        r = self.root
        for pad in _findall(r, "pad"):
            try:
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
                self.pads.append((shape, x, y, w, h, rot, dr, ptype))
            except Exception:
                continue
        for ln in _findall(r, "fp_line") + _findall(r, "gr_line"):
            s, e = _find(ln, "start"), _find(ln, "end")
            lay = _find(ln, "layer")
            wid = _find(ln, "width") or _find(ln, "stroke")
            if s and e:
                self.lines.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2]),
                                   lay[1] if lay else "", _f(wid[1]) if wid else 0.1))
        for c in _findall(r, "fp_circle") + _findall(r, "gr_circle"):
            ctr, end = _find(c, "center"), _find(c, "end")
            lay = _find(c, "layer")
            wid = _find(c, "width")
            if ctr and end:
                cx, cy = _f(ctr[1]), _f(ctr[2])
                rad = math.hypot(_f(end[1]) - cx, _f(end[2]) - cy)
                self.circles.append((cx, cy, rad, lay[1] if lay else "", _f(wid[1]) if wid else 0.1))
        for rc in _findall(r, "fp_rect") + _findall(r, "gr_rect"):
            s, e = _find(rc, "start"), _find(rc, "end")
            lay = _find(rc, "layer")
            wid = _find(rc, "width")
            if s and e:
                self.rects.append((_f(s[1]), _f(s[2]), _f(e[1]), _f(e[2]),
                                   lay[1] if lay else "", _f(wid[1]) if wid else 0.1))
        for pol in _findall(r, "fp_poly") + _findall(r, "gr_poly"):
            pts = _find(pol, "pts")
            lay = _find(pol, "layer")
            wid = _find(pol, "width")
            if pts:
                pp = [(_f(xy[1]), _f(xy[2])) for xy in _findall(pts, "xy")]
                if pp:
                    self.polys.append((pp, lay[1] if lay else "", _f(wid[1]) if wid else 0.1))

    def bbox(self) -> Tuple[float, float, float, float]:
        xs, ys = [], []
        for (_s, x, y, w, h, _r, _d, _t) in self.pads:
            xs += [x - w / 2, x + w / 2]
            ys += [y - h / 2, y + h / 2]
        for (x1, y1, x2, y2, _l, _w) in self.lines + self.rects:
            xs += [x1, x2]; ys += [y1, y2]
        for (cx, cy, rr, _l, _w) in self.circles:
            xs += [cx - rr, cx + rr]; ys += [cy - rr, cy + rr]
        for (pp, _l, _w) in self.polys:
            xs += [p[0] for p in pp]; ys += [p[1] for p in pp]
        if not xs:
            return (-1, -1, 1, 1)
        return (min(xs), min(ys), max(xs), max(ys))

    def summary(self) -> dict:
        x0, y0, x1, y1 = self.bbox()
        layers = set()
        for coll in (self.lines, self.rects):
            for item in coll:
                layers.add(item[4])
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
        x0, y0, x1, y1 = self.bbox()
        span = max(x1 - x0, y1 - y0, 0.5)
        margin = px * 0.10
        scale = (px - 2 * margin) / span
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

        def T(mx, my):
            return QPointF((mx - cx) * scale + px / 2, (my - cy) * scale + px / 2)

        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)

        def draw_lines(coll):
            for (x1_, y1_, x2_, y2_, lay, w) in coll:
                pen = QPen(_layer_color(lay)); pen.setWidthF(max(w * scale, 1.0))
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(T(x1_, y1_), T(x2_, y2_))

        # courtyard + fab + silk graphics
        draw_lines([l for l in self.lines if "CrtYd" in l[4]])
        draw_lines([l for l in self.lines if "Fab" in l[4]])
        for (a, b, c2, d, lay, w) in self.rects:
            pen = QPen(_layer_color(lay)); pen.setWidthF(max(w * scale, 1.0)); p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(T(a, b), T(c2, d)))
        for (pcx, pcy, rr, lay, w) in self.circles:
            pen = QPen(_layer_color(lay)); pen.setWidthF(max(w * scale, 1.0)); p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            ctr = T(pcx, pcy)
            p.drawEllipse(ctr, rr * scale, rr * scale)
        for (pp, lay, w) in self.polys:
            poly = QPolygonF([T(x, y) for (x, y) in pp])
            col = _layer_color(lay)
            p.setPen(QPen(col, max(w * scale, 1.0)))
            p.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 60)))
            p.drawPolygon(poly)

        # pads (copper) on top
        for (shape, x, y, w, h, rot, dr, ptype) in self.pads:
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

        # silk last (most visible)
        draw_lines([l for l in self.lines if "SilkS" in l[4]])
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


def render_footprint_image(path: Path, px: int = 420) -> Optional[QImage]:
    fp = load_footprint(path)
    return fp.render(px) if fp else None


def footprint_summary(path: Path) -> Optional[dict]:
    fp = load_footprint(path)
    return fp.summary() if fp else None


# ---------------------------------------------------------------------------
# 3D model rendering — STEP -> mesh via cascadio (OpenCASCADE, the SnapMagic
# approach), then a small software rasteriser draws a shaded thumbnail. All
# local, no display required. Degrades gracefully if cascadio isn't installed.
# ---------------------------------------------------------------------------
def have_3d() -> bool:
    try:
        import cascadio  # noqa: F401
        import trimesh   # noqa: F401
        import numpy     # noqa: F401
        return True
    except Exception:
        return False


import contextlib


@contextlib.contextmanager
def _suppress_native_stderr():
    """Silence OpenCASCADE's C-level stderr chatter (skipped-node warnings)."""
    import os
    import sys
    try:
        fd = sys.stderr.fileno()
    except Exception:
        yield
        return
    saved = os.dup(fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, fd)
        yield
    finally:
        os.dup2(saved, fd)
        os.close(devnull)
        os.close(saved)


def _load_step_mesh(step_path: Path):
    """Return (vertices Nx3, faces Mx3) numpy arrays, or (None, None)."""
    import os
    import tempfile
    import cascadio
    import trimesh
    import numpy as np
    glb = tempfile.NamedTemporaryFile(suffix=".glb", delete=False).name
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
        return np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int)
    finally:
        try:
            os.unlink(glb)
        except Exception:
            pass


def step_summary(step_path: Path) -> Optional[dict]:
    if not have_3d():
        return None
    try:
        import numpy as np
        v, f = _load_step_mesh(step_path)
        if v is None or len(v) == 0:
            return None
        dims = v.max(0) - v.min(0)
        # glTF/GLB from cascadio is in metres; convert to mm for display
        if float(dims.max()) < 1.0:
            dims = dims * 1000.0
        return {"triangles": int(len(f)),
                "size_mm": [round(float(d), 2) for d in dims]}
    except Exception:
        return None


def render_step_image(step_path: Path, px: int = 420) -> Optional[QImage]:
    """Render a shaded 3D thumbnail of a STEP model (None if unavailable)."""
    if not have_3d():
        return None
    try:
        import numpy as np
        v, faces = _load_step_mesh(step_path)
        if v is None or len(faces) == 0:
            return None
        v = v - (v.max(0) + v.min(0)) / 2.0      # center

        # 3/4 isometric-ish view
        rx, rz = math.radians(-65.0), math.radians(35.0)
        Rx = np.array([[1, 0, 0],
                       [0, math.cos(rx), -math.sin(rx)],
                       [0, math.sin(rx), math.cos(rx)]])
        Rz = np.array([[math.cos(rz), -math.sin(rz), 0],
                       [math.sin(rz), math.cos(rz), 0],
                       [0, 0, 1]])
        vr = v @ (Rx @ Rz).T

        proj = vr[:, :2]
        pmin, pmax = proj.min(0), proj.max(0)
        ctr = (pmin + pmax) / 2.0
        margin = px * 0.12
        s = (px - 2 * margin) / max(float((pmax - pmin).max()), 1e-6)

        tris = vr[faces]                          # (M,3,3)
        normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        nlen = np.linalg.norm(normals, axis=1)
        nlen[nlen == 0] = 1.0
        normals = normals / nlen[:, None]
        light = np.array([0.35, 0.45, 0.82]); light /= np.linalg.norm(light)
        ndotl = normals @ light
        shade = np.clip(0.26 + 0.74 * np.clip(ndotl, 0.0, 1.0), 0.0, 1.0)
        depth = tris[:, :, 2].mean(1)
        order = np.argsort(depth)                 # far -> near (painter's)
        # backface cull: keep faces pointing toward the +z camera (clean solid)
        front = normals[:, 2] > 0
        order = order[front[order]]
        if len(order) < 4:                        # winding inconsistent -> show all
            order = np.argsort(depth)
            shade = np.clip(0.26 + 0.74 * np.abs(ndotl), 0.0, 1.0)

        def to2d(pt):
            return QPointF((pt[0] - ctr[0]) * s + px / 2.0,
                           (pt[1] - ctr[1]) * s + px / 2.0)

        img = QImage(px, px, QImage.Format_ARGB32)
        img.fill(BG)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        base = (198, 204, 212)
        for i in order:
            sh = shade[i]
            col = QColor(int(base[0] * sh), int(base[1] * sh), int(base[2] * sh))
            poly = QPolygonF([to2d(tris[i][0]), to2d(tris[i][1]), to2d(tris[i][2])])
            pen = QPen(col); pen.setWidthF(0.7)
            p.setPen(pen); p.setBrush(QBrush(col))
            p.drawPolygon(poly)
        p.end()
        return img
    except Exception:
        return None
