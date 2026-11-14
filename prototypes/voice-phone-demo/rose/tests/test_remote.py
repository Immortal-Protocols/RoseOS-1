"""The remote link end to end, without models: pairing, signed requests, the TLS listener's surface.

    .venv/bin/python -m rose.tests.test_remote

Starts the dashboard and the remote listener exactly as ``python -m rose.app`` does
(``--ui-only``: the scripted FakeConductor, no MLX), on free ports with ROSE_CONFIG_DIR in a
temporary directory, and plays the phone with ``cryptography`` (P-256 key, SHA256withECDSA
signatures over the canonical string in REMOTE.md) and the ``websockets`` client.
Never touches port 8091/8443 or ~/.config/rose-local.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import http.client
import json
import logging
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect as ws_connect

FAILURES: list[str] = []
logging.getLogger("websockets").setLevel(logging.CRITICAL)  # a server close mid-handshake is expected here
HOSTS = ["phone-test.tail0000.ts.net", "100.100.1.2"]


def check(cond: bool, what: str) -> None:
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        FAILURES.append(what)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Clock:
    """Wall time plus an offset the test can move."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


class Rose:
    """The dashboard + remote listener in a background thread, built by rose.app's own functions."""

    def __init__(self, dash_port: int, remote_port: int, clock: Clock):
        from rose import app
        from rose.remote import Remote

        self.args = app.parse_args(["--ui-only", "--port", str(dash_port), "--remote-port", str(remote_port)])
        self.server = app.Server(self.args)
        self.remote = Remote(port=remote_port, clock=clock, name="Test Mac", hosts=lambda: list(HOSTS))
        self.servers = app.build_servers(self.server, self.args, self.remote)
        self.thread = threading.Thread(target=lambda: asyncio.run(app.serve_all(self.servers)), daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while not all(s.started for s, _ in self.servers):
            if time.time() > deadline or not self.thread.is_alive():
                raise RuntimeError("servers did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        for s, _ in self.servers:
            s.should_exit = True
        self.thread.join(15)
        self.remote.flush()


class Phone:
    """A paired phone: a P-256 key and the signing rule from the spec."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.spki = self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo)
        self.id = hashlib.sha256(self.spki).hexdigest()

    def pub_b64(self) -> str:
        return base64.b64encode(self.spki).decode()

    def headers(self, path: str, pin: str, *, method: str = "GET", t_ms: int | None = None,
                nonce: str | None = None, signer: "Phone | None" = None, device: str | None = None) -> dict:
        t = str(int(time.time() * 1000) if t_ms is None else t_ms)
        nonce = nonce or base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        msg = ("rose-auth-v1\n" + method + "\n" + path + "\n" + t + "\n" + nonce + "\n" + pin).encode()
        sig = (signer or self).key.sign(msg, ec.ECDSA(hashes.SHA256()))
        return {"X-Rose-Device": device or self.id, "X-Rose-Time": t, "X-Rose-Nonce": nonce,
                "X-Rose-Signature": base64.b64encode(sig).decode()}


def tls() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the test checks the pin itself, like the phone
    return ctx


def https(port: int, method: str, path: str, body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, dict | str]:
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=tls(), timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw.decode("utf-8", "replace")
    finally:
        conn.close()


def peer_pin(port: int) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with tls().wrap_socket(raw) as s:
            return hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()


def local(port: int, method: str, path: str, body: dict | None = None,
          headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def pair(port: int, code: str, phone: Phone, name: str = "Test Pixel", model: str = "Pixel 9") -> tuple[int, dict]:
    body = json.dumps({"code": code, "publicKey": phone.pub_b64(), "name": name, "model": model}).encode()
    return https(port, "POST", "/pair", body, {"Content-Type": "application/json"})


def code_of(uri: str) -> str:
    return re.search(r"[?&]code=([A-Z2-7]{26})(?:&|$)", uri).group(1)


def ws_status(port: int, path: str, headers: dict) -> int | str:
    """HTTP status of a refused websocket handshake, or 'open' (closing it again)."""

    return ws_refusal(port, path, headers)[0]


def ws_refusal(port: int, path: str, headers: dict) -> tuple[int | str, dict]:
    """(status, response headers) of a remote websocket handshake; ('open', {}) if accepted."""

    try:
        with ws_connect(f"wss://127.0.0.1:{port}{path}", ssl=tls(), additional_headers=headers,
                        open_timeout=5, server_hostname="localhost"):
            return "open", {}
    except InvalidStatus as exc:
        return exc.response.status_code, {k.lower(): v for k, v in exc.response.headers.raw_items()}


def dash_ws(port: int, path: str, headers: dict | None = None, host: str | None = None) -> int | str:
    """A dashboard websocket (to 127.0.0.1, sending ``Host: host`` if given): the HTTP status if
    refused, else the close code it gets within 2 s (or 'open' if it stays open)."""

    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        with ws_connect(f"ws://{host or '127.0.0.1:' + str(port)}{path}", sock=sock,
                        additional_headers=headers or {}, open_timeout=5) as ws:
            deadline = time.time() + 2
            try:
                while time.time() < deadline:
                    ws.recv(timeout=max(0.01, deadline - time.time()))
            except ConnectionClosed as exc:
                return exc.rcvd.code if exc.rcvd else "closed"
            except TimeoutError:
                pass
            return "open"
    except InvalidStatus as exc:
        return exc.response.status_code
    finally:
        sock.close()


# ---------------------------------------------------------------------------- tests
def test_unit() -> None:
    from rose.remote import NonceCache, canonical_message, pair_uri, route_of

    t = [1000.0]
    cache = NonceCache(clock=lambda: t[0], ttl=240, limit=3)
    check(cache.add("a") and cache.add("b") and cache.add("c"), "nonce cache accepts fresh nonces")
    check(not cache.add("a"), "nonce cache refuses a repeat")
    check(not cache.add("d"), "nonce cache full of live nonces fails closed")
    t[0] += 241
    check(cache.add("d") and cache.add("a"), "nonces expire after 240 s")
    check(canonical_message("GET", "/ws/audio", "1", "n", "p") == b"rose-auth-v1\nGET\n/ws/audio\n1\nn\np",
          "canonical string layout")
    uri = pair_uri("Nathan’s MacBook Pro (2)", ["a.ts.net", "100.64.0.1"], 8443, "f" * 64, "A" * 26)
    check(uri == "roseagent://pair?v=1&name=Nathan%E2%80%99s%20MacBook%20Pro%20%282%29"
          "&hosts=a.ts.net,100.64.0.1&port=8443&pin=" + "f" * 64 + "&code=" + "A" * 26, "pair URI format")
    check([route_of(h) for h in ("x.tail1.ts.net", "100.127.0.1", "192.168.1.2", "100.128.0.1")]
          == ["Tailscale", "Tailscale", "Unsupported", "Unsupported"], "route labels")


def test_live(cfg: Path) -> None:
    clock = Clock()
    dash, rport = free_port(), free_port()
    rose = Rose(dash, rport, clock)
    try:
        run_checks(rose, dash, rport, clock, cfg)
    finally:
        rose.stop()

    # Restart: the pin and the paired devices survive; invites do not.
    pin_before = rose.remote.identity.pin
    survivor = getattr(rose, "survivor", None)
    if survivor is None:
        check(False, "the first run finished (restart checks skipped)")
        return
    rose2 = Rose(dash, rport, Clock())
    try:
        check(rose2.remote.identity.pin == pin_before, "pin stable across restarts")
        check(peer_pin(rport) == pin_before, "restarted listener presents the same certificate")
        check(local(dash, "GET", "/api/pair/status")[1]["pending"] is False, "invites do not survive a restart")
        st, body = https(rport, "GET", "/hello", headers=survivor.headers("/hello", pin_before))
        check(st == 200, f"paired device still authenticates after a restart ({st})")
        # A second request inside the minute is only marked dirty; the clean exit must write it.
        rose2.remote.clock = lambda: time.time() + 30
        st, _ = https(rport, "GET", "/hello", headers=survivor.headers("/hello", pin_before,
                                                                          t_ms=int((time.time() + 30) * 1000)))
        check(st == 200, "second /hello 30 s later")
        expect = (time.time() + 30) * 1000
    finally:
        rose2.stop()
    on_disk = {d["id"]: d for d in json.loads((cfg / "devices.json").read_text())["devices"]}
    check(abs((on_disk.get(survivor.id, {}).get("lastSeen") or 0) - expect) < 5000,
          "lastSeen of the last request is on disk after a clean exit")


def run_checks(rose: Rose, dash: int, rport: int, clock: Clock, cfg: Path) -> None:
    # ------------------------------------------------------------ invite + identity
    st, inv = local(dash, "POST", "/api/pair/new", {})
    check(st == 200, f"POST /api/pair/new ({st})")
    pin = inv["pin"]
    check(re.fullmatch(r"[0-9a-f]{64}", pin) is not None, "pin is 64 lowercase hex")
    check(peer_pin(rport) == pin, "TLS certificate SHA-256 equals the pin")
    check(inv["hosts"] == HOSTS and inv["serverName"] == "Test Mac", "hosts and server name in the invite")
    check(re.fullmatch(r"roseagent://pair\?v=1&name=Test%20Mac&hosts=phone-test\.tail0000\.ts\.net,100\.100\.1\.2"
                       rf"&port={rport}&pin={pin}&code=[A-Z2-7]{{26}}", inv["uri"]) is not None,
          "QR URI matches the spec")
    png = base64.b64decode(inv["png"].split(",", 1)[1])
    check(inv["png"].startswith("data:image/png;base64,") and png.startswith(b"\x89PNG"), "invite carries a PNG QR")
    check(abs(inv["expiresAt"] - (time.time() + 600) * 1000) < 5000, "invite expires in 10 minutes")
    st, _ = local(dash, "POST", "/api/pair/new", {}, {"Host": "evil.example:8091"})
    check(st == 403, "pairing API refuses a foreign Host header (DNS rebinding)")
    st, _ = local(dash, "POST", "/api/pair/new", {}, {"Origin": "http://evil.example"})
    check(st == 403, "pairing API refuses a foreign Origin")

    # ------------------------------------------------------------ dashboard: loopback Host/Origin only
    check(dash_ws(dash, "/ws/phone") == 4009, "dashboard /ws/phone refuses implicit USB before pairing too")
    for path in ("/ws/phone", "/ws/events", "/ws/audio"):
        check(dash_ws(dash, path, {"Origin": "https://evil.example"}) == 403,
              f"dashboard {path} refuses a foreign Origin (cross-site websocket)")
        check(dash_ws(dash, path, host=f"evil.example:{dash}") == 403,
              f"dashboard {path} refuses a foreign Host (DNS rebinding)")
    check(dash_ws(dash, "/ws/events", {"Origin": f"http://127.0.0.1:{dash}"}) == "open",
          "dashboard /ws/events accepts its own Origin")
    for look in ("", "?look=screen", "?look=apps"):
        st, _ = local(dash, "GET", "/api/realphone" + look, headers={"Host": f"evil.example:{dash}"})
        check(st == 403, f"/api/realphone{look} refuses a foreign Host")
    st, _ = local(dash, "GET", "/api/realphone", headers={"Origin": "http://evil.example"})
    check(st == 403, "/api/realphone refuses a foreign Origin")
    st, body = local(dash, "GET", "/api/realphone", headers={"Host": f"localhost:{dash}"})
    check(st == 200 and body == {"real": False}, f"/api/realphone from the Mac itself -> 200 ({st})")
    st, _ = local(dash, "GET", "/api/state", headers={"Host": f"evil.example:{dash}"})
    check(st == 403, "the rest of the dashboard API refuses a foreign Host too")

    a, b = Phone(), Phone()

    # ------------------------------------------------------------ wrong code, malformed bodies
    code = code_of(inv["uri"])
    wrong = "A" * 26 if code != "A" * 26 else "B" * 26
    st, body = pair(rport, wrong, a)
    check(st == 403, f"wrong code -> 403 ({st} {body})")
    st, _ = https(rport, "POST", "/pair", b"{not json", {"Content-Type": "application/json"})
    check(st == 400, "malformed JSON -> 400")
    rsa_spki = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    st, _ = https(rport, "POST", "/pair", json.dumps({"code": code, "publicKey": base64.b64encode(rsa_spki).decode(),
                                                      "name": "x", "model": "y"}).encode())
    check(st == 400, "non-P-256 key -> 400")
    st, _ = https(rport, "POST", "/pair", b"x" * 5000, {"Content-Type": "application/json"})
    check(st == 400, "oversized body -> 400")
    check(local(dash, "GET", "/api/pair/status")[1]["pending"] is True, "invite still pending after 4 failures")
    st, _ = pair(rport, wrong, a)  # fifth failure
    st, body = pair(rport, code, a)
    check(st == 403, f"5 failed attempts destroy the invite: right code then -> 403 ({body})")
    stat_ = local(dash, "GET", "/api/pair/status")[1]
    check(stat_["pending"] is False and stat_["state"] == "locked", "status shows the lockout")

    # ------------------------------------------------------------ expiry
    code = code_of(local(dash, "POST", "/api/pair/new", {})[1]["uri"])
    clock.offset = 601
    st, body = pair(rport, code, a)
    check(st == 403 and "expired" in body.get("error", ""), f"expired code -> 403 ({body})")
    clock.offset = 0

    # ------------------------------------------------------------ success, reuse
    code = code_of(local(dash, "POST", "/api/pair/new", {})[1]["uri"])
    st, body = pair(rport, code.lower(), a)
    check(st == 200 and body == {"deviceId": a.id, "serverName": "Test Mac", "serverId": pin[:16]},
          f"pair success -> 200 with deviceId = sha256(SPKI) ({body})")
    stat_ = local(dash, "GET", "/api/pair/status")[1]
    check(stat_["pairedDeviceId"] == a.id and stat_["pairedDeviceName"] == "Test Pixel", "status reports the pairing")
    st, body = pair(rport, code, b)
    check(st == 403, f"a used code cannot be reused ({st})")
    check(dash_ws(dash, "/ws/phone") == 4009, "dashboard (USB) /ws/phone is shut while a phone is paired")

    # ------------------------------------------------------------ storage
    check(stat.S_IMODE(os.stat(cfg).st_mode) == 0o700, "config dir is 0700")
    for name in ("devices.json", "tls-key.pem", "tls-cert.pem"):
        mode = stat.S_IMODE(os.stat(cfg / name).st_mode)
        check(mode == 0o600, f"{name} is 0600 ({oct(mode)})")
    stored = json.loads((cfg / "devices.json").read_text())["devices"]
    check(len(stored) == 1 and stored[0]["id"] == a.id and stored[0]["publicKey"] == a.pub_b64()
          and stored[0]["revokedAt"] is None, "devices.json holds the paired key")
    check(not list(cfg.glob(".*.tmp")), "no temporary files left behind")

    # ------------------------------------------------------------ signed requests
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", pin))
    check(st == 200 and body == {"serverName": "Test Mac", "serverId": pin[:16], "version": 1, "phone": "fake"},
          f"signed GET /hello -> 200 ({body})")
    st, body = https(rport, "GET", "/hello")
    check(st == 401, "unsigned /hello -> 401")
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", pin, signer=b))
    check(st == 401 and body.get("error") == "bad signature", f"wrong key -> 401 ({body})")
    st, body = https(rport, "GET", "/hello", headers=a.headers("/ws/audio", pin))
    check(st == 401, "signature over another path -> 401")
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", "0" * 64))
    check(st == 401, "signature bound to another server's pin -> 401")
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", pin, method="POST"))
    check(st == 401, "signature over another method -> 401")
    st, body = https(rport, "GET", "/hello?x=1", headers=a.headers("/hello", pin))
    check(st == 401, "query string is part of the signed path")
    st, body = https(rport, "GET", "/hello?x=1", headers=a.headers("/hello?x=1", pin))
    check(st == 200, "signed path with its query -> 200")
    old = int((time.time() - 121) * 1000)
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", pin, t_ms=old))
    check(st == 401 and body.get("error") == "stale time", f"time 121 s old -> 401 ({body})")
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", pin, t_ms=old + 242_000))
    check(st == 401 and body.get("error") == "stale time", "time 121 s ahead -> 401")
    st, _ = https(rport, "GET", "/hello", headers=a.headers("/hello", pin, t_ms=int((time.time() - 100) * 1000)))
    check(st == 200, "time 100 s old is within the window")
    replay = a.headers("/hello", pin)
    first, _ = https(rport, "GET", "/hello", headers=replay)
    st, body = https(rport, "GET", "/hello", headers=replay)
    check(first == 200 and st == 401 and body.get("error") == "replayed nonce", f"nonce replay -> 401 ({body})")
    st, body = https(rport, "GET", "/hello", headers=b.headers("/hello", pin))
    check(st == 401 and body.get("error") == "unknown device", f"unknown device -> 401 ({body})")
    bad = a.headers("/hello", pin)
    bad["X-Rose-Nonce"] = "short"
    check(https(rport, "GET", "/hello", headers=bad)[0] == 401, "malformed nonce -> 401")

    # ------------------------------------------------------------ websockets
    # 403 only for "pair again" (unknown or revoked device); 409 + X-Rose-Error for refusals that
    # clear up by themselves, so the phone retries instead of showing "Not paired".
    for path in ("/ws/events", "/ws/audio", "/ws/phone"):
        check(ws_status(rport, path, b.headers(path, pin)) == 403, f"{path}: unknown device refused before accept (403)")
        check(ws_status(rport, path, a.headers(path, pin, signer=b)) == 409, f"{path}: bad signature -> 409")
        check(ws_status(rport, path, {}) == 409, f"{path}: no auth headers -> 409")
        check(ws_status(rport, path, a.headers(path, pin)) == "open", f"{path}: signed handshake accepted")
    stale = a.headers("/ws/events", pin, t_ms=int((time.time() - 200) * 1000))
    st, hdrs = ws_refusal(rport, "/ws/events", stale)
    server_t = int(hdrs.get("x-rose-server-time", "0"))
    check(st == 409 and hdrs.get("x-rose-error") == "stale-time" and abs(server_t - time.time() * 1000) < 5000,
          f"websocket with stale time -> 409, X-Rose-Error stale-time and the Mac's time ({st} {hdrs.get('x-rose-error')})")
    h = a.headers("/ws/events", pin)
    ws_status(rport, "/ws/events", h)
    st, hdrs = ws_refusal(rport, "/ws/events", h)
    check(st == 409 and hdrs.get("x-rose-error") == "replayed-nonce", "websocket nonce replay -> 409 replayed-nonce")
    st, hdrs = ws_refusal(rport, "/ws/events", b.headers("/ws/events", pin))
    check(st == 403 and "x-rose-error" not in hdrs, "unknown device -> plain 403")
    with ws_connect(f"wss://127.0.0.1:{rport}/ws/events", ssl=tls(), additional_headers=a.headers("/ws/events", pin),
                    open_timeout=5) as ws:
        hello = json.loads(ws.recv(timeout=5))
        check(hello.get("kind") == "hello" and "state" in hello, "remote /ws/events streams the dashboard's events")

    # ------------------------------------------------------------ lastSeen while connected
    with ws_connect(f"wss://127.0.0.1:{rport}/ws/events", ssl=tls(), additional_headers=a.headers("/ws/events", pin),
                    open_timeout=5) as ws:
        ws.recv(timeout=5)
        clock.offset = 3600  # an hour on the same open websocket
        rose.remote.seen()   # what keep_seen runs every minute
        on_disk = {d["id"]: d for d in json.loads((cfg / "devices.json").read_text())["devices"]}
        seen_ms = on_disk[a.id]["lastSeen"]
        check(abs(seen_ms - (time.time() + 3600) * 1000) < 5000,
              "a device with an open websocket keeps lastSeen current on disk")
    clock.offset = 0

    # ------------------------------------------------------------ not served remotely
    for method, path in (("GET", "/"), ("GET", "/app.js"), ("GET", "/app.css"), ("GET", "/audio.js"),
                         ("GET", "/api/state"), ("GET", "/api/phone"), ("GET", "/phone.png"),
                         ("POST", "/api/say"), ("POST", "/api/pair/new"), ("GET", "/api/pair/devices"),
                         ("GET", "/api/realphone"), ("GET", "/docs"), ("GET", "/openapi.json")):
        st, _ = https(rport, method, path, b"{}" if method == "POST" else None,
                      {**a.headers(path, pin, method=method), "Content-Type": "application/json"})
        check(st == 404, f"remote port does not serve {method} {path} ({st})")
    st, _ = https(rport, "GET", "/pair")
    check(st == 405, "GET /pair is not allowed")

    # ------------------------------------------------------------ revoke
    with ws_connect(f"wss://127.0.0.1:{rport}/ws/events", ssl=tls(), additional_headers=a.headers("/ws/events", pin),
                    open_timeout=5) as ws:
        ws.recv(timeout=5)
        st, body = local(dash, "POST", "/api/pair/revoke", {"deviceId": a.id})
        check(st == 200 and body == {"ok": True}, "POST /api/pair/revoke")
        code = None
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                ws.recv(timeout=deadline - time.time())
        except ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd else None
        except TimeoutError:
            pass
        check(code == 4401, f"revoking closes the device's open websocket ({code})")
    st, body = https(rport, "GET", "/hello", headers=a.headers("/hello", pin))
    check(st == 401 and body.get("error") == "device revoked", f"revoked device -> 401 ({body})")
    check(ws_status(rport, "/ws/audio", a.headers("/ws/audio", pin)) == 403, "revoked device websocket -> 403")
    devs = local(dash, "GET", "/api/pair/devices")[1]["devices"]
    check(len(devs) == 1 and devs[0]["revoked"] is True and "publicKey" not in devs[0], "device list shows revoked")
    check(local(dash, "POST", "/api/pair/revoke", {"deviceId": "0" * 64})[0] == 404, "revoking an unknown id -> 404")
    check(dash_ws(dash, "/ws/phone") == 4009, "revocation does not reopen implicit USB phone control")

    # ------------------------------------------------------------ re-pair the same key
    code = code_of(local(dash, "POST", "/api/pair/new", {})[1]["uri"])
    st, body = pair(rport, code, a, name="Renamed Pixel")
    check(st == 200 and body["deviceId"] == a.id, "re-pairing the same key with a new code -> 200")
    devs = local(dash, "GET", "/api/pair/devices")[1]["devices"]
    check(len(devs) == 1 and devs[0]["revoked"] is False and devs[0]["name"] == "Renamed Pixel",
          "re-pairing clears revokedAt and updates the name")
    check(https(rport, "GET", "/hello", headers=a.headers("/hello", pin))[0] == 200, "re-paired device authenticates")
    rose.survivor = a
    check_connection_limits(rport, a, pin)


def check_connection_limits(rport: int, a: Phone, pin: str) -> None:
    """Idle or trickling TLS connections are dropped and capped per IP, so they cannot fill the
    listener and lock the paired phone out with 503s."""

    from rose import remote as remote_mod

    remote_mod.HEADER_TIMEOUT_S, remote_mod.MAX_PER_PEER = 1.5, 4
    idle = []
    try:
        for _ in range(4):
            idle.append(tls().wrap_socket(socket.create_connection(("127.0.0.1", rport), timeout=5)))
        idle[0].sendall(b"GET /hello HTTP/1.1\r\nHo")  # a trickle that never finishes
        extra = tls().wrap_socket(socket.create_connection(("127.0.0.1", rport), timeout=5))
        try:
            extra.settimeout(3)
            check(extra.recv(1) == b"", "a connection over the per-IP cap is closed at once")
        except (OSError, ssl.SSLError):
            check(True, "a connection over the per-IP cap is closed at once")
        finally:
            extra.close()
        time.sleep(2.5)
        closed = 0
        for s in idle:
            try:
                s.settimeout(1)
                closed += s.recv(1) == b""
            except (OSError, ssl.SSLError) as exc:
                closed += not isinstance(exc, (TimeoutError, socket.timeout))
        check(closed == 4, f"connections without request headers are closed after the deadline ({closed}/4)")
        st, _ = https(rport, "GET", "/hello", headers=a.headers("/hello", pin))
        check(st == 200, f"the paired phone gets through afterwards ({st})")
    finally:
        for s in idle:
            with contextlib.suppress(OSError):
                s.close()
        remote_mod.HEADER_TIMEOUT_S, remote_mod.MAX_PER_PEER = 10, 16


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rose-remote-test-") as tmp:
        cfg = Path(tmp) / "config"
        os.environ["ROSE_CONFIG_DIR"] = str(cfg)
        test_unit()
        test_live(cfg)
    if FAILURES:
        print(f"\nFAIL: {len(FAILURES)} check(s)")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("\nOK: all remote-link checks passed")


if __name__ == "__main__":
    main()
