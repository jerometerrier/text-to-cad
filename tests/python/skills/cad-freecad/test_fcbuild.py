"""skills/cad-freecad/scripts/fcbuild.py: FreeCAD PartDesign models to .FCStd + STEP.

The launcher's own behaviour (finding freecadcmd, arguments, the missing-FreeCAD
exit) runs everywhere. The builds need FreeCAD itself, which CI does not
install, so they run only where freecadcmd is found; the expected volumes come
from the construction, not from a recorded run.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path, repo_path

add_repo_path("skills/cad-freecad/scripts")

import fcbuild  # noqa: E402

SCRIPT = repo_path("skills/cad-freecad/scripts/fcbuild.py")
EXAMPLE = repo_path("skills/cad-freecad/examples/drilled_plate.py")
FREECADCMD = fcbuild.find_freecadcmd()


class LauncherTest(unittest.TestCase):
    def test_an_explicit_or_environment_path_wins_and_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "freecadcmd"
            fake.write_text("", encoding="utf-8")
            self.assertEqual(fcbuild.find_freecadcmd(str(fake), environ={}), str(fake))
            self.assertEqual(fcbuild.find_freecadcmd(None, environ={"FREECADCMD": str(fake)}), str(fake))
            self.assertIsNone(fcbuild.find_freecadcmd(None, environ={"FREECADCMD": str(fake) + ".missing"}))

    def test_set_parses_numbers_and_refuses_the_rest(self) -> None:
        self.assertEqual(fcbuild.parse_set(["length=120", " width = 4.5"]), {"length": 120.0, "width": 4.5})
        for bad in ("length", "=3", "length=long"):
            with self.assertRaises(ValueError):
                fcbuild.parse_set([bad])

    def test_missing_freecad_exits_2_with_the_install_hint(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(fcbuild, "find_freecadcmd", return_value=None), contextlib.redirect_stderr(stderr):
            self.assertEqual(fcbuild.main([str(EXAMPLE)]), 2)
        self.assertIn("FREECADCMD", stderr.getvalue())

    def test_an_unknown_format_exits_2_before_freecad_runs(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(fcbuild, "find_freecadcmd", return_value=sys.executable), \
                mock.patch.object(fcbuild.subprocess, "run") as run, contextlib.redirect_stderr(stderr):
            self.assertEqual(fcbuild.main([str(EXAMPLE), "--formats", "step,obj"]), 2)
        run.assert_not_called()
        self.assertIn("obj", stderr.getvalue())


@unittest.skipUnless(FREECADCMD, "FreeCAD (freecadcmd) is not installed")
class BuildTest(unittest.TestCase):
    def _build(self, model: Path, out: Path, *args: str) -> tuple[int, dict]:
        proc = subprocess.run([sys.executable, str(SCRIPT), str(model), "--out-dir", str(out), *args],
                              capture_output=True, text=True, env=dict(os.environ, FREECADCMD=FREECADCMD))
        self.assertTrue(proc.stdout, proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def test_the_documented_example_builds_its_tree_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self._build(EXAMPLE, Path(tmp), "--formats", "step,stl", "--set", "length=120")
            self.assertEqual(code, 0, report["errors"])
            for kind in ("fcstd", "step", "stl"):
                self.assertTrue(Path(report["files"][kind]).is_file(), kind)
            (body,) = report["bodies"]
            self.assertEqual([f["type"] for f in body["features"]], [
                "Sketcher::SketchObject", "PartDesign::Pad", "Sketcher::SketchObject",
                "PartDesign::Pocket", "PartDesign::Fillet"])
            self.assertEqual(body["features"][1]["expressions"], {".Length": "Params.thickness"})
            self.assertEqual(len(body["features"][4]["edges"]), 4)
            self.assertEqual(body["bbox"]["size"], [120.0, 50.0, 8.0])
            length, width, t, d, r = 120.0, 50.0, 8.0, 6.5, 6.0
            expected = t * (length * width - 4 * math.pi * (d / 2) ** 2 - 4 * (r * r - math.pi * r * r / 4))
            self.assertAlmostEqual(body["volume"], expected, places=2)
            self.assertTrue(all(s["dof"] == 0 for s in report["sketches"]))

    def test_a_conflicting_sketch_fails_and_exports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "conflict.py"
            model.write_text(
                "import fcad\n"
                "def build(doc):\n"
                "    body = fcad.body(doc)\n"
                "    sk = fcad.sketch(body, 'XY')\n"
                "    g = fcad.circle(sk, 10)\n"
                "    fcad.constrain(sk, 'Diameter', g, v=12)\n"
                "    fcad.pad(body, sk, 3)\n"
                "    return body\n",
                encoding="utf-8",
            )
            code, report = self._build(model, Path(tmp) / "out")
            self.assertEqual(code, 1)
            self.assertTrue(any("conflicting" in e for e in report["errors"]), report["errors"])
            self.assertEqual(sorted(report["files"]), ["fcstd"])


if __name__ == "__main__":
    unittest.main()
