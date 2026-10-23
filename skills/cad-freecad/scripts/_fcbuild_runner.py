"""The half of fcbuild.py that runs inside freecadcmd. Do not run it directly.

freecadcmd takes no script arguments, so `fcbuild.py` hands over a JSON request
through the FCBUILD_REQUEST environment variable and reads the JSON report this
writes back. Nothing here prints: OpenCascade writes its own chatter to stdout.
"""

import json
import os
import runpy
import sys
import traceback

import FreeCAD as App

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fcad  # noqa: E402

# What a sketch may be attached to without referencing generated topology.
STABLE_SUPPORTS = {
    "App::Plane", "App::Line", "App::Point", "App::Origin",
    "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point", "PartDesign::CoordinateSystem",
}


def _round(x, digits=6):
    return round(float(x), digits)


def _bbox(shape):
    b = shape.BoundBox
    return {"min": [_round(b.XMin), _round(b.YMin), _round(b.ZMin)],
            "max": [_round(b.XMax), _round(b.YMax), _round(b.ZMax)],
            "size": [_round(b.XLength), _round(b.YLength), _round(b.ZLength)]}


def _check_sketch(sk, strict, errors, warnings):
    info = {"name": sk.Name, "label": sk.Label}
    status = sk.solve()
    info["solve"] = status
    info["dof"] = sk.DoF
    for key in ("ConflictingConstraints", "RedundantConstraints", "MalformedConstraints",
                "PartiallyRedundantConstraints"):
        info[key] = list(getattr(sk, key, []))
    where = f"sketch {sk.Name} ({sk.Label})"
    if status != 0:
        errors.append(f"{where}: solver failed (solve() == {status})")
    if info["ConflictingConstraints"]:
        errors.append(f"{where}: conflicting constraints {info['ConflictingConstraints']} (1-based)")
    if info["RedundantConstraints"]:
        errors.append(f"{where}: redundant constraints {info['RedundantConstraints']} (1-based)")
    if info["MalformedConstraints"]:
        errors.append(f"{where}: malformed constraints {info['MalformedConstraints']} (1-based)")
    if info["PartiallyRedundantConstraints"]:
        warnings.append(f"{where}: partially redundant constraints {info['PartiallyRedundantConstraints']}")
    if sk.DoF > 0:
        (errors if strict else warnings).append(f"{where}: under-constrained, {sk.DoF} degree(s) of freedom left")
    supports = [ref[0] for ref in (sk.AttachmentSupport or [])]
    if sk.MapMode == "Deactivated" or not supports:
        warnings.append(f"{where}: not attached; attach it to an origin or datum plane")
    for obj in supports:
        if obj.TypeId not in STABLE_SUPPORTS:
            warnings.append(f"{where}: attached to {obj.Name} ({obj.TypeId}), generated topology "
                            "that is renamed when earlier features change; attach to an origin or datum plane")
    return info


def _feature(obj):
    entry = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    profile = getattr(obj, "Profile", None)
    if profile:
        entry["profile"] = profile[0].Name
    base = getattr(obj, "Base", None)
    if obj.TypeId in ("PartDesign::Fillet", "PartDesign::Chamfer") and base:
        entry["base"] = base[0].Name
        entry["edges"] = list(base[1])
    if "Type" in obj.PropertiesList and obj.TypeId.startswith("PartDesign::"):
        entry["mode"] = str(obj.Type)
    for prop in ("Length", "Radius", "Size"):
        if prop in obj.PropertiesList and entry.get("mode", "Length") == "Length":
            entry[prop.lower()] = _round(getattr(obj, prop).Value)
    exprs = {path: expr for path, expr in (obj.ExpressionEngine or [])}
    if exprs:
        entry["expressions"] = exprs
    return entry


def _export(req, bodies, report):
    out_dir, stem = req["out_dir"], req["stem"]
    files = report["files"]
    if "step" in req["formats"]:
        import Import
        path = os.path.join(out_dir, stem + ".step")
        Import.export(bodies, path)
        files["step"] = path
    if "stl" in req["formats"]:
        import Mesh
        import MeshPart
        path = os.path.join(out_dir, stem + ".stl")
        mesh = Mesh.Mesh()
        for b in bodies:
            mesh.addMesh(MeshPart.meshFromShape(Shape=b.Shape, LinearDeflection=req["stl_tolerance"],
                                                AngularDeflection=0.25))
        mesh.write(path)
        files["stl"] = path


def build(req, report):
    fcad.OVERRIDES = dict(req["set"])
    doc = App.newDocument(req["stem"])
    model = req["model"]
    sys.path.insert(0, os.path.dirname(model))
    namespace = runpy.run_path(model, run_name="fcbuild_model")
    if not callable(namespace.get("build")):
        raise SystemExit(f"{model} defines no build(doc) function")
    result = namespace["build"](doc)
    doc.recompute()

    if result is None:
        bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
    elif isinstance(result, (list, tuple)):
        bodies = list(result)
    else:
        bodies = [result]

    errors, warnings = report["errors"], report["warnings"]
    for obj in doc.Objects:
        if "Invalid" in obj.State or "Error" in obj.State or not obj.isValid():
            status = " ".join(obj.getStatusString().split())
            errors.append(f"{obj.Name} ({obj.Label}) failed to recompute: {status}")

    sheet = next((o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"), None)
    if sheet is not None:
        report["params"] = {alias: _round(sheet.get(alias)) for alias in
                            (sheet.getAlias(c) for c in sheet.getUsedCells()) if alias}

    report["sketches"] = [_check_sketch(o, req["strict"], errors, warnings)
                          for o in doc.Objects if o.TypeId == "Sketcher::SketchObject"]

    for b in bodies:
        shape = b.Shape
        entry = {"name": b.Name, "label": b.Label, "type": b.TypeId,
                 "tip": b.Tip.Name if getattr(b, "Tip", None) else None,
                 "features": [_feature(o) for o in getattr(b, "Group", [])]}
        if shape.isNull():
            errors.append(f"{b.Name}: empty shape")
        else:
            entry.update(valid=shape.isValid(), solids=len(shape.Solids),
                         volume=_round(shape.Volume, 3), area=_round(shape.Area, 3), bbox=_bbox(shape))
            if not shape.isValid():
                errors.append(f"{b.Name}: shape is not valid (shape.isValid() is False)")
            if len(shape.Solids) != 1:
                errors.append(f"{b.Name}: {len(shape.Solids)} solids; a PartDesign Body must be one solid")
            elif shape.Volume <= 0:
                errors.append(f"{b.Name}: non-positive volume")
        report["bodies"].append(entry)

    fcstd = os.path.join(req["out_dir"], req["stem"] + ".FCStd")
    doc.saveAs(fcstd)
    report["files"]["fcstd"] = fcstd
    if not errors:
        _export(req, bodies, report)


def doctor(report):
    import Part  # noqa: F401
    import PartDesign  # noqa: F401
    import Sketcher  # noqa: F401
    report["freecad"] = ".".join(App.Version()[:3])
    report["python"] = sys.version.split()[0]


def main():
    with open(os.environ["FCBUILD_REQUEST"], encoding="utf-8") as fh:
        req = json.load(fh)
    report = {"ok": False, "errors": [], "warnings": [], "files": {}, "bodies": [], "sketches": []}
    try:
        if req.get("doctor"):
            doctor(report)
        else:
            build(req, report)
        report["ok"] = not report["errors"]
    except BaseException as exc:  # noqa: BLE001 - every failure belongs in the report
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["traceback"] = traceback.format_exc()
    with open(req["report"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)


main()
