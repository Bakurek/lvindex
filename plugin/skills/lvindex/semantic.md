# Writing VI summaries

Read this before `lvindex summary template` or `summary set`. A summary tells later sessions what a VI does and why,
so they open the right VIs; it never replaces the card or the XML as evidence, and never replaces reading a VI before
you edit it.

## When

- The user asks for summaries or documentation of a part of the project: summarize its VIs in batches.
- You exported a VI and read its card or XML for the task anyway, and `card <vi>` shows no current summary: write one
  at the end of the task, all such VIs in one file and one `summary set` call. Skip accessors and VIs whose diagram
  only passes its inputs through (a dynamic dispatch base with no code): their card says enough.
- Never export a VI only to summarize it unless the user asked for that.

## How

1. `lvindex summary template <vi>`: a JSON entry with the VI's handler frame names. It works only while the VI is in
   the state of its cached export; otherwise export it again first.
2. Fill the entry from that VI's card, frames and export only:
   - `purpose`: one sentence, what the VI does and why it exists;
   - `behavior`: 2-5 points: order of steps, side effects (messages and to whom, queues, files, hardware, globals),
     waits and timeouts, how it ends, errors returned;
   - `frames`: one line per handler frame you have something to say about, names exactly as in the template;
   - `questions`: what the export does not tell you: intent, why a value, what a dynamically dispatched callee does,
     whether a path can happen at run time. Ask, do not guess. A plausible sentence you cannot point to in the export
     is a question, not behavior.
   Name VIs, messages, symbols and constants exactly as written, in backticks. Purpose + behavior at most ~700
   characters.
3. Write all entries into one file with the Write tool (`{"A.vi": {...}, "B.vi": {...}}`), then
   `lvindex summary set <file>`. REFUSED entries name the reason; fix and set them again. A warning about a name
   found nowhere means a typo or an invention: check it against the card.

## Questions

- If an open question matters for the current task, ask the user now; store the answer with
  `lvindex summary answer <vi> <#> "<text>"` (`@<file>` for a longer one). Otherwise leave it open.
- `summary questions [filter]` lists open questions, e.g. to go through them with the user.
- An answer is the user's knowledge: it stays when the summary is rewritten and is shown as `note`; after a change of
  the VI it is marked "VI changed since", check whether it still holds.
