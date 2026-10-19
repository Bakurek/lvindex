---
name: lvindex
description: Use lvindex before reading, editing or reviewing VIs of a LabVIEW project that has a .claude\lvindex.toml or a single .lvproj. It answers "what does this VI do, who calls it, what changed" from an offline index instead of whole AIXML exports, keeps every export as a card for later sessions, and checks after an edit that only the intended part of a VI changed.
---

# Working on a LabVIEW project with lvindex

`lvindex <command>` from inside the project folder; for another folder pass `--project <folder | lvindex.toml>`
instead of `cd`. Not found: `python -I -m lvindex <command>`; if that fails too, stop and tell the user to install it
(`pipx install git+https://github.com/Bakurek/lvindex`), do not look for a checkout. `<vi>` = a path, `Class:File.vi`
or a unique substring. `lvindex` alone prints the manual; `--max-tokens N` caps any output.

The output is facts read from the files; `VIM?`, `sym?` and dispatcher frames without a `[messages]` section are
guesses - say so when you pass them on.

## Read the index before an export

1. `card <vi>`: terminals, calls, messages sent, handled cases, events.
2. `ctx <vi>`: who reaches it from the entry points, through which handler frames, and what it uses.
3. `card <vi> --frames`, then `--frame NAME` (one handler with its callee cards) or `--xml NAME` (the XML of the
   matching frames only).
4. Export a VI only for what these cannot answer.

Finding your way: `overview` (entry points, classes, message loops), `find <term>` (`-s` also free text), `msgs [name]`
(sender -> queue -> handler), `path <a> <b>`, `diff <ref> [<ref2>]` (skeleton facts changed between git revisions).
Markers: `(no card)` = never exported; `(card stale)` = saved after its export, do not rely on it; `(not in lvclass)`
= stray file in a class folder.

## Before editing

`callers <vi>` (dynamic dispatch expanded; `by name?` lines are a heuristic), `class <name>` (overrides, children,
interfaces), `uses <typedef|class>`. VIs called by reference or by name have no static callers: never present a list
from `callers`, `find` or `unexported` as complete for them.

## Every export goes into the cache

- Export only the VIs the task needs, never a whole project unless the user asks, with labview-mcp
  `lvai_convert_vis_to_aixml` (refresh=true) to files in a fresh scratch folder; never `returnContent`, never print an
  export into the context.
- With the lvindex plugin a hook stores each export: a message starting with `lvindex hook:` follows; if it says
  "NOT stored cleanly", read `review.md` before using those cards. Without it run
  `update --dir <folder> --backend <name>` (`<name>` = the backend the export tool answered).
- Then work from `card`, not from the export file.

## After an edit

Before the first `check` or `update` of the session read `review.md` in this skill's folder: it explains every
answer. In short: save the VI in LabVIEW, `check <vi>`, export what is CHANGED / NEW / `DEPS?`, read the CARD DELTA
and confirm that only the intended frames changed. Anything else is a regression of the edit: tell the user.

## Do not

- Run `lvindex trust` (lets a project's extension code run) unless the user asked and you have read the extension.
- Edit the cache folder (`.index`) by hand; `repair` and `recard` fix it.
