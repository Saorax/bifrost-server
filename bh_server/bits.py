from __future__ import annotations

import struct


def _width(v: int) -> int:
    n = max(1, v.bit_length())
    return n + (n & 1)


class BitWriter:

    def __init__(self) -> None:
        self.out = bytearray()
        self.cur, self.cur_n = 0, 0

    def bits_(self, n: int, v: int) -> "BitWriter":
        if n <= 0:
            return self
        total = self.cur_n + n
        val = (self.cur << n) | (v & ((1 << n) - 1))
        keep = total & 7
        if total >= 8:
            self.out += (val >> keep).to_bytes(total >> 3, "big")
            val &= (1 << keep) - 1
        self.cur, self.cur_n = val, keep
        return self

    def bool(self, b: bool) -> "BitWriter":
        return self.bits_(1, 1 if b else 0)

    def uint(self, v: int) -> "BitWriter":
        v &= 0xFFFFFFFF
        n = _width(v)
        return self.bits_(4, n // 2 - 1).bits_(n, v)

    def small(self, v: int) -> "BitWriter":
        n = _width(v)
        if n > 16:
            raise ValueError("small() holds 16 bits at most")
        return self.bits_(3, n // 2 - 1).bits_(n, v)

    def u64(self, v: int) -> "BitWriter":
        n = _width(v)
        return self.bits_(5, n // 2 - 1).bits_(n, v)

    def byte(self, v: int) -> "BitWriter":
        return self.bits_(8, v)

    def f32(self, v: float) -> "BitWriter":
        return self.raw(struct.pack(">f", v))

    def string(self, s: str) -> "BitWriter":
        data = s.encode("utf-8")[:65535]
        self.bits_(16, len(data))
        return self.raw(data)

    def raw(self, data: bytes) -> "BitWriter":
        if not data:
            return self
        if self.cur_n == 0:
            self.out += data
            return self
        return self.bits_(8 * len(data), int.from_bytes(data, "big"))

    @property
    def bits(self) -> list[int]:
        n = len(self.out) * 8 + self.cur_n
        v = (int.from_bytes(self.out, "big") << self.cur_n) | self.cur
        return [(v >> (n - 1 - i)) & 1 for i in range(n)]

    def bytes(self) -> bytes:
        if self.cur_n:
            return bytes(self.out) + bytes([(self.cur << (8 - self.cur_n)) & 0xFF])
        return bytes(self.out)


MAX_OVERREAD = 512


class BitReader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def bits_(self, n: int) -> int:
        if n <= 0:
            return 0
        if self.pos + n > len(self.data) * 8 + MAX_OVERREAD:
            raise IndexError(f"read of {n} bits past the end ({len(self.data) * 8 - self.pos} left)")
        start, end = self.pos >> 3, (self.pos + n + 7) >> 3
        chunk = self.data[start:end]
        if len(chunk) < end - start:
            chunk = bytes(chunk) + bytes(end - start - len(chunk))
        val = int.from_bytes(chunk, "big")
        val >>= (end << 3) - (self.pos + n)
        self.pos += n
        return val & ((1 << n) - 1)

    def bool(self) -> bool:
        return self.bits_(1) != 0

    def uint(self) -> int:
        return self.bits_((self.bits_(4) + 1) * 2)

    def small(self) -> int:
        return self.bits_((self.bits_(3) + 1) * 2)

    def u64(self) -> int:
        return self.bits_((self.bits_(5) + 1) * 2)

    def byte(self) -> int:
        return self.bits_(8)

    def raw(self, n: int) -> bytes:
        return self.bits_(8 * n).to_bytes(n, "big") if n else b""

    def f32(self) -> float:
        return struct.unpack(">f", self.raw(4))[0]

    def string(self) -> str:
        n = self.bits_(16)
        return self.raw(n).decode("utf-8", "replace")

    def left(self) -> int:
        return len(self.data) * 8 - self.pos
