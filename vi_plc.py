"""Optional [plc] layer: read-only TwinCAT side of an HMI <-> PLC interface (parsed on every call, nothing stored).
Paths and names come from the [plc] section of vi-tools.toml (repo, ads_config, cmd_gvl, interface); without it
load() returns empty data.

  gvl     {GVL_X.var: {type, init, doc, file}} from every .TcGVL of the TwinCAT project (doc = trailing comment +
          the // comment block above the declaration, e.g. "// HMI: ..." interface notes)
  refs    {GVL_X.var: {POU: "w"|"r"}} from every .TcPOU: "w" when the variable (or one of its members / elements)
          is assigned with := or receives an FB output with =>; POUs under a Tests folder or named *_Test are kept
          apart in tests {GVL_X.var: {POU: "w"|"r"}}
  ads     {GVL_X.var: [config file]} symbols listed in the HMI ADS config (config\\ADS\\*.xml) - the HMI reads or
          writes these by configuration, so no VI holds them as a literal
Handshake pairs are a NAME CONVENTION (X / XACK / XRejected, bRequestX / bXACK), not verified behaviour.
"""
import os
import re

import vi_config

_P = vi_config.get().get("plc") or {}
PLC = _P.get("repo") or ""
ADS = _P.get("ads_config") or ""
CMD_GVL = _P.get("cmd_gvl", "")
INTERFACE = tuple(_P.get("interface", []))
CONFIGURED = bool(_P)
DECL = re.compile(r"<Declaration><!\[CDATA\[(.*?)\]\]>", re.S)
VAR = re.compile(r"^\s*(\w+)\s*(?:AT\s+%\S+\s*)?:\s*([^:=;]+?)\s*(?::=\s*([^;]*?))?\s*;\s*(?://\s*(.*))?$")
REF = re.compile(r"(=>\s*)?\b(GVL_\w+)\.(\w+)((?:\s*\[[^\]]*\]|\.\w+)*)(\s*:=)?")


def files(ext):
    if not PLC:
        return
    for d, dirs, fs in os.walk(PLC):
        dirs[:] = [x for x in dirs if not x.startswith((".", "_"))]
        for fn in fs:
            if fn.endswith(ext):
                yield os.path.join(d, fn)


def read(p):
    with open(p, "rb") as f:
        b = f.read()
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:                  # older TwinCAT files are saved in the Windows code page
        return b.decode("cp1250", errors="replace")


def load():
    gvl, refs, tests, ads = {}, {}, {}, {}
    for p in files(".TcGVL"):
        g = os.path.basename(p)[:-6]
        m = DECL.search(read(p))
        lines = (m.group(1) if m else "").split("\n")
        for i, line in enumerate(lines):
            v = VAR.match(line)
            if not v:
                continue
            j = i
            while j > 0 and lines[j - 1].strip().startswith("//"):
                j -= 1
            above = " ".join(x.strip().lstrip("/").strip() for x in lines[j:i])
            gvl[f"{g}.{v.group(1)}"] = {"type": v.group(2).strip(), "init": (v.group(3) or "").strip(),
                                         "doc": " | ".join(x for x in (above, (v.group(4) or "").strip()) if x),
                                         "file": os.path.relpath(p, PLC)}
    for p in files(".TcPOU"):
        pou = os.path.basename(p)[:-6]
        test = re.search(r"(^|[\\/])Tests?([\\/]|$)", os.path.dirname(os.path.relpath(p, PLC))) or \
            re.search(r"_Tests?$", pou, re.I)
        for out, g, v, _, w in REF.findall(read(p)):
            r = (tests if test else refs).setdefault(f"{g}.{v}", {})
            r[pou] = "w" if out or w or r.get(pou) == "w" else "r"
    if ADS and os.path.isdir(ADS):
        for fn in sorted(os.listdir(ADS)):
            if fn.lower().endswith(".xml"):
                for s in re.findall(r'<Parameter Name="SymbolName">([^<]+)<', read(os.path.join(ADS, fn))):
                    ads.setdefault(s.strip(), []).append(fn)
    return {"gvl": gvl, "refs": refs, "tests": tests, "ads": {k: sorted(set(v)) for k, v in ads.items()}}


def declared(gvl, sym):
    """Declaration of sym, or of the variable it is a member of (GVL_X.st.field -> GVL_X.st): (key, entry)."""
    parts = sym.split(".")
    for n in range(len(parts), 1, -1):
        k = ".".join(parts[:n])
        if k in gvl:
            return k, gvl[k]
    return None, None


def handshakes(gvl):
    """[(cmd, ack, rejected)] in the [plc] cmd_gvl by name convention."""
    if not CMD_GVL:
        return []
    cmd = {k.split(".", 1)[1] for k in gvl if k.startswith(CMD_GVL + ".")}
    out = []
    for n in sorted(cmd):
        if not n.endswith("ACK"):
            continue
        base = n[:-3]
        c = base if base in cmd else ("bRequest" + base[1:] if "bRequest" + base[1:] in cmd else None)
        if c:
            rej = base + "Rejected" if base + "Rejected" in cmd else None
            out.append((c, n, rej))
    return out
