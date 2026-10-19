"""CARD DELTA and semantic diff on a small hand-written AIXML export, through the CLI (`semdiff a b`).

The VI: a While Loop with a dispatcher case structure (frames Start / Stop / Default, zero-config heuristic).
Each test writes a variant of it and checks the diff output. Run: python -m unittest discover -s tests
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LVINDEX = os.path.join(os.path.dirname(HERE), "lvindex.py")
with open(os.path.join(HERE, "data", "dispatcher.xml"), encoding="utf-8") as _f:
    BASE = _f.read()


def renumber(text, by=1000):
    """Shift every uid and wire reference: what a regeneration of the VI may do."""
    text = re.sub(r'(uid(?:_parent)?=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + by}"', text)

    def wires(m):
        return f'{m.group(1)}="' + re.sub(r"(?<![\w.])(\d+)(?=\.)", lambda w: str(int(w.group(1)) + by),
                                          m.group(2)) + '"'
    return re.sub(r'\b(inputs|outputs|selectin|selectout)="([^"]*)"', wires, text)


class Diff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        open(os.path.join(cls.tmp.name, "Demo.lvproj"), "w").close()      # zero-config project for the CLI
        cls.base = cls.write("base", BASE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def write(cls, name, text):
        p = os.path.join(cls.tmp.name, name + ".xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def diff(self, name, text):
        env = {**os.environ, "LVINDEX_PROJECT": self.tmp.name, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-I", LVINDEX, "semdiff", self.base, self.write(name, text)],
                           env=env, capture_output=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_same(self):
        out = self.diff("same", BASE)
        self.assertIn("CARD DELTA: none", out)
        self.assertIn("(3 of 3 frames unchanged)", out)
        self.assertIn("NO LOGICAL DIFF", out)

    def test_regenerated_uids_only(self):
        out = self.diff("renum", renumber(BASE))
        self.assertIn("CARD DELTA: none", out)
        self.assertIn("only 0% of the old uids are left", out)

    def test_regenerated_with_constant_change(self):
        out = self.diff("renum_timeout", renumber(BASE.replace('value="100"', 'value="200"')))
        self.assertIn("~ frame [Start]: constants: +timeout=200, -timeout=100", out)
        self.assertIn("(2 of 3 frames unchanged)", out)

    def test_connector_index(self):
        out = self.diff("conidx", BASE.replace('"state in" conIdx="1"', '"state in" conIdx="4"'))
        self.assertIn("(VI) terminals: +in state in [4] string, -in state in [1] string", out)

    def test_one_of_two_equal_calls_removed(self):
        out = self.diff("one_log", re.sub(r'\s*<Call [^>]*uid="27"[^>]*/>', "", BASE))
        self.assertIn("~ frame [Start]: calls: -Demo:Log.vi", out)

    def test_frame_renamed(self):
        out = self.diff("halt", BASE.replace("&quot;Stop&quot;", "&quot;Halt&quot;"))
        self.assertIn("- frame [Stop]", out)
        self.assertIn("+ frame [Halt]: constants: +stop=false", out)

    def test_growth_is_not_regeneration(self):
        extra = "".join(f'<Constant _name="k{i}" outputs="" type="int32" uid="{90 + i}" uid_parent="30" value="{i}"/>'
                        for i in range(20))
        out = self.diff("grow", BASE.replace('<Node _name="Add"', extra + '<Node _name="Add"'))
        self.assertNotIn("old uids are left", out)
        self.assertIn("~ frame [Default]: constants: +k0=0", out)


if __name__ == "__main__":
    unittest.main()
