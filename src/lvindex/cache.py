"""lvindex - VI review cache + logic index for a LabVIEW project. Run: lvindex [--project P] <command> ...
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
                        (+ - ~, wires resolved)
  update --dir <folder> the same for every export in a folder, matched to its VI by the path hash in labview-mcp's
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

Maintenance: ackdeps <vi>... | show <vi> | semdiff <a.xml> <b.xml> | skeleton [--full] | repair | recard | list
Extensions: trust [--remove] allows the [extensions] of this lvindex.toml to run (once per project and user)
Store: the project's cache folder (default <project root>\\.index, lvindex.toml `cache` overrides):
  index.json (keys = VI paths relative to src, export names relative to xml\\), xml\\, skeleton.json,
  index.lock (held while index.json is modified), .gitignore (`*`, written when the folder is created).
"""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

from . import cards as K
from . import cli
from . import config
from . import skeleton as S
from .cards import make_card

CFG = config.get()
K.configure(CFG)

ROOT = CFG["cache"]
INDEX = os.path.join(ROOT, "index.json")
LOCK = os.path.join(ROOT, "index.lock")
XMLDIR = os.path.join(ROOT, "xml")
SKEL = os.path.join(ROOT, "skeleton.json")
SRC = CFG["src"]
TAG_RE = re.compile(CFG["tags"]["literal"].encode()) if (CFG.get("tags") or {}).get("literal") else None
STRIP = [r' description="[^"]*"', r' connection="[^"]*"']
SKIP_ATTR = {"uid", "uid_parent", "description", "connection", "instance", "outputs"}
DEPS_V = 4                 # dependency-baseline format in index entries (4: + subVI connector panes)
_held = [0]
_sk = {}


# ---------------------------------------------------------------- index
def mkroot():
    """Create the cache folder; a new one gets a .gitignore so it never shows up in the project's repo."""
    if not os.path.isdir(ROOT):
        os.makedirs(ROOT, exist_ok=True)
        with open(os.path.join(ROOT, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*\n")


def load():
    """index.json stores export file names relative to xml\\ (older entries: absolute paths, also from a cache
    folder that has since moved); in memory they are absolute paths."""
    try:
        with open(INDEX, encoding="utf-8") as f:
            idx = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        bad = f"{INDEX}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        with contextlib.suppress(OSError):
            os.replace(INDEX, bad)
        print(f"WARNING: index.json corrupt, kept as {os.path.basename(bad)} - starting with an empty cache; "
              f"`repair` adopts the cached exports in xml\\ again", file=sys.stderr)
        return {}
    for e in idx.values():
        x = e.get("xml")
        if x and (not os.path.isabs(x) or not os.path.exists(x)):
            e["xml"] = os.path.join(XMLDIR, os.path.basename(x))
    for k in [k for k in idx if os.path.isabs(k)]:              # older caches: absolute keys
        nk = key(k)
        if os.path.isabs(nk):                                  # not under src: maybe the project moved
            parts = os.path.normcase(os.path.normpath(k)).split(os.sep)
            nk = next((os.sep.join(parts[i:]) for i in range(1, len(parts) - 1)     # at least folder + file
                       if os.path.isfile(os.path.join(SRC, *parts[i:]))), nk)
        if nk != k:
            old = idx.pop(k)
            if nk not in idx or idx[nk].get("exported_at", 0) < old.get("exported_at", 0):
                idx[nk] = old
    return idx


def save(idx):
    mkroot()
    here = os.path.normcase(XMLDIR)
    out = {k: {**e, "xml": os.path.basename(e["xml"])}
           if e.get("xml") and os.path.normcase(os.path.dirname(e["xml"])) == here else e for k, e in idx.items()}
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    S.replace(tmp, INDEX)


@contextlib.contextmanager
def locked(timeout=60, stale=300):
    """Exclusive lock for load-modify-save of index.json across processes (re-entrant within one process)."""
    if _held[0]:
        _held[0] += 1
        try:
            yield
        finally:
            _held[0] -= 1
        return
    mkroot()
    t0 = time.time()
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - os.stat(LOCK).st_mtime > stale:     # holder crashed: one waiter renames it away
                    gone = f"{LOCK}.{os.getpid()}.stale"
                    os.replace(LOCK, gone)
                    os.remove(gone)
                    continue
            except (FileNotFoundError, PermissionError):
                continue
            if time.time() - t0 > timeout:
                sys.exit(f"index locked by another process ({LOCK}) - retry later")
            time.sleep(0.1)
    _held[0] = 1
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        _held[0] = 0
        with contextlib.suppress(FileNotFoundError):
            os.remove(LOCK)


SRC_REAL = os.path.normcase(os.path.realpath(SRC)).rstrip(os.sep) + os.sep     # also for a drive root


def key(vi):
    """Index key of a VI: its path relative to src (normcased), so a moved project keeps its cache; the absolute
    path for a VI outside src."""
    p = os.path.normcase(os.path.realpath(vi))
    return p[len(SRC_REAL):] if p.startswith(SRC_REAL) else p


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def meta(vi):
    st = os.stat(vi)
    return {"mtime": st.st_mtime, "size": st.st_size}


def fresh(idx):
    """Rebuild cards made by an older cards.py (CARD_V differs); returns the current index."""
    if not any(e.get("card") and e["card"].get("v") != K.CARD_V for e in idx.values()):
        return idx
    with locked():
        idx = load()
        n = gone = 0
        for e in idx.values():
            if e.get("card") and e["card"].get("v") != K.CARD_V:
                if e.get("xml") and os.path.exists(e["xml"]):
                    e["card"] = make_card(e["xml"])
                    n += 1
                else:                                       # export lost: an old-version card is not trusted
                    del e["card"]
                    gone += 1
        if n or gone:
            save(idx)
    print(f"(rebuilt {n} cards: card format or project configuration changed"
          f"{f'; dropped {gone} without XML' if gone else ''})",
          file=sys.stderr)
    return idx


# ---------------------------------------------------------------- skeleton
def skel(full=False):
    if _sk and not full:
        return _sk["sk"]
    old = S.load(SKEL)
    try:
        if old and os.path.normcase(old.get("src", "")) != os.path.normcase(SRC):
            old = None                               # another project's skeleton: start fresh
        sk = S.build(SRC, old, full, CFG["skip"], TAG_RE, CFG["hash"])
    except FileNotFoundError as ex:
        sys.exit(f"ERROR: {ex} - skeleton not refreshed")
    if old and old.get("vis") and len(sk["vis"]) < len(old["vis"]) // 2:
        print(f"WARNING: {len(sk['vis'])} VIs found, {len(old['vis'])} before - skeleton.json NOT saved "
              f"(src {sk['src']} incomplete?)", file=sys.stderr)
        return sk
    strip = lambda d: {k: v for k, v in (d or {}).items() if k != "stats"}  # noqa: E731
    if strip(sk) != strip(old):
        mkroot()
        try:
            S.save(sk, SKEL)
        except OSError as ex:                              # another process is saving it: next command retries
            print(f"(skeleton.json not saved: {ex})", file=sys.stderr)
    _sk["sk"] = sk
    return sk


def qname(sk, vi):
    """Qualified skeleton name of a VI path (None outside the skeleton)."""
    k = key(vi)
    for q, v in sk["vis"].items():
        if key(os.path.join(sk["src"], v["file"])) == k:
            return q
    return None


def find_vi(sk, name, quiet=False):
    """VI path for a file path, a qualified name or a unique substring of one; None (and a message) otherwise."""
    if os.path.isfile(name):
        return name
    t = name.lower()
    m = [q for q in sk["vis"] if q.lower() in (t, t + ".vi")] or sorted(q for q in sk["vis"] if t in q.lower())
    if len(m) == 1:
        return os.path.join(sk["src"], sk["vis"][m[0]]["file"])
    if not quiet:
        print(f"UNKNOWN NAME '{name}': " + (f"ambiguous - {', '.join(m[:12])}{' ...' if len(m) > 12 else ''}"
                                             if m else "no such file and no VI with this name in the skeleton"))
    return None


def entry_path(sk, q):
    return os.path.join(sk["src"], sk["vis"][q]["file"])


def card_map(sk, idx=None):
    """{qualified name: (card, stale)} for exported VIs; stale = VI file differs from the exported one."""
    idx = fresh(idx if idx is not None else load())
    out = {}
    for q, v in sk["vis"].items():
        e = idx.get(key(entry_path(sk, q)), {})
        if e.get("card"):
            out[q] = (e["card"], e.get("mtime") != v["mtime"] or e.get("size") != v["size"])
    return out


def mark(sk, q, cm=None):
    m = []
    if cm is not None:
        m.append("" if q in cm and not cm[q][1] else " (card stale)" if q in cm else " (no card)")
    if sk["vis"].get(q, {}).get("orphan"):
        m.append(" (not in lvclass)")
    return "".join(m)


def dep_baseline(sk, q, t_export):
    """Dependency fingerprints at export time (None = saved after the export before it was recorded)."""
    return {d: S.fp_at(sk, d, t_export) for d in S.dep_fps(sk, q)}


def deps_flag(sk, q, e):
    """' DEPS? ...' when a dependency fingerprint differs from the one recorded at export."""
    if q is None:
        return ""
    if e.get("deps_v") == 3 and "deps_fp" in e:             # keep that baseline (and its ackdeps); subVI panes
        cur = S.dep_fps(sk, q)                               # were not recorded then: tracked from now on
        e["deps_fp"], e["deps_v"] = {**{d: fp for d, fp in cur.items() if d.startswith("pane ")}, **e["deps_fp"]}, DEPS_V
    elif e.get("deps_v") != DEPS_V:                          # entry from an older fingerprint scheme
        base, cur = dep_baseline(sk, q, e.get("exported_at", e["mtime"])), S.dep_fps(sk, q)
        base.update({d: cur[d] for d, fp in base.items() if d.startswith("pane ") and fp is None})   # as for v3
        e["deps_fp"], e["deps_v"] = base, DEPS_V
    base, cur = e["deps_fp"], S.dep_fps(sk, q)
    ch = []
    for d, fp in sorted(cur.items()):
        if d not in base:
            if not d.startswith("pane "):          # a new subVI call changes the caller's file anyway; a new key
                ch.append(f"{d} (new)")            # without that = the dispatch target resolved differently
        elif base[d] is None:
            ch.append(f"{d} (unknown at export)")
        elif base[d] != fp:
            ch.append(d)
    ch += [f"{d} (gone)" for d in sorted(base) if d not in cur and not d.startswith("pane ")]
    return f"  DEPS? {', '.join(ch[:4])}{f' +{len(ch) - 4}' if len(ch) > 4 else ''}" if ch else ""


# ---------------------------------------------------------------- semantic diff
def label(e):
    t, n = e.tag, K.plain(e.get("_name")) or e.get("_id") or ""
    if t == "Call":
        n = e.get("target", "").replace("\\3A", ":").replace(".lvclass", "")
    elif t == "CaseFrame":
        n = "[" + e.get("selector", "") + "]"
    elif t == "Constant":
        n = f"{n}={e.get('value', '')[:60]}"
    return f"{t}:{n}"


def parse(path):
    root = ET.parse(path).getroot()
    nodes, parent, paths, prod = {}, {}, {}, {}

    def walk(e, path):
        u = e.get("uid")
        if u:
            nodes[u], paths[u] = e, path
        for port, wire in WIRE.findall(e.get("outputs") or ""):
            prod[wire] = (e, port)             # wire "id.term" -> (driving element, its output terminal)
        for c in e:
            parent[c] = e
            walk(c, path + [label(e)] if e.tag in ("Structure", "CaseFrame") else path)
    walk(root, [])
    return nodes, parent, paths, prod, K.undecoded(path)


WIRE = re.compile(r"(?:^|,)([^:,]*):(\d+\.[^,]*)")


def name(e, nd=frozenset()):
    """Short reference to a wire's source; a constant with its value (it tells two same-name constants apart after a
    regeneration), as <not decoded> when either export could not decode it (nd: uids)."""
    if e.tag == "Constant":
        v = K.NOT_DECODED if e.get("uid") in nd else e.get("value", "")[:60]
        return f"Constant:{K.plain(e.get('_name'))}={v}"
    lab = label(e)
    return f"{lab}#{e.get('uid')}" if e.tag in ("Node", "Call") else lab


def src_of(model, wire, depth=0):
    """Name of the object driving a wire, following tunnels and shift registers."""
    nodes, parent, _, prod, nd = model
    if wire not in prod:
        return "?"
    e, port = prod[wire]
    if depth > 12 or e.tag not in ("Tunnel", "Left", "Right"):
        return f"{name(e)}.{port}" if e.tag in ("Node", "Call") else name(e, nd)
    ins = e.get("inputs")
    if not ins and e.tag == "Tunnel":
        owner = parent.get(e)
        if owner is not None and owner.tag == "CaseFrame":        # frame input tunnel -> structure tunnel
            struct = parent.get(owner)
            for t in (struct.findall("Tunnel") if struct is not None else []):
                if t.get("_id") == e.get("_id") and t.get("inputs"):
                    ins = t.get("inputs")
        elif owner is not None:                                   # case output tunnel: value depends on frame
            return f"{name(owner)}#{owner.get('uid')}.{e.get('_id')}"
    m = WIRE.search(ins or "")
    if not m:
        return f"{e.tag}:{e.get('_id', '')}(unwired)"
    inner = src_of(model, m.group(2), depth + 1)
    return f"ShiftReg(init {inner})" if e.tag == "Left" else inner


def resolve(model, ref):
    if re.fullmatch(r"\d+\.[^,]*", ref):                       # selectin / selectout: bare wire
        return f"<{src_of(model, ref)}>"
    return re.sub(r"(?<=:)(\d+\.[^,]*)", lambda m: f"<{src_of(model, m.group(1))}>", ref)


def desc(model, e):
    """Attributes to compare, normalised where exporters differ: inputs in port order without the unwired ones,
    targets with ":", names unescaped; the value of a constant the exporter could not decode as <not decoded>."""
    a = {k: v for k, v in e.attrib.items() if k not in SKIP_ATTR}
    if "target" in a:
        a["target"] = a["target"].replace("\\3A", ":")
    if "_name" in a:
        a["_name"] = K.plain(a["_name"])
    if "inputs" in a:                                  # commas inside names are escaped (\2C)
        wired = sorted((x for x in a["inputs"].split(",") if x and not x.endswith(":")),
                       key=lambda x: x.partition(":")[0])    # stable: ports of the same name keep their order
        if wired:
            a["inputs"] = ",".join(wired)
        else:
            del a["inputs"]
    if e.tag == "Constant" and e.get("uid") in model[4]:
        a["value"] = K.NOT_DECODED
    for k in ("inputs", "selectin", "selectout"):
        if k in a:
            a[k] = resolve(model, a[k])
    return a


def semdiff(old_path, new_path):
    A, B = parse(old_path), parse(new_path)
    nd = A[4] | B[4]                                   # not decoded in either export: compared on neither side
    A, B = (*A[:4], nd), (*B[:4], nd)
    kept = len(set(A[0]) & set(B[0]))                  # old uids still present
    rows, unknown = [], []
    for u in sorted(set(A[0]) | set(B[0]), key=int):
        if u in A[0] and u not in B[0]:
            e = A[0][u]
            if e.tag != "Tunnel" or e.get("mode"):
                rows.append((A[2][u], "-", f"{label(e)} {desc(A, e)}"))
        elif u in B[0] and u not in A[0]:
            e = B[0][u]
            if e.tag != "Tunnel" or e.get("mode"):
                rows.append((B[2][u], "+", f"{label(e)} {desc(B, e)}"))
        else:
            ea, eb = A[0][u], B[0][u]
            da, db = desc(A, ea), desc(B, eb)
            if eb.tag == "Tunnel" and not eb.get("inputs") and da.get("mode") == db.get("mode"):
                continue                       # frame input tunnels: consumers already show the resolved source
            if eb.tag == ea.tag == "Constant" and u in nd:
                da.pop("value", None)
                db.pop("value", None)
                unknown.append(K.plain(eb.get("_name")) or "const")
            ch = {k: (da.get(k), db.get(k)) for k in sorted(set(da) | set(db)) if da.get(k) != db.get(k)}
            if ea.tag != eb.tag:
                ch = {"tag": (ea.tag, eb.tag), **ch}
            if ch:
                rows.append((B[2][u], "~", f"{label(eb)} " + "; ".join(f"{k}: {o} -> {n}" for k, (o, n) in ch.items())))
    rows.sort(key=lambda r: " > ".join(r[0]))
    out, last = [], None
    if rows and A[0] and kept < len(A[0]) / 2:
        out.append(f"(only {100 * kept // len(A[0])}% of the old uids are left - probably a regenerated VI: the rows "
                   f"below list most objects as removed and added; the CARD DELTA shows what changed, except wiring)")
    for path, op, text in rows:
        p = " > ".join(path) or "(top)"
        if p != last:
            out.append(f"@ {p}")
            last = p
        out.append(f"  {op} {text}")
    if unknown:
        out.append(f"? {len(unknown)} constant value(s) not compared, not decoded by the exporter: "
                   f"{', '.join(sorted(set(unknown)))}")
    return "\n".join(out)


def own_facts(m, scope, skip):
    """Multisets of the objects in scope, not looking into the handler frames in skip: calls (with the polymorphic
    instance), primitives and structures, constants (label=value), case selectors."""
    f = {k: Counter() for k in ("calls", "primitives", "constants", "cases")}

    def walk(e):
        for c in e:
            if c in skip:
                continue
            if c.tag == "Call":
                inst = K.unesc(c.get("instance")).split(":")[-1]
                f["calls"][K.target(c) + (f" [{inst}]" if inst else "")] += 1
            elif c.tag in ("Node", "Structure"):
                f["primitives"][c.get("_name") or c.tag] += 1
            elif c.tag == "Constant":
                v = K.NOT_DECODED if c.get("uid") in m.undecoded else c.get("value", "")[:60]
                f["constants"][(K.plain(c.get("_name")) or "const", v)] += 1      # shown as name=value
            elif c.tag == "CaseFrame":                      # "Error .." = "Error" (a regeneration respells it)
                sel = K.unesc(c.get("selector"))
                f["cases"][f"[{'Error' if sel == 'Error ..' else sel}]"] += 1
            walk(c)
    walk(scope)
    return f


def delta_facts(path):
    """(facts of the VI outside its handler frames, {(kind, name, parent, n): facts}). n numbers frames sharing
    kind, name and parent, in document order (pair_frames does not rely on it)."""
    m = K.Model(path)
    hf = K.handler_frames(m)
    skip = {f for *_, f in hf}
    c = K.card_of(m, m.root, top=True)
    vi = {"terminals": Counter(f"{'in' if e.tag == 'Control' else 'out'} {e.get('_name')} [{e.get('conIdx')}] "
                               f"{e.get('type', '')}" for e in m.root
                               if e.tag in ("Control", "Indicator") and e.get("conIdx")),
          **own_facts(m, m.root, skip), "sends": Counter(c["sends"]),
          **{f"{K.LABEL} {ro}": Counter(s) for ro, s in c["tags"].items()},
          "events gen": Counter(c["events"]["gen"]), "events reg": Counter(c["events"]["reg"]),
          "status consts": Counter(c["results"]), "dynamic": Counter(c["dynamic"])}
    frames, seen = {}, Counter()
    for kind, n, parent, f in hf:
        n = "Error" if n == "Error .." else n
        seen[(kind, n, parent)] += 1
        frames[(kind, n, parent, seen[(kind, n, parent)])] = own_facts(m, f, skip)
    return vi, frames


def drop_undecoded(x, y):
    """Constants multisets ({(name, value): n}) without the constants that cannot be compared because a value was not
    decoded: per name, <not decoded> pairs on both sides go first; of the rest, after the equal decoded values, the
    leftovers go only when every decoded leftover on one side can stand for a <not decoded> one on the other side
    (equal counts). Otherwise they all stay, so an added, removed or changed constant of that name is still shown.
    Returns (x, y, {name: constants not compared})."""
    names = {n for n, v in (*x, *y) if v == K.NOT_DECODED}
    if not names:
        return x, y, {}
    x, y, skipped = Counter(x), Counter(y), {}
    for n in sorted(names):
        key = (n, K.NOT_DECODED)
        both = min(x[key], y[key])
        x[key] -= both
        y[key] -= both
        x, y = +x, +y                                  # drop zero counts
        xs = Counter({v: c for v, c in x.items() if v[0] == n})
        ys = Counter({v: c for v, c in y.items() if v[0] == n})
        eq = xs & ys
        lx, ly = xs - eq, ys - eq
        tx, ty = sum(lx.values()), sum(ly.values())
        gone = both
        if tx and tx == ty and tx - lx[key] <= ly[key] and ty - ly[key] <= lx[key]:
            x, y = x - lx, y - ly
            gone += tx
        if gone:
            skipped[n] = gone
    return x, y, skipped


def fact_delta(a, b, cap=12, skipped=None):
    """['field: +x, -y', ...] for the fields whose multisets differ ("(x2)" = twice). Constants that cannot be compared
    (value not decoded) are left out and counted in skipped ({name: n}) when given."""
    def item(sign, v, n):
        v = "=".join(v) if isinstance(v, tuple) else v
        v = v if len(v) <= 120 else v[:117] + "..."
        return f"{sign}{v}{f' (x{n})' if n > 1 else ''}"
    parts = []
    for k in dict.fromkeys([*a, *b]):
        x, y = a.get(k, Counter()), b.get(k, Counter())
        if k == "constants":
            x, y, nd = drop_undecoded(x, y)
            if skipped is not None:
                for n, c in nd.items():
                    skipped[n] = skipped.get(n, 0) + c
        if x != y:
            ch = [item("+", v, n) for v, n in sorted((y - x).items())] + [item("-", v, n) for v, n in sorted((x - y).items())]
            parts.append(f"{k}: {', '.join(ch[:cap])}{f' +{len(ch) - cap} more' if len(ch) > cap else ''}")
    return parts


def frame_head(k):
    kind, n, parent, _ = k
    return f"[{n}]" + (f" (in {parent})" if parent else "") + (" event" if kind == "event" else "")


def fact_cost(a, b):
    return sum(sum(((a.get(k, Counter()) - b.get(k, Counter())) + (b.get(k, Counter()) - a.get(k, Counter()))).values())
               for k in set(a) | set(b))


def pair_frames(fa, fb):
    """[(old key | None, new key | None)]: frames paired by kind, name and parent; frames sharing those are paired
    by the smallest difference, not by position (a regeneration may reorder the frames of a structure)."""
    group = lambda fr: {k[:3]: [x for x in fr if x[:3] == k[:3]] for k in fr}  # noqa: E731
    ga, gb = group(fa), group(fb)
    out = []
    for g in dict.fromkeys([*ga, *gb]):
        xa, xb = list(ga.get(g, [])), list(gb.get(g, []))
        for _, a, b in sorted((fact_cost(fa[a], fb[b]), a, b) for a in xa for b in xb):
            if a in xa and b in xb:
                out.append((a, b))
                xa.remove(a)
                xb.remove(b)
        out += [(a, None) for a in xa] + [(None, b) for b in xb]
    return out


def card_delta(old_path, new_path):
    """What changed between two exports, matched by name instead of uid, so it also holds for a regenerated VI:
    terminals (name, connector index, type), calls, primitives, constants, case selectors, messages, tags and events,
    for the VI outside its handler frames and per handler frame. Wiring is not compared."""
    va, fa = delta_facts(old_path)
    vb, fb = delta_facts(new_path)
    nd = {}
    rows = [f"  (VI) {x}" for x in fact_delta(va, vb, skipped=nd)]
    unknown = [f"{n}{f' (x{c})' if c > 1 else ''} in (VI)" for n, c in nd.items()]
    same = both = 0
    for a, b in pair_frames(fa, fb):
        if b is None:
            rows.append(f"  - frame {frame_head(a)}")
        elif a is None:
            rows.append(f"  + frame {frame_head(b)}: {' | '.join(fact_delta({}, fb[b])) or '-'}")
        else:
            both += 1
            nd = {}
            ch = fact_delta(fa[a], fb[b], skipped=nd)
            unknown += [f"{n}{f' (x{c})' if c > 1 else ''} in {frame_head(b)}" for n, c in nd.items()]
            if ch:
                rows.append(f"  ~ frame {frame_head(b)}: {' | '.join(ch)}")
            else:
                same += 1
    tail = f" ({same} of {both} frames unchanged)" if both else ""
    note = ([f"  ? constants not compared, value not decoded by the exporter: {', '.join(unknown)}"]
            if unknown else [])
    if not rows:
        return "\n".join([f"CARD DELTA: none - terminals, calls, primitives, constants, cases, messages, tags and events "
                          f"equal{tail}; wiring is not compared (per-uid diff below)"] + note)
    return "\n".join([f"CARD DELTA (matched by name, independent of uids; wiring not compared){tail}:"] + rows + note)


def diff_report(old_path, new_path):
    """Card delta first (short, survives regeneration), then the per-uid semantic diff."""
    d = semdiff(old_path, new_path)
    return card_delta(old_path, new_path) + "\n" + (d if d else "NO LOGICAL DIFF")


# ---------------------------------------------------------------- cache commands
def with_callers(sk, vis):
    """The given VI paths plus their direct callers (dispatch expanded), each once."""
    from . import query as Q
    r, out, seen = Q.rev_index(sk), [], set()
    for vi in list(vis) + [entry_path(sk, c) for vi in vis for c in sorted({c for c, _, _ in r.get(qname(sk, vi), [])})]:
        if key(vi) not in seen:
            seen.add(key(vi))
            out.append(vi)
    return out


def check(vis, callers=False):
    with locked():
        idx = load()
        sk = skel()
        paths = [p for p in (find_vi(sk, n) for n in vis) if p]
        for vi in with_callers(sk, paths) if callers else paths:
            if vi is None:
                continue
            k = key(vi)
            e = idx.get(k)
            m = meta(vi)
            if e is None:
                idx[k] = {"pending": {**m, "sha256": sha(vi)}}
                print(f"NEW       {vi}")
                continue
            if "xml" not in e:
                e["pending"] = {**m, "sha256": sha(vi)}
                print(f"NEW       {vi}")
                continue
            flag = deps_flag(sk, qname(sk, vi), e)
            if m["mtime"] == e["mtime"] and m["size"] == e["size"]:
                e.pop("pending", None)                      # back to the exported file: an older CHANGED is void
                print(f"UNCHANGED {vi}{flag}")
            else:
                h = sha(vi)
                if h == e["sha256"]:
                    e.update(m)
                    e.pop("pending", None)
                    print(f"SAME      {vi}  (saved, content identical){flag}")
                else:
                    e["pending"] = {**m, "sha256": h}
                    print(f"CHANGED   {vi}{flag}")
        save(idx)


def self_check(sk, q, xml_path):
    """Compare the export with the saved file (skeleton): calls and tagged literals. A mismatch means the export
    came from unsaved edits in LabVIEW memory, or a binary heuristic of skeleton.py broke."""
    if q is None:
        return "SELF-CHECK skipped (VI outside the skeleton)"
    root = ET.parse(xml_path).getroot()
    exp_calls, exp_syms, case_syms = set(), set(), set()
    for e in root.iter():
        if e.tag == "Call":
            for a in ("target", "instance"):
                if e.get(a):
                    exp_calls.add(e.get(a).replace("\\3A", ":").split(":")[-1])
        elif e.tag == "Constant":
            exp_syms |= set(K.SYMBOL.findall(e.get("value", ""))) if K.SYMBOL else set()
        elif e.tag in ("Control", "Indicator"):
            exp_syms |= set(K.SYMBOL.findall(e.get("_name", ""))) if K.SYMBOL else set()
        elif e.tag == "Node" and e.get("_name") == "Static VI Reference" and e.get("target"):
            exp_calls.add(e.get("target").replace("\\3A", ":").split(":")[-1])     # SVVI in the skeleton
        elif e.tag == "CaseFrame":
            case_syms |= set(K.SYMBOL.findall((e.get("selector") or "").replace("\\3A", ":"))) if K.SYMBOL else set()
    v = sk["vis"][q]
    case_only = case_syms - exp_syms - set(v["syms"])      # string case selectors live outside the heaps read
    exp_syms |= case_syms - case_only
    sk_calls = {c.split(":")[-1] for c, k in v["calls"].items()
                if not set(k) & {"CPM", "VIM?"} and not c.startswith("Instance'")}
    exp_calls = {c for c in exp_calls if not c.startswith("Instance'") and not c.lower().endswith(".vim")}
    project = {x.split(":")[-1] for x in sk["vis"]}
    unlinked = {c for c in exp_calls - sk_calls if c not in project}    # Express VI internals etc.: never linked
    exp_calls -= unlinked
    diffs = []
    for what, a, b in (("calls", exp_calls, sk_calls), ("symbols", exp_syms, set(v["syms"]))):
        if a - b:
            diffs.append(f"{what} only in export: {', '.join(sorted(a - b)[:6])}")
        if b - a:
            diffs.append(f"{what} only in saved file: {', '.join(sorted(b - a)[:6])}")
    note = f"; {len(case_only)} symbols only in string case selectors (not visible to the skeleton)" if case_only else ""
    if not diffs:
        return f"SELF-CHECK ok ({len(exp_calls)} calls, {len(exp_syms)} symbols match the saved file{note})"
    return ("SELF-CHECK MISMATCH - export from unsaved edits, or a skeleton heuristic broke: " + "; ".join(diffs) + note)


def store(idx, vi, aixml, vi_meta, backend=None, check=None):
    """Write the stripped export under a content-unique name (a crash before the index save keeps the old XML
    valid) and update the index entry (backend: the AIXML backend that made the export, None = not given; check: the
    SELF-CHECK line when the caller made it already). Returns (new xml, previous xml)."""
    os.makedirs(XMLDIR, exist_ok=True)
    with open(aixml, encoding="utf-8") as f:
        text = f.read()
    for p in STRIP:
        text = re.sub(p, "", text)
    name_ = os.path.splitext(os.path.basename(vi))[0]
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    out = os.path.join(XMLDIR, f"{name_}.{hashlib.md5(key(vi).encode()).hexdigest()[:8]}.{h}.xml")
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    S.replace(out + ".tmp", out)
    sk = skel()
    q = qname(sk, vi)
    t = os.stat(aixml).st_mtime
    prev = idx.get(key(vi), {}).get("xml")
    idx[key(vi)] = {**vi_meta, "xml": out, "exported_at": t, "path": os.path.realpath(vi), "card": make_card(out),
                   "deps_fp": dep_baseline(sk, q, t) if q else {}, "deps_v": DEPS_V,
                   "backend": backend or idx.get(key(vi), {}).get("backend")}
    print(check or self_check(sk, q, out))
    return out, prev


def drop_old(prev, out):
    if prev and os.path.normcase(prev) != os.path.normcase(out):
        with contextlib.suppress(FileNotFoundError):
            os.remove(prev)


def update(vi, aixml, backend=None, by_name=False):
    """by_name: the export was matched to the VI by its name only (update --dir): a SELF-CHECK MISMATCH then stores
    nothing, since the export may belong to another VI of that name."""
    with locked():
        vi = find_vi(skel(), vi)
        if vi is None:
            return
        idx = load()
        e = idx.get(key(vi), {})
        now = {**meta(vi), "sha256": sha(vi)}
        pend = e.get("pending")
        if os.stat(vi).st_mtime > os.stat(aixml).st_mtime or (pend and pend["sha256"] != now["sha256"]):
            print("REFUSED: VI was saved during/after the export - run check and export again")
            return
        check = self_check(skel(), qname(skel(), vi), aixml) if by_name else None
        if check and check.startswith("SELF-CHECK MISMATCH"):
            print(check)
            print("NOT STORED: matched by name only and the export does not fit the saved file - if it is this VI's "
                  "export, save the VI in LabVIEW and export again, or store it with update <vi> <xml>")
            return
        if e.get("xml") and os.path.exists(e["xml"]):
            if backend and e.get("backend") and e["backend"] != backend:
                print(f"NOTE: the cached export came from backend {e['backend']}, this one from {backend} - "
                      f"differences below may come from the backends, not from the VI")
            print(diff_report(e["xml"], aixml))
        else:
            print("NO CACHED VERSION - read the full export")
        out, prev = store(idx, vi, aixml, now, backend, check)
        save(idx)
        drop_old(prev, out)
        print("XML:", out)


def path_hash(vi):
    """The 8 hex digits labview-mcp puts into an export's file name: MD5 of the lower-cased full VI path
    (AixmlTools.ExportName). Both spellings of the path, in case the export was asked with the resolved one."""
    return {hashlib.md5(p.lower().encode("utf-8")).hexdigest()[:8] for p in (os.path.abspath(vi), os.path.realpath(vi))}


def export_target(sk, xml_path):
    """(VI path, how, why) for an export: how = "path" when the file name carries labview-mcp's path hash
    ('<VI name>.<8 hex>.xml') of exactly one VI, "name" when the qualified name in the export's root element (else the
    file name) fits exactly one VI; VI None (with why) otherwise. Same-name VIs outside any class or library (stray
    copies) have the same name in the export, so only the path hash tells them apart."""
    with open(xml_path, encoding="utf-8", errors="replace") as f:
        m = re.search(r'<VI _name="([^"]*)"', f.read(2000))
    name_ = ":".join(K.short(K.unesc(m.group(1))).split(":")[-2:]).lower() if m else ""   # innermost owner, as in cards
    stem, h = re.match(r"(.*?)(?:\.([0-9A-Fa-f]{8}))?$", os.path.splitext(os.path.basename(xml_path))[0]).groups()
    same_stem = [q for q, v in sk["vis"].items()
                 if os.path.splitext(os.path.basename(v["file"]))[0].lower() == stem.lower()]
    if h:
        hit = [q for q in same_stem if h.lower() in path_hash(entry_path(sk, q))]
        if len(hit) == 1:
            return entry_path(sk, hit[0]), "path", None

    def card_name(v):
        own = sk["classes"].get(v.get("class") or "", {}).get("chain", [None])[-1]
        fn = os.path.basename(v["file"])
        return (f"{own}:{fn}" if own else fn).lower()
    named = [q for q, v in sk["vis"].items() if card_name(v) == name_] if name_ else []
    cand = named or same_stem
    if len(cand) == 1:
        return entry_path(sk, cand[0]), "name", None
    if not cand:
        return None, None, "no VI with this name"
    shown = ", ".join(sorted(cand)[:6]) + (" ..." if len(cand) > 6 else "")
    return None, None, f"{len(cand)} VIs with this name ({shown})" + (" and no path hash of one of them" if h else "")


def update_dir(folder, backend=None):
    """update for every export in a folder, each matched to its VI (export_target). Several exports of one VI: only
    the newest is applied, so an older one never replaces it in the cache."""
    sk = skel()
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".xml")) if os.path.isdir(folder) else []
    if not files:
        print(f"no .xml exports in {folder}")
    found = [(fn, os.path.join(folder, fn)) for fn in files]
    found = [(fn, p, *export_target(sk, p)) for fn, p in found]
    newest = {}
    for fn, p, vi, how, why in found:
        if vi and (key(vi) not in newest or os.stat(p).st_mtime > os.stat(newest[key(vi)]).st_mtime):
            newest[key(vi)] = p
    for fn, p, vi, how, why in found:
        if vi and newest[key(vi)] != p:
            print(f"===== {fn} -> {qname(sk, vi)}: SKIPPED, {os.path.basename(newest[key(vi)])} is a newer export of it")
            continue
        print(f"===== {fn} -> {qname(sk, vi) if vi else f'NO UNIQUE VI ({why}) - pass it as update <vi> <xml>'}")
        if vi:
            update(vi, p, backend, by_name=how == "name")


def seed(vi, aixml, backend=None):
    vi = find_vi(skel(), vi)
    if vi is None:
        return
    if os.stat(vi).st_mtime > os.stat(aixml).st_mtime:
        print(f"SKIP {vi} (saved after export)")
        return
    with locked():
        idx = load()
        out, prev = store(idx, vi, aixml, {**meta(vi), "sha256": sha(vi)}, backend)
        save(idx)
        drop_old(prev, out)
        print("SEEDED", out)


def ackdeps(vis):
    with locked():
        idx = load()
        sk = skel()
        for name_ in vis:
            vi = find_vi(sk, name_)
            if vi is None:
                continue
            e, q = idx.get(key(vi), {}), qname(sk, vi)
            if not e.get("xml") or q is None or meta(vi) != {"mtime": e["mtime"], "size": e["size"]}:
                print(f"SKIP {vi} (not cached or not UNCHANGED - check / export first)")
                continue
            e["deps_fp"], e["deps_v"] = S.dep_fps(sk, q), DEPS_V
            print(f"ACK  {vi}  ({len(e['deps_fp'])} deps)")
        save(idx)


def repair():
    """Adopt exports in xml\\ that no index entry points to (e.g. a crash between XML write and index save)."""
    sk = skel()
    with locked():
        idx = load()
        ref = {os.path.normcase(e["xml"]) for e in idx.values() if e.get("xml")}
        ids = {hashlib.md5(k.encode()).hexdigest()[:8]: q for q in sk["vis"]       # name part: md5 of the key
               for k in (key(entry_path(sk, q)), os.path.normcase(os.path.realpath(entry_path(sk, q))))}  # or older
        n = 0
        for fn in sorted(os.listdir(XMLDIR)) if os.path.isdir(XMLDIR) else []:
            p = os.path.join(XMLDIR, fn)
            if not fn.endswith(".xml") or os.path.normcase(p) in ref:
                continue
            parts = fn[:-4].split(".")
            q = next((ids[x] for x in parts[1:] if x in ids), None)
            vi = entry_path(sk, q) if q else None
            if vi is None:
                print(f"ORPHAN    {fn}: no VI in the skeleton - left in place")
            elif os.stat(vi).st_mtime > os.stat(p).st_mtime:
                print(f"STALE     {fn}: {q} saved after this export - left in place (export again)")
            elif (os.path.exists(idx.get(key(vi), {}).get("xml") or "")
                  and os.stat(idx[key(vi)]["xml"]).st_mtime >= os.stat(p).st_mtime):
                print(f"OLDER     {fn}: {q} has a newer cached export - deleted")
                os.remove(p)
            else:
                out, prev = store(idx, vi, p, {**meta(vi), "sha256": sha(vi)})
                save(idx)
                drop_old(prev, out)
                if os.path.normcase(out) != os.path.normcase(p):
                    os.remove(p)
                print(f"ADOPTED   {fn} -> {q}")
                n += 1
    print(f"{n} exports adopted")


def show(vi):
    vi = find_vi(skel(), vi)
    if vi:
        print(load().get(key(vi), {}).get("xml", "NOT EXPORTED"))


def skeleton(opt=""):
    sk = skel(full=opt == "--full")
    s = sk["stats"]
    print(f"project {CFG['name']}: {SRC}  (config: {CFG['file'] or 'none, zero-config'})")
    print(f"{len(sk['vis'])} VIs, {len(sk['ctls'])} typedefs, {len(sk['classes'])} classes/libs; "
          f"rescanned {s['rescanned']}, errors {s['errors']}, same-name VIs kept by path {len(s['dups'])}, "
          f"skipped folders {', '.join(s.get('skipped', [])) or '-'}  -> {SKEL}")
    for d in s["dups"][:10]:
        print(f"  by path: {d}")
    for q, v in sk["vis"].items():
        if v.get("error"):
            print(f"  ERROR {q}: {v['error']}")
    nofp = [c for c, i in sk["classes"].items() if not i["lib"] and not i["fp"] and not i.get("interface")]
    if nofp:
        print(f"  WARNING: private data not decodable (DEPS? blind): {', '.join(nofp)}")


def recard():
    with locked():
        idx = load()
        for k, e in idx.items():
            if e.get("xml") and os.path.exists(e["xml"]):
                e["card"] = make_card(e["xml"])
                e.setdefault("path", os.path.join(SRC, k) if not os.path.isabs(k) else k)
        save(idx)
    print(f"{sum(1 for e in idx.values() if e.get('card'))} cards")


def listing():
    sk = skel()
    for q, (c, stale) in sorted(card_map(sk).items()):
        print(f"{q}{' (card stale)' if stale else ''}  [{', '.join(c['out'])}]")


# ---------------------------------------------------------------- CLI
FLAGS_WITH_VALUE = {"-n", "-d", "--frame", "--xml", "--backend", "--dir", "--max-tokens"}
FLAGS = {"-s", "--all", "--frames", "--full", "--remove", "--with-callers"}


def main(argv):
    from . import ext as E
    from . import query as Q
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    def upd(p, o):
        if "--dir" in o:
            if p:
                sys.exit("update --dir <folder> takes no other arguments")
            return update_dir(o["--dir"], o.get("--backend"))
        if len(p) != 2:
            sys.exit("update needs <vi> <aixml>, or --dir <folder>")
        update(*p, backend=o.get("--backend"))
    cmds = {"check": (lambda p, o: check(p, bool(o.get("--with-callers"))), 1, 999), "update": (upd, 0, 2),
            "seed": (lambda p, o: seed(*p, backend=o.get("--backend")), 2, 2),
            "ackdeps": (lambda p, o: ackdeps(p), 1, 999),
            "show": (lambda p, o: show(*p), 1, 1), "semdiff": (lambda p, o: print(diff_report(*p)), 2, 2),
            "skeleton": (lambda p, o: skeleton("--full" if o.get("--full") else ""), 0, 0),
            "repair": (lambda p, o: repair(), 0, 0), "recard": (lambda p, o: recard(), 0, 0),
            "list": (lambda p, o: listing(), 0, 0),
            "trust": (lambda p, o: E.trust(CFG, remove=bool(o.get("--remove"))), 0, 0), **Q.COMMANDS}
    if argv[1:2] != ["trust"]:                    # trust must not load (and warn about) the code it is asked about
        cmds.update(E.commands(CFG, cmds))
    if len(argv) < 2 or (argv[1] in ("-h", "--help", "help") and argv[1] not in cmds):
        h = E.help_text(CFG)
        sys.exit(__doc__ + (f"\nExtensions ({', '.join(E.load(CFG))}):\n{h}" if h else ""))
    if argv[1] not in cmds:                       # short: the caller is often an assistant following a newer skill
        missing = [n for n in CFG.get("extensions", {}) if n not in E.load(CFG)]
        sys.exit(f'unknown command "{argv[1]}". Commands: {", ".join(sorted(cmds))}; `lvindex` without arguments '
                 "prints the manual.\n" + (f"Extensions not loaded: {', '.join(missing)} (their commands are missing "
                                           "here).\n" if missing else "") + cli.outdated_hint())
    fn, lo, hi = cmds[argv[1]]
    pos, opts, i = [], {}, 2
    while i < len(argv):
        a = argv[i]
        if a in FLAGS_WITH_VALUE:
            if i + 1 >= len(argv):
                sys.exit(f"{a} needs a value")
            opts[a] = argv[i + 1]
            i += 2
        elif a in FLAGS:
            opts[a] = True
            i += 1
        elif a.startswith("-") and len(a) > 1 and not os.path.exists(a):
            sys.exit(f"unknown option {a} (known: {', '.join(sorted(FLAGS_WITH_VALUE | FLAGS))})\n{cli.outdated_hint()}")
        else:
            pos.append(a)
            i += 1
    for a in ("-n", "-d", "--max-tokens"):
        if a in opts and not opts[a].isdigit():
            sys.exit(f"{a} needs a number")
    if not lo <= len(pos) <= hi:
        sys.exit(f"{argv[1]} needs {lo}{'' if lo == hi else '-' + str(hi)} argument(s), got {len(pos)} "
                 f"(quote names with spaces; run without arguments for help)")
    if "--max-tokens" not in opts:
        fn(pos, opts)
        return
    buf = io.StringIO()                                # whole lines up to the budget, ~4 characters per token
    try:
        with contextlib.redirect_stdout(buf):
            fn(pos, opts)
    finally:
        text = buf.getvalue()
        lines, budget, out = text[:-1].split("\n") if text.endswith("\n") else text.split("\n"), \
            int(opts["--max-tokens"]) * 4, []
        for line in lines if text else []:
            if len(line) + 1 > budget:
                break
            out.append(line)
            budget -= len(line) + 1
        if out:
            print("\n".join(out))
        if text and len(out) < len(lines):
            print(f"-- showing {len(out)} of {len(lines)} lines (--max-tokens {opts['--max-tokens']}, "
                  f"~4 characters per token)")
