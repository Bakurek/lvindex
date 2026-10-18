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
- **Cards**: compact facts of an exported VI, about 20–40x smaller than the AIXML export. Each card lists:
  - calls and handler frames (message cases, event cases);
  - messages sent and the queues they go to;
  - constants and tagged literals.
- **Review cache**: `check` tells which VIs changed since their last export. `update` prints a semantic diff
  against the cached export, so unchanged VIs never have to be read again.
- **Queries**: `overview`, `ctx`, `callers`, `calls`, `uses`, `class`, `card`, `find`, `msgs`, and more. Each answers
  in about 0.2 s.

The format knowledge (RSRC link records, AIXML structure) is reverse-engineered and not documented by NI. Treat the
output as facts extracted with care, not as a guarantee. Heuristic parts are labelled as such in the output.

## Requirements

- Python 3.11+, standard library only.
- For cards and diffs: AIXML exports of VIs. The tools were built against exports made with `lvai_convert_vis_to_aixml`
  of the [labview-mcp](https://github.com/Zuehlke/labview-mcp) plugin for Claude. The skeleton and the queries built on it work
  without any export.
- Windows (the only platform tested).

## Quick start

```bat
cd C:\path\to\MyProject
python -I C:\path\to\lvindex\vi_cache.py overview
python -I C:\path\to\lvindex\vi_cache.py callers "My Class:Do Something.vi"
python -I C:\path\to\lvindex\vi_cache.py check "My Class:Do Something.vi"
```

Run `vi_cache.py` without arguments for the full manual. `<vi>` can be a path, a qualified name (`Class:File.vi`),
or a unique substring of one.

## Project and configuration

The project is found from the current folder, in this order:
1. `--project <folder | vi-tools.toml>`
2. `.claude\vi-tools.toml`, walking up from the current folder
3. the outermost folder with a single `.lvproj` (zero-config)

Outside a project the tools stop with an error.

Zero-config runs the core only: skeleton, cache and cards. Optional layers are switched on by sections of
`vi-tools.toml`:
- `[tags]`: indexed literals and their read / write / wait roles;
- `[messages]`: your queued-message library;
- `[actors]`: message-loop classes;
- `[plc]`: a TwinCAT project to cross-check symbols against.

See [examples/vi-tools.toml](examples/vi-tools.toml) and the docstring of `vi_config.py`.

The cache lives in `<project root>\.index`. The folder gets its own `.gitignore`, so it never shows up in the
project's repository. It holds:
- `skeleton.json`;
- `index.json` with the cards and export metadata;
- `xml\` with the cached exports.

## Workflow for VI review

1. Save the VI in LabVIEW, then run `check <vi>`. It prints UNCHANGED, SAME, CHANGED or NEW, and adds `DEPS?` when a
   typedef or the class private data changed since the export.
2. Export only the CHANGED / NEW VIs to AIXML.
3. Run `update <vi> <export.xml>` to see the semantic diff, store the export and self-check it against the saved file.
4. Read the diff and the cards, plus `card <vi> --xml <frame>` for the one case you need, instead of the whole VI.

## Files

| File | Role |
|---|---|
| `vi_rsrc.py` | LabVIEW RSRC container reader |
| `vi_skeleton.py` | project skeleton from the binaries: owners, calls, dispatch, fingerprints |
| `vi_cards.py` | cards and handler frames from AIXML |
| `vi_cache.py` | entry point: cache, check / update / semantic diff, command dispatch |
| `vi_query.py` | index queries |
| `vi_config.py` | project discovery and configuration |
| `vi_plc.py` | optional TwinCAT layer |

Design notes and known gaps: [ROADMAP.md](ROADMAP.md).

LabVIEW is a trademark of National Instruments / Emerson. This project is not affiliated with NI.

License: MIT, see [LICENSE](LICENSE).
