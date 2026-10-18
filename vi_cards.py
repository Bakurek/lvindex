"""Mechanical facts ("card") extracted from a cached AIXML export - no interpretation, no LLM.

card = {v, vi, in, out, calls, plc: {read, write, wait, case, used, dyn}, sends, handles,
        events: {gen, reg, handled}, results, dynamic}
Generic parts (always): terminals, calls, Call Parent Method, user events, Event Structure frames, frame consts.
Project parts (configure(cfg) with the vi-tools.toml sections; absent section = empty field):
  plc      [tags]: string literals matching tags.literal with a role (read / write / wait from tags.roles regexes
           on the callee name, case = only branched on, used = anything else); dyn = role calls whose symbol input
           is not a string constant (comes from data at run time)
  sends    [messages]: "<message>[<inner>] -> <queue>" for calls of the enqueue VIs; inner = first field of a
           {string, variant} message-cluster constant bundled into the data (envelope messages); queue
           "<field>:<accessor>" when the object comes from an Unbundle, plain "<accessor>" for the own object
  handles  [messages]: cases of the Case Structure driven by a dequeue VI, plus "X (in Default)" for a case
           structure nested in a frame and driven by its selector value. Without [messages]: case structures that
           look like dispatchers (>= 3 named cases, not error / boolean, inside a loop)
  results  status_enum: enum constants containing that item, not necessarily outputs
frames(path) -> per-handler cards for those cases and Event Structure frames (computed on demand, never stored).
CARD_V changes with this file and with the configuration: older cards are rebuilt lazily by vi_cache.py.
"""
import hashlib
import re
import xml.etree.ElementTree as ET

with open(__file__, "rb") as _f:
    _SRC_HASH = hashlib.sha256(_f.read()).hexdigest()
CARD_V = _SRC_HASH[:8]

WIRE = re.compile(r"(?:^|,)([^:,]*):(\d+\.[^,]*)")
SYMBOL = None             # tags.literal (str regex) or None
ROLES = {}                # role -> [compiled regex on the lowercase callee name]
MSG = None                # [messages] section or None
STATUS = None             # status_enum item or None
SYMBOL_PORT = "symbol"    # tags.symbol_port: input of a role call that carries the literal
PASS_THROUGH = {"Index Array", "Build Array", "Array Subset", "Select", "Bundle", "Bundle By Name",
                "Unbundle", "Unbundle By Name", "Concatenate Strings", "To Variant", "Variant To Data"}
NOISE_CALL = re.compile(r"(^|:)(Clear Errors|Get LV Class Name)\.vi$")


def configure(cfg):
    """Apply the project configuration (vi_config.get()); also changes CARD_V."""
    global SYMBOL, ROLES, MSG, STATUS, CARD_V, NOISE_CALL, SYMBOL_PORT
    tags = cfg.get("tags") or {}
    SYMBOL_PORT = tags.get("symbol_port", "symbol").lower()
    SYMBOL = re.compile(tags["literal"]) if tags.get("literal") else None
    ROLES = {r: [re.compile(x) for x in pats] for r, pats in (tags.get("roles") or {}).items()} if SYMBOL else {}
    MSG = cfg.get("messages") or None
    STATUS = cfg.get("status_enum")
    names = [re.escape(n) for n in (MSG or {}).get("enqueue", []) + (MSG or {}).get("dequeue", [])]
    NOISE_CALL = re.compile(r"(^|:)(" + "|".join(names + ["Clear Errors", "Get LV Class Name"]) + r")[^:]*\.vi$")
    sig = repr((SYMBOL and SYMBOL.pattern, sorted((tags.get("roles") or {}).items()), MSG, STATUS, SYMBOL_PORT))
    CARD_V = hashlib.sha256((_SRC_HASH + sig).encode()).hexdigest()[:8]


def msg_cfg(k, default):
    return (MSG or {}).get(k, default)


def is_enqueue(t):
    return MSG is not None and any(n in t for n in MSG.get("enqueue", []))


def is_dequeue(t):
    return MSG is not None and any(n in t for n in MSG.get("dequeue", []))


def short(name):
    return (name or "").replace(".lvclass", "").replace(".lvlib", "")


def target(e):
    """Callee as "<innermost class / library>:<file>" (same naming as the skeleton)."""
    parts = short(e.get("target", "").replace("\\3A", ":")).split(":")
    return ":".join(parts[-2:])


def vi_name(e):
    return target(e).split(":")[-1]


def unesc(s):
    return (s or "").replace("\\3A", ":").replace("\\2C", ",").replace('"', "").strip()


def enum_names(t):
    m = re.match(r"u?int\d+\{(.*)\}$", t or "")
    return m.group(1).split(",") if m else None


def status_names(t):
    """Names of the configured status enum (status_enum) - a bare enum or the first field of a cluster."""
    if not STATUS:
        return None
    m = re.match(r"(?:cluster\{)?u?int\d+\{([^}]*)\}", t or "")
    names = m.group(1).split(",") if m else None
    return names if names and STATUS in names else None


class Model:
    def __init__(self, path):
        self.root = ET.parse(path).getroot()
        self.parent, self.prod, self.cons, self.sel = {}, {}, {}, {}
        for e in self.root.iter():
            for c in e:
                self.parent[c] = e
            for port, wire in WIRE.findall(e.get("outputs") or ""):
                self.prod[wire] = (e, port)
            for port, wire in WIRE.findall(e.get("inputs") or ""):
                self.cons.setdefault(wire, []).append((e, port))
            if e.tag == "CaseFrame" and e.get("selectout"):
                self.prod[e.get("selectout")] = (e, "selector")    # value of the case selector inside a frame
            if e.tag == "Structure" and e.get("selectin"):
                self.sel.setdefault(e.get("selectin"), []).append(e)

    def inputs(self, e):
        return WIRE.findall(e.get("inputs") or "")                # list: ports may repeat

    def source(self, wire, depth=0):
        """(element, port) driving a wire, following tunnels / shift registers."""
        if wire not in self.prod:
            return None, None
        e, port = self.prod[wire]
        if depth > 12 or e.tag not in ("Tunnel", "Left", "Right"):
            return e, port
        ins = e.get("inputs")
        owner = self.parent.get(e)
        if not ins and e.tag == "Tunnel" and owner is not None and owner.tag == "CaseFrame":
            struct = self.parent.get(owner)
            for t in struct.findall("Tunnel") if struct is not None else []:
                if t.get("_id") == e.get("_id") and t.get("inputs"):
                    ins = t.get("inputs")
        m = WIRE.search(ins or "")
        return self.source(m.group(2), depth + 1) if m else (e, port)

    def leaves(self, wire, depth=0):
        """Sources of a wire, looking through pass-through nodes (arrays, bundles, selects)."""
        e, port = self.source(wire)
        if e is None or depth > 8:
            return [(e, port)]
        if e.tag == "Node" and e.get("_name") in PASS_THROUGH:
            out = [x for _, w in self.inputs(e) for x in self.leaves(w, depth + 1)]
            return out or [(e, port)]
        return [(e, port)]

    def sinks(self, wire, depth=0, seen=None):
        """Final consumers of a wire: through tunnels, pass-through nodes and case selectors."""
        seen = seen if seen is not None else set()
        if wire in seen or depth > 16:
            return []
        seen.add(wire)
        out = []
        for st in self.sel.get(wire, []):                         # value used as case selector
            for frame in st.findall("CaseFrame"):
                if frame.get("selectout"):
                    out += self.sinks(frame.get("selectout"), depth + 1, seen)
        for e, port in self.cons.get(wire, []):
            nxt = []
            if e.tag in ("Tunnel", "Left", "Right"):
                nxt = [w for _, w in WIRE.findall(e.get("outputs") or "")]
                if e.tag == "Tunnel" and self.parent.get(e) is not None and self.parent[e].tag == "Structure":
                    for frame in self.parent[e].findall("CaseFrame"):
                        for t in frame.findall("Tunnel"):
                            if t.get("_id") == e.get("_id"):
                                nxt += [w for _, w in WIRE.findall(t.get("outputs") or "")]
            elif e.tag == "Node" and e.get("_name") in PASS_THROUGH:
                nxt = [w for _, w in WIRE.findall(e.get("outputs") or "")]
            else:
                out.append((e, port))
            for w in nxt:
                out += self.sinks(w, depth + 1, seen)
        return out


def short_type(t):
    t = t or ""
    if t.startswith("ref{UDClassInst}"):
        return "class"
    if t.startswith("cluster{bool.status,int32.code"):
        return "error"
    return re.sub(r"\{.*\}", "{..}", t)[:30]


def ev_name(m, wire):
    e, port = m.source(wire)
    if e is None:
        return "?"
    if e.tag == "Call":
        return re.sub(r"^(Read|Get) |\.vi$", "", vi_name(e))
    if e.tag == "Node" and e.get("fields"):
        return port or e.get("fields")
    return e.get("_name") or e.tag


def role_of(e):
    if e.tag != "Call" or not ROLES:
        return None
    n = vi_name(e).lower()
    for role, pats in ROLES.items():
        if any(p.search(n) for p in pats):
            return role
    return None


def roles(m, wires):
    rs = {role_of(s) for w in wires for s, _ in m.sinks(w)}
    rs.discard(None)
    return rs or {"used"}


def describe(e, port):
    if e is None:
        return "unwired"
    if e.tag == "Call":
        return f"{vi_name(e)}.{port}"
    if e.tag == "Node":
        return f"{e.get('_name')}.{port}"
    return e.get("_name") or e.tag


def object_origins(m, wire, depth=0):
    """Where an object wire comes from: through arrays / selects / loop tunnels and the 'input cluster' of a bundle,
    stopping at an Unbundle (the field = another actor) or anything else."""
    e, port = m.source(wire)
    if e is not None and e.tag == "Tunnel" and not e.get("inputs") and depth <= 10:
        st = m.parent.get(e)                               # case output tunnel: value from every frame
        ins = [w for f in (st.findall("CaseFrame") if st is not None else []) for t in f.findall("Tunnel")
               if t.get("_id") == e.get("_id") for _, w in m.inputs(t)]
        return [x for w in ins for x in object_origins(m, w, depth + 1)] or [(e, port)]
    if e is None or depth > 10 or e.tag != "Node":
        return [(e, port)]
    n, ins = e.get("_name", ""), m.inputs(e)
    if n in ("Bundle", "Bundle By Name"):
        follow = [w for p, w in ins if p == "input cluster"]
    elif n in ("Build Array", "Index Array", "Array Subset", "Select"):
        follow = [w for p, w in ins if p not in ("s", "index", "length") and not p.startswith("index")]
    else:
        return [(e, port)]
    return [x for w in follow for x in object_origins(m, w, depth + 1)] or [(e, port)]


def queue_names(m, ins):
    """Queues an Enqueue writes to. A queue accessor (Read Self-Queue / Read Caller-Queue ...) on an actor object:
    '<field>:<accessor>' when the object is unbundled from another actor (also through arrays and loops - one entry
    per field), plain '<accessor>' when it is the VI's own object, '?:<accessor>' when its origin is unknown."""
    return queue_of(m, queue_wire(ins))


def queue_wire(ins):
    return next((ins[p] for p in msg_cfg("queue_ports", ["Message Queue"]) if p in ins), "")


def queue_of(m, wire, depth=0):
    q, qport = m.source(wire)
    if q is None or depth > 6:
        return ["?"]
    if q.tag == "Call" and (qport or "").startswith("dup") and (is_enqueue(target(q)) or is_dequeue(target(q))):
        return queue_of(m, queue_wire(dict(m.inputs(q))), depth + 1)   # queue passed on from a previous call
    if q.tag == "Node" and q.get("_name") in ("Build Array", "Index Array", "Select"):
        return sorted({x for p, w in m.inputs(q) if p not in ("s", "index") for x in queue_of(m, w, depth + 1)})
    if q.tag != "Call":
        return [q.get("_name") or q.tag]
    n = re.sub(r"^Read |\.vi$", "", vi_name(q))
    obj = next((w for p, w in m.inputs(q) if p.endswith(" in")), None)
    out = []
    for s, port in (object_origins(m, obj) if obj else [(None, None)]):
        if s is not None and s.tag == "Node" and s.get("_name", "").startswith("Unbundle"):
            out.append(f"{port}:{n}")
        elif s is not None and (s.tag in ("Control", "Left", "Right")
                                or (s.tag == "Call" and (port or "").endswith(" out"))):
            out.append(n)                                  # own object (terminal, shift register, class pass-through)
        else:
            out.append(f"?:{n}")
    return sorted(set(out))


def inner_message(m, wire):
    """First field of a {string, variant} message-cluster constant bundled into the data (envelope messages)."""
    s, _ = m.source(wire)
    for _ in range(6):
        if s is None:
            return None
        t = s.get("type", "")
        if s.tag == "Constant" and t.startswith("cluster{string.") and "variant" in t:
            mm = re.match(r"\[([^,\]]*)", s.get("value", ""))
            return mm.group(1).strip() if mm and mm.group(1).strip() else None
        if s.tag == "Node" and s.get("_name") in ("Bundle By Name", "Bundle"):
            w = dict(m.inputs(s)).get("input cluster")
            if not w:
                return None
            s, _ = m.source(w)
            continue
        return None
    return None


def card_of(m, scope, top=False):
    """Card for the subtree of scope (the whole VI when top)."""
    r = m.root
    c = {"v": CARD_V, "vi": short(r.get("_name")), "in": [], "out": [], "calls": set(), "plc": {}, "sends": set(),
         "handles": [], "events": {"gen": set(), "reg": set(), "handled": []}, "results": set(), "dynamic": []}

    def add_syms(text, wires):
        syms = SYMBOL.findall(text or "") if SYMBOL else []
        if syms:
            for ro in roles(m, wires):
                c["plc"].setdefault(ro, set()).update(syms)

    if top:
        for e in r:
            if e.tag in ("Control", "Indicator") and e.get("conIdx"):
                c["in" if e.tag == "Control" else "out"].append(f"{e.get('_name')}:{short_type(e.get('type'))}")
    for e in scope.iter():
        if e.tag == "Call":
            t = target(e)
            c["calls"].add(t)
            if is_enqueue(t):
                ins = dict(m.inputs(e))
                inner = inner_message(m, ins.get(msg_cfg("data_port", "Message Data"), ""))
                for qn in queue_names(m, ins):
                    for port in msg_cfg("message_ports", ["Message"]):
                        if port not in ins:
                            continue
                        s, _ = m.source(ins[port])
                        if s is not None and s.tag == "Constant":
                            for msg in re.findall(r"[^\[\],]+", s.get("value", "")):
                                c["sends"].add(f"{msg.strip()}{f'[{inner}]' if inner else ''} -> {qn}")
                        elif s is not None and s.tag == "CaseFrame":
                            sel = unesc(s.get("selector"))       # message = the case's own selector value
                            if sel and sel != "Default" and "," not in sel and ".." not in sel:
                                c["sends"].add(f"{sel} -> {qn}")
                            else:
                                c["sends"].add(f"<case {sel}> -> {qn}")
                        else:
                            c["dynamic"].append(f"message via {port} -> {qn}")
            if role_of(e):
                for port, w in m.inputs(e):
                    if SYMBOL_PORT in port.lower():
                        src = [x for x in m.leaves(w) if not (x[0] is not None and x[0].tag == "Constant"
                                                              and "string" in x[0].get("type", ""))]
                        if src:
                            c["plc"].setdefault("dyn", set()).add(
                                f"{vi_name(e)} {port}<-{describe(*src[0])}")
        elif e.tag == "Node" and e.get("_name") == "Call Parent Class Method":
            c["calls"].add("Call Parent Method")
        elif e.tag == "Node" and e.get("_name") == "Generate User Event":
            ue = dict(m.inputs(e)).get("user event", "")
            c["events"]["gen"].add(ev_name(m, ue))
        elif e.tag == "Node" and e.get("_name") == "Register For Events":
            for port, wire in m.inputs(e):
                if not port.startswith(("error in", "event registration refnum")):
                    c["events"]["reg"].add(ev_name(m, wire))
        elif e.tag == "Constant":
            v, t = e.get("value", ""), e.get("type", "")
            if "string" in t:
                add_syms(v, [w for _, w in WIRE.findall(e.get("outputs") or "")])
            names = status_names(t)
            if names:
                n = re.match(r"\[?(\d+)", v)
                if n and int(n.group(1)) < len(names):
                    c["results"].add(names[int(n.group(1))])
        elif e.tag == "CaseFrame":
            if e.get("selectout"):
                add_syms(e.get("selector"), [e.get("selectout")])
            else:                                                 # symbol only used to branch on
                for sym in (SYMBOL.findall(e.get("selector") or "") if SYMBOL else []):
                    c["plc"].setdefault("case", set()).add(sym)
        if e.tag == "Structure" and e.get("_name") == "Event Structure":
            c["events"]["handled"] += [unesc(f.get("selector")) for f in e.findall("CaseFrame")]
    if top:
        c["handles"] = [f"{name} (in {parent})" if parent else name
                        for kind, name, parent, _ in handler_frames(m) if kind == "msg"]
    for k in ("calls", "sends", "results"):
        c[k] = sorted(c[k])
    c["plc"] = {k: sorted(v) for k, v in sorted(c["plc"].items())}
    c["events"] = {k: sorted(set(v)) for k, v in c["events"].items()}
    return c


def make_card(path):
    m = Model(path)
    return card_of(m, m.root, top=True)


def handler_frames(m):
    """[(kind, name, parent, frame)]: message cases of the dequeue-driven case structure (plus cases nested in a
    frame and driven by its selector value), or without [messages] dispatcher-like case structures, and Event
    Structure frames."""
    out = []

    def nested(frame, label):
        so = frame.get("selectout")
        if not so:
            return
        for st in m.sel.get(so, []):
            for f in st.findall("CaseFrame"):
                n = unesc(f.get("selector"))
                if n != "Default":
                    out.append(("msg", n, label, f))
                nested(f, label if n == "Default" else n)

    deq = {e for e in m.root.iter("Call") if is_dequeue(target(e))}
    for st in m.root.iter("Structure"):
        if st.get("selectin"):
            s, port = m.source(st.get("selectin"))
            if (s in deq and port in msg_cfg("message_ports", ["Message"])) or (MSG is None and dispatcher(m, st)):
                for f in st.findall("CaseFrame"):
                    n = unesc(f.get("selector"))
                    out.append(("msg", n, None, f))
                    nested(f, n)
        if st.get("_name") == "Event Structure":
            for f in st.findall("CaseFrame"):
                out.append(("event", re.sub(r":\s*User Event$", "", unesc(f.get("selector"))), None, f))
    return out


def dispatcher(m, st):
    """Heuristic, used only when no message library is configured: a case structure inside a loop whose selector
    comes from a subVI output or a shift register (a received message / the next state), with >= 2 named (string /
    enum) cases besides Default and error cases."""
    if st.get("_name") != "Case Structure":
        return False
    src, _ = m.source(st.get("selectin"))
    if src is None or src.tag not in ("Call", "Left", "Right"):
        return False
    sels = [unesc(f.get("selector")) for f in st.findall("CaseFrame")]
    named = [x for x in sels if x and "Default" not in x
             and x not in ("True", "False", "No Error", "Error ..", "Error")
             and not re.fullmatch(r"-?\d+(\.\.-?\d*)?(,\s*-?\d+)*", x)]
    if len(named) < 2:
        return False
    p = m.parent.get(st)
    while p is not None:
        if p.tag == "Structure" and p.get("_name") in ("While Loop", "For Loop", "Timed Loop"):
            return True
        p = m.parent.get(p)
    return False


TYPE_PORTS = {"type", "target class", "data type", "default value"}


def consts(m, frame):
    """Numeric and enum constants of a frame by label (business numbers: timeouts, limits, enum choices).
    Constants that only define a data type (Variant To Data 'type' etc.) are skipped."""
    out = []
    for e in frame.iter("Constant"):
        t, v, n = e.get("type", ""), e.get("value", ""), e.get("_name") or "const"
        sinks = [port for _, w in WIRE.findall(e.get("outputs") or "") for _, port in m.sinks(w)]
        if sinks and all((port or "").lower() in TYPE_PORTS for port in sinks):
            continue
        names = enum_names(t)
        if names:
            if v.isdigit() and int(v) < len(names):
                out.append(f"{n}={names[int(v)]}")
        elif re.fullmatch(r"(u?int\d+|double|single|float\d*)", t):
            out.append(f"{n}={v}")
    return list(dict.fromkeys(out))


def frames(path):
    """Per-handler cards: [{kind, name, parent, card, consts, xml_chars}]."""
    m = Model(path)
    return [{"kind": k, "name": n, "parent": p, "card": card_of(m, f), "consts": consts(m, f),
             "xml_chars": len(ET.tostring(f, encoding="unicode"))} for k, n, p, f in handler_frames(m)]


def frame_xml(path, name):
    """XML of the handler frames whose name contains name."""
    m = Model(path)
    return [(k, n, ET.tostring(f, encoding="unicode")) for k, n, p, f in handler_frames(m) if name.lower() in n.lower()]


def fmt_frame(fr, extra=None, hide=None):
    """One line per handler frame; hide(callee) -> True drops plumbing calls (default: queue/error utilities)."""
    c = fr["card"]
    parts = []
    calls = [x for x in c["calls"] if not NOISE_CALL.search(x) and not (hide and hide(x))]
    if calls:
        parts.append("calls: " + ", ".join(f"{x}{(extra or {}).get(x, '')}" for x in calls))
    if c["sends"]:
        parts.append("sends: " + ", ".join(c["sends"]))
    for ro, syms in c["plc"].items():
        parts.append(f"plc {ro}: {', '.join(syms)}")
    for k in ("gen", "reg"):
        if c["events"][k]:
            parts.append(f"event {k}: {', '.join(c['events'][k])}")
    if fr["consts"]:
        parts.append("consts: " + ", ".join(fr["consts"][:8]) + (f" +{len(fr['consts']) - 8}" if len(fr["consts"]) > 8 else ""))
    if c["dynamic"]:
        parts.append("dynamic: " + ", ".join(c["dynamic"]))
    head = f"[{fr['name']}]" + (f" (in {fr['parent']})" if fr["parent"] else "") + (" event" if fr["kind"] == "event" else "")
    return f"{head} " + (" | ".join(parts) or "-")


def fmt(c, rel=""):
    lines = [f"## {c['vi']}  {rel}".rstrip()]
    if c["in"] or c["out"]:
        lines.append(f"  in: {', '.join(c['in'])} | out: {', '.join(c['out'])}")
    if c["calls"]:
        lines.append(f"  calls: {', '.join(c['calls'])}")
    for ro, syms in c["plc"].items():
        lines.append(f"  plc {ro}: {', '.join(syms)}")
    if c["sends"]:
        lines.append(f"  sends: {', '.join(c['sends'])}")
    if c["handles"]:
        lines.append(f"  handles: {', '.join(c['handles'])}")
    for k, v in c["events"].items():
        if v:
            lines.append(f"  events {k}: {', '.join(v)}")
    if c["results"]:
        lines.append(f"  status consts: {', '.join(c['results'])}")
    if c["dynamic"]:
        lines.append(f"  dynamic: {', '.join(c['dynamic'])}")
    return "\n".join(lines)
