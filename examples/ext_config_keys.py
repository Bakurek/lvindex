r"""Example lvindex extension: cross-check string literals in VIs against keys declared in project text files.

Use it as a template. It shows the four hooks: COMMANDS, HELP, find(), overview().
Typical case: VIs read settings / tags / channels by name ("Motor.Speed") from INI, JSON or XML files; this
lists which names the VIs use, which the files declare, and the mismatches.

lvindex.toml:
  [tags]
  literal = '\b(?:Motor|Valve)\.[A-Za-z_]\w*'          # the names the VIs hold as string constants

  [extensions.keys]
  file = "ext_config_keys.py"                     # this file, relative to lvindex.toml
  files = ["../config/*.ini"]                     # glob patterns, relative to lvindex.toml
  key = '^\s*([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*='       # regex per line, group 1 = a declared name
"""
import glob
import re

HELP = """  keys <term>           declared keys (config files) and VIs holding them as literal, with roles
  keydrift              literals in VIs without a declared key; declared keys no VI uses"""
API = None                                       # set by the loader before setup()
API_VERSION = 1                                  # extension API this file was written for
_keys = {}


def setup(api):
    global KEY
    KEY = re.compile(api.config.get("key", r"^\s*([\w.]+)\s*="))


def declared():
    """{key: [file]} from the configured files (read once per run)."""
    if not _keys:
        for pat in API.config.get("files", []):
            for p in glob.glob(API.path(pat), recursive=True):
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        m = KEY.search(line)
                        if m:
                            _keys.setdefault(m.group(1), []).append(p)
    return _keys


def find(api, term):
    hit = sorted(k for k in declared() if term.lower() in k.lower())
    return [f"-- keys: {len(hit)} declared keys match: {', '.join(hit[:8])}  (keys {term})"] if hit else []


def overview(api):
    lit, keys = api.literals(), declared()
    return [f"## Config keys: {len(keys)} declared, {len(set(keys) & set(lit))} used by VIs  (keys <term> | keydrift)"]


def keys(p, o):
    t, lit, cm = p[0].lower(), API.literals(), API.cards()
    for k in sorted(set(declared()) | set(lit)):
        if t not in k.lower():
            continue
        vis = []
        for q in lit.get(k, []):
            c, stale = cm.get(q, (None, False))
            vis.append(f"{q}({'/'.join(API.roles(c, k)) or 'sym?'})" if c and not stale else f"{q}{API.mark(q)}")
        print(f"== {k}  declared in: {', '.join(declared().get(k, [])) or 'NOT DECLARED'}")
        print(f"  VIs: {', '.join(vis) or 'none'}")


def keydrift(p, o):
    lit, keys_ = API.literals(), declared()
    print("literals without a declared key: " + (", ".join(sorted(set(lit) - set(keys_))) or "-"))
    print("declared keys no VI uses: " + (", ".join(sorted(set(keys_) - set(lit))) or "-"))


COMMANDS = {"keys": (keys, 1, 1), "keydrift": (keydrift, 0, 0)}
