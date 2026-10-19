# Design notes and roadmap

## Design decisions
- **Project discovery** (config.py): `--project` / `LVINDEX_PROJECT`, then `.claude\lvindex.toml` walking up from the
  current folder, then the outermost folder with a single `.lvproj` (zero-config, announced on stderr). Nothing found
  = error; the tools never fall back to a project they happen to be stored in.
- **Cache** per project in `<project root>\.index` (a new folder gets `.gitignore` = `*`); `cache` in lvindex.toml
  overrides it. index.json stores export names relative to `xml\` and keys relative to `src`, so both the cache folder
  and the project can be moved (older absolute keys are migrated on load, also after a move).
- **Config hash**: only `skip` and `tags.literal` invalidate the skeleton; the card signature covers the card-related
  settings, so cards are rebuilt from the stored XML when they change.
- **Optional layers**, off when the section is absent: `[tags]` (literal regex + roles), `[messages]` (enqueue /
  dequeue VIs, ports), `[actors]` (base class, entry VI, queue accessor names), `status_enum`, `hide`, `hide_calls`,
  `skip`.
- **Extensions** for anything project-specific (PLC, protocols, config formats): one file in the project, listed in
  lvindex.toml, loaded only then; hooks `COMMANDS`, `find`, `overview`, `HELP`; failures are isolated
  (EXTENSIONS.md). The core contains no PLC / protocol / driver knowledge.
- **Skeleton naming**: owner = innermost class / library; name collisions are resolved (`Name [Lib]`, `Name.lvlib`,
  relative-path VI keys) instead of dropping VIs; owners are resolved by candidates (the caller's library chain, the
  link chain, suffixes), classes win over same-name libraries; parents outside `src` are kept as `extern_parent`;
  interfaces dispatch to implementers.
- **Cards**: innermost call-target naming; message envelopes recognised by their `{string, variant}` shape; without
  `[messages]` handler frames come from a dispatcher heuristic (a case structure inside a loop whose selector comes
  from a subVI output or a shift register, with >= 2 named cases).
- **Queries**: `overview` lists build-spec entry points (fallback: VIs nobody calls), interfaces and external-parent
  groups; `msgs` only with `[messages]`; extension commands only when
  the extension is listed and trusted.

## Known gaps
- SubVI connector panes (DEPS? `pane <subVI>`): the fingerprint covers pattern, slot labels, types and the input /
  output and dynamic-dispatch flags; required / recommended flags are left out, and a typedef terminal counts by
  name only (its content is the typedef's own DEPS? entry). Element types of refnums are resolved heuristically.
  Caches from before the pane check start tracking panes at the first `check` after the upgrade.
- The AIXML backend of an export is recorded only when `update` / `seed` get `--backend`; exports from different
  backends can produce card and diff churn (pointed out when both are known).
- The extension API (`ext.Api`) is versioned (`API_VERSION`), but `api.V` / `api.K` / `api.S` expose core
  internals outside that version and may change.
- Tests: CI covers the diffs on hand-written AIXML (`tests/test_diff.py`); the binary readers are checked locally
  against real projects with `tests/golden.py`, whose suites and expected outputs stay outside the repository
  (project VIs cannot be published).
- No generic queue identity across VIs; no table of native queue / notifier node names.
- `.llb` / `.lvlibp` contents are not read; VIs called by reference or by name have no static callers - `callers`
  and `ctx` only hint at them (`by name?`: the file name appears as a string in another VI's diagram).
- Nothing Actor Framework / DQMH / JKI specific (deliberately).
