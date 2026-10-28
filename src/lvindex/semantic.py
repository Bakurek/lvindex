"""VI summaries (the semantic layer): written by an assistant, stored, validated, marked stale and served by lvindex.

lvindex calls no model and never writes a summary itself; it keeps what `summary set` gets in semantic.json next to
index.json (same lock). A summary is bound to the saved VI file it describes (sha256, as `check` compares it), not
to one export: it stays current across re-exports and `recard`, and turns STALE when the VI file changes. What the
writer could not determine from the export goes into `questions` instead of a guess; the user's answers are kept
as notes, which never turn stale, only show "VI changed since". A summary points at what to read; the card and the
XML stay the evidence.
"""
import json
import os
import re
import sys
import time

from . import cache as V
from . import cards as K
from . import skeleton as S

PATH = os.path.join(V.ROOT, "semantic.json")
LANG = (V.CFG.get("semantic") or {}).get("language", "English")
WARN_CHARS, MAX_CHARS, FRAME_CHARS = 700, 1000, 160     # purpose + behavior; one frame line (warning only)
TICK = re.compile(r"`([^`\n]+)`")
FILE_EXT = re.compile(r"\.(vi|vim|vit|ctl|ctt|lvclass|lvlib)$", re.I)
_broken, _sha = [], {}


# ---------------------------------------------------------------- storage
def load():
    """{index key: record}; a corrupt file is kept aside like index.json. If it cannot be moved aside, _broken is set
    and the writing commands refuse, so it is never overwritten."""
    try:
        with open(PATH, encoding="utf-8") as f:
            vis = json.load(f).get("vis", {})
        if isinstance(vis, dict):
            return vis
    except FileNotFoundError:
        return {}
    except (ValueError, OSError, AttributeError):
        pass
    bad = f"{PATH}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(PATH, bad)
        print(f"WARNING: semantic.json corrupt, kept as {os.path.basename(bad)} - starting without summaries",
              file=sys.stderr)
    except OSError as ex:
        _broken.append(str(ex))
        print(f"WARNING: semantic.json unreadable and not movable ({ex}) - summaries ignored, none written",
              file=sys.stderr)
    return {}


def writable():
    if _broken:
        sys.exit(f"semantic.json unreadable and not movable ({_broken[0]}) - nothing written; move it away by hand")


def save(vis):
    V.mkroot()
    with open(PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"v": 1, "vis": vis}, f, indent=1, ensure_ascii=False)
    S.replace(PATH + ".tmp", PATH)


def when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def state(sk, q, rec):
    """(current, saved-at text) of a summary or an answer against the VI file now: the file's time and size as
    recorded, else its content hash."""
    v = sk["vis"].get(q)
    if v is None:
        return False, "?"
    if (v["mtime"], v["size"]) == (rec.get("mtime"), rec.get("size")):
        return True, ""
    k = (q, v["mtime"], v["size"])
    if k not in _sha:                                   # one hash per VI and command
        try:
            _sha[k] = V.sha(V.entry_path(sk, q))
        except OSError:
            _sha[k] = None
    same = _sha[k] is not None and _sha[k] == rec.get("basis")
    return same, "" if same else when(v["mtime"])


def tag(cur, at):
    return "[summary]" if cur else f"[summary, STALE: VI changed {at}]"


def record(sk, q, sem):
    return sem.get(V.key(V.entry_path(sk, q))) if sem and q in sk["vis"] else None


def purpose(sk, q, sem):
    """'<purpose> [status]' or None."""
    rec = record(sk, q, sem)
    if not rec:
        return None
    return f"{rec['purpose']} {tag(*state(sk, q, rec))}"


def norm(frame):
    return frame.strip().strip("<>").strip().lower()


def frame_line(sk, q, sem, frame):
    """The summary line of a handler frame of q ('' if none)."""
    rec = record(sk, q, sem)
    if not rec:
        return ""
    fr = {norm(k): v for k, v in (rec.get("frames") or {}).items()}
    line = fr.get(norm(frame))
    return f"{line} {tag(*state(sk, q, rec))}" if line else ""


def match(sk, q, sem, t, width=50):
    """'...text around the first hit of t... [status]' in the summary or notes of q, '' without a hit."""
    rec = record(sk, q, sem)
    if not rec:
        return ""
    texts = [rec["purpose"], *rec.get("behavior", []), *(f"[{k}] {x}" for k, x in (rec.get("frames") or {}).items()),
             *(f"{x['q']} -> {x['a']}" for x in rec.get("questions", []) if x.get("a"))]
    for s in texts:
        i = s.lower().find(t)
        if i >= 0:
            a, b = max(0, i - width), i + len(t) + width
            return f"{'...' if a else ''}{s[a:b]}{'...' if b < len(s) else ''} {tag(*state(sk, q, rec))}"
    return ""


def head(sk, q, sem):
    """Summary lines printed on top of a card ([] without a summary)."""
    rec = record(sk, q, sem)
    if not rec:
        return []
    cur, at = state(sk, q, rec)
    out = [f"  summary {tag(cur, at)}: {rec['purpose']}"] + [f"    - {b}" for b in rec.get("behavior", [])]
    open_ = 0
    for x in rec.get("questions", []):
        if x.get("a"):
            changed = not state(sk, q, x)[0]
            out.append(f"  note [{'note, VI changed since' if changed else 'note'}]: {x['q']} -> {x['a']}")
        else:
            open_ += 1
    if open_:
        out.append(f"  open questions: {open_} (lvindex summary questions \"{q}\")")
    return out


# ---------------------------------------------------------------- writing
def exported(vi, e):
    """(sha256, meta) of the saved VI when it is in the state of its cached export, else (None, why). The meta is
    read before the hash, once: a save in between makes the record look stale, never current."""
    if not e.get("card") or not os.path.exists(e.get("xml") or ""):
        return None, "not exported - export it and `lvindex update` it first"
    m = V.meta(vi)
    if m == {"mtime": e["mtime"], "size": e["size"]}:
        return e["sha256"], m
    h = V.sha(vi)
    return (h, m) if h == e["sha256"] else (None, "VI changed since its export - export it again, a summary must "
                                                  "describe the saved VI")


def frame_names(xml):
    return [f["name"] for f in K.frames(xml)]


def template(sk, name):
    vi = V.find_vi(sk, name)
    if not vi:
        return
    q = V.qname(sk, vi) or vi
    e = V.load().get(V.key(vi), {})
    h, why = exported(vi, e)
    if not h:
        print(f"NO TEMPLATE for {q}: {why}")
        return
    t = {q: {"purpose": "", "behavior": [], "frames": {f: "" for f in frame_names(e["xml"])}, "questions": []}}
    print(json.dumps(t, indent=1, ensure_ascii=False))
    print(f"""Fill it from this VI's card and export only, in {LANG}, write it to a file and run
`lvindex summary set <file.json>` (one file may hold several VIs: {{"A.vi": {{...}}, "B.vi": {{...}}}}).
- purpose: one sentence, what the VI does and why it exists;
- behavior: 2-5 points: order of steps, side effects (messages sent and to whom, queues, files, hardware, globals),
  waits and timeouts, how it ends, errors returned;
- frames: one line per handler frame, names as given; drop frames you have nothing to say about;
- questions: what the export does not tell (intent, why a value, what a dispatched callee does) - ask, do not
  guess; the user answers them with `lvindex summary answer`;
- name VIs, messages, symbols and constants exactly as written, in backticks; purpose + behavior at most
  ~{WARN_CHARS} characters (refused over {MAX_CHARS}), a frame line ~120 (warned over {FRAME_CHARS}).""")


def skeleton_names(sk):
    return {x.lower() for k in list(sk["vis"]) + list(sk["ctls"]) + list(sk["classes"])
            for x in [k, *k.split(":"), os.path.splitext(k.split(":")[-1])[0]]}


def haystack(e):
    with open(e["xml"], encoding="utf-8", errors="replace") as f:
        text = f.read()
    return (K.fmt(e["card"]) + "\n" + K.unesc(text)).lower().replace("&amp;", "&")


def known(tok, hay, names):
    """A backticked token is known when it occurs as written in the card / export / skeleton; a wildcard by its
    prefix; a field path (a.b.c, not a file name) when each part of 3+ characters occurs as a whole word. A weak
    filter: it catches invented names, not wrong statements."""
    t = tok.lower().strip()
    if t in hay or t in names:
        return True
    if "*" in t:
        pre = t.split("*")[0]
        return len(pre) >= 3 and pre in hay
    if FILE_EXT.search(t):                              # a VI / typedef / class must exist as written
        return False
    parts = [p for p in re.split(r"[.:]", t) if p]
    return len(parts) > 1 and all(len(p) >= 3 and re.search(rf"(?<!\w){re.escape(p)}(?!\w)", hay) for p in parts)


def check_entry(e, x, names):
    """(errors, warnings) for one summary against its VI's card and export."""
    err, warn = [], []
    frames = x.get("frames") or {}
    have = {norm(f) for f in frame_names(e["xml"])}
    bad = [f for f in frames if norm(f) not in have]
    if bad:
        err.append(f"frames not in the card: {', '.join(bad)} (card frames: {', '.join(sorted(have)) or 'none'})")
    n = len(x["purpose"]) + sum(len(b) for b in x["behavior"])
    if n > MAX_CHARS:
        err.append(f"purpose + behavior {n} characters, at most {MAX_CHARS}")
    elif n > WARN_CHARS:
        warn.append(f"purpose + behavior {n} characters (aim at {WARN_CHARS})")
    long_ = [f for f, v in frames.items() if len(v) > FRAME_CHARS]
    if long_:
        warn.append(f"frame lines over {FRAME_CHARS} characters: {', '.join(long_)}")
    hay = haystack(e)
    text = " ".join([x["purpose"], *x["behavior"], *frames.values()])
    unknown = sorted({t for t in TICK.findall(text) if not known(t, hay, names)})
    if unknown:
        warn.append(f"not found in the card, export or skeleton (check them): {', '.join(unknown[:10])}"
                    + (f" +{len(unknown) - 10}" if len(unknown) > 10 else ""))
    return err, warn


def shape(x):
    """The entry in canonical form, or a reason why it is malformed."""
    if not isinstance(x, dict):
        return None, "not an object"
    qs = x.get("questions", x.get("unclear", []))
    y = {"purpose": x.get("purpose"), "behavior": x.get("behavior", []), "frames": x.get("frames") or {},
         "questions": qs}
    if not isinstance(y["purpose"], str) or not y["purpose"].strip():
        return None, "`purpose` missing"
    if not (isinstance(y["behavior"], list) and all(isinstance(b, str) for b in y["behavior"])):
        return None, "`behavior` must be a list of strings"
    if not (isinstance(y["frames"], dict) and all(isinstance(v, str) for v in y["frames"].values())):
        return None, "`frames` must map frame names to strings"
    if not (isinstance(qs, list) and all(isinstance(v, str) for v in qs)):
        return None, "`questions` must be a list of strings"
    y["frames"] = {k: v for k, v in y["frames"].items() if v.strip()}
    if x.get("model"):
        y["model"] = str(x["model"])
    return y, None


def set_(sk, path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as ex:
        sys.exit(f"cannot read {path}: {ex}")
    if not isinstance(data, dict) or "purpose" in data:
        sys.exit('summary set expects {"<vi>": {"purpose": ..., "behavior": [...], ...}, ...} (`summary template`)')
    stored = refused = 0
    names = skeleton_names(sk)
    with V.locked():
        idx, sem = V.load(), load()
        writable()
        for name, raw in data.items():
            vi = V.find_vi(sk, name, quiet=True)
            q = (V.qname(sk, vi) or vi) if vi else name
            x, why = shape(raw)
            e = idx.get(V.key(vi), {}) if vi else {}
            h, m = exported(vi, e) if vi and x else (None, None)
            err, warn = check_entry(e, x, names) if h else ([], [])
            reason = "unknown or ambiguous VI name" if not vi else why or (m if not h else "; ".join(err))
            if reason:
                print(f"REFUSED {q}: {reason}")
                refused += 1
                continue
            old = sem.get(V.key(vi), {})
            answered = [a for a in old.get("questions", []) if a.get("a")]
            new = [{"q": t} for t in dict.fromkeys(x.pop("questions")) if t not in {a["q"] for a in answered}]
            sem[V.key(vi)] = {"basis": h, **m, "export": os.path.basename(e["xml"]),
                              "backend": e.get("backend"), "at": time.time(), **x, "questions": answered + new}
            stored += 1
            print(f"STORED  {q}: {len(x['purpose']) + sum(map(len, x['behavior']))} ch, {len(x['frames'])} frames, "
                  f"{len(new)} open questions" + "".join(f"\n  warning: {w}" for w in warn))
        if stored:
            save(sem)
    print(f"{stored} stored, {refused} refused")


def questions(sk, p, o):
    sem = load()
    t = (p[0] if p else "").lower()
    n = 0
    for q in sorted(sk["vis"]):
        rec = record(sk, q, sem)
        if not rec or t not in q.lower():
            continue
        qs = [(i, x) for i, x in enumerate(rec.get("questions", []), 1) if o.get("--all") or not x.get("a")]
        if not qs:
            continue
        print(f"{q} {tag(*state(sk, q, rec))}")
        for i, x in qs:
            n += 0 if x.get("a") else 1
            print(f"  #{i} {x['q']}" + (f"\n     -> {x['a']}" if x.get("a") else ""))
    print(f"-- {n} open questions" + ("; answer with: lvindex summary answer <vi> <#> \"<text>\" (or @<file>)"
                                       if n else ""))


def answer(sk, name, num, text):
    vi = V.find_vi(sk, name)
    if not vi:
        return
    if text.startswith("@"):
        if not os.path.isfile(text[1:]):
            sys.exit(f"no file {text[1:]} - nothing stored (an answer starting with @ or - goes in a file: @<file>)")
        with open(text[1:], encoding="utf-8-sig") as f:
            text = f.read().strip()
    with V.locked():
        sem = load()
        writable()
        rec = sem.get(V.key(vi))
        qs = rec.get("questions", []) if rec else []
        if not num.isdigit() or not 1 <= int(num) <= len(qs):
            print(f"NO QUESTION #{num} for {V.qname(sk, vi) or vi} ({len(qs)} questions)")
            return
        x = qs[int(num) - 1]
        before = x.get("a")
        m = V.meta(vi)                                     # before the hash, as in exported()
        x.update({"a": text, "by": "user", "at": time.time(), "basis": V.sha(vi), **m})
        save(sem)
    print(f"ANSWERED #{num} of {V.qname(sk, vi) or vi}: {x['q']}"
          + (f"\n  (replaced the earlier answer: {before})" if before else ""))


def stats(sk, p):
    from . import query as Q
    sem, cm = load(), V.card_map(sk)
    t = (p[0] if p else "").lower()
    per = {}
    for q in sk["vis"]:
        if Q.is_accessor(sk, q) or t not in q.lower():
            continue
        rec = record(sk, q, sem)
        own = q.split(":")[0] if ":" in q else "(no owner)"
        row = per.setdefault(own, [0, 0, 0, 0, 0])     # current, stale, missing with a current card, no card, open q
        if rec:
            row[0 if state(sk, q, rec)[0] else 1] += 1
            row[4] += sum(1 for x in rec.get("questions", []) if not x.get("a"))
        elif q in cm and not cm[q][1]:
            row[2] += 1
        else:
            row[3] += 1
    tot = [sum(r[i] for r in per.values()) for i in range(5)]
    print(f"# summaries: {tot[0]} current, {tot[1]} stale; {tot[2]} VIs with a current card have none, {tot[3]} have "
          f"no current card; {tot[4]} open questions (accessors left out)")
    for own, r in sorted(per.items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0])):
        if r[0] or r[1] or t:
            print(f"  {own}: {r[0]} current, {r[1]} stale, {r[2]} missing, {r[3]} without card"
                  + (f", {r[4]} open questions" if r[4] else ""))


def summary(p, o):
    sub, rest = p[0], p[1:]
    sk = V.skel()
    forms = {"template": (1, 1), "set": (1, 1), "questions": (0, 1), "answer": (3, 3), "stats": (0, 1)}
    if sub not in forms or not forms[sub][0] <= len(rest) <= forms[sub][1]:
        sys.exit("summary template <vi> | set <file.json> | questions [<filter>] [--all] | answer <vi> <#> <text|@file>"
                 " | stats [<filter>]")
    if sub == "template":
        template(sk, rest[0])
    elif sub == "set":
        set_(sk, rest[0])
    elif sub == "questions":
        questions(sk, rest, o)
    elif sub == "answer":
        answer(sk, *rest)
    else:
        stats(sk, rest)


COMMANDS = {"summary": (summary, 1, 4)}
