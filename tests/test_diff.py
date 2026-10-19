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


def undecode(text, uid):
    """The constant's value as labview-mcp's pylabview reader leaves a value it cannot decode: empty, plus a comment."""
    text = re.sub(rf'(<Constant [^>]*uid="{uid}"[^>]*value=")[^"]*"', r'\1"', text)
    return text.replace("</VI>", f"  <!-- vi2aixml: constant {uid}: value of type TypeDef not decoded -->\n</VI>")


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

    def test_error_case_respelled(self):
        text = BASE.replace('<CaseFrame selector="Default"', '<CaseFrame selector="Error"')
        self.base_err = self.write("base_err", text)
        env = {**os.environ, "LVINDEX_PROJECT": self.tmp.name, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-I", LVINDEX, "semdiff", self.base_err,
                            self.write("err_dots", text.replace('selector="Error"', 'selector="Error .."'))],
                           env=env, capture_output=True, encoding="utf-8")
        self.assertIn("CARD DELTA: none", r.stdout)

    def test_duplicate_frames_reordered(self):
        start = BASE.index('  <Structure _name="While Loop"')
        end = BASE.rindex("</VI>")
        loop = BASE[start:end]
        loop2 = renumber(loop, 500).replace('value="100"', 'value="300"')
        before = BASE[:start] + loop + loop2 + BASE[end:]
        after = BASE[:start] + renumber(loop2, 2000) + renumber(loop, 2000) + BASE[end:]
        Diff.base = self.write("dup_base", before)
        try:
            out = self.diff("dup_swapped", after)
        finally:
            Diff.base = self.write("base", BASE)
        self.assertIn("CARD DELTA: none", out)
        self.assertIn("(6 of 6 frames unchanged)", out)

    def diff_pair(self, name, old, new):
        Diff.base = self.write(name + "_old", old)
        try:
            return self.diff(name + "_new", new)
        finally:
            Diff.base = self.write("base", BASE)

    def test_inputs_order_and_target_spelling(self):
        two = BASE.replace('inputs="timeout:24.value"', 'inputs="timeout:24.value,state:10.value"')
        swapped = two.replace('inputs="timeout:24.value,state:10.value"', 'inputs="state:10.value,timeout:24.value"')
        out = self.diff_pair("order", two, swapped.replace("Demo.lvlib\\3AStart.vi", "Demo.lvlib:Start.vi"))
        self.assertIn("CARD DELTA: none", out)
        self.assertIn("NO LOGICAL DIFF", out)

    def test_unwired_input_and_escaped_name(self):
        old = BASE.replace('inputs="timeout:24.value"', 'inputs="timeout:24.value,priority:"').replace(
            '_name="timeout"', '_name="time\\2C\\0A  out"')
        new = BASE.replace('_name="timeout"', '_name="time,\n  out"')
        out = self.diff_pair("unwired", old, new)
        self.assertIn("CARD DELTA: none", out)
        self.assertIn("NO LOGICAL DIFF", out)

    def test_undecoded_constant_one_side(self):
        out = self.diff("undecoded", undecode(BASE, "24"))
        self.assertIn("CARD DELTA: none", out)
        self.assertIn("? constants not compared, value not decoded by the exporter: timeout in [Start]", out)
        self.assertIn("? 1 constant value(s) not compared, not decoded by the exporter: timeout", out)
        self.assertNotIn("NO LOGICAL DIFF", out)

    def test_undecoded_constant_both_sides(self):
        out = self.diff_pair("undecoded2", undecode(BASE, "24"), undecode(BASE, "24"))
        self.assertIn("timeout in [Start]", out)          # not compared is said, not reported as equal
        self.assertIn("1 constant value(s) not compared", out)

    def test_undecoded_constant_does_not_hide_an_added_one(self):
        extra = '<Constant _name="timeout" outputs="" type="int32" uid="40" uid_parent="23" value="5"/>'
        out = self.diff("undecoded_add", undecode(BASE, "24").replace("<Call inputs=\"timeout", extra + "<Call inputs=\"timeout"))
        self.assertIn("+timeout=5", out)

    def test_undecoded_constants_of_other_names_differ(self):
        out = self.diff_pair("undecoded_name", undecode(BASE, "24"),
                             undecode(BASE.replace('_name="timeout"', '_name="delay"'), "24"))
        self.assertIn("+delay=<not decoded>, -timeout=<not decoded>", out)

    def test_undecoded_pair_does_not_hide_a_changed_same_name_constant(self):
        extra = '<Constant _name="timeout" outputs="" type="int32" uid="40" uid_parent="23" value="{}"/>'
        at = "<Call inputs=\"timeout"
        old = undecode(BASE, "24").replace(at, extra.format(5) + at)
        out = self.diff_pair("nd_pair", old, renumber(old.replace('value="5"', 'value="6"'), 1000).replace(
            "constant 24:", "constant 1024:"))
        self.assertIn("constants: +timeout=6, -timeout=5", out)
        self.assertIn("timeout in [Start]", out)

    def test_name_with_equals_sign(self):
        out = self.diff_pair("eq_name", undecode(BASE, "24"), BASE.replace('_name="timeout"', '_name="timeout=b"'))
        self.assertIn("+timeout=b=100", out)
        self.assertIn("-timeout=<not decoded>", out)

    def test_same_port_order_kept(self):
        two = BASE.replace('inputs="timeout:24.value"', 'inputs="el:24.value,el:29.value"')
        out = self.diff_pair("ports", two, two.replace("el:24.value,el:29.value", "el:29.value,el:24.value"))
        self.assertNotIn("NO LOGICAL DIFF", out)

    def test_regenerated_rewire_between_same_name_constants(self):
        extra = '<Constant _name="timeout" outputs="value:40.value" type="int32" uid="40" uid_parent="23" value="200"/>'
        at = "<Call inputs=\"timeout"
        old = BASE.replace(at, extra + at)
        new = renumber(old.replace('inputs="timeout:24.value"', 'inputs="timeout:40.value"'))
        out = self.diff_pair("rewire", old, new)
        self.assertIn("timeout:<Constant:timeout=100>", out)
        self.assertIn("timeout:<Constant:timeout=200>", out)

    def test_tag_change(self):
        out = self.diff("tag", BASE.replace('<Constant _name="stop"', '<Control _name="stop"'))
        self.assertIn("tag: Constant -> Control", out)

    def test_growth_is_not_regeneration(self):
        extra = "".join(f'<Constant _name="k{i}" outputs="" type="int32" uid="{90 + i}" uid_parent="30" value="{i}"/>'
                        for i in range(20))
        out = self.diff("grow", BASE.replace('<Node _name="Add"', extra + '<Node _name="Add"'))
        self.assertNotIn("old uids are left", out)
        self.assertIn("~ frame [Default]: constants: +k0=0", out)


if __name__ == "__main__":
    unittest.main()
