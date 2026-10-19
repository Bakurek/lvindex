"""Unit tests without a project: connector-pane fingerprints on synthetic VI blocks, cards of a hand-written export.
Run: python -m unittest discover -s tests"""
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
from lvindex import cards as K  # noqa: E402
from lvindex import skeleton as S  # noqa: E402

I32, STR, CLUSTER, FUNC = 0x03, 0x30, 0x50, 0xF0


def td(kind, data=b"", label=None):
    """One type descriptor: u16 size, flags (0x40 = has label), type, data, Pascal label (padded to even)."""
    tail = b""
    if label is not None:
        tail = bytes([len(label)]) + label.encode()
    body = bytes([0x40 if label is not None else 0, kind]) + data + tail
    if len(body) % 2:
        body += b"\0"
    return struct.pack(">H", len(body) + 2) + body


def vi_blocks(types, slots, flags=None, pattern=0x78):
    """Blocks of a VI whose connector pane has 12 slots; slots = {slot: flat index of its type} (0 = void),
    types = flat type list (index 0 must be void); flags = {slot: terminal flags}."""
    idx = [slots.get(s, 0) for s in range(12)]
    tail = struct.pack(">HH", 0, pattern) + b"".join(struct.pack(">I", (flags or {}).get(s, 0)) for s in range(12))
    func = td(FUNC, struct.pack(">H", 12) + b"".join(struct.pack(">H", i) for i in idx) + tail + b"\0\0")
    flat = list(types) + [func]
    vctp = struct.pack(">I", len(flat)) + b"".join(flat) + struct.pack(">HH", 1, len(flat) - 1)
    return {"VCTP": [vctp], "CONP": [struct.pack(">H", 1)]}


VOID = td(0x00)
OUT = 0x100


class Pane(unittest.TestCase):
    def fp(self, *a, **k):
        return S.pane_fp(vi_blocks(*a, **k))

    def setUp(self):
        self.base = self.fp([VOID, td(I32, label="count"), td(STR, label="name")], {2: 1, 11: 2}, {2: OUT})

    def test_order_of_type_list_does_not_matter(self):
        self.assertEqual(self.base, self.fp([VOID, td(STR, label="name"), td(I32, label="count")], {2: 2, 11: 1},
                                            {2: OUT}))

    def test_label_type_slot_direction_change(self):
        for case in (([VOID, td(I32, label="total"), td(STR, label="name")], {2: 1, 11: 2}, {2: OUT}),
                     ([VOID, td(STR, label="count"), td(STR, label="name")], {2: 1, 11: 2}, {2: OUT}),
                     ([VOID, td(I32, label="count"), td(STR, label="name")], {3: 1, 11: 2}, {3: OUT}),
                     ([VOID, td(I32, label="count"), td(STR, label="name")], {2: 1, 11: 2}, {})):
            self.assertNotEqual(self.base, self.fp(*case), case)

    def test_terminal_added(self):
        self.assertNotEqual(self.base, self.fp([VOID, td(I32, label="count"), td(STR, label="name"),
                                                td(I32, label="extra")], {2: 1, 11: 2, 8: 3}, {2: OUT}))

    def test_required_flag_and_dispatch_flag(self):
        types = [VOID, td(I32, label="count"), td(STR, label="name")]
        self.assertEqual(self.base, self.fp(types, {2: 1, 11: 2}, {2: OUT, 11: 0x200}))      # required: ignored
        self.assertNotEqual(self.base, self.fp(types, {2: 1, 11: 2}, {2: OUT, 11: 0x8000}))  # dynamic dispatch

    def test_cluster_element_change(self):
        def cl(elem):
            return [VOID, elem, td(CLUSTER, struct.pack(">HH", 1, 1), label="data")]
        a = self.fp(cl(td(I32, label="x")), {10: 2})
        self.assertNotEqual(a, self.fp(cl(td(STR, label="x")), {10: 2}))
        self.assertEqual(a, self.fp(cl(td(I32, label="x")), {10: 2}))

    def test_no_pane(self):
        self.assertIsNone(S.pane_fp({"VCTP": vi_blocks([VOID], {})["VCTP"]}))
        b = vi_blocks([VOID], {})
        b["CONP"] = [struct.pack(">H", 0)]
        self.assertIsNone(S.pane_fp(b))


class Cards(unittest.TestCase):
    def test_dispatcher_card_and_frames(self):
        p = os.path.join(HERE, "data", "dispatcher.xml")
        c = K.make_card(p)
        self.assertEqual(c["in"], ["state in:string", "error in:error"])
        self.assertIn("Demo:Start.vi", c["calls"])
        self.assertEqual(c["handles"], ["Start", "Stop", "Default"])
        fr = {f["name"]: f for f in K.frames(p)}
        self.assertEqual(fr["Start"]["consts"], ["timeout=100"])
        self.assertEqual(sorted(fr["Start"]["card"]["calls"]), ["Demo:Log.vi", "Demo:Start.vi"])
        self.assertIn('selector="&quot;Stop&quot;"', K.frame_xml(p, "stop")[0][2])


if __name__ == "__main__":
    unittest.main()
