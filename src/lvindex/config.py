"""Project configuration of lvindex: which LabVIEW project, where its cache lives, optional layers, extensions.

Discovery (first hit wins):
  1. env LVINDEX_PROJECT (set by `--project <path>`): a .toml file, or a folder with .claude\\lvindex.toml,
     lvindex.toml or a single *.lvproj (also in its src\\ subfolder)
  2. walking up from the current folder: .claude\\lvindex.toml (or lvindex.toml)
  The old name vi-tools.toml (and env VI_PROJECT) is still accepted, with a warning.
  3. walking up from the current folder: the OUTERMOST folder (or its src\\) with exactly one *.lvproj
     -> zero-config (a nested example / sub-project .lvproj does not win); announced on stderr
  Nothing found = error: the tools never fall back to a project they happen to be stored in.
Zero-config = core only (skeleton, cache, cards with calls / events / frames); every optional layer is off.

lvindex.toml (paths relative to the file; every key optional except src):
  name = "My App"                        project name in outputs
  src = "../src"                         folder with the .lvproj sources
  skip = ["external"]                    folder names not scanned (vendored code)
  cache = "../cache"                     default: <project root>\\.index (root = the folder above .claude\\ /
                                         src\\, or of the lvindex.toml / .lvproj); a new folder gets .gitignore `*`
  hide = ['(^|[\\\\/])accessors[\\\\/]']        regexes on the file path of VIs hidden in trees / unexported lists
  hide_calls = ['^Clear Errors\\.vi$']    regexes on callee names hidden in trees and frame cards
  status_enum = "Success"               enum constants containing this item are listed as `status consts`
  [tags]                                 string literals worth indexing (e.g. symbol / tag / key names)
  label = "plc"; literal = 'regex'       label = name of the tags in outputs (default "tag")
  [tags.roles]  read = ['^read symbol'] ; write = [...] ; wait = [...]   regexes on the lowercase callee name;
                                         its symbol inputs get that role
  [messages]                             a message-queue library: VIs whose call sends / receives a message
  enqueue = ["Enqueue Message"]; dequeue = ["Dequeue Message"]
  queue_ports = ["Message Queue"]; message_ports = ["Message"]; data_port = "Message Data"
  [actors]                               message-loop classes: children of `base` with an `entry` VI
  base = "Loop"; entry = "Core.vi"
  self_queue = "Self-Queue"              accessor (last part of a queue name) = the sender's own queue
  caller_queue = "Caller-Queue"          accessor = the queue of the actor that launched the sender
  named_queue = '(.+) Queue$'            regex on the accessor, group 1 = name of the owning actor
                                         (each key optional: without it that kind of queue stays "owner unknown")
  [extensions.<name>]                    project-specific extension module (see EXTENSIONS.md); loaded ONLY when
  file = "my_ext.py"                     listed here, never in zero-config. Other keys = the extension's settings
"""
import glob
import hashlib
import json
import os
import sys
import tomllib

NAME = "lvindex.toml"
LEGACY = "vi-tools.toml"                          # name before 2026-10-18, still found (with a warning)
KNOWN = {"name", "src", "skip", "cache", "hide", "hide_calls", "status_enum", "tags", "messages", "actors",
         "extensions"}
DEFAULT_HIDE = [r"(^|[\\/])accessors[\\/]"]
DEFAULT_HIDE_CALLS = [r"^(Clear Errors|Simple Error Handler|General Error Handler|Error Cluster From Error Code|"
                      r"Merge Errors|Get LV Class Name)\.vi$"]
_cfg = {}


def _lvprojs(d):
    for base in (d, os.path.join(d, "src")):
        found = glob.glob(os.path.join(glob.escape(base), "*.lvproj"))
        if len(found) == 1:
            return found[0]
    return None


def _outermost(start, fn):
    """Last (outermost) hit walking up - a nested example / sub-project .lvproj must not win."""
    d, hit = os.path.abspath(start), None
    while True:
        hit = fn(d) or hit
        up = os.path.dirname(d)
        if up == d:
            return hit
        d = up


def _toml_in(d):
    for name in (NAME, LEGACY):
        for p in (os.path.join(d, ".claude", name), os.path.join(d, name)):
            if os.path.isfile(p):
                if name == LEGACY:
                    print(f"WARNING: {p}: the old name {LEGACY} still works, rename it to {NAME}", file=sys.stderr)
                return p
    return None


def _walk_up(start, fn):
    d = os.path.abspath(start)
    while True:
        hit = fn(d)
        if hit:
            return hit
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def discover():
    """(kind, path): kind 'toml' or 'lvproj'."""
    env = os.environ.get("LVINDEX_PROJECT")
    if not env and os.environ.get("VI_PROJECT"):
        env = os.environ["VI_PROJECT"]
        print("WARNING: env VI_PROJECT is the old name, use LVINDEX_PROJECT", file=sys.stderr)
    if env:
        if os.path.isfile(env):
            return ("toml", env) if env.lower().endswith(".toml") else ("lvproj", env)
        hit = _toml_in(env)
        if hit:
            return "toml", hit
        hit = _lvprojs(env)
        if hit:
            return "lvproj", hit
        sys.exit(f"LVINDEX_PROJECT={env}: no {NAME} and no single .lvproj there")
    hit = _walk_up(os.getcwd(), _toml_in)
    if hit:
        return "toml", hit
    hit = _outermost(os.getcwd(), _lvprojs)
    if hit:
        print(f"(project: zero-config from {hit})", file=sys.stderr)
        return "lvproj", hit
    sys.exit(f"no LabVIEW project found from {os.getcwd()}: run from inside a project, or pass "
             f"--project <folder|{NAME}>")


def _root(kind, path):
    """Project root folder: above .claude\\ for an lvindex.toml there, above src\\ for an .lvproj there,
    otherwise the folder of the file."""
    d = os.path.dirname(os.path.abspath(path))
    up = ".claude" if kind == "toml" else "src"
    return os.path.dirname(d) if os.path.basename(d).lower() == up else d


def load():
    kind, path = discover()
    if kind == "lvproj":
        raw, here = {}, os.path.dirname(path)
        src, lvproj = here, path
    else:
        try:
            with open(path, encoding="utf-8-sig") as f:          # -sig: editors that write a BOM
                raw = tomllib.loads(f.read())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as ex:
            sys.exit(f"{path}: not valid TOML (UTF-8) - {ex}")
        here = os.path.dirname(path)
        unknown = set(raw) - KNOWN
        if unknown:
            print(f"WARNING: unknown keys in {path}: {', '.join(sorted(unknown))}", file=sys.stderr)
        if "src" not in raw:
            sys.exit(f"{path}: key `src` missing")
        src = os.path.normpath(os.path.join(here, raw["src"]))
        lvproj = _lvprojs(src)
    rel = lambda p: os.path.normpath(os.path.join(here, p))  # noqa: E731
    root = _root(kind, path)
    cfg = {"file": path if kind == "toml" else None, "lvproj": lvproj, "src": src, "root": root,
           "name": raw.get("name") or (os.path.splitext(os.path.basename(lvproj))[0] if lvproj else src),
           "skip": [s.lower() for s in raw.get("skip", [])],
           "cache": rel(raw["cache"]) if raw.get("cache") else os.path.join(root, ".index"),
           "hide": raw.get("hide", DEFAULT_HIDE), "hide_calls": raw.get("hide_calls", DEFAULT_HIDE_CALLS),
           "status_enum": raw.get("status_enum"), "tags": raw.get("tags"), "messages": raw.get("messages"),
           "actors": raw.get("actors"), "dir": here, "extensions": {}}
    for nm, sec in (raw.get("extensions") or {}).items():
        if not isinstance(sec, dict) or not sec.get("file"):
            sys.exit(f"{path}: [extensions.{nm}] needs `file` (path of the extension module)")
        cfg["extensions"][nm] = {"file": rel(sec["file"]), "config": {k: v for k, v in sec.items() if k != "file"}}
    sig = {"skip": cfg["skip"], "literal": (cfg["tags"] or {}).get("literal")}   # what the skeleton depends on
    cfg["hash"] = hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:8]
    return cfg


def get():
    if not _cfg:
        _cfg.update(load())
    return _cfg
