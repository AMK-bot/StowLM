"""Write fence: a descendant symlink pointing outside experiments/mvp/video/ is refused (final-code P2-3)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

VIDEO = Path(__file__).resolve().parents[1]
TOOLS = VIDEO / "stowlm-brag" / "tools"
sys.path.insert(0, str(TOOLS))
import build_comp  # noqa: E402
from safe_path import UnsafeDest, safe_dest, write_text  # noqa: E402


class Fence(unittest.TestCase):
    def setUp(self):
        self.outside = Path(tempfile.mkdtemp(prefix="stowlm-fence-"))
        self.base = VIDEO / "tests" / "_tmp_fence"
        shutil.rmtree(self.base, ignore_errors=True)
        (self.base / "out").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def assertOutsideEmpty(self):
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_plain_destination_ok(self):
        write_text(self.base / "out" / "composition" / "index.html", "x")
        self.assertTrue((self.base / "out" / "composition" / "index.html").is_file())

    def test_escape_refused(self):
        for p in ["/tmp/x", VIDEO, VIDEO / "brag-vendor" / "x", VIDEO / ".tools" / "x", VIDEO / "record-kit" / "x",
                  self.base / ".." / ".." / ".." / "x"]:
            with self.assertRaises(UnsafeDest):
                safe_dest(p)

    def test_case_aliases_refused(self):
        """code14 P2-2: on a case-insensitive filesystem these name the protected roots."""
        for p in [VIDEO / "RECORD-KIT" / "x", VIDEO / "Record-Kit" / "vo" / "shot-01.wav", VIDEO / "BRAG-VENDOR" / "x",
                  VIDEO / ".TOOLS" / "x", VIDEO / ".Tools" / "state" / "x", VIDEO / "RECORD-KIT"]:
            with self.assertRaises(UnsafeDest, msg=str(p)):
                safe_dest(p)

    def test_identity_refused_for_existing_alias(self):
        alias = VIDEO / "record-kit"
        if alias.exists() and (VIDEO / "RECORD-KIT").exists():  # case-insensitive volume: same directory
            self.assertTrue(os.path.samefile(alias, VIDEO / "RECORD-KIT"))
        with self.assertRaises(UnsafeDest):
            safe_dest(VIDEO / "demo-cut" / ".." / "RECORD-KIT" / "x")

    def test_descendant_dir_symlink(self):
        (self.base / "out" / "composition").symlink_to(self.outside, target_is_directory=True)
        for dest in [self.base / "out" / "composition" / "index.html",
                     self.base / "out" / "composition" / "assets" / "shots" / "a.mp4"]:
            with self.assertRaises(UnsafeDest):
                write_text(dest, "x")
        with self.assertRaises(UnsafeDest):
            build_comp.copy(TOOLS / "safe_path.py", self.base / "out" / "composition" / "assets" / "a.py")
        self.assertOutsideEmpty()

    def test_nested_assets_symlink(self):
        (self.base / "out" / "composition").mkdir()
        (self.base / "out" / "composition" / "assets").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(UnsafeDest):
            build_comp.copy(TOOLS / "safe_path.py", self.base / "out" / "composition" / "assets" / "music" / "a.py")
        self.assertOutsideEmpty()

    def test_leaf_symlink(self):
        target = self.outside / "victim.json"
        (self.base / "out" / "timing.json").symlink_to(target)
        with self.assertRaises(UnsafeDest):
            write_text(self.base / "out" / "timing.json", "x")
        self.assertFalse(target.exists())

    def test_out_symlink_refused(self):
        link = self.base / "linked-out"
        link.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(UnsafeDest):
            build_comp.contained_out(str(link))

    def test_builder_end_to_end(self):
        """The xray builder with a composition/ symlinked outside: refused, nothing written outside."""
        (self.base / "out" / "composition").symlink_to(self.outside, target_is_directory=True)
        r = subprocess.run([sys.executable, str(TOOLS / "build_extras.py"), "xray", "--out", str(self.base / "out")],
                           cwd=TOOLS, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("symlink", r.stderr + r.stdout)
        self.assertOutsideEmpty()

    def test_shell_guard(self):
        (self.base / "out" / "composition").symlink_to(self.outside, target_is_directory=True)
        ok = subprocess.run([sys.executable, str(TOOLS / "safe_path.py"), str(self.base / "out" / "a.mp4")])
        bad = subprocess.run([sys.executable, str(TOOLS / "safe_path.py"), str(self.base / "out" / "composition" / "a.mp4")],
                             capture_output=True)
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
