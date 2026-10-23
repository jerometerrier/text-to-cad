#!/usr/bin/env python3
"""Build a FreeCAD PartDesign model script into an .FCStd with its feature tree, plus STEP/STL.

Runs with any Python 3.9+ (standard library only) and drives FreeCAD's
console, freecadcmd, which runs the model in FreeCAD's own Python.

Usage:
    python fcbuild.py MODEL.py [--out-dir DIR] [--name STEM] [--formats step,stl]
                      [--set NAME=VALUE ...] [--allow-underconstrained]
    python fcbuild.py --doctor

The model defines `build(doc)`; see the skill's SKILL.md. Prints one JSON
report on stdout. Exit codes: 0 built and every check passed, 1 the model
failed or a check failed (the .FCStd is still saved for inspection, no
STEP/STL is written), 2 FreeCAD was not found or the arguments are wrong.

freecadcmd is found by --freecadcmd, then $FREECADCMD, then PATH, then the
usual install locations.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "_fcbuild_runner.py"
FORMATS = ("step", "stl")
NAMES = ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe")
KNOWN_LOCATIONS = (
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/Applications/FreeCAD.app/Contents/Resources/bin/FreeCADCmd",
    "/usr/lib/freecad/bin/freecadcmd",
    "/usr/lib/freecad/bin/FreeCADCmd",
    "/usr/lib/freecad-daily/bin/FreeCADCmd",
    "/snap/bin/freecad.cmd",
    "C:/Program Files/FreeCAD*/bin/freecadcmd.exe",
    "C:/Program Files/FreeCAD*/bin/FreeCADCmd.exe",
)
INSTALL_HINT = (
    "freecadcmd (FreeCAD's console) was not found. Install FreeCAD 1.0 or newer "
    "(https://www.freecad.org/downloads.php, or `conda install -c conda-forge freecad`), "
    "then put freecadcmd on PATH or set FREECADCMD to its path."
)


def find_freecadcmd(explicit: str | None = None, environ=os.environ) -> str | None:
    for candidate in (explicit, environ.get("FREECADCMD")):
        if candidate:
            return candidate if Path(candidate).is_file() or shutil.which(candidate) else None
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    conda = environ.get("CONDA_PREFIX")
    patterns = ([f"{conda}/bin/freecadcmd", f"{conda}/Library/bin/freecadcmd.exe"] if conda else []) + list(KNOWN_LOCATIONS)
    for pattern in patterns:
        for match in sorted(glob.glob(pattern), reverse=True):
            if Path(match).is_file():
                return match
    return None


def parse_set(items: list[str]) -> dict[str, float]:
    values = {}
    for item in items:
        name, sep, raw = item.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--set wants NAME=VALUE, not {item!r}")
        try:
            values[name.strip()] = float(raw)
        except ValueError:
            raise ValueError(f"--set {name}: {raw!r} is not a number") from None
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fcbuild.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("model", nargs="?", help="model script defining build(doc)")
    parser.add_argument("--out-dir", help="output directory (default: the model's directory)")
    parser.add_argument("--name", help="output file stem (default: the model's stem)")
    parser.add_argument("--formats", default="step", help="comma list of exports besides .FCStd: step, stl, none (default: step)")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override a params() default; repeatable")
    parser.add_argument("--allow-underconstrained", action="store_true", help="report sketch degrees of freedom as warnings, not errors")
    parser.add_argument("--stl-tolerance", type=float, default=0.05, help="STL linear deflection in mm (default 0.05)")
    parser.add_argument("--freecadcmd", help="path to freecadcmd")
    parser.add_argument("--doctor", action="store_true", help="check that FreeCAD and its PartDesign/Sketcher modules load")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.doctor and not args.model:
        parser.error("MODEL is required unless --doctor is given")
    freecadcmd = find_freecadcmd(args.freecadcmd)
    if freecadcmd is None:
        print(INSTALL_HINT, file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="fcbuild-") as tmp:
        report_path = Path(tmp) / "report.json"
        request: dict = {"report": str(report_path)}
        if args.doctor:
            request["doctor"] = True
        else:
            model = Path(args.model).resolve()
            if not model.is_file():
                print(f"model not found: {args.model}", file=sys.stderr)
                return 2
            formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
            formats = [] if formats == ["none"] else formats
            bad = sorted(set(formats) - set(FORMATS))
            try:
                overrides = parse_set(args.set)
                if bad:
                    raise ValueError(f"unknown format(s) {', '.join(bad)}; choose from {', '.join(FORMATS)} or none")
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2
            out_dir = Path(args.out_dir).resolve() if args.out_dir else model.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            request.update(model=str(model), out_dir=str(out_dir), stem=args.name or model.stem,
                           formats=formats, set=overrides, strict=not args.allow_underconstrained,
                           stl_tolerance=args.stl_tolerance)
        request_path = Path(tmp) / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")

        env = dict(os.environ, FCBUILD_REQUEST=str(request_path))
        proc = subprocess.run([freecadcmd, str(RUNNER)], env=env, cwd=tmp,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        if not report_path.is_file():
            print(f"freecadcmd exited {proc.returncode} without a report; its output ends:\n"
                  + "\n".join(proc.stdout.splitlines()[-40:]), file=sys.stderr)
            return 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
    report["freecadcmd"] = freecadcmd
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
