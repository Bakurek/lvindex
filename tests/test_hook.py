"""The plugin's PostToolUse hook (plugin/hooks/lvindex_hook.py), run as Claude Code runs it: hook JSON on stdin,
JSON with additionalContext (or nothing) on stdout. lvindex is the checkout (LVINDEX_CLI); the VI is a stand-in file
and the export the hand-written tests/data/dispatcher.xml.
Run: python -m unittest discover -s tests
"""
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
REPO = os.path.dirname(HERE)
HOOK = os.path.join(REPO, "plugin", "hooks", "lvindex_hook.py")
EXPORT = os.path.join(HERE, "data", "dispatcher.xml")
TOOL = "mcp__plugin_labview-mcp_labview__lvai_convert_vis_to_aixml"


def mcp_name(vi):
    h = hashlib.md5(os.path.abspath(vi).lower().encode("utf-8")).hexdigest()[:8].upper()
    return f"{os.path.splitext(os.path.basename(vi))[0]}.{h}.xml"


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.proj)
        open(os.path.join(self.proj, "Demo.lvproj"), "w").close()
        self.vi = os.path.join(self.proj, "A.vi")
        with open(self.vi, "wb") as f:
            f.write(b"version 1")
        t = time.time() - 1000
        os.utime(self.vi, (t, t))
        self.out = os.path.join(self.tmp.name, "exports")
        os.makedirs(self.out)
        self.data = os.path.join(self.tmp.name, "plugin-data")

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, name=None):
        p = os.path.join(self.out, name or mcp_name(self.vi))
        shutil.copy(EXPORT, p)
        return p

    def start_cache(self):
        """lvindex in use in the project: a first `update` creates .index\\index.json."""
        env = {**os.environ, "LVINDEX_PROJECT": self.proj}
        subprocess.run([sys.executable, "-I", os.path.join(REPO, "lvindex.py"), "update", self.vi, self.export("first.xml")],
                       env=env, capture_output=True)

    def hook(self, tool_input, tool=TOOL, response=None, cli=True, extra_env=None):
        env = {k: v for k, v in os.environ.items() if k not in ("LVINDEX_PROJECT", "LVINDEX_CLI")}
        env.update(extra_env or {})
        env["CLAUDE_PLUGIN_DATA"] = self.data
        env["PATH"] = os.path.dirname(sys.executable)          # no `lvindex` command on PATH
        if cli:
            env["LVINDEX_CLI"] = os.path.join(REPO, "lvindex.py")
        event = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": tool_input,
                 "tool_response": response or [{"type": "text", "text": json.dumps({"ok": True, "backend": "grpc"})}]}
        r = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(event), env=env, capture_output=True,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def test_export_is_stored_in_a_project_with_a_cache(self):
        self.start_cache()
        self.export()
        msg = self.hook({"viPaths": self.vi, "outputDirectory": self.out})
        self.assertIn("lvindex hook: stored", msg)
        self.assertIn("NO CHANGE", msg)                         # same content as the first export

    def test_a_users_lvindex_project_does_not_redirect_the_hook(self):
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(os.path.join(other, ".index"))
        open(os.path.join(other, "Other.lvproj"), "w").close()
        self.start_cache()
        with open(EXPORT, encoding="utf-8") as f:
            text = f.read().replace('value="100"', 'value="200"')
        with open(os.path.join(self.out, mcp_name(self.vi)), "w", encoding="utf-8") as f:
            f.write(text)
        msg = self.hook({"viPaths": self.vi, "outputDirectory": self.out}, extra_env={"LVINDEX_PROJECT": other})
        self.assertIn("+timeout=200", msg)
        self.assertFalse(os.path.exists(os.path.join(other, ".index", "index.json")))

    def test_problems_come_first_and_the_header_says_so(self):
        self.start_cache()
        x = self.export()
        t = os.stat(x).st_mtime + 10
        os.utime(self.vi, (t, t))                               # the VI was saved after the export
        msg = self.hook({"viPaths": self.vi, "outputDirectory": self.out})
        self.assertTrue(msg.startswith("lvindex hook: 1 of 1 exports NOT stored cleanly"), msg)
        self.assertIn("REFUSED", msg)

    def test_changed_export_gives_a_brief_card_delta(self):
        self.start_cache()
        with open(EXPORT, encoding="utf-8") as f:
            text = f.read().replace('value="100"', 'value="200"')
        with open(os.path.join(self.out, mcp_name(self.vi)), "w", encoding="utf-8") as f:
            f.write(text)
        msg = self.hook({"viPaths": self.vi + "\n", "outputDirectory": self.out})
        self.assertIn("+timeout=200", msg)
        self.assertIn("stored", msg)
        self.assertNotIn("\n@ ", msg)
        with open(os.path.join(self.proj, ".index", "index.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["a.vi"]["backend"], "grpc")  # read from the tool's answer

    def test_singular_tool(self):
        self.start_cache()
        x = self.export("any name.xml")
        msg = self.hook({"viPath": self.vi, "aiXmlFilePath": x},
                        tool="mcp__plugin_labview-mcp_labview__lvai_convert_vi_to_aixml")
        self.assertIn("lvindex hook: stored", msg)

    def test_silent_where_lvindex_is_not_in_use(self):
        self.export()                                           # zero-config project, but no cache yet
        self.assertEqual(self.hook({"viPaths": self.vi, "outputDirectory": self.out}), "")
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".index")))

    def test_missing_lvindex_is_said_once_per_session(self):
        self.start_cache()
        self.export()
        probe = subprocess.run([sys.executable, "-I", "-c", "import importlib.util as u; print(bool(u.find_spec('lvindex')))"],
                               capture_output=True, text=True)
        if probe.stdout.strip() == "True":
            self.skipTest("lvindex is installed in this Python: the hook would use it")
        msg = self.hook({"viPaths": self.vi, "outputDirectory": self.out}, cli=False)
        self.assertIn("NOT stored", msg)
        self.assertEqual(self.hook({"viPaths": self.vi, "outputDirectory": self.out}, cli=False), "")

    def test_bad_input_never_fails(self):
        self.assertEqual(self.hook({"viPaths": 5, "outputDirectory": None}), "")      # exit 0, nothing to say


if __name__ == "__main__":
    unittest.main()
