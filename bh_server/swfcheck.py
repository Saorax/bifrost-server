from __future__ import annotations

import lzma
import struct
import zlib

DO_ACTION, DO_INIT_ACTION, DO_ABC_OLD, DO_ABC, END = 12, 59, 72, 82, 0
GLOBALS = {"Object", "Array", "EventDispatcher", "DisplayObject", "InteractiveObject", "DisplayObjectContainer",
           "Sprite", "MovieClip", "Shape", "Bitmap", "BitmapData", "SimpleButton", "Font"}
CALLS = {"addFrameScript", "registerFont"}
MAX_BODY = 1 << 20


class Bad(Exception):
    pass


def body_of(data: bytes) -> bytes:
    if len(data) < 8:
        raise Bad("not a SWF")
    sig = data[:3]
    if sig == b"FWS":
        body = data[8:]
    elif sig == b"CWS":
        body = zlib.decompressobj().decompress(data[8:], 256 << 20)
    elif sig == b"ZWS":
        if len(data) < 17:
            raise Bad("not a SWF")
        props = data[12:17]
        dict_size = struct.unpack_from("<I", props, 1)[0]
        lc, lp, pb = props[0] % 9, (props[0] // 9) % 5, props[0] // 45
        filt = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}]
        try:
            body = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filt).decompress(data[17:], 256 << 20)
        except lzma.LZMAError as e:
            raise Bad(f"bad LZMA ({e})") from None
    else:
        raise Bad("not a SWF")
    if not body:
        raise Bad("empty SWF")
    nbits = body[0] >> 3
    return body[(5 + 4 * nbits + 7) // 8 + 4:]


def tags(stream: bytes):
    at = 0
    while at + 2 <= len(stream):
        head = struct.unpack_from("<H", stream, at)[0]
        at += 2
        code, length = head >> 6, head & 63
        if length == 63:
            if at + 4 > len(stream):
                raise Bad("cut short")
            length = struct.unpack_from("<I", stream, at)[0]
            at += 4
        if at + length > len(stream):
            raise Bad("cut short")
        yield code, stream[at:at + length]
        at += length
        if code == END:
            return


class Reader:
    def __init__(self, b: bytes) -> None:
        self.b, self.at = b, 0

    def u8(self) -> int:
        if self.at >= len(self.b):
            raise Bad("ABC cut short")
        self.at += 1
        return self.b[self.at - 1]

    def u16(self) -> int:
        return self.u8() | self.u8() << 8

    def u30(self) -> int:
        v = 0
        for i in range(5):
            byte = self.u8()
            v |= (byte & 0x7F) << (7 * i)
            if not byte & 0x80:
                return v
        return v

    def skip(self, n: int) -> bytes:
        if self.at + n > len(self.b):
            raise Bad("ABC cut short")
        self.at += n
        return self.b[self.at - n:self.at]


class Abc:

    def __init__(self, data: bytes) -> None:
        r = self.r = Reader(data)
        r.u16(), r.u16()
        for _ in range(max(0, r.u30() - 1)):
            r.u30()
        for _ in range(max(0, r.u30() - 1)):
            r.u30()
        r.skip(8 * max(0, r.u30() - 1))
        self.strings = [""]
        for _ in range(max(0, r.u30() - 1)):
            self.strings.append(r.skip(r.u30()).decode("utf-8", "replace"))
        for _ in range(max(0, r.u30() - 1)):
            r.u8(), r.u30()
        for _ in range(max(0, r.u30() - 1)):
            for _ in range(r.u30()):
                r.u30()
        self.names: list[str | None] = [None]
        for _ in range(max(0, r.u30() - 1)):
            kind = r.u8()
            if kind in (0x07, 0x0D):
                r.u30()
                self.names.append(self.string(r.u30()))
            elif kind in (0x0F, 0x10):
                self.names.append(self.string(r.u30()))
            elif kind in (0x11, 0x12):
                self.names.append(None)
            elif kind in (0x09, 0x0E):
                self.names.append(self.string(r.u30()))
                r.u30()
            elif kind in (0x1B, 0x1C):
                r.u30()
                self.names.append(None)
            elif kind == 0x1D:
                r.u30()
                for _ in range(r.u30()):
                    r.u30()
                self.names.append(None)
            else:
                raise Bad(f"multiname kind {kind}")
        for _ in range(r.u30()):
            n = r.u30()
            r.u30()
            for _ in range(n):
                r.u30()
            r.u30()
            flags = r.u8()
            if flags & 0x08:
                for _ in range(r.u30()):
                    r.u30(), r.u8()
            if flags & 0x80:
                for _ in range(n):
                    r.u30()
        for _ in range(r.u30()):
            r.u30()
            for _ in range(2 * r.u30()):
                r.u30()
        self.own: set[str] = set()
        self.classes: set[str] = set()
        count = r.u30()
        for _ in range(count):
            name = self.name(r.u30())
            self.classes.add(name or "")
            r.u30()
            if r.u8() & 0x08:
                r.u30()
            for _ in range(r.u30()):
                r.u30()
            r.u30()
            self.traits()
        for _ in range(count):
            r.u30()
            self.traits()
        for _ in range(r.u30()):
            r.u30()
            self.traits()
        self.bodies: list[bytes] = []
        for _ in range(r.u30()):
            r.u30(), r.u30(), r.u30(), r.u30(), r.u30()
            self.bodies.append(r.skip(r.u30()))
            if r.u30():
                raise Bad("exception handlers")
            self.traits()
        self.own |= self.classes

    def string(self, i: int) -> str:
        if i >= len(self.strings):
            raise Bad("string index")
        return self.strings[i]

    def name(self, i: int) -> str | None:
        if i >= len(self.names):
            raise Bad("multiname index")
        return self.names[i]

    def traits(self) -> None:
        r = self.r
        for _ in range(r.u30()):
            name = self.name(r.u30())
            if name:
                self.own.add(name)
            kind = r.u8()
            k = kind & 0x0F
            if k in (0, 6):
                r.u30(), r.u30()
                if r.u30():
                    r.u8()
            elif k in (1, 2, 3, 4, 5):
                r.u30(), r.u30()
            else:
                raise Bad(f"trait kind {k}")
            if kind & 0x40:
                for _ in range(r.u30()):
                    r.u30()


ALLOWED = {
    0xD0: "", 0xD1: "", 0x30: "", 0x1D: "", 0x47: "", 0x29: "", 0x02: "", 0x09: "", 0x2A: "",
    0x20: "", 0x21: "", 0x26: "", 0x27: "", 0x28: "",
    0x24: "b", 0x65: "b",
    0x25: "u", 0x2D: "u", 0x2E: "u", 0x2F: "u", 0x2C: "u", 0x56: "u", 0x55: "u", 0x58: "u", 0x49: "u",
    0xF0: "u", 0xF1: "u", 0xF2: "u",
    0x60: "n", 0x5D: "n", 0x5E: "n", 0x66: "n", 0x68: "n", 0x61: "n",
    0x4F: "nn",
}


def check_body(abc: Abc, code: bytes) -> None:
    r = Reader(code)
    while r.at < len(code):
        op = r.u8()
        kind = ALLOWED.get(op)
        if kind is None:
            raise Bad(f"instruction 0x{op:02x}")
        if kind == "b":
            r.u8()
        elif kind == "u":
            v = r.u30()
            if op == 0x49 and v != 0:
                raise Bad("constructsuper with arguments")
        elif kind in ("n", "nn"):
            name = abc.name(r.u30())
            if kind == "nn":
                r.u30()
            if name is None:
                raise Bad("a name looked up at run time")
            if op == 0x60 and name not in GLOBALS and name not in abc.classes:
                raise Bad(f"reads global {name}")
            if op in (0x5D, 0x5E) and name not in CALLS and name not in abc.own:
                raise Bad(f"looks up {name}")
            if op in (0x66, 0x68, 0x61) and name not in abc.own:
                raise Bad(f"property {name}")
            if op == 0x4F and name not in CALLS:
                raise Bad(f"calls {name}")


def problem(data: bytes) -> str | None:
    try:
        for code, tag in tags(body_of(data)):
            if code in (DO_ACTION, DO_INIT_ACTION):
                return "AVM1 code"
            if code == DO_ABC_OLD:
                return "an old DoABC tag"
            if code == DO_ABC:
                if len(tag) < 5:
                    return "a short DoABC tag"
                end = tag.index(b"\0", 4)
                abc = Abc(tag[end + 1:])
                for body in abc.bodies:
                    if len(body) > MAX_BODY:
                        return "a long method"
                    check_body(abc, body)
    except Bad as e:
        return f"code beyond graphics ({e})"
    except (ValueError, IndexError, struct.error, zlib.error) as e:
        return f"unreadable ({type(e).__name__})"
    return None
