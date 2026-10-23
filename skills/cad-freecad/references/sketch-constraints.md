# Sketch constraints in FreeCAD

Agents fail most often on sketches. Read this before writing a profile the
`fcad.rectangle` / `fcad.circle` helpers do not cover.

## Indices

- Geometry ids are returned by `sk.addGeometry(...)`, starting at 0.
- Point positions: `1` start, `2` end, `3` centre (circles, arcs).
- `-1` is the sketch's horizontal axis and `-2` its vertical axis; the
  origin is point `(-1, 1)` (`fcad.ROOT`).
- `sk.addConstraint` returns a 0-based constraint index; the build report
  and FreeCAD's solver messages count constraints from 1.

## Degrees of freedom

Count before writing: a point has 2, a line 4, a circle 3, an arc 5. Every
constraint removes some:

| Constraint | Removes | Use |
| --- | --- | --- |
| `Coincident` (point, point) | 2 | join consecutive segments; put a centre on `ROOT` |
| `Horizontal` / `Vertical` (line) | 1 | axis-aligned edges |
| `DistanceX` / `DistanceY` (point, point, v) | 1 | signed offsets, sizes along an axis |
| `Distance` (line, v) or (point, point, v) | 1 | lengths not along an axis |
| `Diameter` / `Radius` (circle/arc, v) | 1 | holes, rounds |
| `Tangent` (geo, geo) or (geo, pos, geo, pos) | 1 or 2 | line to arc; the endpoint form also joins them |
| `Equal` (geo, geo) | 1 | repeated sizes |
| `Symmetric` (p1, p2, line or point) | 2 | keep a profile centred |
| `Angle` (line, line, radians) | 1 | non-orthogonal edges |
| `PointOnObject` (point, geo) | 1 | a point on a line or axis |

A closed profile with `n` segments needs exactly as many removed degrees as
it has. Too few leaves DoF > 0 (build fails); one too many is reported as
redundant or conflicting (build fails).

## Common mistakes

- **Coincident plus Tangent at the same joint.** The endpoint `Tangent`
  form (`geo1, pos1, geo2, pos2`) already makes the points coincident; adding
  `Coincident` too is redundant.
- **Horizontal on every edge of a rectangle.** Two `Horizontal` and two
  `Vertical` on four joined lines is already one too many if a `Parallel` is
  also added. Use 2 + 2, nothing else.
- **Dimensioning a size twice** (`DistanceX` on the top and the bottom line):
  redundant once the edges are horizontal.
- **Angles in degrees.** `Angle` takes radians (`math.radians(30)`).
- **Geometry far from its constrained position.** The solver starts from the
  geometry you drew: draw it at the intended size to avoid flipped or
  self-intersecting solutions.
- **Open profiles.** Pad and Pocket need closed wires; every endpoint joins
  exactly one other endpoint.
- **Construction lines** (`sk.addGeometry(geo, True)`) do not make faces but
  still count degrees of freedom; constrain them too.

## Slot example (two lines, two arcs)

```python
import math
import FreeCAD as App, Part
import fcad

def slot(sk, length, width):
    """A horizontal slot centred on the origin, fully constrained."""
    L, r = fcad.value(length), fcad.value(width) / 2
    V = App.Vector
    right = sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(L / 2, 0, 0), V(0, 0, 1), r), -math.pi / 2, math.pi / 2))
    left = sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(-L / 2, 0, 0), V(0, 0, 1), r), math.pi / 2, 3 * math.pi / 2))
    top = sk.addGeometry(Part.LineSegment(V(L / 2, r, 0), V(-L / 2, r, 0)))
    bottom = sk.addGeometry(Part.LineSegment(V(-L / 2, -r, 0), V(L / 2, -r, 0)))
    fcad.constrain(sk, "Tangent", right, 2, top, 1)
    fcad.constrain(sk, "Tangent", top, 2, left, 1)
    fcad.constrain(sk, "Tangent", left, 2, bottom, 1)
    fcad.constrain(sk, "Tangent", bottom, 2, right, 1)
    fcad.constrain(sk, "Horizontal", top)
    fcad.constrain(sk, "Equal", right, left)
    fcad.constrain(sk, "Symmetric", right, 3, left, 3, *fcad.ROOT)
    fcad.constrain(sk, "Radius", right, v=width / 2)
    fcad.constrain(sk, "DistanceX", left, 3, right, 3, v=length)
```

## Debugging a failing sketch

The report's `sketches[]` lists `dof`, `ConflictingConstraints`,
`RedundantConstraints` and `MalformedConstraints` per sketch. Remove a
redundant constraint rather than loosening the check; for leftover DoF, find
the geometry that can still move (an unanchored profile usually lacks its
position relative to `ROOT`).
