# Design notes and roadmap

## Design decisions
- **Project discovery** (config.py): `--project` / `VI_PROJECT`, then `.claude\vi-tools.toml` walking up from the
  current folder, then the outermost folder with a single `.lvproj` (zero-config, announced on stderr). Nothing found
  = error; the tools never fall back to a project they happen to be stored in.
- **Cache** per project in `<project root>\.index` (a new folder gets `.gitignore` = `*`); `cache` in vi-tools.toml
  overrides it. index.json stores export names relative to `xml\`, so the folder can be moved.
- **Config hash**: only `skip` and `tags.literal` invalidate the skeleton; the card signature covers the card-related
  settings, so cards are rebuilt from the stored XML when they change.
- **Optional layers**, off when the section is absent: `[tags]` (literal regex + roles), `[messages]` (enqueue /
  dequeue VIs, ports), `[actors]` (base class, entry VI, queue accessor names), `status_enum`, `hide`, `hide_calls`,
  `skip`.
- **Extensions** for anything project-specific (PLC, protocols, config formats): one file in the project, listed in
  vi-tools.toml, loaded only then; hooks `COMMANDS`, `find`, `overview`, `HELP`; failures are isolated
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
- `check` does not notice a changed connector pane of a subVI: a caller whose file did not change shows UNCHANGED
  although a fresh export could differ. Planned: subVI connector-pane fingerprints in the DEPS? check.
- The AIXML backend that produced an export is not recorded; exports from different backends can produce card and
  diff churn.
- The extension API (`ext.Api`) is young: `api.V` / `api.K` / `api.S` expose core internals that may change.
- No tests yet: a small fixture LabVIEW project (class, dispatch, QMH) is needed; project VIs cannot be published.
- No generic queue identity across VIs; no table of native queue / notifier node names.
- Index keys are absolute VI paths: moving a project means re-exporting.
- `.llb` / `.lvlibp` contents are not read; VIs called by reference or by name have no callers.
- Nothing Actor Framework / DQMH / JKI specific (deliberately).
