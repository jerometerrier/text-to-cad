"""Helpers for FreeCAD PartDesign models built by `fcbuild.py`.

Runs inside FreeCAD's own Python (freecadcmd), never a plain interpreter:
`fcbuild.py` puts this directory on the model's import path, so a model just
`import fcad`.

Every helper builds a real feature-tree object -- Body, Sketch, Pad, Pocket,
Fillet, Chamfer -- the same objects a person makes by hand in the PartDesign
workbench, so the saved .FCStd opens with its history and stays editable.

Dimensions may be plain numbers or `Param`s from `params()`. A `Param` is bound
to its spreadsheet cell with an expression, so editing the `Params` sheet in
FreeCAD regenerates the part like a SolidWorks global variable. `Param`s combine
with `+ - * /` and keep the binding (`P.length / 2 - P.margin`).

Sketch helpers add fully-constrained geometry: each leaves the sketch with zero
degrees of freedom for what it added, which `fcbuild.py` checks.
"""

from __future__ import annotations

import FreeCAD as App
import Part
import Sketcher

V = App.Vector

# Set by the runner from `fcbuild.py --set name=value`; applied by `params()`.
OVERRIDES: dict[str, float] = {}

# Sketcher's fixed indices: geometry -1 is the sketch's own H axis, whose point 1
# is the origin; point positions 1/2/3 are start, end and centre.
ROOT = (-1, 1)
START, END, CENTER = 1, 2, 3


class Param:
    """A number with the spreadsheet expression that produces it."""

    def __init__(self, value: float, expr: str):
        self.value = float(value)
        self.expr = expr

    def _op(self, other, symbol: str, fn, swap: bool = False) -> "Param":
        o_val, o_expr = (other.value, f"({other.expr})") if isinstance(other, Param) else (float(other), repr(float(other)))
        s_expr = f"({self.expr})"
        if swap:
            return Param(fn(o_val, self.value), f"{o_expr} {symbol} {s_expr}")
        return Param(fn(self.value, o_val), f"{s_expr} {symbol} {o_expr}")

    def __add__(self, o): return self._op(o, "+", lambda a, b: a + b)
    def __radd__(self, o): return self._op(o, "+", lambda a, b: a + b, swap=True)
    def __sub__(self, o): return self._op(o, "-", lambda a, b: a - b)
    def __rsub__(self, o): return self._op(o, "-", lambda a, b: a - b, swap=True)
    def __mul__(self, o):
        if not isinstance(o, Param) and float(o) in (1.0, -1.0):
            return self if float(o) == 1.0 else -self
        return self._op(o, "*", lambda a, b: a * b)

    def __rmul__(self, o): return self.__mul__(o)
    def __truediv__(self, o): return self._op(o, "/", lambda a, b: a / b)
    def __rtruediv__(self, o): return self._op(o, "/", lambda a, b: a / b, swap=True)
    def __neg__(self): return Param(-self.value, f"-({self.expr})")
    def __float__(self): return self.value
    def __repr__(self): return f"Param({self.value!r}, {self.expr!r})"


class _Params:
    def __init__(self, values: dict[str, Param]):
        self.__dict__.update(values)


def value(v) -> float:
    return v.value if isinstance(v, Param) else float(v)


def bind(obj, prop: str, v) -> None:
    """Set `obj.<prop>` to `v`; bind it to the spreadsheet when `v` is a Param."""
    target = obj
    *path, last = prop.split(".")
    for name in path:
        target = getattr(target, name)
    setattr(target, last, value(v))
    if isinstance(v, Param):
        obj.setExpression(f".{prop}", v.expr)


def params(doc, name: str = "Params", **defaults: float) -> _Params:
    """A `Spreadsheet::Sheet` of named design parameters, one row each.

    `fcbuild.py --set length=120` overrides a default without editing the model.
    """
    unknown = sorted(set(OVERRIDES) - set(defaults))
    if unknown:
        raise ValueError(f"--set names unknown parameter(s): {', '.join(unknown)}; known: {', '.join(defaults)}")
    sheet = doc.addObject("Spreadsheet::Sheet", name)
    values: dict[str, Param] = {}
    for row, (key, default) in enumerate(defaults.items(), start=1):
        v = float(OVERRIDES.get(key, default))
        sheet.set(f"A{row}", key)
        sheet.set(f"B{row}", f"={v!r}")
        try:
            sheet.setAlias(f"B{row}", key)
        except ValueError as exc:
            raise ValueError(f"parameter name {key!r} is not a valid spreadsheet alias ({exc}); "
                             "unit symbols such as t, m, g, s, h, in, ft are reserved") from None
        values[key] = Param(v, f"{name}.{key}")
    doc.recompute()
    return _Params(values)


def body(doc, name: str = "Body"):
    return doc.addObject("PartDesign::Body", name)


def origin_feature(body, role: str):
    """The Body's own origin plane or axis: "XY_Plane", "XZ_Plane", "YZ_Plane", "X_Axis"..."""
    for feature in body.Origin.OriginFeatures:
        if feature.Role == role:
            return feature
    raise ValueError(f"{body.Name} has no origin feature {role!r}")


def sketch(body, plane: str = "XY", offset=0.0, name: str = "Sketch"):
    """A sketch attached to one of the Body's origin planes, shifted `offset` along its normal.

    Attach to origin planes (or datum planes built on them), not to a face of
    an earlier feature: a face reference is renamed when the geometry before it
    changes, which is FreeCAD's topological-naming failure.
    """
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(origin_feature(body, f"{plane.upper()}_Plane"), "")]
    sk.MapMode = "FlatFace"
    bind(sk, "AttachmentOffset.Base.z", offset)
    return sk


def constrain(sk, kind: str, *refs, v=None) -> int:
    """Add one Sketcher constraint; a dimensional one takes `v`, bound when a Param."""
    args = (kind, *refs) if v is None else (kind, *refs, value(v))
    index = sk.addConstraint(Sketcher.Constraint(*args))
    if isinstance(v, Param):
        sk.setExpression(f".Constraints[{index}]", v.expr)
    return index


def rectangle(sk, width, height, center=(0.0, 0.0)) -> list[int]:
    """A fully-constrained axis-aligned rectangle; returns its four line ids.

    Centred on the origin it is held by a Symmetric constraint, so it stays
    centred when its size changes; elsewhere its lower-left corner is
    dimensioned from the origin.
    """
    w, h = value(width), value(height)
    cx, cy = value(center[0]), value(center[1])
    corners = [V(cx - w / 2, cy - h / 2, 0), V(cx + w / 2, cy - h / 2, 0),
               V(cx + w / 2, cy + h / 2, 0), V(cx - w / 2, cy + h / 2, 0)]
    lines = [sk.addGeometry(Part.LineSegment(corners[i], corners[(i + 1) % 4])) for i in range(4)]
    for i in range(4):
        constrain(sk, "Coincident", lines[i], END, lines[(i + 1) % 4], START)
    constrain(sk, "Horizontal", lines[0])
    constrain(sk, "Horizontal", lines[2])
    constrain(sk, "Vertical", lines[1])
    constrain(sk, "Vertical", lines[3])
    constrain(sk, "DistanceX", lines[0], START, lines[0], END, v=width)
    constrain(sk, "DistanceY", lines[1], START, lines[1], END, v=height)
    if (cx, cy) == (0.0, 0.0) and not any(isinstance(c, Param) for c in center):
        constrain(sk, "Symmetric", lines[0], START, lines[1], END, *ROOT)
    else:
        constrain(sk, "DistanceX", *ROOT, lines[0], START, v=center[0] - width / 2)
        constrain(sk, "DistanceY", *ROOT, lines[0], START, v=center[1] - height / 2)
    return lines


def circle(sk, diameter, center=(0.0, 0.0)) -> int:
    """A fully-constrained circle; its centre is dimensioned from the origin. Returns its id."""
    cx, cy = value(center[0]), value(center[1])
    g = sk.addGeometry(Part.Circle(V(cx, cy, 0), V(0, 0, 1), value(diameter) / 2))
    constrain(sk, "Diameter", g, v=diameter)
    if (cx, cy) == (0.0, 0.0) and not any(isinstance(c, Param) for c in center):
        constrain(sk, "Coincident", g, CENTER, *ROOT)
    else:
        constrain(sk, "DistanceX", *ROOT, g, CENTER, v=center[0])
        constrain(sk, "DistanceY", *ROOT, g, CENTER, v=center[1])
    return g


def _symmetric(feature, on: bool) -> None:
    # FreeCAD 1.0 calls it Midplane; later releases replace it with SideType.
    if not on:
        return
    if "SideType" in feature.PropertiesList:
        feature.SideType = "Symmetric"
    else:
        feature.Midplane = True


def pad(body, profile, length, name: str = "Pad", reversed: bool = False, symmetric: bool = False):
    feature = body.newObject("PartDesign::Pad", name)
    feature.Profile = profile
    bind(feature, "Length", length)
    feature.Reversed = reversed
    _symmetric(feature, symmetric)
    return feature


def pocket(body, profile, depth=None, through_all: bool = False, name: str = "Pocket",
           reversed: bool = False, symmetric: bool = False):
    """Cut by `depth`, or through everything. A pocket cuts against the sketch
    normal; `symmetric=True` with `through_all` cuts both ways, so a sketch on
    a base plane cuts the whole part whichever side the material is on."""
    if (depth is None) == (not through_all):
        raise ValueError("pocket() takes either depth= or through_all=True")
    feature = body.newObject("PartDesign::Pocket", name)
    feature.Profile = profile
    if through_all:
        feature.Type = "ThroughAll"
    else:
        bind(feature, "Length", depth)
    feature.Reversed = reversed
    _symmetric(feature, symmetric)
    return feature


def edges(feature, parallel: str | None = None, at_z=None, between_planar_faces: bool = False) -> list[str]:
    """Names ("Edge7") of the feature's straight edges that match, for a fillet or chamfer.

    Choose edges by geometry, never by a hard-coded name: names are assigned
    by the kernel and change when earlier features change.
    `parallel` is "X", "Y" or "Z"; `at_z` keeps edges lying in that Z plane;
    `between_planar_faces` drops seams and edges touching curved faces.
    """
    feature.Document.recompute()
    shape = feature.Shape
    axis = {"X": V(1, 0, 0), "Y": V(0, 1, 0), "Z": V(0, 0, 1)}.get((parallel or "").upper())
    if parallel and axis is None:
        raise ValueError(f"parallel must be X, Y or Z, not {parallel!r}")
    found = []
    for i, edge in enumerate(shape.Edges, start=1):
        if not isinstance(edge.Curve, (Part.Line, Part.LineSegment)):
            continue
        direction = edge.Vertexes[-1].Point - edge.Vertexes[0].Point
        if axis is not None and direction.cross(axis).Length > 1e-7 * max(direction.Length, 1.0):
            continue
        if at_z is not None and any(abs(v.Point.z - value(at_z)) > 1e-7 for v in edge.Vertexes):
            continue
        faces = {f.hashCode(): f for f in shape.ancestorsOfType(edge, Part.Face)}.values()
        if len(faces) != 2:
            continue  # a seam, or a free edge
        if between_planar_faces and not all(isinstance(f.Surface, Part.Plane) for f in faces):
            continue
        found.append(f"Edge{i}")
    if not found:
        raise ValueError(f"no edge of {feature.Name} matches parallel={parallel!r} at_z={at_z!r}")
    return found


def fillet(body, base, edge_names: list[str], radius, name: str = "Fillet"):
    feature = body.newObject("PartDesign::Fillet", name)
    feature.Base = (base, edge_names)
    bind(feature, "Radius", radius)
    return feature


def chamfer(body, base, edge_names: list[str], size, name: str = "Chamfer"):
    feature = body.newObject("PartDesign::Chamfer", name)
    feature.Base = (base, edge_names)
    bind(feature, "Size", size)
    return feature
