"""VI summaries (`summary template / set / questions / answer / stats` and their lines in card / ctx / find) through
the CLI, on a zero-config temp project with a stand-in VI and the hand-written tests/data/dispatcher.xml.
Run: python -m unittest discover -s tests
"""
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


class Semantic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.proj)
        open(os.path.join(self.proj, "Demo.lvproj"), "w").close()
        self.vi = os.path.join(self.proj, "A.vi")
        self.t = time.time() - 1000
        self.write_vi(b"version 1")
        x = os.path.join(self.tmp.name, "x.xml")
        shutil.copy(EXPORT, x)
        self.t += 10
        os.utime(x, (self.t, self.t))
        self.assertIn("XML:", self.run_cli("update", self.vi, x))

    def tearDown(self):
        self.tmp.cleanup()

    def write_vi(self, data):
        with open(self.vi, "wb") as f:
            f.write(data)
        self.t += 10
        os.utime(self.vi, (self.t, self.t))

    def run_cli(self, *args):
        env = {**os.environ, "LVINDEX_PROJECT": self.proj, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-I", LVINDEX, *args], env=env, capture_output=True, encoding="utf-8")
        return r.stdout + r.stderr

    def put(self, entries):
        p = os.path.join(self.tmp.name, f"s{time.monotonic_ns()}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        return self.run_cli("summary", "set", p)

    def summary(self, **kw):
        return {"A.vi": {"purpose": "Runs the `Start` / `Stop` dispatcher of the demo.",
                         "behavior": ["`Start` calls `Start.vi` with timeout 100."],
                         "frames": {"Start": "Starts the demo with `timeout` 100."}, **kw}}

    def test_template_lists_the_card_frames(self):
        out = self.run_cli("summary", "template", "A.vi")
        t = json.loads(out[:out.index("\nFill it")])
        self.assertEqual(sorted(t["A.vi"]["frames"]), ["Default", "Start", "Stop"])
        self.write_vi(b"version 2")
        self.assertIn("NO TEMPLATE for A.vi: VI changed since its export", self.run_cli("summary", "template", "A.vi"))

    def test_set_and_serve(self):
        out = self.put(self.summary())
        self.assertIn("STORED  A.vi", out)
        self.assertNotIn("warning", out)
        card = self.run_cli("card", "A.vi")
        self.assertIn("summary [summary]: Runs the `Start` / `Stop` dispatcher", card.split("\n")[1])
        self.assertIn("    = Starts the demo with `timeout` 100. [summary]", self.run_cli("card", "A.vi", "--frames"))
        self.assertIn("  summary: Runs the", self.run_cli("ctx", "A.vi"))
        self.assertIn("summary: Runs the", self.run_cli("find", "dispatcher of"))   # by meaning: no fact matches
        self.assertNotIn("summary:", self.run_cli("find", "start"))                 # facts match: no summary noise
        self.assertIn("1 current, 0 stale", self.run_cli("summary", "stats"))

    def test_set_refuses_and_warns(self):
        self.assertIn("frames not in the card: Pause", self.put(self.summary(frames={"Pause": "x"})))
        self.assertIn("at most 1000", self.put(self.summary(behavior=["x" * 1001])))
        self.assertIn("unknown or ambiguous VI name", self.put({"Nosuch.vi": self.summary()["A.vi"]}))
        self.assertIn("`purpose` missing", self.put({"A.vi": {"behavior": []}}))
        out = self.put(self.summary(behavior=["writes `Bogus.vi` and `Demo.lvlib:Log*`"]))
        self.assertIn("STORED", out)
        self.assertIn("check them): Bogus.vi", out)
        self.assertNotIn("Log*", out)                  # wildcard of a known name
        out = self.put(self.summary(behavior=["then calls `Timeout.vi` and reads `timeout.value`"]))
        self.assertIn("check them): Timeout.vi", out)  # a VI name must exist as written, not as parts
        self.assertNotIn("timeout.value", out.split("check them")[1])

    def test_stale_after_a_change_and_current_after_a_revert(self):
        self.put(self.summary())
        self.write_vi(b"version 2")
        self.assertIn("[summary, STALE: VI changed", self.run_cli("card", "A.vi"))
        self.assertIn("REFUSED A.vi: VI changed since its export", self.put(self.summary()))
        self.write_vi(b"version 1")                    # same content, new time: the summary is current again
        self.assertIn("summary [summary]:", self.run_cli("card", "A.vi"))
        self.run_cli("recard")                         # cards rebuilt: the summary is bound to the VI, not the card
        self.assertIn("summary [summary]:", self.run_cli("card", "A.vi"))

    def test_questions_and_answers_are_kept_as_notes(self):
        self.put(self.summary(questions=["Why is the timeout 100 ms?"]))
        self.assertIn("#1 Why is the timeout 100 ms?", self.run_cli("summary", "questions"))
        self.assertIn("open questions: 1", self.run_cli("card", "A.vi"))
        self.assertIn("no file nosuch.txt", self.run_cli("summary", "answer", "A.vi", "1", "@nosuch.txt"))
        out = self.run_cli("summary", "answer", "A.vi", "1", "The device needs 80 ms.")
        self.assertIn("ANSWERED #1 of A.vi: Why is the timeout 100 ms?", out)
        self.assertIn("-- 0 open questions", self.run_cli("summary", "questions"))
        self.assertIn("note [note]: Why is the timeout 100 ms? -> The device needs 80 ms.", self.run_cli("card", "A.vi"))
        self.assertIn("NO QUESTION #2", self.run_cli("summary", "answer", "A.vi", "2", "x"))
        self.put(self.summary(questions=["Why is the timeout 100 ms?", "Who sends Stop?"]))  # rewritten summary
        card = self.run_cli("card", "A.vi")
        self.assertIn("-> The device needs 80 ms.", card)       # the answer survives, its question is not asked again
        self.assertIn("open questions: 1", card)
        self.write_vi(b"version 2")
        self.assertIn("note [note, VI changed since]", self.run_cli("card", "A.vi"))

    def test_corrupt_file_is_kept_aside(self):
        self.put(self.summary())
        sem = os.path.join(self.proj, ".index", "semantic.json")
        with open(sem, "w") as f:
            f.write('{"vis": []}')
        out = self.run_cli("card", "A.vi")
        self.assertIn("semantic.json corrupt, kept as", out)
        self.assertNotIn("Traceback", out)
        self.assertIn("STORED", self.put(self.summary()))

    def test_nothing_changes_without_summaries(self):
        card = self.run_cli("card", "A.vi")
        self.assertNotIn("summary", card)
        self.assertNotIn("    = ", self.run_cli("card", "A.vi", "--frames"))
        self.assertIn("0 current, 0 stale", self.run_cli("summary", "stats"))
        self.assertIn("summary template <vi> | set", self.run_cli("summary", "bogus"))


if __name__ == "__main__":
    unittest.main()
