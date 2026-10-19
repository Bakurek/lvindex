# Changelog

## 0.3.0 - 2026-10-19

First tagged release. Earlier `0.2.0` builds were never tagged; this lists what the release contains.

- **Skeleton** of a whole project read from the `.vi` / `.ctl` / `.lvclass` binaries without LabVIEW: calls with
  dynamic dispatch resolved, classes, interfaces, libraries, typedef and private-data fingerprints, connector panes,
  string literals. Incremental, cached in the project's `.index` folder.
- **Queries**: `overview`, `ctx`, `callers` (with `by name?` hints), `calls`, `uses`, `class`, `card`, `find`, `msgs`,
  `path` (call paths and message hops), `unexported`, and `diff <ref>` (skeleton facts between git revisions).
  `--max-tokens N` on every command.
- **Review cache**: `check` (UNCHANGED / SAME / CHANGED / NEW, `DEPS?` for changed typedefs, class private data and
  subVI connector panes), `update` with a CARD DELTA that stays valid when a regeneration renumbers every uid, then
  the semantic diff per uid; `update --dir`, `--brief`, `--backend`; `repair` and `recard`; a corrupt `index.json`
  is kept, not overwritten. Exports from both labview-mcp backends (grpc and pylabview) compare without cosmetic
  noise.
- **Configuration**: zero-config from a single `.lvproj`, optional `lvindex.toml` layers (`[tags]`, `[messages]`,
  `[actors]`), and project extensions that run only after `lvindex trust`.
- **Claude Code plugin**: the repository is a plugin marketplace (`/plugin marketplace add Bakurek/lvindex`); the
  plugin ships the usage skill and a hook that stores every labview-mcp export in the cache.
- **CLI**: install with `pipx` or `uv tool`; `lvindex --version` shows the installed commit; the manual (`lvindex`,
  `--help`) also prints outside a project; an unknown command or option says how to upgrade when a skill names it.
- Tested on Windows with Python 3.11 to 3.14 and on projects saved by LabVIEW 2015 to 2026 Q3.
