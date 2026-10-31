from __future__ import annotations

import socket
import struct
import sys

_TCP_INFO = getattr(socket, "TCP_INFO", 11)
_LINUX_RTT_OFFSET = 68
_SIO_TCP_INFO = 0xD8000027
_WIN_RTT_OFFSET = 20


def _linux(sock: socket.socket) -> float | None:
    data = sock.getsockopt(socket.IPPROTO_TCP, _TCP_INFO, 104)
    if len(data) < _LINUX_RTT_OFFSET + 4:
        return None
    return struct.unpack_from("I", data, _LINUX_RTT_OFFSET)[0] / 1000.0


_ws2 = None


def _windows(sock: socket.socket) -> float | None:
    global _ws2
    import ctypes
    from ctypes import wintypes
    if _ws2 is None:
        _ws2 = ctypes.WinDLL("ws2_32")
        _ws2.WSAIoctl.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
    version = wintypes.DWORD(0)
    out = ctypes.create_string_buffer(128)
    got = wintypes.DWORD(0)
    if _ws2.WSAIoctl(sock.fileno(), _SIO_TCP_INFO, ctypes.byref(version), 4, out, 128, ctypes.byref(got), None, None):
        return None
    if got.value < _WIN_RTT_OFFSET + 4:
        return None
    return struct.unpack_from("<I", out.raw, _WIN_RTT_OFFSET)[0] / 1000.0


def rtt_ms(sock: socket.socket) -> float | None:
    try:
        if sys.platform.startswith("linux"):
            return _linux(sock)
        if sys.platform == "win32":
            return _windows(sock)
    except (OSError, ValueError, AttributeError, ImportError):
        pass
    return None
