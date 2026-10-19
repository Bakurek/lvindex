---
name: lvindex
description: Use lvindex before reading, editing or reviewing VIs of a LabVIEW project that has a .claude\lvindex.toml or a single .lvproj. It answers "what does this VI do, who calls it, what changed" from an offline index instead of whole AIXML exports, and checks after an edit that only the intended part of a VI changed.
---

# Working on a LabVIEW project with lvindex

lvindex is a command line tool. Call it as `lvindex <command>` from inside the project folder; for another
folder pass `--project <folder | lvindex.toml>` rather than `cd` there. A user's permission rule for `lvindex *`
then allows the call without a prompt. If `lvindex` is not found, use `python -I -m lvindex <command>` (installed
with pip); if that fails too, stop and tell the user to install it
(`pipx install git+https://github.com/Bakurek/lvindex` or `uv tool install git+https://github.com/Bakurek/lvindex`),
do not look for a checkout. `lvindex` without arguments prints the full manual. `<vi>` is a path, a qualified name (`Class:File.vi`) or a unique substring of one.

Its output is facts extracted from the files. Parts marked as heuristic (`VIM?`, `sym?`, dispatcher frames without
a `[messages]` section) are guesses; say so when you pass them on.

## Before reading a VI

Read the index first and the VI's AIXML only for what the index cannot answer:
1. `card <vi>`: terminals, calls, messages sent, handled cases, events.
2. `ctx <vi>`: who reaches it from the entry points, through which handler frames, and what it uses.
3. `card <vi> --frames`, then `card <vi> --frame NAME` for one handler with its callee cards, and
   `card <vi> --xml NAME` for the XML of the frames whose name contains NAME.
4. The whole export only when the question is about wiring the card does not show.

To find your way around: `overview` (entry points, classes, message loops), `find <term>` (literals, messages,
events, calls; `-s` also free text), `msgs [name]` (sender -> queue -> handler, when configured), `path <a> <b>`
(how a reaches b: calls with dispatch, and message hops when configured), `diff <ref> [<ref2>]` (what changed in
the source files between git revisions, as skeleton facts - for reviewing a branch or a commit).
Long outputs: add `--max-tokens N` to any command; it says how much it left out.

Markers: `(no card)` = never exported, `(card stale)` = saved after its export, do not rely on that card,
`(not in lvclass)` = stray file in a class folder.

## Before editing a VI

- `callers <vi>`: who is affected, with dynamic dispatch expanded. VIs called by reference or by name are not
  static callers; a `by name?` line lists VIs whose diagram holds the file name as a string (heuristic). Say so
  when the list is the basis of a decision.
- `class <name>` before changing a method: overrides, children, interfaces.
- `uses <typedef|class>` before changing a typedef or a class's private data.

## After an edit (labview-mcp regeneration, or the user editing in LabVIEW)

1. The VI must be saved: `check` reads the file on disk, an export may come from LabVIEW memory.
2. `check <vi>` (`--with-callers` also checks its direct callers). UNCHANGED / SAME = nothing to do; CHANGED / NEW =
   export; `DEPS?` = a typedef or the class
   private data changed since the export: export again, or `ackdeps <vi>` after checking it does not matter;
   `DEPS? ... (unknown at export)` = the dependency was saved after the export before its state was recorded, same
   choice. `DEPS? pane <subVI>` = the connector pane of a called subVI changed (terminal added, removed, renamed,
   retyped, moved): the caller's export is outdated although its file is unchanged - export the caller again.
3. Export the CHANGED / NEW VIs with labview-mcp `lvai_convert_vis_to_aixml` (refresh=true) into a scratch folder.
   A class member with class-typed terminals cannot be regenerated through AIXML at all (both labview-mcp backends
   refuse it); an edit of such a VI is the user's, in LabVIEW.
4. `update <vi> <export.xml> --backend <name>` for each, or `update --dir <folder> --backend <name>` for all exports
   in a folder; `<name>` is the `backend` the export tool answered (e.g. grpc). With `--dir`, of several exports of
   one VI only the newest is applied, the others print `SKIPPED`. Read the CARD DELTA first. It matches by name, not by uid, so it stays
   valid when a regeneration renumbered every uid. It compares terminals (name, connector index, type), calls,
   primitives, constants, case selectors, messages, tags and events, for the VI outside its handler frames `(VI)`
   and per handler frame. Confirm that only the intended frames changed. Anything else (a call lost, a frame gone,
   a constant or terminal changed) is a regression of the edit: tell the user before going on.
   The CARD DELTA does not compare wiring. The per-uid diff below it does, but after a regeneration (it then says
   that few old uids are left) it lists most objects as removed and added; for a wiring question read the affected
   frame with `card <vi> --xml NAME` instead.
   `NO CACHED VERSION` = first export of this VI: there is nothing to compare, read the card. A `NOTE` about a
   backend change = differences may come from the two backends, not from the VI.
   `? constants not compared` = the exporter (pylabview) could not decode those values, so a change of them is
   invisible: if the edit may have touched one, export the VI with the grpc backend and update again.
5. `SELF-CHECK MISMATCH` = the export came from unsaved edits (save, export again) or the skeleton misread the
   file; `SELF-CHECK skipped` = the VI is outside the skeleton; `REFUSED` = the VI was saved during the export:
   `check` and export again.

## Do not

- Run `lvindex trust`, which allows a project's extension code to run, unless the user asked for it and you have
  read the extension file.
- Edit anything in the cache folder (`.index`) by hand; `repair` and `recard` fix it. After `WARNING: index.json
  corrupt` run `repair` once; tell the user, the old file is kept next to it.
- Present a list from `callers`, `unexported` or `find` as complete for VIs called dynamically.
