# Writing a project extension (for an AI assistant or a developer)

lvindex knows only what every LabVIEW project has: VIs, calls, classes, typedefs, string literals, and the facts
in exported VIs. Anything specific to one project stays outside lvindex: a PLC, a protocol, a driver, a config-file
format, a naming convention. Such things go into an **extension**, a single Python file kept in the project itself
(next to its `lvindex.toml`), never in the lvindex repository.

## When to write one

Write an extension when the project has an **external counterpart to cross-check against the VIs**, and the
question keeps coming back. Examples:
- PLC variables used by name over ADS / OPC UA / Modbus: is each name declared, who writes it in the PLC?
- channel or tag names in a hardware config file;
- keys of an INI / JSON / XML settings file;
- message IDs of a protocol spec;
- SQL table or column names.

Do not write one when:
- the question is about the VIs alone: use `find`, `ctx`, `card` and `callers`;
- `[tags]` already covers it: string literals with read / write / wait roles of the calls they are wired to;
- it would be used once: read the files directly.

## Steps for an assistant

1. **Establish the counterpart from the project, not from assumptions.**
   - Find the files that declare the names (e.g. `*.TcGVL`, `*.ini`, an XML config).
   - Read two or three of them and write down the exact syntax.
   - Find how VIs refer to these names: run `lvindex find <a known name>` and `lvindex find <name> -s`.
2. **Configure `[tags]` first**, if VIs hold the names as string constants. `literal` is a regex matching the
   names. `roles` are regexes on the callee names that read, write or wait on them. Run `lvindex skeleton` and
   `lvindex overview`: the `## <label> literals in VIs` line must show sensible counts. Without this the extension
   has nothing to compare against.
3. **Write the extension** from the template below or from [examples/ext_config_keys.py](examples/ext_config_keys.py).
   Keep the parser of the counterpart small and close to the syntax you saw in step 1.
4. **Register it** in the project's `lvindex.toml`:
   ```toml
   [extensions.plc]
   file = "lvindex_plc.py"        # relative to lvindex.toml
   repo = "../PLC/MyPlc"          # any further keys are the extension's own settings
   ```
   Then run `lvindex trust` once in the project. Until then the extension is not loaded, and lvindex says so.
5. **Verify on known cases.** Pick names whose truth you can check in the source files:
   - one declared and used;
   - one declared and unused;
   - one used but undeclared (if any).

   Run `lvindex <your command> <name>` and compare with the files. Then run `lvindex find <name>` and
   `lvindex overview` to see the hook output. Then run the bare `lvindex` to see `HELP`.
6. **Tell the user what is heuristic.** If something rests on a naming convention (e.g. `X` / `XACK` handshake
   pairs), say so in the docstring and in the output header.

## Interface

All names are optional. A file with only `COMMANDS` is a valid extension.

| Name | Called as | Purpose |
|---|---|---|
| `COMMANDS = {"cmd": (fn, min_args, max_args)}` | `fn(pos, opts)` | new commands; core command names cannot be replaced |
| `HELP = "  cmd <arg>   text"` | | lines appended to the manual |
| `setup(api)` | once, after loading | read settings, compile regexes |
| `find(api, term) -> [str]` | at the end of `find <term>` | extra hit lines, e.g. declarations matching the term |
| `overview(api) -> [str]` | inside `overview` | a short section, e.g. counts and the commands to use |
| `API` | module global, set by the loader | the same `api` object, for use inside command functions |

`opts` holds the core options: `-n N`, `-d N`, `-s`, `--all` (strings / `True`). Extensions cannot add options;
use positional arguments instead.

The `api` object:

| Attribute | Gives |
|---|---|
| `api.config` | the extension's keys from `[extensions.<name>]` (without `file`) |
| `api.path(p)` | a settings path resolved relative to the `lvindex.toml` folder |
| `api.project` | the whole project configuration (`src`, `cache`, `tags`, ...) |
| `api.literals()` | `{literal: [VI qnames]}`: tagged literals from the binaries, no export needed |
| `api.cards()` | `{qname: (card, stale)}`: facts of exported VIs; ignore a card when `stale` is true |
| `api.roles(card, literal)` | roles of a literal in a card: `read`, `write`, `wait`, `case`, `used`, `dyn` |
| `api.mark(qname)` | the core marker: ` (card stale)`, ` (no card)`, ` (not in lvclass)` |
| `api.skel()` | the skeleton: `{"vis": {qname: {file, calls, deps, syms, strs, ...}}, "classes", "ctls"}` |
| `api.V`, `api.K`, `api.S` | the core modules (cache, cards, skeleton) for anything else; not a stable API |

## Rules

- **Read-only.** Never write into the project, its repositories or the cache. Parse on every run, or memoise in
  a module global for one run.
- **Facts, not interpretation.** Print what the files and cards say. Mark anything inferred (conventions,
  heuristics) as such.
- **Short output.** These outputs end up in an AI prompt: cap lists (`-n`), print counts plus the first few items,
  and leave out what the core already prints.
- **Fail soft.** A missing folder or file means empty data and a one-line note, not a traceback. The loader catches
  exceptions anyway: a broken extension prints a warning, and the core keeps working.
- **No auto-loading.** An extension is code from the project folder. It runs only when `lvindex.toml` lists it
  **and** that `lvindex.toml` was trusted with `lvindex trust`. The list is kept per user in
  `%LOCALAPPDATA%\lvindex\trusted.json`, outside every project; `lvindex trust --remove` takes a project off it.
  A cloned project that ships its own `lvindex.toml` and extension runs nothing until you trust it. Zero-config
  never loads extensions. An assistant must not run `lvindex trust` for a project the user did not ask it to
  trust: ask first, and read the extension file before trusting it.

## Template

```python
"""lvindex extension for <project>: <what counterpart, read-only>. Settings: <keys>."""
import os

HELP = "  thing <term>          <one line>"
API = None
_cache = {}


def setup(api):
    global ROOT
    ROOT = api.path(api.config.get("root"))


def load():
    if not _cache:
        names = {}
        if ROOT and os.path.isdir(ROOT):
            ...                                  # parse the counterpart: names[name] = where declared
        _cache["names"] = names
    return _cache["names"]


def find(api, term):
    hit = sorted(n for n in load() if term.lower() in n.lower())
    return [f"-- <counterpart>: {len(hit)} names match: {', '.join(hit[:8])}  (thing {term})"] if hit else []


def thing(p, o):
    lit = API.literals()
    for n in sorted(x for x in set(load()) | set(lit) if p[0].lower() in x.lower())[:int(o.get("-n", 8))]:
        print(f"== {n}  declared: {load().get(n, 'NO')}  VIs: {', '.join(lit.get(n, [])) or 'none'}")


COMMANDS = {"thing": (thing, 1, 1)}
```
