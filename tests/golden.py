"""Golden-output regression runner: runs lvindex queries on a real project and compares stdout with stored files.

  python -I tests/golden.py <suite.toml> [...] [--update]

A suite (kept outside the repository when it names a real project):
  cwd = "D:/Projects/MyApp"            folder the queries run in
  project = "test.toml"                optional --project (relative to the suite file), e.g. a test lvindex.toml
                                       whose `cache` points at a scratch folder
  expected = "golden/myapp"            folder of the stored outputs (relative to the suite file)
  queries = [["overview"], ["callers", "My Class:Do.vi"], ...]

Only stdout is compared; stderr (notes such as rebuilt cards) is shown for a failing query. A project being edited
in LabVIEW meanwhile changes outputs (stale cards): read the diff before calling it a regression.
--update stores the current outputs as expected. Exit code 1 when any output differs or is missing.
"""
import difflib
import os
import re
import subprocess
import sys
import tomllib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LVINDEX = os.path.join(REPO, "lvindex.py")


def run_suite(path, update):
    with open(path, encoding="utf-8-sig") as f:
        s = tomllib.loads(f.read())
    here = os.path.dirname(os.path.abspath(path))
    exp_dir = os.path.join(here, s["expected"])
    pre = ["--project", os.path.join(here, s["project"])] if s.get("project") else []
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("LVINDEX_PROJECT", None)
    os.makedirs(exp_dir, exist_ok=True)
    bad = 0
    for i, q in enumerate(s["queries"], 1):
        name = f"{i:02d}_{re.sub(r'[^A-Za-z0-9]+', '_', ' '.join(q))[:60].strip('_')}.txt"
        r = subprocess.run([sys.executable, "-I", LVINDEX, *pre, *q], cwd=s["cwd"], env=env, capture_output=True,
                           encoding="utf-8", errors="replace")
        exp = os.path.join(exp_dir, name)
        if update:
            with open(exp, "w", encoding="utf-8", newline="\n") as f:
                f.write(r.stdout)
            print(f"stored  {name}")
            continue
        if not os.path.exists(exp):
            print(f"MISSING {name} (run with --update)")
            bad += 1
            continue
        with open(exp, encoding="utf-8") as f:
            old = f.read()
        if old == r.stdout:
            print(f"ok      {name}")
            continue
        bad += 1
        d = list(difflib.unified_diff(old.splitlines(), r.stdout.splitlines(), "expected", "now", lineterm="", n=1))
        print(f"DIFF    {name}  (exit {r.returncode})")
        print("\n".join("    " + x for x in d[:40]) + (f"\n    ... +{len(d) - 40} lines" if len(d) > 40 else ""))
        if r.returncode and r.stderr.strip():
            print("    stderr: " + r.stderr.strip().splitlines()[-1])
    return bad


def main(argv):
    update = "--update" in argv
    suites = [a for a in argv if a != "--update"]
    if not suites:
        sys.exit(__doc__)
    bad = 0
    for p in suites:
        print(f"== {p}")
        bad += run_suite(p, update)
    if not update:
        print(f"{bad} output(s) differ" if bad else "all outputs equal")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
