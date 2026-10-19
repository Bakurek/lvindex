"""Cache commands (check / update / update --dir / repair) through the CLI, on a zero-config temp project.

The VI is a stand-in file (check and update only hash it; the skeleton records it as unreadable) and the export
is the hand-written tests/data/dispatcher.xml. File times are set with os.utime, so no test sleeps.
Run: python -m unittest discover -s tests
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LVINDEX = os.path.join(os.path.dirname(HERE), "lvindex.py")
EXPORT = os.path.join(HERE, "data", "dispatcher.xml")


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.proj)
        open(os.path.join(self.proj, "Demo.lvproj"), "w").close()
        self.vi = os.path.join(self.proj, "A.vi")
        self.t = time.time() - 1000
        self.write_vi(b"version 1")

    def tearDown(self):
        self.tmp.cleanup()

    def write_vi(self, data):
        with open(self.vi, "wb") as f:
            f.write(data)
        self.t += 10
        os.utime(self.vi, (self.t, self.t))

    def export(self, name="x.xml", folder=None):
        p = os.path.join(folder or self.tmp.name, name)
        shutil.copy(EXPORT, p)
        self.t += 10
        os.utime(p, (self.t, self.t))
        return p

    def run_cli(self, *args):
        env = {**os.environ, "LVINDEX_PROJECT": self.proj, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-I", LVINDEX, *args], env=env, capture_output=True, encoding="utf-8")
        return r.stdout + r.stderr

    def test_update_after_reverting_a_changed_vi(self):
        self.assertIn("XML:", self.run_cli("update", self.vi, self.export()))
        self.write_vi(b"version 2")
        self.assertIn("CHANGED", self.run_cli("check", self.vi))
        self.write_vi(b"version 1")                    # reverted: same content, new time
        self.assertIn("SAME", self.run_cli("check", self.vi))
        out = self.run_cli("update", self.vi, self.export("y.xml"))
        self.assertNotIn("REFUSED", out)
        self.assertIn("XML:", out)

    def test_update_refused_when_saved_after_check(self):
        self.run_cli("update", self.vi, self.export())
        self.write_vi(b"version 2")
        self.run_cli("check", self.vi)
        x = self.export("y.xml")
        self.write_vi(b"version 3")                    # saved after the export
        self.assertIn("REFUSED", self.run_cli("update", self.vi, x))

    def test_corrupt_index_is_kept(self):
        self.run_cli("update", self.vi, self.export())
        index = os.path.join(self.proj, ".index", "index.json")
        with open(index, "w") as f:
            f.write("{broken")
        out = self.run_cli("check", self.vi)
        self.assertIn("index.json corrupt, kept as", out)
        kept = glob.glob(index + ".corrupt-*")
        self.assertEqual(len(kept), 1)
        with open(kept[0]) as f:
            self.assertEqual(f.read(), "{broken")
        self.assertIn("1 exports adopted", self.run_cli("repair"))

    def test_repair_with_missing_cached_export(self):
        self.run_cli("update", self.vi, self.export())
        xml = glob.glob(os.path.join(self.proj, ".index", "xml", "*.xml"))[0]
        shutil.copy(xml, xml[:-12] + "deadbeef.xml")  # an export no entry points to
        os.remove(xml)                                 # the entry's own export is gone
        out = self.run_cli("repair")
        self.assertNotIn("Traceback", out)
        self.assertIn("ADOPTED", out)

    def test_update_dir_applies_only_the_newest_export(self):
        d = os.path.join(self.tmp.name, "exports")
        os.makedirs(d)
        self.export("A.0000ffff.xml", d)               # older, sorts first by name
        self.export("A.ffff0000.xml", d)
        newest = self.export("A.00000000.xml", d)      # newest, sorts first of all
        out = self.run_cli("update", "--dir", d)
        self.assertEqual(out.count("SKIPPED"), 2, out)
        self.assertIn(f"===== {os.path.basename(newest)} -> ", out)

    def test_unknown_command_and_option_are_short_and_point_at_an_upgrade(self):
        out = self.run_cli("nosuch")
        self.assertIn('unknown command "nosuch"', out)
        self.assertIn("callers", out)                  # the command list
        self.assertIn("is older than they are", out)
        self.assertNotIn("Reviewing VIs", out)         # not the whole manual
        out = self.run_cli("callers", "A.vi", "--bogus")
        self.assertIn("unknown option --bogus", out)
        self.assertIn("is older than they are", out)
        self.assertIn("Reviewing VIs", self.run_cli("--help"))

    def test_version_outside_a_project(self):
        env = {k: v for k, v in os.environ.items() if k != "LVINDEX_PROJECT"}
        r = subprocess.run([sys.executable, "-I", LVINDEX, "--version"], cwd=self.tmp.name, env=env,
                           capture_output=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^lvindex \d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
