# lvindex

An offline index and review cache for LabVIEW projects, built to let an AI assistant (or a human) reason about a
program's logic without exporting and reading whole VIs again and again.

- **Skeleton** of the whole project, read straight from the `.vi` / `.ctl` / `.lvclass` binaries. LabVIEW is not
  needed for this part. It records:
  - every VI and its calls, with dynamic dispatch resolved;
  - classes, interfaces and libraries;
  - typedefs and private-data fingerprints;
  - string literals.

  It is incremental: a rebuild of a ~450-VI project takes about 2.6 s, an update about 0.2 s.
- **Cards**: compact facts of an exported VI, a small fraction of the AIXML export. Each card lists:
  - calls and handler frames (message cases, event cases);
  - messages sent and the queues they go to;
  - constants and tagged literals.
- **Review cache**: `check` tells which VIs changed since their last export. `update` prints a semantic diff
  against the cached export, so unchanged VIs never have to be read again.
- **Queries**: `overview`, `ctx`, `callers`, `calls`, `uses`, `class`, `card`, `find`, `msgs`, `path`, and more.
  Each answers in about 0.2 s. `diff <ref>` compares the source files of two git revisions at skeleton level.

Measured on a ~460-VI project (tokens approximated as characters / 4, what an assistant reads to answer):

| Task | lvindex vs reading exports |
|---|---|
| handler frames of a message loop / one handler | 47x / 218x fewer tokens than its export |
| usage context of a VI | 14x |
| reviewing an edit of a regenerated VI | 29x fewer than re-reading the export |
| callers of a method, uses of a literal, a message's sender and handler | 2–6x fewer than grep over the exports |

The grep comparison flatters grep: it ran only over the exported fifth of the project, and grep resolves neither
dynamic dispatch nor message routing. Median over these 7 tasks: 14x.

The format knowledge (RSRC link records, AIXML structure) is reverse-engineered and not documented by NI. Treat the
output as facts extracted with care, not as a guarantee. Heuristic parts are labelled as such in the output.

## Requirements

- Python 3.11+, standard library only.
- For cards and diffs: AIXML exports of VIs. The tools were built against exports made with `lvai_convert_vis_to_aixml`
  of the [labview-mcp](https://github.com/Zuehlke/labview-mcp) plugin for Claude, from both of its backends: grpc
  (LabVIEW running) and pylabview (no LabVIEW needed; the diffs list the constants it could not decode as not
  compared, and a few node kinds are missing from its exports). The skeleton and the queries built on it work without
  any export.
- Windows (the only platform tested).

## Quick start

Install it (`pip install -e C:\path\to\lvindex` gives the `lvindex` command), or run it from the checkout
without installing (`python -I C:\path\to\lvindex\lvindex.py ...`):

```bat
cd C:\path\to\MyProject
lvindex overview
lvindex callers "My Class:Do Something.vi"
lvindex check "My Class:Do Something.vi"
```

Run `lvindex` without arguments for the full manual. `<vi>` can be a path, a qualified name (`Class:File.vi`),
or a unique substring of one.

## Project and configuration

The project is found from the current folder, in this order:
1. `--project <folder | lvindex.toml>`
2. `.claude\lvindex.toml`, walking up from the current folder
3. the outermost folder with a single `.lvproj` (zero-config)

Outside a project the tools stop with an error.

Zero-config runs the core only: skeleton, cache and cards. Optional layers are switched on by sections of
`lvindex.toml`:
- `[tags]`: indexed literals and their read / write / wait roles;
- `[messages]`: your queued-message library;
- `[actors]`: message-loop classes and the names of their queue accessors.

See [examples/lvindex.toml](examples/lvindex.toml) and the docstring of `src/lvindex/config.py`.

## Extensions

Anything specific to one project, such as a PLC, a protocol, a driver or a config-file format, belongs in an
**extension**: one Python file kept in the project next to its `lvindex.toml` and listed there as
`[extensions.<name>]`. An extension adds commands and extra lines to `find` and `overview`, and reads the index
through a small API. It is loaded only when listed and after you run `lvindex trust` once in that project
(a cloned project cannot run its own code on you); never in zero-config. [EXTENSIONS.md](EXTENSIONS.md) explains
when to write one and how (also as instructions for an AI assistant). [examples/ext_config_keys.py](examples/ext_config_keys.py)
is a working example.

The cache lives in `<project root>\.index`. The folder gets its own `.gitignore`, so it never shows up in the
project's repository. It holds:
- `skeleton.json`;
- `index.json` with the cards and export metadata;
- `xml\` with the cached exports.

## Workflow for VI review

1. Save the VI in LabVIEW, then run `check <vi>` (`--with-callers` adds its direct callers). It prints UNCHANGED,
   SAME, CHANGED or NEW, and adds `DEPS?` when a typedef, the class private data or the connector pane of a
   called subVI changed since the export.
2. Export only the CHANGED / NEW VIs to AIXML.
3. Run `update <vi> <export.xml>` to see the diff, store the export and self-check it against the saved file
   (`update --dir <folder>` does this for every export in a folder; of several exports of one VI only the newest is
   applied). The diff starts with a CARD DELTA: terminals, calls, primitives, constants, case selectors, messages
   and events, for the VI and per handler frame, compared by name, so it also shows what changed when a
   regeneration renumbered every uid. It does not compare wiring; the semantic diff per uid that follows does.
   `--backend <name>` records which exporter made the export, so a later backend change is pointed out.
4. Read the diff and the cards, plus `card <vi> --xml <frame>` for the one case you need, instead of the whole VI.

If the cache gets out of step, `repair` re-adopts the exports in `xml\` that no index entry points to, and `recard`
rebuilds all cards from the stored exports. A corrupt `index.json` is kept as `index.json.corrupt-<time>`, not
overwritten; `repair` then restores the entries from `xml\`.

## Using it from an AI assistant

[skills/lvindex/SKILL.md](skills/lvindex/SKILL.md) is the usage contract for an assistant: index before reading a
VI, callers before editing, check / export / update and the CARD DELTA after an edit. For Claude Code copy the
`skills\lvindex` folder to `%USERPROFILE%\.claude\skills\` (all projects) or to the project's `.claude\skills\`.

## Files

| File (`src/lvindex/`) | Role |
|---|---|
| `rsrc.py` | LabVIEW RSRC container reader |
| `skeleton.py` | project skeleton from the binaries: owners, calls, dispatch, fingerprints |
| `cards.py` | cards and handler frames from AIXML |
| `cache.py` | cache, check / update / semantic diff, command dispatch |
| `query.py` | index queries |
| `config.py` | project discovery and configuration |
| `ext.py` | extension loader and API |
| `cli.py` | command line entry point (`lvindex`, `python -m lvindex`, `lvindex.py`) |

Tests: `python -I -m unittest discover -s tests` (runs in CI: diffs on hand-written AIXML, connector-pane
fingerprints on synthetic type blocks, check / update / repair through the CLI on a temporary zero-config project);
`python -I tests/golden.py <suite.toml> [--update]` compares query outputs on a real project with stored ones
(the suite format is in its docstring; keep suites of private projects out of the repository).

Design notes and known gaps: [ROADMAP.md](ROADMAP.md).

LabVIEW is a trademark of National Instruments / Emerson. This project is not affiliated with NI.

License: MIT, see [LICENSE](LICENSE).
