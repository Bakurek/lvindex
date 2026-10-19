"""Claude Code PostToolUse hook of the lvindex plugin: store every labview-mcp VI -> AIXML export in the lvindex cache.

Runs after `lvai_convert_vis_to_aixml` / `lvai_convert_vi_to_aixml`. For each exported VI that lies in a project where
lvindex is in use (a `.claude\\lvindex.toml`, an `lvindex.toml` or a `.index\\index.json` above it - other projects,
vi.lib and user.lib are left alone), it runs `lvindex update <vi> <export> --brief` for exactly that VI and project
(LVINDEX_PROJECT set to the folder found, so neither a user's LVINDEX_PROJECT nor a same-named VI elsewhere can
redirect it) and hands the output to Claude as additional context: problems first, then the short CARD DELTAs.

Input: the hook JSON on stdin (tool_name, tool_input, tool_response). Output: hookSpecificOutput.additionalContext,
or nothing. Never blocks: every failure ends in exit code 0 with at most a short note.
lvindex is found as LVINDEX_CLI (an lvindex executable or a checkout's lvindex.py), else `lvindex` on PATH, else
`python -I -m lvindex`. Standard library only.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

CAP = 6000                    # characters of context handed back to Claude (the hook limit is 10 000)
BUDGET = 100                  # seconds for all lvindex runs of one call (the hook itself has 120)
PROBLEM = re.compile(r"REFUSED|NOT STORED|NO UNIQUE VI|SELF-CHECK MISMATCH|NOT confirmed|did not run|not stored|"
                     r"Traceback|ERROR|locked by another process")


def lvindex_cmd():
    cli = os.environ.get("LVINDEX_CLI")
    if cli:
        return [sys.executable, "-I", cli] if cli.lower().endswith(".py") else [cli]
    exe = shutil.which("lvindex")
    if exe:
        return [exe]
    if importlib.util.find_spec("lvindex"):
        return [sys.executable, "-I", "-m", "lvindex"]
    return None


def project_root(vi):
    """Nearest folder above the VI with an lvindex configuration or cache, or None (lvindex not in use there)."""
    d = os.path.dirname(os.path.abspath(vi))
    while True:
        if any(os.path.isfile(os.path.join(d, *p)) for p in
               ((".claude", "lvindex.toml"), ("lvindex.toml",), (".index", "index.json"))):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def mcp_name(vi):
    """File name labview-mcp gives an export: <VI name>.<MD5 of the lower-cased full path, 8 hex>.xml."""
    h = hashlib.md5(os.path.abspath(vi).lower().encode("utf-8")).hexdigest()[:8].upper()
    return f"{os.path.splitext(os.path.basename(vi))[0]}.{h}.xml"


def vi_list(text):
    """VI paths of viPaths: one per line; a line that is no file is also split at ';'."""
    out = []
    for line in (x.strip() for x in str(text or "").splitlines()):
        if line:
            out += [line] if os.path.isfile(line) else [v.strip() for v in line.split(";") if v.strip()]
    return out


def exports(tool_name, tool_input):
    """[(vi, export file)] of this call; a VI or export file that does not exist is left out."""
    if tool_name.endswith("lvai_convert_vi_to_aixml"):
        pairs = [(tool_input.get("viPath") or "", tool_input.get("aiXmlFilePath") or "")]
    else:
        out = tool_input.get("outputDirectory") or ""
        names = {f.lower(): f for f in os.listdir(out)} if os.path.isdir(out) else {}
        pairs = [(v, os.path.join(out, names.get(mcp_name(v).lower(), mcp_name(v))))
                 for v in vi_list(tool_input.get("viPaths"))]
    return [(v, x) for v, x in pairs if v and os.path.isfile(v) and os.path.isfile(x)]


def backend(tool_response):
    m = re.search(r'backend\\*"\s*:\s*\\*"([A-Za-z]+)', json.dumps(tool_response))
    return m.group(1) if m else None


def note_once(session, text):
    """text, unless it was already given in this session (marker in the plugin's data folder)."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA") or tempfile.gettempdir()
    mark = os.path.join(data, f"lvindex-hook-{re.sub(r'[^A-Za-z0-9_-]', '', session or 'none')}.noted")
    if os.path.exists(mark):
        return ""
    try:
        os.makedirs(data, exist_ok=True)
        open(mark, "w").close()
    except OSError:
        pass
    return text


def store(cmd, vi, x, root, b, deadline):
    """Output of `lvindex update <vi> <x> --brief` in the VI's project."""
    left = deadline - time.time()
    if left < 5:
        return "not stored: the hook ran out of time - run `lvindex update <vi> <export>` yourself"
    env = {k: v for k, v in os.environ.items() if k not in ("LVINDEX_PROJECT", "VI_PROJECT")}
    env["LVINDEX_PROJECT"] = root
    try:
        r = subprocess.run(cmd + ["update", vi, x, "--brief"] + (["--backend", b] if b else []), cwd=root, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=left)
        return r.stdout.strip() + ("\n" + r.stderr.strip() if r.returncode and r.stderr.strip() else "")
    except (OSError, subprocess.TimeoutExpired) as ex:
        return f"lvindex did not run ({ex.__class__.__name__}: {ex}) - nothing stored"


def run(event):
    items = [(vi, x, project_root(vi)) for vi, x in exports(event.get("tool_name", ""), event.get("tool_input") or {})]
    items = [(vi, x, root) for vi, x, root in items if root]
    if not items:
        return ""
    cmd = lvindex_cmd()
    if cmd is None:
        return note_once(event.get("session_id"), "lvindex hook: the export was NOT stored in the lvindex cache - the "
                         "lvindex command is not installed (pipx install git+https://github.com/Bakurek/lvindex, or "
                         "set LVINDEX_CLI). Run `lvindex update --dir <folder>` yourself once it is.")
    b, deadline = backend(event.get("tool_response")), time.time() + BUDGET
    bad, good = [], []
    for vi, x, root in items:
        text = store(cmd, vi, x, root, b, deadline)
        (bad if PROBLEM.search(text) else good).append(f"== {vi}\n{text}")
    if bad:
        head = (f"lvindex hook: {len(bad)} of {len(items)} exports NOT stored cleanly - act on these first (save the VI "
                "in LabVIEW and export again, or run `lvindex update <vi> <export>` yourself):")
    else:
        head = ("lvindex hook: stored in the project's lvindex cache (do not run `update` for these exports again). "
                "Use `lvindex card <vi>` / `--frame` / `--xml NAME` instead of reading the exports. CARD DELTA = "
                "against the export each one replaced; `lvindex show <vi> --diff` = its full diff:")
    msg, rest = head, list(bad) + ([f"lvindex hook: stored {len(good)} more:"] if bad and good else []) + good
    for i, part in enumerate(rest):
        if len(msg) + len(part) + 1 > CAP and i:
            msg += f"\n... {len(rest) - i} more not shown (`lvindex show <vi> --diff` per VI)"
            break
        msg += "\n" + part
    return msg[:CAP]


def main():
    try:
        event = json.load(sys.stdin)
        msg = run(event)
    except Exception as ex:                             # a hook must never break the session
        msg = f"lvindex hook failed: {ex.__class__.__name__}: {ex}"
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
