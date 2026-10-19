"""The manual `lvindex` prints without a command (also outside a project, see cli.py)."""

TEXT = """lvindex - VI review cache + logic index for a LabVIEW project. Run: lvindex [--project P] <command> ...
(without installing: python -I <repo>\\lvindex.py ...; `lvindex --version` = version and install source)

Project: found from the current folder (.claude\\lvindex.toml, else a single *.lvproj = zero-config) or --project
<folder | lvindex.toml>; see config.py. Optional layers (tags, messages, actors) and project extensions
(EXTENSIONS.md) exist only when configured.
<vi> anywhere = a file path, "Class:File.vi", or a unique substring of a qualified name.

Reviewing VIs (VI must be SAVED in LabVIEW first - the export may come from memory, check looks at disk):
  check <vi> [...]      UNCHANGED / SAME (hash equal) / CHANGED / NEW, + DEPS? when a typedef the VI links
                        (also nested), its own class's private data or the connector pane of a subVI it calls
                        ("pane <subVI>") changed since the cached export.
                        "(unknown at export)" = saved after the export before its state was recorded: re-export,
                        or `ackdeps` after checking it. --with-callers also checks the direct callers
  (export CHANGED/NEW with lvai_convert_vis_to_aixml refresh=true into the scratchpad)
  update <vi> <aixml>   diff vs the cached export, store it, SELF-CHECK of the export against the saved file;
                        refuses if the VI was saved during export. The diff: CARD DELTA (terminals, calls, primitives,
                        constants, cases, messages, events - for the VI and per handler frame, matched by name, so also
                        valid when a regeneration changed every uid; no wiring), then the semantic diff per stable uid
                        (+ - ~, wires resolved). --brief: CARD DELTA cut to about 1200 characters, the per-uid diff
                        only counted (`show <vi> --diff` prints both later). An export identical to the cached one
                        (e.g. stored by the plugin hook) prints NO CHANGE and stores nothing; an export older than
                        the cached one is REFUSED
  update --dir <folder> the same, brief (--diff = full output), for every export in a folder, matched to its VI by
                        the path hash in labview-mcp's
                        file name (<VI>.<8 hex>.xml), else by the name in the export; a name that fits several VIs
                        (same-name copies outside any class) is refused, and a name-only match with SELF-CHECK
                        MISMATCH is not stored
  [--backend NAME]      on update / seed: the AIXML backend that made the export (labview-mcp answers it as
                        `backend`); a change of backend against the cached export is pointed out
  seed <vi> <aixml>     store an export without diff (VI not saved after the export)

Understanding the program (skeleton = all VIs from the binary files, no export; card = facts of an exported VI):
  overview              project map: entry points, classes / interfaces, message loops and handlers, optional
                        layers, coverage
  ctx <vi>              usage context: call chains from the roots, handler frames on the way, callees, typedefs,
                        tagged literals, card state
  card <vi> [--frames | --frame NAME | --xml NAME]   card of an exported VI; per-handler cards (message cases or
                        dispatcher cases + Event Structure frames, callee card state); --frame also prints the callee
                        cards (one hop); --xml = XML of that frame only (or of any case whose selector matches).
                        Nested cases are flattened into the frame
  find <term> [-s] [-n N]   matched items: tagged literals (role from the card, "sym?" = role unknown), messages
                        and events (cards), calls, extension hits; -s also VIs that match only by a string
  callers <vi> [-n N]   dispatch expanded: DyOM on a wire of class W runs W's nearest implementation or an override
                        in a descendant / implementer of W; CPM = Call Parent Method; VIM? = malleable VI (heuristic)
  calls <vi> [-d N] [--all]   callees as a tree (hidden plumbing shown with --all)
  uses <typedef|class>  VIs linking a typedef (also through nested typedefs) or a class
  class <name> [--all]  superclass / external parent, interfaces, children, overrides, typedefs, methods
  unexported [filter] [--all] [-n N]   VIs without a current card, with file size
  msgs [name]           [messages]: senders -> queue owner -> handler (cards only; gaps printed as such)
  diff <ref> [<ref2>]   what changed in the source files between two git revisions (or a revision and the working
                        tree, untracked files included), as skeleton facts: calls, typedefs, literals, connector pane,
                        class parents and members; a typedef shows any resave as a content change
  path <a> <b> [-n N]   shortest call paths from a down to b (dispatch expanded; with [messages] + [actors] also
                        message hops sender -> owning actor's entry VI, from current cards)
Any command: --max-tokens N cuts the output at about N tokens (whole lines) and says how much was left out.
Markers: "(no card)" never exported, "(card stale)" VI saved after its export - card not used,
  "(not in lvclass)" file in a class folder but not a member (stray copy or dead code).
Call kinds: IUVI static, DyOM dynamic dispatch, CPM Call Parent Method, PUPV polymorphic, SVVI static VI reference,
  VIM? malleable VI guessed from a .vim name in the diagram.
Limits: roles, messages and events exist only for exported VIs; names built at run time are invisible to literals;
  VIs called by reference or by name have no callers; .llb / .lvlibp contents are not read.

Maintenance: ackdeps <vi>... | show <vi> [--diff] | semdiff <a.xml> <b.xml> | skeleton [--full] | repair | recard | list
Extensions: trust [--remove] allows the [extensions] of this lvindex.toml to run (once per project and user)
Store: the project's cache folder (default <project root>\\.index, lvindex.toml `cache` overrides):
  index.json (keys = VI paths relative to src, export names relative to xml\\), xml\\, skeleton.json,
  index.lock (held while index.json is modified), .gitignore (`*`, written when the folder is created).
"""
