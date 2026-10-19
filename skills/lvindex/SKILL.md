---
name: lvindex
description: Use lvindex before reading, editing or reviewing VIs of a LabVIEW project that has a .claude\lvindex.toml or a single .lvproj. It answers "what does this VI do, who calls it, what changed" from an offline index instead of whole AIXML exports, and checks after an edit that only the intended part of a VI changed.
---

# Working on a LabVIEW project with lvindex

lvindex is a command line tool: `lvindex <command>` when installed, otherwise
`python -I <lvindex checkout>\lvindex.py <command>`. Run it from inside the project folder (or pass
`--project <folder | lvindex.toml>`). `lvindex` without arguments prints the full manual. `<vi>` is a path, a
qualified name (`Class:File.vi`) or a unique substring of one.

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
events, calls; `-s` also free text), `msgs [name]` (sender -> queue -> handler, when configured).

Markers: `(no card)` = never exported, `(card stale)` = saved after its export, do not rely on that card,
`(not in lvclass)` = stray file in a class folder.

## Before editing a VI

- `callers <vi>`: who is affected, with dynamic dispatch expanded. VIs called by reference or by name do not show
  up there; say so when the list is the basis of a decision.
- `class <name>` before changing a method: overrides, children, interfaces.
- `uses <typedef|class>` before changing a typedef or a class's private data.

## After an edit (labview-mcp regeneration, or the user editing in LabVIEW)

1. The VI must be saved: `check` reads the file on disk, an export may come from LabVIEW memory.
2. `check <vi>`. UNCHANGED / SAME = nothing to do; CHANGED / NEW = export; `DEPS?` = a typedef or the class
   private data changed since the export: export again, or `ackdeps <vi>` after checking it does not matter;
   `DEPS? ... (unknown at export)` = the dependency was saved after the export before its state was recorded, same
   choice. `check` does not notice a changed connector pane of a subVI: its callers stay UNCHANGED. When an edit
   changed a connector pane, export the callers (`callers <vi>`) as well, or tell the user their cards may be stale.
3. Export the CHANGED / NEW VIs with labview-mcp `lvai_convert_vis_to_aixml` (refresh=true) into a scratch folder.
4. `update <vi> <export.xml>` for each. Read the CARD DELTA first. It matches by name, not by uid, so it stays
   valid when a regeneration renumbered every uid. It compares terminals (name, connector index, type), calls,
   primitives, constants, case selectors, messages, tags and events, for the VI outside its handler frames `(VI)`
   and per handler frame. Confirm that only the intended frames changed. Anything else (a call lost, a frame gone,
   a constant or terminal changed) is a regression of the edit: tell the user before going on.
   The CARD DELTA does not compare wiring. The per-uid diff below it does, but after a regeneration (it then says
   that few old uids are left) it lists most objects as removed and added; for a wiring question read the affected
   frame with `card <vi> --xml NAME` instead.
   `NO CACHED VERSION` = first export of this VI: there is nothing to compare, read the card.
5. `SELF-CHECK MISMATCH` = the export came from unsaved edits (save, export again) or the skeleton misread the
   file; `SELF-CHECK skipped` = the VI is outside the skeleton; `REFUSED` = the VI was saved during the export:
   `check` and export again.

## Do not

- Run `lvindex trust`, which allows a project's extension code to run, unless the user asked for it and you have
  read the extension file.
- Edit anything in the cache folder (`.index`) by hand; `repair` and `recard` fix it.
- Present a list from `callers`, `unexported` or `find` as complete for VIs called dynamically.
