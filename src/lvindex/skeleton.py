"""No-export skeleton of the whole project, read straight from the files (no LabVIEW; incremental by mtime/size).

Per VI (from the binary RSRC container, see rsrc.py):
  calls  - subVIs called on the block diagram (LIbd block): {name: [kinds]}, kinds:
           IUVI static call, DyOM dynamic-dispatch call, CPM Call Parent Method (stored as DyOM, flag byte 02),
           PUPV polymorphic VI, SVVI static VI reference, VIM? malleable VI (heuristic, see below).
           A DyOM name is "<wire class>:<method>" - the wire class need not implement the method (inherited).
           Malleable VI calls are linked as "Instance'<uuid>.vi" without the .vim name; they are replaced by the
           *.vim names found in the diagram heap (kind VIM?, cannot tell which instance is which).
  deps   - typedefs and classes linked by the VI (LIvi / LIfp / LIbd: TDCC, VICC, TCPI, FPPI, DDPI)
  ext    - callees whose link path lies in vi.lib / user.lib / instr.lib
  syms   - strings matching the project's `tags.literal` regex (block-diagram and front-panel heaps; string case
           selectors are NOT stored there); empty without that config
  strs   - other printable strings of the diagram heap (constants, labels, comments) - full-text search only
  orphan - true when the file lies in a class folder but is not listed in the .lvclass
Per typedef (.ctl): deps (nested typedefs / classes), sha256.
Per class / library (.lvclass / .lvlib XML): parent (superclass inside src), extern_parent (outside src, e.g. vi.lib),
  implements (interfaces), interface flag, lib chain, members, version, fp = hash of the type (VCTP) and default-data
  (DFDS) blocks of the private data (NI.LVClass.FlattenedPrivateDataCTL) - stable when only methods change.
hist: {dep: [[fingerprint, file mtime when first seen], ...]} - lets cache.py find the fingerprint a dependency
  had at export time.
Naming: owner = innermost class / library. Qualified VI name = "<owner>:<file>"; an owner name that is not unique
  gets its outer library ("Name [Lib]") or, for a library clashing with a class, ".lvlib". A VI whose qualified name
  is taken (e.g. a stray copy outside the class) is keyed by its relative path - nothing is dropped.
A class's private data is referenced as "<Class>:<Class>.ctl" (no file) and resolved to the class.
"""
import hashlib
import html
import json
import os
import re
import struct

from .rsrc import blocks, parse, unpack

SCHEMA = 7
FP_V = 1          # fingerprint definition version: history survives schema changes while this is equal
EXT_PATH = (b"<userlib>", b"<vilib>", b"<instrlib>")
IDENT = re.compile(rb"([A-Z][A-Za-z0-9]{3})\x00\x00\x00[\x00-\x0f]")
SEGMENT = re.compile(rb"[ -~]+?(?:\.lvclass|\.lvlib|\.vim|\.vi|\.xctl|\.ctl)(?![a-z0-9])")
TEXT = re.compile(rb"[A-Za-z][A-Za-z0-9_ .:\-]{3,60}")
DYOM_FLAG = re.compile(rb"[\x00\x01]\x01([\x00-\x02])\x00\x00\x00\x02")   # after the path; 02 = Call Parent Method
PRIVATE = re.compile(r'<Property Name="NI.LVClass.FlattenedPrivateDataCTL" Type="Bin">([^<]*)<')
CALL_KINDS = {"IUVI", "DyOM", "PUPV", "SVVI"}
DEP_KINDS = {"TDCC", "VICC", "TCPI", "FPPI", "DDPI"}
LINK_BLOCKS = ("LIvi", "LIfp", "LIbd")
CHAIN = "/"                      # separator of the library chain inside raw link names
B64 = bytes((c - 0x21) if 0x21 <= c < 0x61 else 0xFF for c in range(256))


def short(name):
    return name.replace(".lvclass", "").replace(".lvlib", "")


def decode_bin(s):
    """LabVIEW text encoding of Type="Bin" properties: 6 bits per char ('!' = 0), MSB first."""
    v = html.unescape(re.sub(r"\s", "", s)).encode("latin1", "ignore").translate(B64).replace(b"\xff", b"")
    out = bytearray()
    n = len(v) - len(v) % 4
    for i in range(0, n, 4):
        out += ((v[i] << 18) | (v[i + 1] << 12) | (v[i + 2] << 6) | v[i + 3]).to_bytes(3, "big")
    bits = nb = 0
    for c in v[n:]:                                     # tail: same rule as bit by bit, leftover bits dropped
        bits, nb = (bits << 6) | c, nb + 6
        if nb >= 8:
            nb -= 8
            out.append((bits >> nb) & 0xFF)
    return bytes(out)


def private_fp(text):
    """Hash of the private data's type and default-data blocks (None when not decodable)."""
    m = PRIVATE.search(text)
    if not m:
        return None
    d = decode_bin(m.group(1))
    i = d.find(b"RSRC")
    if i < 0:
        return None
    try:
        b = parse(d[i:])
    except Exception:
        return None
    h = hashlib.sha256()
    for k in ("VCTP", "DFDS"):
        for s in b.get(k, []):
            h.update(k.encode() + unpack(s))
    return h.hexdigest()[:16]


def ncase(p):
    return os.path.normcase(os.path.normpath(p))


def walk(src, skip):
    for d, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x.lower() not in skip]
        yield d, files


def owners(src, skip, old_classes=None):
    """(file -> owner key, classes, class folders, chain map). Owner = innermost .lvclass / .lvlib."""
    raw = {}                                        # normcased file -> parsed facts
    for d, files in walk(src, skip):
        for fn in files:
            if not fn.lower().endswith((".lvclass", ".lvlib")):
                continue
            p = os.path.join(d, fn)
            with open(p, encoding="utf-8-sig", errors="replace") as f:
                x = f.read()
            raw[ncase(p)] = {"path": p, "fn": fn, "x": x, "lib": fn.lower().endswith(".lvlib"), "st": os.stat(p),
                             "items": re.findall(r'<Item Name="([^"]+)" Type="(VI|LVClass|Library)" URL="([^"]+)"', x),
                             "parents": re.findall(r'<Item Name="([^"]+)" Type="Parent" URL="([^"]+)"', x)}
    container = {}                                  # owner file -> the library listing it
    for k, r in raw.items():
        for _, typ, url in r["items"]:
            if typ in ("LVClass", "Library"):
                container[ncase(os.path.join(r["path"], url))] = k

    def chain(k):
        out, seen = [], set()
        while k in raw and k not in seen:
            seen.add(k)
            out.insert(0, short(raw[k]["fn"]))
            k = container.get(k)
        return tuple(out)
    chains = {k: chain(k) for k in raw}
    by_base = {}
    for k, ch in chains.items():
        by_base.setdefault(ch[-1], []).append(k)
    key = {}
    for base, ks in by_base.items():
        cls_ks = [k for k in ks if not raw[k]["lib"]]
        for k in ks:
            if len(ks) == 1 or (len(cls_ks) == 1 and k == cls_ks[0]):
                key[k] = base                       # unique, or the only class next to same-name libraries
            elif raw[k]["lib"] and len([o for o in ks if raw[o]["lib"]]) == 1:
                key[k] = f"{base}.lvlib"
            else:
                ch = chains[k]
                key[k] = f"{base} [{ch[-2]}]" if len(ch) > 1 else f"{base} [{os.path.relpath(raw[k]['path'], src)}]"
    seen = {}
    for k in sorted(key):                           # last resort: still equal -> relative path
        if key[k] in seen:
            key[k] = os.path.relpath(raw[k]["path"], src)
        seen[key[k]] = k
    own, classes, dirs_ = {}, {}, set()
    for k, r in raw.items():
        name, st, x = key[k], r["st"], r["x"]
        prev = (old_classes or {}).get(name, {})
        same = prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size
        members = []
        for item, typ, url in r["items"]:
            if typ == "VI":
                own[ncase(os.path.join(r["path"], url))] = name
                members.append(item)
        if not r["lib"]:
            dirs_.add(ncase(os.path.dirname(r["path"])))
        parents = []
        for item, url in r["parents"]:
            pk = ncase(os.path.join(r["path"], url))
            parents.append(key[pk] if pk in key else
                           {"extern": ":".join(short(s) for s in item.split(":"))})
        ver = re.search(r'<Property Name="NI.Lib.Version" Type="Str">([^<]*)<', x)
        classes[name] = {"parents": parents, "methods": sorted(members), "file": os.path.relpath(r["path"], src),
                         "version": ver.group(1) if ver else None, "lib": r["lib"], "chain": list(chains[k]),
                         "interface": bool(re.search(r'NI.LVClass.IsInterface" Type="Bool">true', x)),
                         "fp": None if r["lib"] else prev["fp"] if same and "fp" in prev else private_fp(x),
                         "mtime": st.st_mtime, "size": st.st_size}
    for c, i in classes.items():                    # superclass = first non-interface parent; the rest: interfaces
        internal = [p for p in i["parents"] if isinstance(p, str)]
        sup = next((p for p in internal if not classes[p]["interface"]), None)
        ext = [p["extern"] for p in i["parents"] if isinstance(p, dict)]
        i["parent"] = sup
        i["extern_parent"] = ext[0] if ext and not sup else None
        i["implements"] = [p for p in internal if p != sup]
        del i["parents"]
    cmap = {tuple(chains[k]): key[k] for k in raw}
    return own, classes, dirs_, cmap


def path_info(rest):
    """(last .lvclass segment of the PTH0 path, bytes after the path) - rest starts with b'PTH0'."""
    try:
        n = struct.unpack_from(">I", rest, 4)[0]
        body, after = rest[8:8 + n], rest[8 + n:]
        cnt, pos, segs = struct.unpack_from(">H", body, 2)[0], 4, []
        for _ in range(cnt):
            ln = body[pos]
            segs.append(body[pos + 1:pos + 1 + ln].decode("latin1"))
            pos += 1 + ln
    except (struct.error, IndexError):
        return None, b""
    cls = [s for s in segs if s.lower().endswith(".lvclass")]
    return (short(cls[-1]) if cls else None), after


def link_entries(li):
    """(ident, raw name, info) per entry of a link-info block. raw name = "<lib chain, outer/inner>:<leaf>".
    info: {"cpm": True} for Call Parent Method, {"pathcls": C} = class named in the entry's path, ext flag."""
    marks = [(m.start(), m.group(1).decode("latin1")) for m in IDENT.finditer(li)]
    for i, (pos, ident) in enumerate(marks):
        chunk = li[pos + 4: marks[i + 1][0] if i + 1 < len(marks) else len(li)]
        head, sep, rest = chunk.partition(b"PTH0")
        segs = []
        for x in SEGMENT.findall(head):
            x = re.sub(r"^[^A-Za-z0-9_<]+", "", x.decode("latin1"))
            if x and x not in segs:
                segs.append(x)
        libs = [short(x) for x in segs if x.lower().endswith((".lvclass", ".lvlib"))]
        leaf = [x for x in segs if not x.lower().endswith((".lvclass", ".lvlib"))]
        info = {"ext": bool(sep) and any(t in rest[:400] for t in EXT_PATH)}
        if ident == "DyOM" and sep:
            pathcls, after = path_info(sep + rest)
            m = DYOM_FLAG.search(after[:48])
            info.update(pathcls=pathcls, cpm=bool(m and m.group(1) == b"\x02"))
        if leaf:
            yield ident, (CHAIN.join(libs) + ":" if libs else "") + leaf[-1], info
        elif libs:
            yield ident, CHAIN.join(libs), info


def links(b):
    """(calls, deps, ext) with raw names - ext: callee names whose link path is in vi.lib / user.lib."""
    calls, deps, ext = {}, set(), set()
    for blk in LINK_BLOCKS:
        for ident, name, info in link_entries(b.get(blk, [b""])[0]):
            if blk == "LIbd" and ident in CALL_KINDS:
                if ident == "DyOM" and ":" not in name and info.get("pathcls"):
                    name = f"{info['pathcls']}:{name}"         # member call: wire class only in the path
                calls.setdefault(name, set()).add("CPM" if info.get("cpm") else ident)
                if info.get("ext"):
                    ext.add(name)
            elif ident in DEP_KINDS and not name.lower().endswith((".vi", ".vim")):
                deps.add(name)
    return calls, deps, ext


def scan_vi(path, symbol=None):
    b = blocks(path)
    calls, deps, ext = links(b)
    syms, strs = set(), set()
    for k, secs in b.items():
        if k.startswith(("BDH", "FPH")):                 # FP too: controls named after symbols
            for raw in secs:
                h = unpack(raw)
                if symbol is not None:
                    syms |= {x.decode("latin1") for x in symbol.findall(h)}
                if k.startswith("BDH"):
                    strs |= {x.decode("latin1").strip() for x in TEXT.findall(h)}
    inst = [c for c in calls if c.startswith("Instance'")]
    vims = sorted(s for s in strs if s.lower().endswith(".vim"))
    if inst and vims:
        all_ext = all(c in ext for c in inst)
        for c in inst:
            del calls[c]
            ext.discard(c)
        for v in vims:
            calls.setdefault(v, set()).add("VIM?")
            if all_ext:
                ext.add(v)
    strs = {s for s in strs if not s.lower().endswith((".lvclass", ".lvlib", ".vi", ".vim", ".ctl"))}
    return {"calls": {k: sorted(v) for k, v in calls.items()}, "deps": sorted(deps), "syms": sorted(syms),
            "strs": sorted(strs - syms), "ext": sorted(ext)}


def scan_ctl(path):
    _, deps, _ = links(blocks(path))
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return {"deps": sorted(deps), "sha256": h.hexdigest()}


def owner_candidates(part, caller_chain, cmap, ends):
    """Owner keys a raw library chain ("Outer/Inner") may denote, most specific first: the caller's enclosing
    libraries + the chain (a link omits libraries the caller shares), the exact chain, project chains ending with
    it, then the same for shorter suffixes."""
    ch = tuple(part.split(CHAIN))
    out = []

    def add(k):
        if k and k not in out:
            out.append(k)
    for i in range(len(caller_chain), 0, -1):
        add(cmap.get(tuple(caller_chain[:i]) + ch))
    for j in range(len(ch)):
        add(cmap.get(ch[j:]))
        for k in ends.get(ch[j:], []):
            add(k)
    return out


def build(src, old=None, full=False, skip=(), symbol=None, cfg_hash=""):
    """Incremental: re-scan only files whose mtime/size changed (full: all; fingerprint history kept).
    skip: folder names not scanned; symbol: compiled bytes regex for `syms` (None = none); cfg_hash: a different
    value forces a full rescan. Raises if src is not a folder."""
    if not os.path.isdir(src):
        raise FileNotFoundError(f"skeleton src folder not found: {src}")
    skip = {s.lower() for s in skip}
    if not old or old.get("schema") != SCHEMA or old.get("cfg") != cfg_hash:
        keep = old and old.get("fp_v", 1 if old.get("schema") == 3 else None) == FP_V
        old = {"vis": {}, "ctls": {}, "hist": old["hist"] if keep else {}}
    own, classes, class_dirs, cmap = owners(src, skip, None if full else old.get("classes"))
    ends = {}                                        # every suffix of a project chain -> owner keys
    for ch, k in cmap.items():
        for j in range(len(ch)):
            ends.setdefault(ch[j:], []).append(k)
    base_of = {k: ch[-1] for ch, k in cmap.items()}
    new, stats = {"vis": {}, "ctls": {}}, {"rescanned": 0, "errors": 0, "dups": [], "skipped": sorted(skip)}
    old_by_file = {kind: {v["file"]: (q, v) for q, v in old[kind].items()} for kind in ("vis", "ctls")}
    for d, files in walk(src, skip):
        for fn in sorted(files):
            kind = "vis" if fn.lower().endswith((".vi", ".vim")) else "ctls" if fn.lower().endswith(".ctl") else None
            if not kind:
                continue
            p = os.path.join(d, fn)
            rel = os.path.relpath(p, src)
            st = os.stat(p)
            cls = own.get(ncase(p))
            q = f"{cls}:{fn}" if cls else fn
            if q in new[kind]:                       # taken: keep the file under its relative path
                stats["dups"].append(rel)
                q = rel
            nd = os.path.normcase(d)
            orphan = cls is None and any(nd == c or nd.startswith(c + os.sep) for c in class_dirs)
            prev = None if full else old_by_file[kind].get(rel, (None, None))[1]
            if prev and prev["mtime"] == st.st_mtime and prev["size"] == st.st_size:
                new[kind][q] = {**prev, "orphan": orphan, "class": cls}
                continue
            stats["rescanned"] += 1
            try:
                info = scan_vi(p, symbol) if kind == "vis" else scan_ctl(p)
            except Exception as ex:                     # unusual file: keep it listed
                stats["errors"] += 1
                info = {"calls": {}, "deps": [], "syms": [], "strs": [], "ext": [], "sha256": None,
                        "error": str(ex)[:80]}
            new[kind][q] = {**info, "raw_calls": info.get("calls", {}), "raw_deps": info["deps"],
                            "raw_ext": info.get("ext", []), "file": rel,
                            "mtime": st.st_mtime, "size": st.st_size, "class": cls, "orphan": orphan}
    vis, ctls = new["vis"], new["ctls"]

    def qual(cls, n, pool, ext=False):
        """Project name of a raw link name: library chain -> the owner that really has the leaf (classes win
        over same-name libraries); unqualified -> own class member; vi.lib names keep their innermost owner."""
        cchain = classes[cls]["chain"] if cls in classes else []
        if ":" in n:
            part, leaf = n.rsplit(":", 1)
            if ext:
                return f"{part.split(CHAIN)[-1]}:{leaf}"
            cands = owner_candidates(part, cchain, cmap, ends)
            for k in cands:
                if f"{k}:{leaf}" in pool or leaf == f"{base_of[k]}.ctl":
                    return f"{k}:{leaf}"
            pick = [k for k in cands if not classes[k]["lib"]] or cands
            return f"{pick[0] if pick else part.split(CHAIN)[-1]}:{leaf}"
        if not ext and (CHAIN in n or (n,) in ends):           # a bare library / class reference
            cands = owner_candidates(n, cchain, cmap, ends)
            pick = [k for k in cands if not classes[k]["lib"]] or cands
            return pick[0] if pick else n.split(CHAIN)[-1]
        if cls and not ext and (f"{cls}:{n}" in pool or n == f"{base_of.get(cls, cls)}.ctl"):
            return f"{cls}:{n}"
        return n
    for kind in ("vis", "ctls"):
        for v in new[kind].values():
            cls = v.get("class")
            if kind == "vis":
                ext = set(v.get("raw_ext", v.get("ext", [])))
                calls = {}
                for n, k in v["raw_calls"].items():
                    calls.setdefault(qual(cls, n, vis, n in ext), set()).update(k)
                v["calls"] = {n: sorted(k) for n, k in calls.items()}
                v["ext"] = sorted({qual(cls, n, vis, True) for n in ext})
            v["deps"] = sorted({qual(cls, n, ctls) for n in v["raw_deps"]})
    # fingerprint history (fingerprint, file mtime when first seen)
    hist = {k: list(h) for k, h in old.get("hist", {}).items()}
    cur = {q: ((e.get("sha256") or "?")[:16], e["mtime"]) for q, e in ctls.items()}
    cur.update({f"class {c}": (i["fp"], i["mtime"]) for c, i in classes.items() if not i["lib"]})
    for k, (fp, mt) in cur.items():
        h = hist.setdefault(k, [])
        if not h or h[-1][0] != fp:
            h.append([fp, mt])
    return {"schema": SCHEMA, "fp_v": FP_V, "cfg": cfg_hash, "src": src, "vis": vis, "ctls": ctls,
            "classes": classes, "hist": hist, "stats": stats}


def save(sk, path):
    tmp = f"{path}.{os.getpid()}.tmp"          # per process: parallel commands must not share a tmp file
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sk, f, ensure_ascii=False)
    replace(tmp, path)


def replace(tmp, path, tries=20):
    """os.replace with retries: on Windows it fails while another process has the target open."""
    import time
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.1)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def superclasses(sk, cls):
    """Superclass chain inside the project (no interfaces)."""
    out, c = [], sk["classes"].get(cls, {}).get("parent")
    while c and c not in out and c in sk["classes"]:
        out.append(c)
        c = sk["classes"][c].get("parent")
    return out


def ancestors(sk, cls):
    """Superclass chain inside the project, then implemented interfaces (default implementations)."""
    out = superclasses(sk, cls)
    todo = [cls] + list(out)
    while todo:                                      # interfaces, also those inherited by interfaces
        x = todo.pop(0)
        for i in sk["classes"].get(x, {}).get("implements", []):
            if i not in out and i != cls:
                out.append(i)
                todo.append(i)
    return out


def descendants(sk, cls):
    """Subclasses and implementers (for an interface), transitively."""
    kids = sorted(c for c, i in sk["classes"].items() if i.get("parent") == cls or cls in i.get("implements", []))
    out = []
    for k in kids:
        for x in [k] + descendants(sk, k):
            if x not in out:
                out.append(x)
    return out


def implementation(sk, cls, fn):
    """Nearest implementation of method fn for objects of exactly class cls (self, else nearest ancestor)."""
    for c in [cls] + ancestors(sk, cls):
        if f"{c}:{fn}" in sk["vis"]:
            return f"{c}:{fn}"
    return None


def class_of_dep(sk, name):
    """Class name if a dependency refers to a class (or to its private data "<C>:<C>.ctl")."""
    c = name.split(":", 1)[0] if name.endswith(".ctl") and ":" in name else name
    info = sk["classes"].get(c)
    if info and not info["lib"] and (c == name or name.split(":", 1)[1] == f"{info['chain'][-1]}.ctl"):
        return c
    return None


def dep_fps(sk, q):
    """{dep: fingerprint} for the typedefs a VI links (followed transitively through typedefs) and for its own
    class's private data - other classes' private data is not accessible to the VI. Names that do not resolve
    (vi.lib, skipped folders) are left out."""
    v = sk["vis"].get(q)
    out, todo, seen = {}, list(v["deps"]) if v else [], set()
    if v and v.get("class") in sk["classes"] and not sk["classes"][v["class"]]["lib"]:
        out[f"class {v['class']}"] = sk["classes"][v["class"]]["fp"]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        if class_of_dep(sk, n):
            continue
        if n in sk["ctls"]:
            e = sk["ctls"][n]
            out[n] = (e.get("sha256") or "?")[:16]
            todo += e["deps"]
    return out


def fp_at(sk, dep, t):
    """Fingerprint a dependency had at time t (None when unknown: first seen with a later mtime)."""
    best = None
    for fp, mt in sk.get("hist", {}).get(dep, []):
        if mt <= t:
            best = fp
    return best


def canon(sk, name):
    """Skeleton key for a callee named "<innermost owner base>:<file>" (card naming); the name itself when it is a
    key already or ambiguous."""
    if name in sk["vis"] or ":" not in name:
        return name
    part, leaf = name.rsplit(":", 1)
    cands = [k for k, i in sk["classes"].items() if i["chain"][-1] == part and f"{k}:{leaf}" in sk["vis"]]
    return f"{cands[0]}:{leaf}" if len(cands) == 1 else name
