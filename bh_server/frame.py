from __future__ import annotations

from dataclasses import dataclass, field
import struct

HAS_EXTRA = 0x8000
V1011, V1100, V1101 = "10.11", "11.00", "11.01"


def client_version(a: int) -> str:
    if a >= 1101000000:
        return V1101
    return V1100 if a >= 1100000000 else V1011


def carries_960(t: int) -> bool:
    if t in (16, 2463, 2467):
        return False
    return 2400 < t < 3000 or 1700 < t < 2000 or 2000 < t < 2300


def carries_924(t: int) -> bool:
    if t in (16, 2463, 178):
        return False
    return t < 500 or t in (10400, 10405, 10202)


@dataclass
class Packet:
    type: int
    payload: bytes = b""
    field_960: int = 0
    field_924: int = 0
    later: dict = field(default_factory=dict)

    def since(self, version: str, p: "Packet") -> "Packet":
        self.later[version] = p
        return self

    def for_client(self, version: str) -> "Packet":
        best = self
        for v in sorted(self.later):
            if v <= version:
                best = self.later[v]
        return best

    def extra(self) -> int | None:
        if carries_960(self.type):
            return self.field_960
        if carries_924(self.type):
            return self.field_924
        return None


def encode(p: Packet) -> bytes:
    extra = p.extra()
    if len(p.payload) > 0xFFFF:
        raise ValueError("payload longer than 65535 bytes")
    head = struct.pack(">H", p.type | (HAS_EXTRA if extra is not None else 0))
    if extra is not None:
        head += struct.pack(">I", extra & 0xFFFFFFFF)
    return head + struct.pack(">H", len(p.payload)) + p.payload


class Decoder:

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[Packet]:
        self.buf += data
        out = []
        while True:
            p, used = self._one()
            if p is None:
                return out
            del self.buf[:used]
            out.append(p)

    def _one(self) -> tuple[Packet | None, int]:
        b = self.buf
        if len(b) < 2:
            return None, 0
        raw = struct.unpack_from(">H", b)[0]
        t, at = raw & ~HAS_EXTRA, 2
        extra = None
        if raw & HAS_EXTRA:
            if len(b) < at + 4:
                return None, 0
            extra = struct.unpack_from(">I", b, at)[0]
            at += 4
        if len(b) < at + 2:
            return None, 0
        n = struct.unpack_from(">H", b, at)[0]
        at += 2
        if len(b) < at + n:
            return None, 0
        p = Packet(t, bytes(b[at:at + n]))
        if extra is not None:
            if carries_960(t):
                p.field_960 = extra
            else:
                p.field_924 = extra
        return p, at + n
