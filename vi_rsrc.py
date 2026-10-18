"""Minimal reader of the LabVIEW RSRC container (.vi/.ctl): block ident -> list of section payloads.

Layout as in pylabview LVrsrcontainer/LVblock (big-endian):
  RSRCHeader (32 B) at 0 and again at rsrc_info_offset: id[6] fmtver(u16) type[4] creator[4]
      rsrc_info_offset rsrc_info_size rsrc_data_offset rsrc_data_size
  BlockInfoListHeader (20 B) after the 2nd header: int1 int2 int3 blockinfo_offset blockinfo_size
  at info+blockinfo_offset: blockinfo_count(u32, n-1), then BlockHeader[n]: ident[4] count(u32, n-1) offset(u32)
  BlockSectionStart (20 B) at info+blockinfo_offset+offset: idx name_offset int3 data_offset int5
  section data at rsrc_data_offset+data_offset: size(u32) + raw bytes
"""
import struct
import zlib


def blocks(path):
    with open(path, "rb") as f:
        return parse(f.read())


def parse(d):
    if d[:4] != b"RSRC":
        raise ValueError("not an RSRC file")
    info_off = struct.unpack_from(">I", d, 16)[0]
    data_off = struct.unpack_from(">I", d, 24 + info_off)[0]
    bi_off = info_off + struct.unpack_from(">I", d, info_off + 32 + 12)[0]
    count = struct.unpack_from(">I", d, bi_off)[0] + 1
    out = {}
    for i in range(count):
        ident, n, off = struct.unpack_from(">4sII", d, bi_off + 4 + 12 * i)
        secs = []
        for s in range(n + 1):
            _, _, _, doff, _ = struct.unpack_from(">iIIII", d, bi_off + off + 20 * s)
            size = struct.unpack_from(">I", d, data_off + doff)[0]
            secs.append(d[data_off + doff + 4: data_off + doff + 4 + size])
        out[ident.decode("latin1")] = secs
    return out


def unpack(raw):
    """Heap blocks are zlib streams, usually after a 4-byte uncompressed size."""
    for skip in (4, 0):
        if len(raw) > skip + 2 and raw[skip] == 0x78:
            try:
                return zlib.decompress(raw[skip:])
            except zlib.error:
                pass
    return raw
