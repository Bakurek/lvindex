"""Cache commands (check / update / update --dir / repair) through the CLI, on a zero-config temp project.

The VI is a stand-in file (check and update only hash it; the skeleton records it as unreadable) and the export
is the hand-written tests/data/dispatcher.xml. File times are set with os.utime, so no test sleeps.
Run: python -m unittest discover -s tests
"""
import glob
import hashlib
import json
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
        self.assertIn("NO CHANGE", out)                # same export as the cached one; SAME refreshed the entry
        self.assertIn("UNCHANGED", self.run_cli("check", self.vi))

    def test_resave_without_change_makes_the_card_current_again(self):
        self.run_cli("update", self.vi, self.export())
        self.write_vi(b"version 1 saved again")        # a layout-only save: the file differs, the export does not
        self.assertIn("CHANGED", self.run_cli("check", self.vi))
        out = self.run_cli("update", self.vi, self.export("y.xml"))
        self.assertIn("the card is current again", out)
        self.assertIn("UNCHANGED", self.run_cli("check", self.vi))

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

    def stray(self, rel, data):
        """A stand-in VI at proj\\<rel>, outside any class or library."""
        p = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        os.utime(p, (self.t, self.t))
        return p

    def export_of(self, folder, name, body, vi_name="W.vi"):
        p = os.path.join(folder, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f'<VI _name="{vi_name}">\n{body}</VI>\n')
        self.t += 10
        os.utime(p, (self.t, self.t))
        return p

    @staticmethod
    def mcp_name(vi):
        """File name labview-mcp gives an export (AixmlTools.ExportName: MD5 of the lower-cased full path)."""
        h = hashlib.md5(os.path.abspath(vi).lower().encode("utf-8")).hexdigest()[:8].upper()
        return f"{os.path.splitext(os.path.basename(vi))[0]}.{h}.xml"

    def cached_xml(self, vi):
        index = os.path.join(self.proj, ".index", "index.json")
        if not os.path.exists(index):
            return None
        with open(index, encoding="utf-8") as f:
            idx = json.load(f)
        e = idx.get(os.path.normcase(os.path.relpath(vi, self.proj)))
        if not e:
            return None
        with open(os.path.join(self.proj, ".index", "xml", e["xml"]), encoding="utf-8") as f:
            return f.read()

    def test_update_dir_tells_same_name_stray_vis_apart(self):
        a, b = self.stray(os.path.join("a", "W.vi"), b"stray a"), self.stray(os.path.join("b", "W.vi"), b"stray b!")
        d = os.path.join(self.tmp.name, "exports")
        os.makedirs(d)
        const = '  <Constant type="string" uid="1" uid_parent="root" value="{}"/>\n'
        self.export_of(d, self.mcp_name(b), const.format("from b"))
        self.export_of(d, self.mcp_name(a), const.format("from a"))
        self.export_of(d, "W.xml", const.format("no hash"))                  # same name, nothing to tell them apart
        self.export_of(d, "W.DEADBEEF.xml", const.format("other path"))      # hash of neither
        out = self.run_cli("update", "--dir", d)
        self.assertEqual(out.count("NO UNIQUE VI (2 VIs with this name"), 2, out)
        self.assertIn('value="from a"', self.cached_xml(a))
        self.assertIn('value="from b"', self.cached_xml(b))
        self.assertEqual(self.run_cli("check", a, b).count("UNCHANGED"), 2)

    def test_update_dir_does_not_store_a_name_match_that_fails_the_self_check(self):
        self.stray("B.vi", b"callee")
        d = os.path.join(self.tmp.name, "exports")
        os.makedirs(d)
        x = self.export_of(d, "A.xml", '  <Call target="B.vi" uid="1" uid_parent="root"/>\n', vi_name="A.vi")
        out = self.run_cli("update", "--dir", d)
        self.assertIn("SELF-CHECK MISMATCH", out)
        self.assertIn("NOT STORED", out)
        self.assertIsNone(self.cached_xml(self.vi))
        out = self.run_cli("update", self.vi, x)                          # named explicitly: stored, mismatch shown
        self.assertIn("SELF-CHECK MISMATCH", out)
        self.assertIn("XML:", out)

    def variant(self, name, timeout, folder=None):
        """The dispatcher export with another timeout constant in frame Start."""
        with open(EXPORT, encoding="utf-8") as f:
            text = f.read().replace('value="100"', f'value="{timeout}"')
        p = os.path.join(folder or self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        self.t += 10
        os.utime(p, (self.t, self.t))
        return p

    def test_update_brief_shows_the_card_delta_only(self):
        self.assertIn("XML:", self.run_cli("update", self.vi, self.export()))
        out = self.run_cli("update", self.vi, self.variant("y.xml", 200), "--brief")
        self.assertIn("constants: +timeout=200, -timeout=100", out)
        self.assertRegex(out, r"per-uid diff with wiring \d+ rows, not shown: lvindex show <vi> --diff")
        self.assertNotIn("\n@ ", out)                  # no per-uid rows
        self.assertNotIn("per-uid diff below", out)
        self.assertIn("stored", out)
        self.assertNotIn("XML:", out)
        self.assertIn('value="200"', self.cached_xml(self.vi))
        shown = self.run_cli("show", self.vi, "--diff")  # the old export is gone, the recorded diff is not
        self.assertIn("+timeout=200", shown)
        self.assertIn("\n@ ", shown)

    def test_wiring_only_change_is_counted_in_brief(self):
        self.run_cli("update", self.vi, self.export())
        with open(EXPORT, encoding="utf-8") as f:
            text = f.read().replace('inputs="timeout:24.value"', 'inputs="timeout:29.value"')   # rewired only
        p = os.path.join(self.tmp.name, "w.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        self.t += 10
        os.utime(p, (self.t, self.t))
        out = self.run_cli("update", self.vi, p, "--brief")
        self.assertIn("CARD DELTA: none", out)
        self.assertRegex(out, r"per-uid diff with wiring [1-9]\d* rows, not shown")

    def test_older_export_does_not_replace_a_newer_one(self):
        old = self.variant("old.xml", 200)
        new = self.variant("new.xml", 300)
        self.run_cli("update", self.vi, new)
        self.assertIn("older than the cached one", self.run_cli("update", self.vi, old))
        self.assertIn('value="300"', self.cached_xml(self.vi))

    def test_identical_export_does_not_confirm_a_card_that_fails_the_self_check(self):
        self.stray("B.vi", b"callee")                  # the export calls B.vi, the saved file (stand-in) does not
        x = self.export_of(self.tmp.name, "a1.xml", '  <Call target="B.vi" uid="1" uid_parent="root"/>\n',
                           vi_name="A.vi")
        self.assertIn("SELF-CHECK MISMATCH", self.run_cli("update", self.vi, x))
        self.write_vi(b"changed outside LabVIEW")
        self.assertIn("CHANGED", self.run_cli("check", self.vi))
        x2 = self.export_of(self.tmp.name, "a2.xml", '  <Call target="B.vi" uid="1" uid_parent="root"/>\n',
                            vi_name="A.vi")             # the same export again, from LabVIEW memory
        out = self.run_cli("update", self.vi, x2)
        self.assertIn("NO CHANGE", out)
        self.assertIn("NOT confirmed", out)
        self.assertIn("CHANGED", self.run_cli("check", self.vi))

    def test_update_dir_is_brief_unless_diff(self):
        self.run_cli("update", self.vi, self.export())
        d = os.path.join(self.tmp.name, "e1")
        os.makedirs(d)
        self.variant(self.mcp_name(self.vi), 200, d)
        out = self.run_cli("update", "--dir", d)
        self.assertIn("+timeout=200", out)
        self.assertNotIn("\n@ ", out)
        d2 = os.path.join(self.tmp.name, "e2")
        os.makedirs(d2)
        self.variant(self.mcp_name(self.vi), 300, d2)
        out = self.run_cli("update", "--dir", d2, "--diff")
        self.assertIn("+timeout=300", out)
        self.assertIn("\n@ ", out)
        self.assertIn("XML:", out)

    def test_update_of_an_already_stored_export_shows_the_delta_recorded_then(self):
        self.run_cli("update", self.vi, self.export())
        self.run_cli("update", self.vi, self.variant("y.xml", 200), "--brief")    # e.g. stored by the hook
        out = self.run_cli("update", self.vi, self.variant("z.xml", 200))          # the same export again
        self.assertIn("NO CHANGE", out)
        self.assertIn("not a new change", out)
        self.assertIn("constants: +timeout=200, -timeout=100", out)

    def test_brief_delta_is_capped_and_keeps_not_compared_rows(self):
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
        os.environ["LVINDEX_PROJECT"] = self.proj
        try:
            from lvindex import cache
        finally:
            sys.path.pop(0)
        rows = ["CARD DELTA (...):"] + [f"  ~ frame [F{i}]: " + "x" * 80 for i in range(40)] + ["  ? constants not compared"]
        out = cache.brief_delta("\n".join(rows))
        self.assertLess(len(out), cache.BRIEF_CHARS + 300)
        self.assertIn("more rows not shown", out)
        self.assertTrue(out.endswith("  ? constants not compared"))

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
