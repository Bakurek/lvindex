# lvindex: storing exports and reviewing an edit

Read this before the first `check` or `update` of a session. SKILL.md has the short version.

## Storing exports

- With the lvindex plugin a hook stores every labview-mcp export by itself: a message starting with `lvindex hook:`
  follows the export tool's answer. It holds the CARD DELTA against the export it replaced (cut to about 1200
  characters) - after an edit, that is the delta to check; `lvindex show <vi> --diff` prints it in full, with the
  per-uid diff (wiring). Do not run `update` for that export again (it would only print NO CHANGE). A hook message
  that starts with "NOT stored cleanly" lists the exports that need action first.
- No `lvindex hook:` message: run `update --dir <folder> --backend <name>` yourself (`<name>` = the `backend` the
  export tool answered, e.g. grpc; leave it out when none). It prints a short CARD DELTA per VI; `--diff` adds the
  per-uid diff with wiring. For one export: `update <vi> <export.xml> --backend <name>` (full output).
- The whole export is read only when the question is about wiring that `card <vi> --xml NAME` does not show; it is
  stored like any export, so the next question about this VI does not need it.

## After an edit (labview-mcp regeneration, or the user editing in LabVIEW)

1. The VI must be saved: `check` reads the file on disk, an export may come from LabVIEW memory.
2. `check <vi>` (`--with-callers` also checks its direct callers):
   - UNCHANGED / SAME = nothing to do; CHANGED / NEW = export.
   - `DEPS?` = a typedef or the class private data changed since the export: export again, or `ackdeps <vi>` after
     checking it does not matter. `DEPS? ... (unknown at export)` = the dependency was saved after the export before
     its state was recorded, same choice.
   - `DEPS? pane <subVI>` = the connector pane of a called subVI changed (terminal added, removed, renamed, retyped,
     moved): the caller's export is outdated although its file is unchanged - export the caller again.
3. Export the CHANGED / NEW VIs with labview-mcp `lvai_convert_vis_to_aixml` (refresh=true) into a scratch folder.
   A class member with class-typed terminals cannot be regenerated through AIXML at all (both labview-mcp backends
   refuse it); an edit of such a VI is the user's, in LabVIEW.
4. Read the CARD DELTA (from the hook message, or from `update` as above):
   - It matches by name, not by uid, so it stays valid when a regeneration renumbered every uid. It compares
     terminals (name, connector index, type), calls, primitives, constants, case selectors, messages, tags and
     events, for the VI outside its handler frames `(VI)` and per handler frame.
   - Confirm that only the intended frames changed. Anything else (a call lost, a frame gone, a constant or terminal
     changed) is a regression of the edit: tell the user before going on.
   - It does not compare wiring. The per-uid diff below it does, but after a regeneration (it then says that few old
     uids are left) it lists most objects as removed and added; for a wiring question read the affected frame with
     `card <vi> --xml NAME` instead.
   - `? constants not compared` = the exporter (pylabview) could not decode those values, so a change of them is
     invisible: if the edit may have touched one, export the VI with the grpc backend and update again.
5. Other answers of `update`:
   - `NO CACHED VERSION` / `NEW CARD` = first export of this VI: nothing to compare, read the card.
   - `NO CHANGE` = identical to the cached export (nothing stored); `NOT confirmed` after it = the VI file changed but
     the export did not: save the VI in LabVIEW and export again.
   - `REFUSED ... older than the cached one` = a newer export is already stored. `REFUSED` (saved during the export)
     = `check` and export again.
   - `SELF-CHECK MISMATCH` = the export came from unsaved edits (save, export again) or the skeleton misread the file;
     `SELF-CHECK skipped` = the VI is outside the skeleton.
   - With `--dir`, of several exports of one VI only the newest is applied, the others print `SKIPPED`;
     `NO UNIQUE VI` (a name that fits several VIs) and `NOT STORED` (matched by name only, SELF-CHECK MISMATCH) store
     nothing - run `update <vi> <xml>` for those after checking which VI the export belongs to.
   - A `NOTE` about a backend change = differences may come from the two backends, not from the VI.

## Cache problems

- `WARNING: index.json corrupt`: run `repair` once and tell the user; the old file is kept next to it.
- Never edit the cache folder (`.index`) by hand; `repair` and `recard` fix it.
