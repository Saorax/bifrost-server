from __future__ import annotations

import hashlib
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = Path(__file__).resolve().parent.parent
CA_DAYS, SERVER_DAYS, RENEW_DAYS = 3650, 730, 30
GIT_OPENSSL = (r"C:\Program Files\Git\mingw64\bin\openssl.exe", r"C:\Program Files\Git\usr\bin\openssl.exe")


def folder(data: Path | None = None) -> Path:
    return Path(data or HERE / "data") / "tls"


def openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    return next((p for p in GIT_OPENSSL if Path(p).exists()), None)


def names(cfg: dict) -> list[str]:
    out = ["localhost", "127.0.0.1"]
    host = urllib.parse.urlsplit(str(cfg.get("public_url") or "")).hostname
    for n in [host, *(cfg.get("tls_names") or [])]:
        if n and str(n) not in out:
            out.append(str(n))
    return out


def _san(entries: list[str]) -> str:
    parts = []
    for n in entries:
        try:
            ipaddress.ip_address(n)
            parts.append(f"IP:{n}")
        except ValueError:
            parts.append(f"DNS:{n}")
    return ",".join(parts)


def _constraints(entries: list[str]) -> str:
    parts = []
    for n in entries:
        try:
            ip = ipaddress.ip_address(n)
            mask = "255.255.255.255" if ip.version == 4 else "FFFF:" * 7 + "FFFF"
            parts.append(f"permitted;IP:{n}/{mask}")
        except ValueError:
            parts.append(f"permitted;DNS:{n}")
    return "critical," + ",".join(parts)


def ca_permits(tool: str, d: Path, wanted: list[str]) -> bool:
    text = _cert_text(tool, d / "ca.pem")
    if "X509v3 Name Constraints" not in text:
        return False
    block = text.split("X509v3 Name Constraints")[1].split("X509v3", 1)[0]
    for n in wanted:
        try:
            ipaddress.ip_address(n)
            if f"IP:{n}/" not in block:
                return False
        except ValueError:
            if f"DNS:{n}" not in block:
                return False
    return True


def _run(tool: str, *args: str) -> None:
    done = subprocess.run([tool, *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"openssl {args[0]} failed: {done.stderr.strip()[:300]}")


def _cert_text(tool: str, pem: Path) -> str:
    return subprocess.run([tool, "x509", "-in", str(pem), "-noout", "-text", "-enddate"], capture_output=True,
                          text=True).stdout


def needs_server_cert(tool: str, d: Path, wanted: list[str], now: float | None = None) -> bool:
    pem = d / "server.pem"
    if not pem.exists() or not (d / "server.key").exists():
        return True
    text = _cert_text(tool, pem)
    have = {s.strip() for s in text.split("X509v3 Subject Alternative Name:")[-1].split("\n")[1].split(",")} \
        if "X509v3 Subject Alternative Name:" in text else set()
    want = {p.replace("IP:", "IP Address:") for p in _san(wanted).split(",")}
    if not want <= have:
        return True
    end = next((line.split("=", 1)[1] for line in text.splitlines() if line.startswith("notAfter=")), "")
    try:
        ends = time.mktime(time.strptime(end.replace(" GMT", ""), "%b %d %H:%M:%S %Y"))
    except ValueError:
        return True
    return ends - (now or time.time()) < RENEW_DAYS * 86400


def make(cfg: dict, data: Path | None = None, log=print) -> Path | None:
    tool = openssl()
    if tool is None:
        log("tls: no openssl command found (Git for Windows has one): the web side stays plain http")
        return None
    d = folder(data)
    d.mkdir(parents=True, exist_ok=True)
    wanted = names(cfg)
    if not (d / "ca.pem").exists() or not (d / "ca.key").exists() or not ca_permits(tool, d, wanted):
        _run(tool, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
             "-keyout", str(d / "ca.key"), "-out", str(d / "ca.pem"), "-days", str(CA_DAYS),
             "-subj", f"/CN={cfg.get('server_name') or 'Bifrost server'} authority",
             "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-addext", "nameConstraints=" + _constraints(wanted))
        (d / "server.pem").unlink(missing_ok=True)
        log(f"tls: made this server's certificate authority for {', '.join(wanted)} ({d / 'ca.pem'})")
    twin = d / "ca-engine.pem"
    if not twin.exists() or twin.stat().st_mtime < (d / "ca.pem").stat().st_mtime:
        make_engine_twin(tool, d)
    if needs_server_cert(tool, d, wanted):
        with tempfile.TemporaryDirectory() as tmp:
            ext = Path(tmp) / "ext.cnf"
            ext.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                           f"extendedKeyUsage=serverAuth\nsubjectAltName={_san(wanted)}\n", encoding="ascii")
            csr = Path(tmp) / "server.csr"
            _run(tool, "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                 "-keyout", str(d / "server.key"), "-out", str(csr), "-subj", f"/CN={wanted[-1]}")
            _run(tool, "x509", "-req", "-in", str(csr), "-CA", str(d / "ca.pem"), "-CAkey", str(d / "ca.key"),
                 "-CAcreateserial", "-out", str(d / "server.pem"), "-days", str(SERVER_DAYS), "-extfile", str(ext))
        log(f"tls: server certificate for {', '.join(wanted)}")
    return d


def make_engine_twin(tool: str, d: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        ext = Path(tmp) / "ext.cnf"
        ext.write_text("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
                       "subjectKeyIdentifier=hash\n", encoding="ascii")
        _run(tool, "x509", "-in", str(d / "ca.pem"), "-signkey", str(d / "ca.key"), "-clrext", "-extfile", str(ext),
             "-days", str(CA_DAYS), "-out", str(d / "ca-engine.pem"))
    return d / "ca-engine.pem"


def server_context(d: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(d / "server.pem"), str(d / "server.key"))
    return ctx


TLS_RECORD = 0x16


class GameChannel:

    def __init__(self, sock: socket.socket, ctx: ssl.SSLContext) -> None:
        self.sock = sock
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = ctx.wrap_bio(self.incoming, self.outgoing, server_side=True)
        self.lock = threading.Lock()
        while True:
            with self.lock:
                try:
                    self.tls.do_handshake()
                    self._flush()
                    break
                except ssl.SSLWantReadError:
                    self._flush()
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("the connection closed during the TLS handshake")
            with self.lock:
                self.incoming.write(data)

    def _flush(self) -> None:
        data = self.outgoing.read()
        if data:
            self.sock.sendall(data)

    def recv(self, n: int) -> bytes:
        while True:
            with self.lock:
                try:
                    data = self.tls.read(n)
                    self._flush()
                    return data
                except ssl.SSLWantReadError:
                    self._flush()
                except (ssl.SSLZeroReturnError, ssl.SSLEOFError):
                    return b""
            data = self.sock.recv(65536)
            if not data:
                return b""
            with self.lock:
                self.incoming.write(data)

    def sendall(self, data: bytes) -> None:
        with self.lock:
            self.tls.write(data)
            self._flush()

    def shutdown(self, how: int) -> None:
        self.sock.shutdown(how)

    def settimeout(self, value) -> None:
        self.sock.settimeout(value)

    def close(self) -> None:
        self.sock.close()


def accept_game(sock: socket.socket, ctx: ssl.SSLContext | None):
    if ctx is None:
        return sock
    first = sock.recv(1, socket.MSG_PEEK)
    if first and first[0] == TLS_RECORD:
        return GameChannel(sock, ctx)
    return sock


def fingerprint(d: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert((d / "ca.pem").read_text(encoding="ascii"))
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def enabled(cfg: dict, data: Path | None = None) -> bool:
    if cfg.get("tls") is False:
        return False
    return cfg.get("tls") is True or (folder(data) / "ca.pem").exists()


def main(argv: list[str]) -> int:
    from .__main__ import load_config
    cfg = load_config()
    cmd = argv[0] if argv else "make"
    if cmd == "make":
        d = make(cfg)
        if d is not None:
            print(f"authority fingerprint {fingerprint(d)}")
        return 0 if d is not None else 1
    d = folder()
    if not (d / "ca.pem").exists():
        print("no certificate yet: python -m bh_server.tls make")
        return 1
    if cmd == "show":
        print(f"{d / 'ca.pem'}\nSHA-256 {fingerprint(d)}")
        return 0
    if cmd == "trust":
        if os.name != "nt":
            print("trust: Windows only (add ca.pem to your system's trusted roots by hand)")
            return 1
        return subprocess.run(["certutil", "-user", "-addstore", "Root", str(d / "ca.pem")]).returncode
    print("python -m bh_server.tls make | show | trust")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
