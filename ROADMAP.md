# Design notes and roadmap

## Design decisions
- **Project discovery** (config.py): `--project` / `LVINDEX_PROJECT`, then `.claude\lvindex.toml` walking up from the
  current folder, then the outermost folder with a single `.lvproj` (zero-config, announced on stderr). Nothing found
  = error; the tools never fall back to a project they happen to be stored in. lvindex.toml is read as UTF-8 with an
  optional BOM.
- **Cache** per project in `<project root>\.index` (a new folder gets `.gitignore` = `*`); `cache` in lvindex.toml
  overrides it. index.json stores export names relative to `xml\` and keys relative to `src`, so both the cache folder
  and the project can be moved (older absolute keys are migrated on load, also after a move). Writes hold
  `index.lock`; a corrupt index.json is kept as `index.json.corrupt-<time>` and `repair` re-adopts the exports. A
  CHANGED seen by `check` is recorded as pending and cleared when the file is back to its exported state, so a
  reverted edit does not block `update`.
- **Binary layer**: the skeleton comes from lvindex's own RSRC reader, not from pylabview. Measured on 70 files of
  two projects: pylabview read all of them, but left the qualified names of the dispatch, typedef and private-data
  link records (DyOM, TCPI, FPPI, DDPI) as raw bytes - exactly the records the skeleton is built from - and took
  about 0.5 s per file against about 6 ms (a full build of a ~450-VI project would grow from 2.6 s to minutes).
  pylabview is used through labview-mcp for AIXML exports without LabVIEW only; the core stays standard library
  only.
- **Config hash**: only `skip` and `tags.literal` invalidate the skeleton; the card signature covers the card-related
  settings, so cards are rebuilt from the stored XML when they change.
- **Optional layers**, off when the section is absent: `[tags]` (literal regex + roles), `[messages]` (enqueue /
  dequeue VIs, ports), `[actors]` (base class, entry VI, queue accessor names), `status_enum`, `hide`, `hide_calls`,
  `skip`.
- **Extensions** for anything project-specific (PLC, protocols, config formats): one file in the project, listed in
  lvindex.toml, loaded only then and only after `lvindex trust`; hooks `COMMANDS`, `find`, `overview`, `HELP`;
  failures are isolated; `API_VERSION` refuses an extension written for another API (EXTENSIONS.md). The core
  contains no PLC / protocol / driver knowledge.
- **Skeleton naming**: owner = innermost class / library; name collisions are resolved (`Name [Lib]`, `Name.lvlib`,
  relative-path VI keys) instead of dropping VIs; owners are resolved by candidates (the caller's library chain, the
  link chain, suffixes), classes win over same-name libraries; parents outside `src` are kept as `extern_parent`;
  interfaces dispatch to implementers.
- **Dependency check** (`check` DEPS?): fingerprints of the typedefs a VI links (nested too), of its own class's
  private data and of the connector panes of the project subVIs it calls directly, compared with the state at
  export time (fingerprint history in skeleton.json). The pane fingerprint comes from CONP -> the VCTP function type:
  pattern, and per used slot its label, type (cluster / array / refnum element indices resolved, so the order of the
  type list does not matter; a typedef by name) and the output / dynamic-dispatch flags. Required / recommended flags
  are left out: they do not change a caller's export.
- **Cards**: innermost call-target naming; message envelopes recognised by their `{string, variant}` shape; without
  `[messages]` handler frames come from a dispatcher heuristic (a case structure inside a loop whose selector comes
  from a subVI output or a shift register, with >= 2 named cases).
- **Edit review** (`update`, `semdiff`): the CARD DELTA first - terminals, calls, primitives, constants, case
  selectors, messages and events as multisets, for the VI outside its handler frames and per frame, matched by name
  (same-name frames by the smallest difference). Measured on a real regeneration: most uids are renumbered, frames
  are reordered, "Error" becomes "Error ..", so the per-uid diff that follows is useful only where uids stay stable
  (an edit made in LabVIEW; an unchanged VI exports with the same uids). Wiring is compared only by the per-uid diff.
- **Queries**: `overview` lists build-spec entry points (fallback: VIs nobody calls), interfaces and external-parent
  groups; `path` walks calls with dispatch expanded plus message hops from current cards; `diff <ref>` compares
  skeleton facts of the source files between git revisions and reads git only (ls-tree, hash-object without `-w`,
  cat-file); `msgs` only with `[messages]`; extension commands only when the extension is listed and trusted.
  `--max-tokens` caps any output in whole lines.
- **Tests**: CI runs ruff and unit tests on hand-written AIXML, synthetic type blocks and the cache commands on a
  temporary zero-config project (windows-latest). The binary
  readers are checked locally against real projects with `tests/golden.py`; those suites and their outputs stay
  outside the repository, because project VIs cannot be published.

## Known gaps
- CI does not exercise the binary readers (rsrc.py, skeleton.py) on real VI files: a format regression is visible
  only in local golden runs (the save versions those runs cover are listed in the README requirements).
- Configuration is read at import time (module globals in cache.py, query.py, cards.py): no in-process use as a
  library, no in-process tests across projects, and it stands in the way of `--json` and an MCP wrapper.
- Pane DEPS?: a typedef terminal of a subVI counts by name only, its content is caught only when the caller links
  that typedef itself (usually true when it wires the terminal); refnum element types are resolved heuristically;
  a VI whose pane type is not a plain function type is not tracked. Caches from before the pane check track panes
  from the first `check` after the upgrade.
- A typedef fingerprint is the hash of the whole `.ctl`, so a resave without a change gives `DEPS?` (and shows in
  `diff` as a content change).
- The AIXML backend of an export is recorded only when `update` / `seed` get `--backend` (pointed out when both are
  known). The diffs normalise what exporters write differently (input order, unwired inputs, `\3A` / `\XX` escapes,
  constant values in wire sources); constants the pylabview reader does not decode are listed as not compared. What
  is left between a grpc and a pylabview export of the same VI are reader gaps (unnamed terminals, missing nodes,
  flattened unbundle paths, dispatch call naming), not removed by lvindex.
- A class member with class-typed terminals cannot be regenerated through AIXML (labview-mcp refuses it), so an
  assistant's edit loop covers non-class VIs only; class code is edited in LabVIEW and reviewed with `check` /
  `update`.
- Cards exist only for exported VIs. Aggregate answers (handlers in `overview`, `msgs`, message hops in `path`, roles
  in `find`) are partial where cards are missing or stale.
- `diff` against the working tree also lists untracked files, runs the clean filters of the path (e.g. git-lfs),
  and shows a case-only rename as removed + added.
- No generic queue identity across VIs; no table of native queue / notifier node names.
- `.llb` / `.lvlibp` contents are not read; VIs called by reference or by name have no static callers - `callers`
  and `ctx` only hint at them (`by name?`: the file name appears as a string in another VI's diagram).
- The extension API is versioned, but `api.V` / `api.K` / `api.S` expose core internals outside that version.
- Nothing Actor Framework / DQMH / JKI specific (deliberately); the zero-config heuristics were tuned on one family
  of queued-message-handler projects.

## Roadmap
Ordered; each step is decided on what the previous one measured.
1. **Measure use by an assistant** before adding layers: in real sessions, does it call lvindex or still read
   exports, which commands, where it falls back, and which wrong answers came from heuristics or stale cards.
2. **Configuration as a context object** instead of module globals: library use, in-process tests, `--json` on
   queries, a thin MCP wrapper if step 1 shows a need.
3. **Honest partial answers**: aggregate queries state how many relevant VIs lack a current card. Cards for
   unexported VIs can come from the pylabview reader without LabVIEW. Measured on the 133 cached VIs of a real
   project: no failures, median 0.77 s per VI; of the 97 VIs unchanged since their LabVIEW export, 83 gave an empty
   CARD DELTA against it, the other 14 differ by reader gaps (see Known gaps).
4. **Generality**: golden runs on Actor Framework, DQMH and JKI state-machine projects; judge the zero-config output
   there; make `[actors] entry` an explicit setting.
5. **Publishable binary fixtures** for CI, saved in more than one LabVIEW version, so CI exercises the RSRC reader.
6. Then, in about this order:
   - per-VI notes (intent, invariants) that survive re-export, starting with the VI documentation the cache now
     strips; location to be decided (versioned with the project, not in the cache);
   - lints that need no notes: a flag written and never read, a dynamically called VI, a magic constant, a
     dispatcher case with no visible reason;
   - Call By Reference / strictly-typed VI refnums matched to VIs by connector pane;
   - `diff` for teams without an assistant: an example CI job or hook that posts the skeleton facts of a pull request
     (git diffs of .vi files are opaque, LVCompare needs LabVIEW);
   - readers and writers of globals / functional globals / queues / notifiers, error propagation;
   - a simpler review workflow (one `sync` step instead of check -> export -> update) once step 1 shows where an
     assistant stumbles;
   - `doc` (Markdown from cards and notes), card-level `diff` where exports of both revisions exist.
