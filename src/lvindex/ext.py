"""Project-specific extensions: Python modules listed as [extensions.<name>] in lvindex.toml (see EXTENSIONS.md).

An extension module may define (all optional):
  COMMANDS = {"cmd": (fn, min_args, max_args)}   fn(pos, opts) - new commands; core command names cannot be replaced
  HELP = "  cmd <arg>   what it does"            lines appended to the manual
  setup(api)                                     called once after loading
  find(api, term) -> [lines]                     extra lines printed at the end of `find <term>`
  overview(api) -> [lines]                       extra lines (sections) printed by `overview`
`api` gives the extension its settings and read access to the index (attributes below).

Trust: an extension is code from the project folder. It is loaded only when lvindex.toml lists it AND that
lvindex.toml was trusted once with `lvindex trust` (list kept per user, outside every project:
%LOCALAPPDATA%\\lvindex\\trusted.json). A cloned project that ships its own lvindex.toml and extension therefore
runs nothing until you trust it; zero-config never loads extensions.
A failing extension prints a warning and is skipped; the core commands keep working.
"""
import importlib.util
import json
import os
import sys
import traceback

_loaded = {}
_warned = set()
_registered = []
TRUST = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "lvindex", "trusted.json")


# ---------------------------------------------------------------- trust
def _key(path):
    return os.path.normcase(os.path.realpath(path))


def trusted():
    try:
        with open(TRUST, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def trust(cfg, remove=False):
    """`lvindex trust [--remove]`: allow (or stop allowing) the extensions of the current lvindex.toml."""
    if not cfg.get("file"):
        sys.exit("zero-config project: there is no lvindex.toml whose extensions could be trusted")
    t, k = trusted(), _key(cfg["file"])
    t = t - {k} if remove else t | {k}
    os.makedirs(os.path.dirname(TRUST), exist_ok=True)
    with open(TRUST, "w", encoding="utf-8") as f:
        json.dump(sorted(t), f, indent=1)
    names = ", ".join(cfg.get("extensions", {})) or "none listed"
    print(f"{'no longer trusted' if remove else 'trusted'}: {cfg['file']} (extensions: {names}) - list: {TRUST}")


# ---------------------------------------------------------------- api
class Api:
    """What an extension gets. Index data is read-only by convention: never modify what these return."""

    def __init__(self, name, sec, cfg):
        from . import cache as V
        from . import cards as K
        from . import skeleton as S
        self.name = name
        self.config = sec["config"]          # the extension's keys from [extensions.<name>] (without `file`)
        self.project = cfg                   # the whole project configuration (config.get())
        self.V, self.K, self.S = V, K, S     # core modules, for anything the helpers below do not cover
        self._cm = None

    def path(self, p):
        """A path from the extension's settings, relative to the lvindex.toml folder."""
        return os.path.normpath(os.path.join(self.project["dir"], p)) if p else ""

    def skel(self):
        """Skeleton of all VIs (refreshed incrementally): {"vis": {qname: {...}}, "ctls", "classes", "src", ...}."""
        return self.V.skel()

    def cards(self):
        """{qname: (card, stale)} for exported VIs; stale = VI saved after its export (do not trust the card)."""
        if self._cm is None:
            self._cm = self.V.card_map(self.skel())
        return self._cm

    def literals(self):
        """{tagged literal ([tags].literal): [qnames of VIs holding it]} from the binaries (no export needed)."""
        out = {}
        for q, v in self.skel()["vis"].items():
            for s in v["syms"]:
                out.setdefault(s, []).append(q)
        return out

    def roles(self, card, literal):
        """Roles (read / write / wait / case / used / dyn) a literal has in a card."""
        return [r for r, syms in card["tags"].items() if literal in syms]

    def mark(self, q):
        """" (card stale)" / " (no card)" / " (not in lvclass)" marker of a VI, as the core prints it."""
        return self.V.mark(self.skel(), q, self.cards())


# ---------------------------------------------------------------- loading and hooks
def _warn_once(key, msg):
    if key not in _warned:
        _warned.add(key)
        print(f"WARNING: {msg}", file=sys.stderr)


def load(cfg):
    """{name: module} of the configured extensions (loaded once per process, only for a trusted lvindex.toml)."""
    ext = cfg.get("extensions", {})
    if ext and _key(cfg["file"]) not in trusted():
        _warn_once("trust", f"extensions {', '.join(ext)} of {cfg['file']} not loaded: this lvindex.toml is not "
                            "trusted - run `lvindex trust` once if you trust the project's code")
        return {}
    for name, sec in ext.items():
        if name in _loaded:
            continue
        mname = f"lvindex_ext_{name}"
        try:
            spec = importlib.util.spec_from_file_location(mname, sec["file"])
            if spec is None:
                raise ImportError(f"cannot load {sec['file']}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mname] = mod                 # dataclasses, pickle, typing need the module registered
            spec.loader.exec_module(mod)
            mod.API = Api(name, sec, cfg)
            if hasattr(mod, "setup"):
                mod.setup(mod.API)
            _loaded[name] = mod
        except (Exception, SystemExit) as ex:        # noqa: BLE001 - an extension must never break the core
            sys.modules.pop(mname, None)
            print(f"WARNING: extension {name} ({sec['file']}) not loaded: {ex!r}", file=sys.stderr)
            _loaded[name] = None
    return {n: m for n, m in _loaded.items() if m is not None}


def call(cfg, hook, *args):
    """Lines from `hook` of every extension that defines it; a failing hook prints a warning."""
    out = []
    for name, mod in load(cfg).items():
        fn = getattr(mod, hook, None)
        if fn is None:
            continue
        try:
            r = fn(mod.API, *args)
            out += [r] if isinstance(r, str) else [str(x) for x in (r or [])]
        except (Exception, SystemExit):              # noqa: BLE001
            print(f"WARNING: extension {name}.{hook} failed:\n{traceback.format_exc(limit=3)}", file=sys.stderr)
    return out


def commands(cfg, core):
    """Extension commands, wrapped so a failure does not end in a bare traceback; names in `core` are refused.
    The accepted names are remembered for registered()."""
    out = {}
    for name, mod in load(cfg).items():
        cmds = getattr(mod, "COMMANDS", {})
        for cmd, entry in (cmds.items() if isinstance(cmds, dict) else []):
            ok = isinstance(entry, tuple) and len(entry) == 3 and callable(entry[0]) and \
                all(isinstance(x, int) for x in entry[1:])
            if not ok:
                _warn_once((name, cmd), f"extension {name}: COMMANDS[{cmd!r}] is not (fn, min_args, max_args) - ignored")
            elif cmd in core or cmd in out:
                _warn_once((name, cmd), f"extension {name}: command `{cmd}` already exists - ignored")
            else:
                out[cmd] = (_guard(name, cmd, entry[0]), entry[1], entry[2])
    _registered[:] = list(out)
    return out


def registered():
    """Extension command names accepted by the last commands() call (the CLI calls it before dispatch)."""
    return list(_registered)


def _guard(name, cmd, fn):
    def run(pos, opts):
        try:
            fn(pos, opts)
        except Exception:                            # noqa: BLE001
            sys.exit(f"extension {name}: `{cmd}` failed:\n{traceback.format_exc(limit=5)}")
    return run


def help_text(cfg):
    lines = [str(getattr(m, "HELP", "") or "").rstrip() for m in load(cfg).values()]
    return "\n".join(x for x in lines if x)
