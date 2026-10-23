"""A drilled plate with rounded corners: Params -> Sketch -> Pad -> Sketch -> Pocket -> Fillet.

Build it with `python <skill-dir>/scripts/fcbuild.py drilled_plate.py --formats step,stl`.
"""

import fcad


def build(doc):
    P = fcad.params(
        doc,
        length=80.0,      # along X
        width=50.0,       # along Y
        thickness=8.0,
        hole_d=6.5,       # M6 clearance
        hole_inset=10.0,  # hole centre to plate edge
        corner_r=6.0,
    )
    body = fcad.body(doc, "Plate")

    outline = fcad.sketch(body, "XY", name="OutlineSketch")
    fcad.rectangle(outline, P.length, P.width)
    plate = fcad.pad(body, outline, P.thickness, name="PlatePad")

    holes = fcad.sketch(body, "XY", name="HolesSketch")
    dx = P.length / 2 - P.hole_inset
    dy = P.width / 2 - P.hole_inset
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        fcad.circle(holes, P.hole_d, center=(dx * sx, dy * sy))
    drilled = fcad.pocket(body, holes, through_all=True, symmetric=True, name="HolesPocket")

    corners = fcad.edges(drilled, parallel="Z", between_planar_faces=True)
    fcad.fillet(body, drilled, corners, P.corner_r, name="CornerFillet")
    return body
