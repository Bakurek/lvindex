# Changelog

## Unreleased

- **Case table**: `card <vi> --cases` lists the case structures that are not handler frames, as a tree: where the
  selector value comes from (terminal, shift register, subVI output, unbundled field, output of another case, a
  primitive with its inputs), then one line per selector value with that branch's own calls, sends, constants,
  flags, short strings and primitives, `(no nodes)` for an empty branch, nested cases indented below. `--frame NAME`
  keeps those inside matching handler frames. The default card says how many such case structures a VI has.
- Constants in frame cards: a cluster whose first field is an enum shows that field (`Abort.Reason=AppError`),
  unless a Bundle By Name sets that field anyway; a constant used only as the type input of Variant To Data is no
  longer listed.
- **VI summaries**: `summary template / set / questions / answer / stats`. The assistant writes a short summary of a
  VI (purpose, behavior, one line per handler frame, open questions) from its card and export; lvindex checks it
  against the card, binds it to the saved VI file (stale when the VI changes, kept across re-exports and `recard`),
  stores it in `semantic.json` in the cache and shows it in `card`, `ctx` and `msgs`; `find` searches it when no fact
  matches. Questions the writer could not answer from the export are kept for the user; answers become notes.
  `[semantic] language` in lvindex.toml. Without summaries every output is unchanged. The skill has a new
  `semantic.md`, read before writing summaries.
- The skill is split: SKILL.md (about 3 KB, loaded in every session) keeps the read path, the markers and the rules;
  the check / update states and the CARD DELTA review moved to `review.md`, read before the first `check` or `update`.

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
