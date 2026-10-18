"""Index queries over the skeleton (all VIs), the cards (exported VIs) and optional layers. See cache.py.
They never touch LabVIEW or the project; they may refresh skeleton.json and rebuild outdated cards in index.json."""
import os
import re

from . import cache as V
from . import cards as K
from . import ext as E
from . import skeleton as S

LIMIT = 30
CFG = V.CFG
HIDE_CALLS = [re.compile(x) for x in CFG["hide_calls"]]
HIDE_FILES = [re.compile(x, re.I) for x in CFG["hide"]]
ACT = CFG.get("actors") or {}
ENTRY = ACT.get("entry", "Core.vi")
NAMED_Q = re.compile(ACT["named_queue"]) if ACT.get("named_queue") else None


# ---------------------------------------------------------------- helpers
def capped(rows, n, what):
    for r in rows[:n]:
        print(r)
    if len(rows) > n:
        print(f"... {len(rows) - n} more {what} (-n {len(rows)} shows all)")


def hits(vals, t, cap=8):
    h = [v for v in vals if t in v.lower()]
    return ", ".join(h[:cap]) + (f" +{len(h) - cap}" if len(h) > cap else "") if h else ""


def num(o, k, default):
    return int(o[k]) if k in o else default


def resolve_q(sk, name):
    vi = V.find_vi(sk, name)
    q = V.qname(sk, vi) if vi else None
    if vi and not q:
        print(f"OUTSIDE SKELETON: {vi} is not under {sk['src']} (or lies in a skipped folder: "
              f"{', '.join(CFG['skip']) or '-'})")
    return q


def is_accessor(sk, q):
    """File hidden by the `hide` path regexes (default: accessors folders)."""
    f = sk["vis"].get(q, {}).get("file", "")
    return bool(f) and any(r.search(f) for r in HIDE_FILES)


def hidden_call(sk, caller, callee):
    """Plumbing hidden in trees: hidden files, vi.lib / user.lib callees, `hide_calls` names."""
    return (callee in sk["vis"].get(caller, {}).get("ext", []) or any(r.search(callee) for r in HIDE_CALLS)
            or is_accessor(sk, callee) or callee.startswith("Instance'"))


def handler_vi(cm, q):
    """VI with handler frames in its current card (message / dispatcher cases or Event Structure frames)."""
    c, stale = cm.get(q, (None, False))
    return bool(c) and not stale and bool(c["handles"] or c["events"]["handled"])


def inherited_ref(sk, x):
    """'W:f.vi' naming a known class that does not implement f (dynamic call on a W wire)."""
    c = x.split(":", 1)[0] if ":" in x else None
    return c in sk["classes"] and not sk["classes"][c]["lib"] and x not in sk["vis"]


def targets(sk, name):
    t = name.lower()
    pool = set(sk["vis"]) | {c for v in sk["vis"].values() for c in v["calls"] if not inherited_ref(sk, c)}
    exact = [x for x in pool if x.lower() in (t, t + ".vi") or x.split(":")[-1].lower() in (t, t + ".vi")]
    return sorted(exact or (x for x in pool if t in x.lower()))


def reach(sk, callee, kinds):
    """VIs a call may execute.
    DyOM on a wire of class W: W's nearest implementation (own or inherited) + overrides in descendants of W.
    CPM (Call Parent Method): nearest implementation from the named ancestor up. VIM?: matching .vim files."""
    out = set()
    for k in kinds:
        if k in ("DyOM", "CPM") and ":" in callee:
            w, fn = callee.split(":", 1)
            out.add(S.implementation(sk, w, fn))
            if k == "DyOM":
                out |= {f"{d}:{fn}" for d in S.descendants(sk, w) if f"{d}:{fn}" in sk["vis"]}
        elif k == "VIM?" and callee not in sk["vis"]:
            out |= {q for q in sk["vis"] if q.split(":")[-1] == callee} or {callee}
        else:
            out.add(callee)
    out.discard(None)
    return out


def polys(sk):
    return {c for v in sk["vis"].values() for c, k in v["calls"].items() if "PUPV" in k}


def rev_index(sk):
    """{implementation: [(caller, callee as called, kinds)]}."""
    r, pv = {}, polys(sk)
    for cq, v in sk["vis"].items():
        if cq in pv:                                       # polymorphic VI listing its own instances
            continue
        for callee, kinds in v["calls"].items():
            for t in reach(sk, callee, kinds):
                r.setdefault(t, []).append((cq, callee, kinds))
    return r


def family(sk, q):
    if ":" not in q:
        return ""
    cls, fn = q.split(":", 1)
    up = [f"{a}:{fn}" for a in S.ancestors(sk, cls) if f"{a}:{fn}" in sk["vis"]]
    down = [d for d in S.descendants(sk, cls) if f"{d}:{fn}" in sk["vis"]]
    inh = [d for d in S.descendants(sk, cls) if S.implementation(sk, d, fn) == q]
    return ((f"  overrides {up[0]}" if up else "") + (f"  overridden in: {', '.join(down)}" if down else "")
            + (f"  inherited by: {', '.join(inh)}" if inh else ""))


def compact(c, cap=220):
    parts = []
    if c["sends"]:
        parts.append("sends " + ", ".join(c["sends"]))
    for ro, syms in c["tags"].items():
        if ro != "used":
            parts.append(f"{K.LABEL} {ro} " + ", ".join(syms))
    if c["events"]["gen"]:
        parts.append("gen " + ", ".join(c["events"]["gen"]))
    if c["results"]:
        parts.append("status " + ", ".join(c["results"]))
    s = "; ".join(parts)
    return s if len(s) <= cap else s[:cap] + "..."


def actors(sk):
    """[actors]: classes with the entry VI that descend from the base class (empty without that section)."""
    if not ACT.get("base"):
        return []
    return sorted(c for c in sk["classes"] if f"{c}:{ENTRY}" in sk["vis"] and ACT["base"] in S.ancestors(sk, c))


def launchers(sk):
    """{actor: [VIs that start its Core.vi]}"""
    r = rev_index(sk)
    return {a: sorted({cq for cq, _, _ in r.get(f"{a}:{ENTRY}", [])}) for a in actors(sk)}


def footer(sk, cm):
    cur = sum(1 for _, st in cm.values() if not st)
    return f"-- {cur}/{len(sk['vis'])} VIs have a current card (roles, messages, events only from cards)"


# ---------------------------------------------------------------- find / callers / calls / uses
def find(p, o):
    term, n, t = p[0], num(o, "-n", LIMIT), p[0].lower()
    sk = V.skel()
    cm = V.card_map(sk)
    rows, hidden = [], 0
    for q, v in sk["vis"].items():
        c, stale = cm.get(q, (None, False))
        c = None if stale else c
        items, prio, shown = [], 3, set()
        if t in q.lower():
            items.append("name")
            prio = 0
        if c:
            for ro, syms in c["tags"].items():
                if hits(syms, t):
                    items.append(f"{K.LABEL} {ro}: {hits(syms, t)}")
                    prio = min(prio, 1)
            for k, vals in [("sends", c["sends"]), ("handles", c["handles"]), ("status", c["results"])] + \
                    [(f"event {k}", vals) for k, vals in c["events"].items()]:
                if hits(vals, t):
                    items.append(f"{k}: {hits(vals, t)}")
                    prio = min(prio, 1)
            shown = {s for syms in c["tags"].values() for s in syms}
            shown |= {x for vals in [c["sends"], c["handles"]] + list(c["events"].values()) for x in vals}
        sym = hits([s for s in v["syms"] if s not in shown], t)
        if sym:
            items.append(f"sym?: {sym}")
            prio = min(prio, 1)
        call = hits(v["calls"], t)
        if call:
            items.append(f"calls: {call}")
            prio = min(prio, 2)
        callee_files = {x.split(":")[-1].lower() for x in v["calls"]}       # subVI labels, already under calls
        st = hits([s for s in v["strs"] if s.lower() not in callee_files
                   and not any(s.lower() in x.lower() for x in shown)], t, 3)
        whole = any(x.lower() == t for x in v["strs"])
        if st:
            if not items and not o.get("-s") and not whole and not handler_vi(cm, q):
                hidden += 1
                continue
            items.append(f"str: {st}")
        if items:
            rows.append((prio, q, f"{q}{V.mark(sk, q, cm)} :: " + " | ".join(items)))
    rows.sort()
    if not rows:
        print(f"NO HIT for '{term}' among VI names, calls, tagged literals and cards")
    capped([r for _, _, r in rows], n, "VIs")
    for line in E.call(CFG, "find", term):
        print(line)
    print(footer(sk, cm) + (f"; {hidden} VIs matching only by a diagram string hidden (-s)" if hidden else ""))


def callers(p, o):
    name, n = p[0], num(o, "-n", LIMIT)
    sk = V.skel()
    T = targets(sk, name)
    if not T:
        print(f"UNKNOWN NAME '{name}': no VI or callee with this name")
        return
    if len(T) > 8:
        print(f"{len(T)} VIs match '{name}' - be more specific:")
        capped(T, n, "names")
        return
    for q in T:
        print(f"== {q}{V.mark(sk, q)}{family(sk, q)}" if q in sk["vis"] else f"== {q}  (outside src skeleton)")
    rows, pv = [], polys(sk)
    for cq, v in sk["vis"].items():
        if cq in pv:
            continue
        for callee, kinds in v["calls"].items():
            r = reach(sk, callee, kinds)
            if r & set(T):
                note = ""
                if r != {callee}:
                    fn = callee.split(":")[-1]
                    names = sorted(x.split(":")[0] if x.split(":")[-1] == fn and ":" in x else x for x in r)
                    note = f"  (runs: {', '.join(names)})"
                rows.append(f"{cq}{V.mark(sk, cq)} -> {callee} [{'/'.join(kinds)}]{note}")
    if not rows:
        print("  no callers in the skeleton (top-level, called by reference / by name, or unused)")
    capped(sorted(rows), n, "callers")


def calls(p, o):
    sk = V.skel()
    q = resolve_q(sk, p[0])
    if not q:
        return
    depth, show_all, cm = min(num(o, "-d", 1), 6), o.get("--all"), V.card_map(sk)
    seen = set()

    def walk(x, d, ind):
        hidden = 0
        for callee, kinds in sorted(sk["vis"].get(x, {}).get("calls", {}).items()):
            if not show_all and hidden_call(sk, x, callee):
                hidden += 1
                continue
            r = sorted(reach(sk, callee, kinds))
            runs = f" -> runs {', '.join(r)}" if r != [callee] else ""
            again = callee in seen and d > 1
            print(f"{ind}{callee} [{'/'.join(kinds)}]{V.mark(sk, callee, cm) if callee in sk['vis'] else ''}{runs}"
                  + (" (see above)" if again else ""))
            if d > 1 and not again:
                seen.add(callee)
                for impl in r:
                    walk(impl, d - 1, ind + "  ")
        if hidden:
            print(f"{ind}(+{hidden} accessors / vi.lib / queue utilities, --all)")
    print(f"== {q}{V.mark(sk, q, cm)}")
    walk(q, depth, "  ")


def uses(p, o):
    sk = V.skel()
    t = p[0].lower()
    cls = [c for c in sk["classes"] if c.lower() == t] or \
          ([] if any(t in x.lower() for x in sk["ctls"]) else [c for c in sk["classes"] if t in c.lower()])
    ctl = [] if cls else ([x for x in sk["ctls"] if x.lower() in (t, t + ".ctl")] or
                          sorted(x for x in sk["ctls"] if t in x.lower()))
    if not cls and not ctl:
        print(f"UNKNOWN NAME '{p[0]}': no typedef or class with this name")
        return
    if len(ctl) > 6:
        print(f"{len(ctl)} typedefs match - be more specific: {', '.join(ctl[:20])}")
        return
    goal = set(ctl) | set(cls) | {f"{c}:{sk['classes'][c]['chain'][-1]}.ctl" for c in cls}
    print(f"== {', '.join(sorted(set(ctl) | set(cls)))}  (links on panels/diagrams; by-name or string-built uses are invisible)")
    rows = []
    for q, v in sk["vis"].items():
        direct = goal & set(v["deps"])
        via, todo, seen = None, list(v["deps"]), set()
        while todo and not direct and via is None:
            n = todo.pop()
            if n in seen or n not in sk["ctls"]:
                continue
            seen.add(n)
            if goal & set(sk["ctls"][n]["deps"]):
                via = n
            todo += sk["ctls"][n]["deps"]
        if direct or via:
            member = cls and v.get("class") in cls
            rows.append(f"{q}{V.mark(sk, q)}" + (f"  (via {via})" if via and not direct else "")
                        + ("  [member]" if member else ""))
    nested = sorted(x for x, e in sk["ctls"].items() if goal & set(e["deps"]))
    if nested:
        print(f"  typedefs containing it: {', '.join(nested)}")
    print(f"  {len(rows)} VIs")
    capped(sorted(rows), num(o, "-n", 60), "VIs")


# ---------------------------------------------------------------- class / unexported / card / ctx
def klass(p, o):
    sk = V.skel()
    cs, name = sk["classes"], p[0]
    m = [c for c in cs if c.lower() == name.lower()] or [c for c in cs if name.lower() in c.lower()]
    if len(m) != 1:
        print(f"{'UNKNOWN NAME' if not m else 'ambiguous'} '{name}'{': ' + ', '.join(m) if m else ''}")
        return
    c = m[0]
    i = cs[c]
    anc, desc_ = S.ancestors(sk, c), S.descendants(sk, c)
    kids = [k for k, x in cs.items() if x.get("parent") == c or c in x.get("implements", [])]
    kind = "library" if i["lib"] else "interface" if i.get("interface") else "class"
    print(f"{c}  {kind} v{i['version']}  {i['file']}")
    if not i["lib"]:
        sup = S.superclasses(sk, c)
        top = sup[-1] if sup else c
        ext = cs[top].get("extern_parent")
        ifs = [x for x in anc if cs.get(x, {}).get("interface")]
        print(f"  superclasses: {' > '.join(sup + ([ext + ' (outside src)'] if ext else [])) or '-'}"
              + (f" | interfaces: {', '.join(ifs)}" if ifs else "")
              + f" | {'implementers' if i.get('interface') else 'children'}: {', '.join(sorted(kids)) or '-'}"
              + (f" | all descendants: {', '.join(desc_)}" if len(desc_) > len(kids) else ""))
    vis = [x for x in i["methods"] if x.lower().endswith((".vi", ".vim"))]
    ctls = [x for x in i["methods"] if x not in vis]
    acc = [x for x in vis if is_accessor(sk, f"{c}:{x}")]
    if not o.get("--all"):
        vis = [x for x in vis if x not in acc]
    ov = [f"{x} ({next(a for a in anc if x in cs[a]['methods'])})" for x in vis if any(x in cs[a]["methods"] for a in anc)]
    down = [f"{x} -> {', '.join(d for d in desc_ if x in cs[d]['methods'])}" for x in vis
            if any(x in cs[d]["methods"] for d in desc_)]
    if ov:
        print(f"  overrides ({len(ov)}): {', '.join(ov)}")
    if down:
        print(f"  overridden in descendants ({len(down)}): {'; '.join(down)}")
    rest = [x for x in vis if not any(x in cs[a]["methods"] for a in anc + desc_)]
    print(f"  other methods ({len(rest)}): {', '.join(rest)}")
    if acc and not o.get("--all"):
        print(f"  accessors: {len(acc)} hidden (--all)")
    if ctls:
        print(f"  typedefs ({len(ctls)}): {', '.join(ctls)}")
    folder = os.path.dirname(i["file"]).lower() + os.sep
    orph = [q for q, v in sk["vis"].items() if v.get("orphan") and v["file"].lower().startswith(folder)]
    if orph:
        print(f"  not in lvclass ({len(orph)}): {', '.join(orph)}")


def unexported(p, o):
    sk = V.skel()
    cm = V.card_map(sk)
    f = (p[0] if p else "").lower()
    rows, acc = [], 0
    for q, v in sorted(sk["vis"].items(), key=lambda x: x[1]["file"].lower()):
        if (q in cm and not cm[q][1]) or not (f in q.lower() or f in v["file"].lower()):
            continue
        if is_accessor(sk, q) and not o.get("--all"):
            acc += 1
            continue
        rows.append(f"{q}{V.mark(sk, q, cm)}  {v['size'] // 1024} KB  [{v['file']}]")
    cur = sum(1 for _, stale in cm.values() if not stale)
    print(f"{len(rows)} VIs without a current card{f' matching {f!r}' if f else ''} "
          f"({cur} current cards of {len(sk['vis'])} VIs{f'; {acc} accessors hidden, --all' if acc else ''})")
    capped(rows, num(o, "-n", 50), "VIs")


def card(p, o):
    sk = V.skel()
    vi = V.find_vi(sk, p[0])
    if not vi:
        return
    q = V.qname(sk, vi)
    idx = V.fresh(V.load())
    e = idx.get(V.key(vi), {})
    if not e.get("card"):
        print(f"NOT EXPORTED: {q or vi} has no card - export it with lvai_convert_vis_to_aixml, then `update`")
        return
    if V.meta(vi) != {"mtime": e["mtime"], "size": e["size"]}:
        print("WARNING: VI saved after this export - card may be stale (check + export)")
    if not any(k in o for k in ("--frames", "--frame", "--xml")):
        print(K.fmt(e["card"], sk["vis"].get(q, {}).get("file", "")))
        return
    if not os.path.exists(e.get("xml", "")):
        print("cached XML missing - export again")
        return
    if "--xml" in o:
        fx = K.frame_xml(e["xml"], o["--xml"])
        if not fx:
            print(f"no handler frame matching '{o['--xml']}'")
        for k, n_, x in fx:
            print(f"== [{n_}] {k}, {len(x)} chars\n{x}")
        return
    cm = V.card_map(sk, idx)
    frs = K.frames(e["xml"])
    if "--frame" in o:
        frs = [f for f in frs if o["--frame"].lower() in f["name"].lower()]
        if not frs:
            print(f"no handler frame matching '{o['--frame']}'")
    if not frs and "--frame" not in o:
        print("no message cases / Event Structure frames in this VI")
    for fr in frs:
        extra, lines = {}, []
        for x0 in fr["card"]["calls"]:
            x = S.canon(sk, x0)                       # card naming -> skeleton key (same-name classes)
            if x not in sk["vis"]:
                continue
            r = reach(sk, x, sk["vis"].get(q, {}).get("calls", {}).get(x, ["IUVI"]))
            note = f" (dispatch: {', '.join(sorted(y.split(':')[0] for y in r))})" if len(r) > 1 else ""
            c, stale = cm.get(x, (None, False))
            extra[x0] = note + ("" if c and not stale else " (card stale)" if c else " (no card)")
            if c and not stale and "--frame" in o and compact(c):
                lines.append(f"    {x}: {compact(c, 400)}")
        print(K.fmt_frame(fr, extra, hide=lambda x: hidden_call(sk, q, x))
              + (f"  [{fr['xml_chars']} ch]" if "--frame" in o else ""))
        for line in lines:
            print(line)


def chains(sk, q, r, limit=8, depth=10):
    """Caller chains from roots (VIs nobody calls) down to q: [[(vi, called as), ...]]."""
    out = []

    def up(x, path):
        if len(out) >= limit * 4:
            return
        ups = [(c, a) for c, a, _ in r.get(x, []) if c not in {p_ for p_, _ in path}]
        if not ups or len(path) >= depth:
            out.append(list(reversed(path)))
            return
        for c, a in sorted(set(ups)):
            up(c, path + [(c, a)])
    up(q, [(q, None)])
    out.sort(key=len)
    return out[:limit]


def ctx(p, o):
    sk = V.skel()
    q = resolve_q(sk, p[0])
    if not q:
        return
    v, cm, r = sk["vis"][q], V.card_map(sk), rev_index(sk)
    c, stale = cm.get(q, (None, False))
    st = "current" if c and not stale else "stale" if c else "none"
    print(f"## {q}  {v['size'] // 1024} KB  card: {st}{V.mark(sk, q)}  [{v['file']}]")
    if family(sk, q):
        print(" " + family(sk, q))
    idx = V.load()
    fcache = {}

    def frame_of(core, called):
        if core not in cm or cm[core][1]:
            return ""
        x = idx.get(V.key(V.entry_path(sk, core)), {}).get("xml")
        if not x or not os.path.exists(x):
            return ""
        if x not in fcache:
            fcache[x] = K.frames(x)
        names = [f["name"] for f in fcache[x] if called in {S.canon(sk, c) for c in f["card"]["calls"]}]
        return f"[{' / '.join(names)}]" if names else ""
    ch = chains(sk, q, r)
    print(f"  used from ({len(ch)} chains{', top 8' if len(ch) >= 8 else ''}):" if ch and len(ch[0]) > 1
          else "  used from: nothing in the skeleton (top-level, by reference or unused)")
    for path in ch:
        if len(path) < 2:
            continue
        parts = []
        for x, called in path:                     # called = how x calls the next VI of the chain
            parts.append(x + (frame_of(x, called) if called and handler_vi(cm, x) else ""))
        print("    " + " > ".join(parts))
    callees, hid = [], 0
    for callee, kinds in sorted(v["calls"].items()):
        if hidden_call(sk, q, callee):
            hid += 1
            continue
        rr = reach(sk, callee, kinds)
        callees.append(callee + (f" (runs {', '.join(sorted(x.split(':')[0] for x in rr))})" if len(rr) > 1 else ""))
    if callees or hid:
        print(f"  calls: {', '.join(callees) or '-'}{f' (+{hid} plumbing)' if hid else ''}")
    td = [d for d in v["deps"] if not S.class_of_dep(sk, d)]
    if td:
        print(f"  typedefs: {', '.join(td)}")
    if c and not stale:
        for ro, syms in c["tags"].items():
            print(f"  {K.LABEL} {ro}: {', '.join(syms)}")
        for k in ("sends", "handles"):
            if c[k]:
                print(f"  {k}: {', '.join(c[k])}")
        for k, vals in c["events"].items():
            if vals:
                print(f"  events {k}: {', '.join(vals)}")
    elif v["syms"]:
        print(f"  sym? (role unknown, no current card): {', '.join(v['syms'])}")


# ---------------------------------------------------------------- messages
def owner(sk, vi_q, queue, launch):
    """Actor owning the queue a VI enqueues to (None = unknown)."""
    cls = vi_q.split(":")[0] if ":" in vi_q else None
    acts = actors(sk)
    field, _, acc = queue.rpartition(":")
    base = cls if not field else field if field in acts else None     # "?:" / non-actor field: unknown
    if ACT.get("self_queue") and acc == ACT["self_queue"]:
        return base if base in acts else None
    if ACT.get("caller_queue") and acc == ACT["caller_queue"]:
        if base not in acts:
            return None
        cores = [x.split(":")[0] for x in launch.get(base, []) if x.endswith(f":{ENTRY}")]
        return f"{cores[0]} (launcher of {base})" if len(cores) == 1 else f"caller of {base} (set at run time)"
    m = NAMED_Q.search(acc) if NAMED_Q else None
    if m:
        cand = [a for a in acts if a == m.group(1) or a.startswith(m.group(1) + " ")]
        return cand[0] + ("" if cand[0] == m.group(1) else " (by name)") if len(cand) == 1 else None
    return None


def msgs(p, o):
    if K.MSG is None:
        print("no message library configured ([messages] in vi-tools.toml) - use card <vi> --frames for handlers")
        return
    sk = V.skel()
    cm = V.card_map(sk)
    launch = launchers(sk)
    cur = {q: c for q, (c, stale) in cm.items() if not stale}
    handled, exported = {}, []
    loops = [(a, f"{a}:{ENTRY}") for a in actors(sk)] or [(q, q) for q, c in cur.items() if c["handles"]]
    for a, q in loops:
        c = cur.get(q)
        if c:
            exported.append(a)
            for h in c["handles"]:
                if h == "Default":
                    continue
                handled.setdefault(re.sub(r" \(in .*\)$", "", h), []).append(a)
    sent = {}
    for q, c in cur.items():
        for s in c["sends"]:
            m = re.match(r"(.+?)(?:\[(.+)\])? -> (.+)$", s)
            if not m or m.group(1).startswith("<"):
                continue
            msg, inner, queue = m.groups()
            sent.setdefault(msg, []).append((q, inner, queue, owner(sk, q, queue, launch)))
    f = (p[0] if p else "").lower()
    names = sorted(n for n in set(sent) | set(handled) if f in n.lower())
    if not names:
        print(f"no message matching '{f}' in the current cards")
    for n in names:
        print(f"{n}")
        for q, inner, queue, own in sorted(sent.get(n, [])):
            print(f"  sent by {q}{f' carrying [{inner}]' if inner else ''} -> {queue} = {own or 'owner unknown'}")
        if not sent.get(n):
            print("  sender: none among the current cards")
        print(f"  handled by: {', '.join(handled[n]) + ' (case)' if n in handled else 'no exported handler'}")
    missing = [a for a in actors(sk) if a not in exported]
    print(f"-- handlers with a current card: {', '.join(exported) or '-'}"
          + (f"; actor entry VIs without a current card: {', '.join(missing)}" if missing else "")
          + f"; {len(cur)} cards in total - senders in unexported VIs are missing")


# ---------------------------------------------------------------- tagged literals
def literals(sk):
    """{tagged literal: [VIs holding it]} from the binaries."""
    out = {}
    for q, v in sk["vis"].items():
        for s in v["syms"]:
            out.setdefault(s, []).append(q)
    return out


# ---------------------------------------------------------------- overview
def entry_points(sk):
    """VIs built as top level in the .lvproj build specifications (Source[n].sourceInclusion = TopLevel)."""
    lp = CFG.get("lvproj")
    if not lp or not os.path.exists(lp):
        return []
    with open(lp, encoding="utf-8-sig", errors="replace") as f:
        t = f.read()
    items = []
    for m in re.finditer(r'Source\[(\d+)\]\.sourceInclusion" Type="Str">TopLevel<', t):
        ids = re.findall(rf'Source\[{m.group(1)}\]\.itemID" Type="Ref">([^<]+)<', t[:m.start()])
        if ids and ids[-1].lower().endswith((".vi", ".vim")):
            items.append(ids[-1].rsplit("/", 1)[-1])
    out = []
    for fn in dict.fromkeys(items):
        cand = [q for q, v in sk["vis"].items() if os.path.basename(v["file"]).lower() == fn.lower()]
        out.append(cand[0] if len(cand) == 1 else f"{fn} ({'not in src' if not cand else 'ambiguous'})")
    return out


def overview(p, o):
    sk = V.skel()
    cm = V.card_map(sk)
    cs = sk["classes"]
    cur = sum(1 for _, st in cm.values() if not st)
    print(f"# {CFG['name']} overview - skeleton ({len(sk['vis'])} VIs, {len(sk['ctls'])} typedefs, "
          f"{sum(1 for i in cs.values() if not i['lib'])} classes) and {cur} current cards; facts only")
    ep = entry_points(sk)
    if ep:
        print(f"## Entry points (build specifications): {', '.join(ep)}")
    else:
        called = {t for v in sk["vis"].values() for c, k in v["calls"].items() for t in reach(sk, c, k)}
        roots = sorted((q for q, v in sk["vis"].items() if q not in called and not v.get("orphan")
                        and not is_accessor(sk, q) and v["calls"]), key=lambda q: -len(sk["vis"][q]["calls"]))
        print(f"## Roots (no build specification; VIs nobody calls, most callees first): {', '.join(roots[:12])}"
              + (f" +{len(roots) - 12}" if len(roots) > 12 else ""))
    print("## Classes (root > descendants)")
    roots = sorted(c for c, i in cs.items() if not i["lib"] and not i.get("interface") and not i.get("parent"))
    by_ext = {}
    for c in roots:
        if cs[c].get("extern_parent"):
            by_ext.setdefault(cs[c]["extern_parent"], []).append(c)
            continue
        kids = [k for k in S.descendants(sk, c) if not cs[k].get("interface")]
        print(f"  {c}" + (f" > {', '.join(kids)}" if kids else ""))
    for ext, cl in sorted(by_ext.items()):
        print(f"  children of {ext} (outside src): {len(cl)}: {', '.join(cl[:10])}{' ...' if len(cl) > 10 else ''}")
    ifs = sorted(c for c, i in cs.items() if i.get("interface"))
    if ifs:
        print("## Interfaces: " + "; ".join(
            f"{x} <- {', '.join(k for k, i in cs.items() if x in i.get('implements', [])) or '-'}" for x in ifs))
    libs = sorted(c for c, i in cs.items() if i["lib"])
    if libs:
        print(f"  libraries: {', '.join(libs)}")
    acts = actors(sk)
    if acts:
        print(f"## Actors ({ACT['base']} children with {ENTRY})")
        launch = launchers(sk)
        for a in acts:
            c, stale = cm.get(f"{a}:{ENTRY}", (None, False))
            hs = ", ".join(c["handles"]) if c and not stale else (f"{ENTRY} card stale" if c else f"{ENTRY} not exported")
            print(f"  {a}  started by {', '.join(launch.get(a, [])) or '?'}  handles: {hs}")
    else:
        hv = [(q, c) for q, (c, st) in sorted(cm.items()) if not st and c["handles"]]
        if hv:
            print("## Handler VIs (cards; without [messages] the cases come from a dispatcher heuristic)")
            for q, c in hv:
                print(f"  {q}: {', '.join(c['handles'][:15])}{' ...' if len(c['handles']) > 15 else ''}")
    lit = literals(sk)
    if lit:
        groups = {}
        for x in lit:
            groups.setdefault(x.split(".")[0], set()).add(x)
        print(f"## {K.LABEL} literals in VIs: " + ", ".join(f"{g} {len(v)}" for g, v in sorted(groups.items())))
    for line in E.call(CFG, "overview"):
        print(line)
    st = sk["stats"]
    vend = sorted({v["file"].split(os.sep)[0] for v in sk["vis"].values()} & {"external", "vendor", "3rdparty", "dependencies"})
    if vend and not CFG["skip"]:
        print(f"## Note: folders {', '.join(vend)} are scanned; if they hold vendored code set skip = [...] in vi-tools.toml")
    print(f"## Coverage: {cur} current cards of {len(sk['vis'])} VIs; same-name VIs kept by path {len(st['dups'])}; "
          f"skipped folders: {', '.join(st.get('skipped', [])) or '-'}")
    print("## Next: ctx <vi> | card <vi> --frames | find <term> | callers <vi> | unexported"
          + (" | msgs" if K.MSG is not None else "")
          + "".join(f" | {c}" for c in E.registered()))


COMMANDS = {"find": (find, 1, 1), "callers": (callers, 1, 1), "calls": (calls, 1, 1), "uses": (uses, 1, 1),
            "class": (klass, 1, 1), "unexported": (unexported, 0, 1), "card": (card, 1, 1), "ctx": (ctx, 1, 1),
            "msgs": (msgs, 0, 1), "overview": (overview, 0, 0)}
