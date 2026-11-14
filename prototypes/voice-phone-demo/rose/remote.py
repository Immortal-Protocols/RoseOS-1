"""Rose remote link v1: Rose Agent reaches this Mac over Tailscale.

The dashboard server stays on 127.0.0.1 and unauthenticated. A second
listener (TLS, 0.0.0.0, port 8443 by default) serves only what a paired phone
needs: ``POST /pair``, ``GET /hello`` and the ``/ws/audio``, ``/ws/events`` and
``/ws/phone`` websockets, handled by the same Server methods the dashboard
uses. The wire contract (QR URI, headers, signed string) is in REMOTE.md.

Trust model, in short:
- The Mac has a self-signed ECDSA P-256 certificate; its SHA-256 (the pin)
  travels in the QR code, so the phone trusts exactly this certificate and no
  CA or hostname is involved.
- The QR code also carries a one-time code (130 bits, 10 minutes, single use,
  5 wrong tries destroy it). ``POST /pair`` with that code enrolls the phone's
  hardware-backed P-256 public key as a device.
- Every later request is signed by that key over method, path, time, a fresh
  nonce and the pin. The server checks the device (known, not revoked), the
  time (within 2 minutes), the nonce (unseen for 4 minutes) and the signature
  before it accepts a websocket or answers.

State lives in ``~/.config/rose-local/`` (``ROSE_CONFIG_DIR`` overrides; the
directory is 0700, files 0600 and written atomically): the TLS key and
certificate, and ``devices.json``. Invites live in memory only. Codes, nonces
and signatures are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import contextlib
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from . import qr

PROTOCOL_VERSION = 1
DEFAULT_REMOTE_PORT = 8443
CONFIG_ENV = "ROSE_CONFIG_DIR"
INVITE_TTL_S = 600
MAX_PAIR_ATTEMPTS = 5
CLOCK_SKEW_S = 120
NONCE_TTL_S = 240
NONCE_CACHE_MAX = 50_000
TOUCH_PERSIST_S = 60
PAIR_BODY_MAX = 4096
NAME_MAX = 64
MAX_HOSTS = 8   # the phone accepts 1..8 hosts
AUTH_PREFIX = "rose-auth-v1"
REVOKED_CLOSE = 4401  # websocket close code when a device is removed while connected
RETRY_STATUS = 409    # websocket upgrade refused for a reason that can clear up (clock, nonce, garbled request)
SEEN_EVERY_S = 60     # how often devices with open websockets get lastSeen refreshed and saved
HEADER_TIMEOUT_S = 10  # a remote connection must finish its request headers this fast
MAX_PER_PEER = 16     # open remote connections allowed from one IP address
REMOTE_CONCURRENCY = 256  # all remote connections (uvicorn answers 503 above this)
B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
TAILSCALE_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{22}")
_TIME = re.compile(r"[0-9]{1,16}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def now_ms(clock: Callable[[], float] = time.time) -> int:
    return int(clock() * 1000)


def _log(msg: str) -> None:
    print(f"[remote] {msg}", flush=True)


# ------------------------------------------------------------------ storage
def config_dir() -> Path:
    """The config directory (created 0700; an existing one is tightened to 0700)."""

    raw = os.environ.get(CONFIG_ENV)
    path = Path(raw).expanduser() if raw else Path.home() / ".config" / "rose-local"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` as a 0600 file via a temporary file and rename."""

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):  # make the rename durable
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def computer_name() -> str:
    """The Mac's user-visible name (``scutil --get ComputerName``), else the hostname."""

    with contextlib.suppress(OSError, subprocess.SubprocessError):
        out = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=3)
        name = out.stdout.strip()
        if out.returncode == 0 and name:
            return name
    return socket.gethostname()


# ------------------------------------------------------------------ identity
@dataclass
class Identity:
    """The listener's TLS key and self-signed certificate, and the pin derived from it."""

    key_path: Path
    cert_path: Path
    pin: str        # lowercase hex SHA-256 of the certificate DER
    server_id: str  # first 16 hex chars of the pin

    @classmethod
    def load_or_create(cls, directory: Path, name: str) -> "Identity":
        key_path, cert_path = directory / "tls-key.pem", directory / "tls-cert.pem"
        if key_path.exists() or cert_path.exists():
            try:
                key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
                cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"cannot read the TLS identity in {directory} ({type(exc).__name__}); "
                                   "paired phones trust this exact certificate, so it is not replaced "
                                   "silently. Move both tls-*.pem files away to start over (and re-pair).") from exc
            if cert.public_key().public_numbers() != key.public_key().public_numbers():
                raise RuntimeError(f"tls-key.pem and tls-cert.pem in {directory} do not match")
            for p in (key_path, cert_path):
                os.chmod(p, 0o600)
        else:
            key, cert = _new_certificate(name)
            atomic_write(key_path, key.private_bytes(serialization.Encoding.PEM,
                                                     serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
            atomic_write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
            _log(f"created a TLS identity in {directory}")
        pin = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
        return cls(key_path, cert_path, pin, pin[:16])


def _new_certificate(name: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Rose on {name}"[:64])])
    start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=3652))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                    x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


# ------------------------------------------------------------------- devices
@dataclass
class Device:
    id: str             # lowercase hex SHA-256 of the SPKI DER
    publicKey: str      # base64 SPKI DER
    name: str
    model: str
    pairedAt: int       # unix ms
    lastSeen: int | None = None
    lastAddress: str | None = None
    revokedAt: int | None = None

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "model": self.model, "pairedAt": self.pairedAt,
                "lastSeen": self.lastSeen, "lastAddress": self.lastAddress, "revoked": self.revokedAt is not None}


class DeviceStore:
    """``devices.json``: paired phones and their public keys."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, ec.EllipticCurvePublicKey] = {}
        self._saved_at = 0.0
        self._dirty = False
        self.devices: dict[str, Device] = {}
        if path.exists():
            os.chmod(path, 0o600)
            try:
                raw = json.loads(path.read_text())
                for d in raw.get("devices", []):
                    dev = Device(**{k: d.get(k) for k in Device.__dataclass_fields__})
                    self.devices[dev.id] = dev
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError(f"cannot read {path} ({type(exc).__name__}); fix or move it away "
                                   "(moving it unpairs every phone)") from exc

    def get(self, device_id: str) -> Device | None:
        with self._lock:
            return self.devices.get(device_id)

    def key(self, dev: Device) -> ec.EllipticCurvePublicKey:
        k = self._keys.get(dev.id)
        if k is None:
            k = serialization.load_der_public_key(base64.b64decode(dev.publicKey))
            assert isinstance(k, ec.EllipticCurvePublicKey)
            self._keys[dev.id] = k
        return k

    def enroll(self, spki: bytes, name: str, model: str, address: str | None) -> Device:
        """Add a device, or refresh one that pairs again with the same key (un-revoking it)."""

        device_id = hashlib.sha256(spki).hexdigest()
        t = now_ms(self.clock)
        with self._lock:
            dev = self.devices.get(device_id)
            if dev is None:
                dev = Device(device_id, base64.b64encode(spki).decode(), name, model, t)
                self.devices[device_id] = dev
            else:
                dev.name, dev.model, dev.pairedAt, dev.revokedAt = name, model, t, None
            dev.lastSeen, dev.lastAddress = t, address
            self._save_locked()
        return dev

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            dev = self.devices.get(device_id)
            if dev is None:
                return False
            if dev.revokedAt is None:
                dev.revokedAt = now_ms(self.clock)
                self._save_locked()
            return True

    def touch(self, dev: Device, address: str | None) -> None:
        """Record a successful request; written to disk at most once a minute."""

        with self._lock:
            dev.lastSeen, dev.lastAddress = now_ms(self.clock), address
            self._dirty = True
            if self.clock() - self._saved_at >= TOUCH_PERSIST_S:
                self._save_locked()

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._save_locked()

    def has_active(self) -> bool:
        """Is any device paired and not revoked?"""

        with self._lock:
            return any(d.revokedAt is None for d in self.devices.values())

    def listing(self) -> list[dict]:
        with self._lock:
            return [d.public() for d in sorted(self.devices.values(), key=lambda d: d.pairedAt)]

    def _save_locked(self) -> None:
        data = {"devices": [asdict(d) for d in self.devices.values()]}
        atomic_write(self.path, (json.dumps(data, indent=2) + "\n").encode())
        self._saved_at = self.clock()
        self._dirty = False


# ------------------------------------------------------------------- invites
@dataclass
class Invite:
    code_hash: bytes
    created: float
    expires: float
    attempts: int = 0
    device_id: str | None = None  # set once used


class Pairing:
    """The one pending invite (a new one replaces it). Only sha256(code) is kept."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.invite: Invite | None = None
        self.last_result = ""   # "", "paired", "locked" (for the status view)
        self._lock = threading.Lock()

    def new(self) -> tuple[str, Invite]:
        code = "".join(secrets.choice(B32_ALPHABET) for _ in range(26))
        t = self.clock()
        with self._lock:
            self.invite = Invite(hashlib.sha256(code.encode()).digest(), t, t + INVITE_TTL_S)
            self.last_result = ""
        return code, self.invite

    def check(self, code: Any) -> str | None:
        """None when ``code`` matches the pending, unexpired, unused invite; else the reason."""

        with self._lock:
            inv = self.invite
            if inv is None:
                return "no pairing code is pending"
            if inv.device_id is not None:
                return "that code was already used"
            if self.clock() >= inv.expires:
                return "that code expired"
            if not isinstance(code, str) or not hmac.compare_digest(
                    hashlib.sha256(code.strip().upper().encode()).digest(), inv.code_hash):
                return "wrong code"
            return None

    def fail(self) -> None:
        """Count a failed attempt; the fifth destroys the invite."""

        with self._lock:
            inv = self.invite
            if inv is None or inv.device_id is not None:
                return
            inv.attempts += 1
            if inv.attempts >= MAX_PAIR_ATTEMPTS:
                self.invite = None
                self.last_result = "locked"
                _log("pairing code destroyed after too many failed attempts")

    def consume(self, code: str, device_id: str) -> bool:
        """Mark the invite used (atomically re-checked)."""

        with self._lock:
            inv = self.invite
            if inv is None or inv.device_id is not None or self.clock() >= inv.expires:
                return False
            if not hmac.compare_digest(hashlib.sha256(code.strip().upper().encode()).digest(), inv.code_hash):
                return False
            inv.device_id = device_id
            self.last_result = "paired"
            return True

    def status(self) -> dict:
        with self._lock:
            inv = self.invite
            if inv is None:
                return {"pending": False, "expiresAt": None, "pairedDeviceId": None,
                        "state": self.last_result or "none"}
            expired = self.clock() >= inv.expires
            state = "paired" if inv.device_id else ("expired" if expired else "pending")
            return {"pending": state == "pending", "expiresAt": int(inv.expires * 1000),
                    "pairedDeviceId": inv.device_id, "state": state}


# -------------------------------------------------------------------- nonces
class NonceCache:
    """Nonces seen in the last ``NONCE_TTL_S`` seconds; full of live entries means refuse (fail closed)."""

    def __init__(self, clock: Callable[[], float] = time.time, ttl: float = NONCE_TTL_S,
                 limit: int = NONCE_CACHE_MAX):
        self.clock, self.ttl, self.limit = clock, ttl, limit
        self._seen: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, t: float) -> None:
        while self._seen:
            nonce, expires = next(iter(self._seen.items()))
            if expires > t:
                break
            self._seen.popitem(last=False)

    def seen(self, nonce: str) -> bool:
        with self._lock:
            self._prune(self.clock())
            return nonce in self._seen

    def add(self, nonce: str) -> bool:
        """Record ``nonce``; False if it was already there (a replay) or the cache is full."""

        with self._lock:
            t = self.clock()
            self._prune(t)
            if nonce in self._seen or len(self._seen) >= self.limit:
                return False
            self._seen[nonce] = t + self.ttl
            return True


# ----------------------------------------------------------------- addresses
def _tailscale_cli() -> str | None:
    for cand in (shutil.which("tailscale"), TAILSCALE_APP_CLI):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def tailscale_self() -> tuple[str | None, list[str]]:
    """MagicDNS name and IPv4s of this machine from ``tailscale status --json`` (if Tailscale runs)."""

    cli = _tailscale_cli()
    if cli is None:
        return None, []
    try:
        out = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=4)
        status = json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, []
    if status.get("BackendState") != "Running":
        return None, []
    me = status.get("Self") or {}
    name = str(me.get("DNSName") or "").rstrip(".") or None
    ips = []
    for ip in me.get("TailscaleIPs") or []:
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(ip) in _TAILNET:
                ips.append(ip)
    return name, ips


def configured_hosts(hosts: list[str]) -> list[str]:
    """Explicit Tailscale QR addresses, with the same v1 grammar as Rose Agent."""
    out = []
    for value in hosts:
        host = value.strip().lower()
        if not host or len(host) > 253:
            raise ValueError('Pair host must be a DNS name or IPv4 address')
        if re.fullmatch(r'[0-9.]+', host):
            try:
                ipaddress.IPv4Address(host)
            except ValueError:
                raise ValueError('Invalid pairing IPv4 address') from None
        elif not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
            raise ValueError('Pair host must not include a scheme, path, credentials or port')
        if not tailscale_host(host):
            raise ValueError("Pair hosts must be Tailscale IPv4 addresses or full MagicDNS names ending in .ts.net")
        if host not in out:
            out.append(host)
    if len(out) > MAX_HOSTS:
        raise ValueError('Too many pairing hosts')
    return out


def tailscale_host(host: str) -> bool:
    """A Tailscale IPv4 address or a full MagicDNS name; never LAN or loopback."""
    try:
        return ipaddress.IPv4Address(host) in _TAILNET
    except ValueError:
        return (len(host) <= 253 and host.endswith(".ts.net") and
                all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")))


def discover_hosts() -> list[str]:
    """Only addresses from the running Tailscale client; no LAN/interface fallback."""
    name, ips = tailscale_self()
    return list(dict.fromkeys(h for h in ([name] if name else []) + ips
                              if tailscale_host(h)))[:MAX_HOSTS]


def route_of(host: str) -> str:
    """Labels allowed endpoints consistently with the phone."""
    return "Tailscale" if tailscale_host(host) else "Unsupported"


def pair_uri(name: str, hosts: list[str], port: int, pin: str, code: str) -> str:
    """``roseagent://pair?v=1&name=..&hosts=..&port=..&pin=..&code=..`` (commas kept literal)."""

    q = urllib.parse.quote
    return (f"roseagent://pair?v={PROTOCOL_VERSION}&name={q(name, safe='')}"
            f"&hosts={','.join(q(h, safe='') for h in hosts)}&port={port}&pin={pin}&code={code}")


def canonical_message(method: str, path: str, time_s: str, nonce: str, pin: str) -> bytes:
    return "\n".join((AUTH_PREFIX, method, path, time_s, nonce, pin)).encode("utf-8")


def request_path(scope: dict) -> str:
    """The path exactly as the client sent it, with ``?query`` if any."""

    raw = scope.get("raw_path")
    path = raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) else scope.get("path", "")
    qs = scope.get("query_string") or b""
    return path + ("?" + qs.decode("latin-1") if qs else "")


def _clean_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    value = _CONTROL.sub("", value).strip()
    if len(value) > NAME_MAX:
        raise ValueError(f"{field_name} is longer than {NAME_MAX} characters")
    return value


def parse_public_key(value: Any) -> bytes:
    """Base64 SPKI DER of an EC P-256 key -> the DER bytes (ValueError otherwise)."""

    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("publicKey missing")
    try:
        der = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("publicKey is not base64") from exc
    try:
        key = serialization.load_der_public_key(der)
    except ValueError as exc:
        raise ValueError("publicKey is not an SPKI DER public key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("publicKey must be an EC P-256 key")
    return der


# -------------------------------------------------------------------- remote
@dataclass
class AuthResult:
    """A checked request. ``final`` refusals (unknown or revoked device) mean "pair again";
    the others (clock, nonce, a garbled or badly signed request) are worth retrying."""

    device: Device | None
    reason: str = ""
    final: bool = False

    @property
    def code(self) -> str:
        """The reason as a header token, e.g. "stale-time" (sent as ``X-Rose-Error``)."""

        return self.reason.replace(" ", "-")


class Remote:
    """Identity, devices, invites and request authentication for the remote listener."""

    def __init__(self, directory: Path | None = None, port: int = DEFAULT_REMOTE_PORT,
                 clock: Callable[[], float] = time.time, name: str | None = None,
                 hosts: Callable[[], list[str]] = discover_hosts):
        self.dir = directory or config_dir()
        self.port = port
        self._clock = clock
        self.name = name or computer_name()
        self.identity = Identity.load_or_create(self.dir, self.name)
        self.devices = DeviceStore(self.dir / "devices.json", clock=self.now)
        self.pairing = Pairing(clock=self.now)
        self.nonces = NonceCache(clock=self.now)
        self.discover = hosts
        self.listening = False  # set once the TLS listener is bound
        self.sockets: dict[str, set] = collections.defaultdict(set)  # device id -> open websockets

    # The clock is swappable (tests move time forward); everything reads it through now().
    def now(self) -> float:
        return self._clock()

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    @clock.setter
    def clock(self, fn: Callable[[], float]) -> None:
        self._clock = fn

    # ------------------------------------------------------------- pairing
    def new_invite(self) -> dict:
        try:
            hosts = configured_hosts(self.discover())
        except ValueError as exc:
            raise LookupError(str(exc)) from None
        if not hosts:
            raise LookupError("Tailscale is not connected on this Mac. Connect Tailscale, then create a new pairing code.")
        code, inv = self.pairing.new()
        uri = pair_uri(self.name, hosts, self.port, self.identity.pin, code)
        png = "data:image/png;base64," + base64.b64encode(qr.png(uri, scale=8, border=4)).decode()
        _log(f"new pairing code, valid 10 min; hosts {', '.join(hosts)}")
        return {"uri": uri, "png": png, "expiresAt": int(inv.expires * 1000), "hosts": hosts,
                "serverName": self.name, "pin": self.identity.pin, "port": self.port}

    def pair(self, body: bytes, address: str | None) -> tuple[int, dict]:
        """``POST /pair``: (status, JSON payload)."""

        try:
            req = json.loads(body.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("body must be a JSON object")
            spki = parse_public_key(req.get("publicKey"))
            name = _clean_text(req.get("name"), "name") or "Android phone"
            model = _clean_text(req.get("model"), "model")
        except (ValueError, UnicodeDecodeError) as exc:
            self.pairing.fail()
            _log(f"pair from {address}: bad request ({exc})")
            return 400, {"error": f"bad request: {exc}"}
        code = req.get("code")
        why = self.pairing.check(code)
        if why is not None:
            self.pairing.fail()
            _log(f"pair from {address}: refused ({why})")
            return 403, {"error": why}
        device_id = hashlib.sha256(spki).hexdigest()
        if not self.pairing.consume(code, device_id):
            return 403, {"error": "that code was already used"}
        dev = self.devices.enroll(spki, name, model, address)
        _log(f"paired {dev.name!r} ({dev.model or 'unknown model'}) id {dev.id[:12]} from {address}")
        return 200, {"deviceId": dev.id, "serverName": self.name, "serverId": self.identity.server_id}

    # ---------------------------------------------------------------- auth
    def authenticate(self, method: str, path: str, headers: Any, address: str | None) -> AuthResult:
        """Check a signed request; on success the device's lastSeen/lastAddress are updated."""

        device_id = headers.get("x-rose-device") or ""
        time_s = headers.get("x-rose-time") or ""
        nonce = headers.get("x-rose-nonce") or ""
        sig_b64 = headers.get("x-rose-signature") or ""

        def deny(reason: str, final: bool = False) -> AuthResult:
            who = device_id[:12] if _HEX64.fullmatch(device_id) else "?"
            _log(f"rejected {method} {path.split('?', 1)[0]} from {address} (device {who}): {reason}")
            return AuthResult(None, reason, final)

        if not (_HEX64.fullmatch(device_id) and _TIME.fullmatch(time_s) and _NONCE.fullmatch(nonce)
                and 0 < len(sig_b64) <= 200):
            return deny("missing or malformed auth headers")
        dev = self.devices.get(device_id)
        if dev is None:
            return deny("unknown device", final=True)
        if dev.revokedAt is not None:
            return deny("device revoked", final=True)
        if abs(now_ms(self.now) - int(time_s)) > CLOCK_SKEW_S * 1000:
            return deny("stale time")
        if self.nonces.seen(nonce):
            return deny("replayed nonce")
        try:
            sig = base64.b64decode(sig_b64, validate=True)
            self.devices.key(dev).verify(sig, canonical_message(method, path, time_s, nonce, self.identity.pin),
                                         ec.ECDSA(hashes.SHA256()))
        except (binascii.Error, ValueError, InvalidSignature):
            return deny("bad signature")
        if not self.nonces.add(nonce):
            return deny("replayed nonce" if self.nonces.seen(nonce) else "nonce cache full")
        self.devices.touch(dev, address)
        return AuthResult(dev)

    # ------------------------------------------------------------ devices
    def revoke(self, device_id: str) -> bool:
        """Revoke a device and close its open websockets (call on the server's event loop)."""

        if not self.devices.revoke(device_id):
            return False
        _log(f"revoked device {device_id[:12]}")
        for ws in list(self.sockets.get(device_id, ())):
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().create_task(_close(ws))
        return True

    @contextlib.contextmanager
    def track(self, device: Device, ws: Any, address: str | None = None):
        """An open websocket of ``device``; lastSeen is refreshed when it closes (and by ``seen``)."""

        self.sockets[device.id].add(ws)
        try:
            yield
        finally:
            self.sockets[device.id].discard(ws)
            if not self.sockets[device.id]:
                self.sockets.pop(device.id, None)
            if device.revokedAt is None:
                self.devices.touch(device, address)

    def seen(self) -> None:
        """Refresh lastSeen of every device with an open websocket, and save what is due."""

        for device_id in list(self.sockets):
            dev = self.devices.get(device_id)
            if dev is not None and dev.revokedAt is None:
                self.devices.touch(dev, dev.lastAddress)
        self.devices.flush()

    async def keep_seen(self) -> None:
        """Run ``seen`` every SEEN_EVERY_S (a connected phone stays "last seen just now")."""

        while True:
            await asyncio.sleep(SEEN_EVERY_S)
            with contextlib.suppress(Exception):
                self.seen()

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self.devices.flush()


async def _close(ws: Any) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=REVOKED_CLOSE, reason="this phone was removed on the Mac")


# --------------------------------------------------------------------- apps
def build_remote_app(server: Any, remote: Remote) -> FastAPI:
    """The TLS listener's app: /pair, /hello and the three websockets, nothing else, plus the signed
    /builder/v1 routes only when the server has an app builder enabled."""

    from .realphone import RealPhone

    app = FastAPI(title="Rose remote", docs_url=None, redoc_url=None, openapi_url=None)
    headers = {"Cache-Control": "no-store"}

    def peer(conn: Any) -> str | None:
        return conn.client.host if conn.client else None

    @app.post("/pair")
    async def pair(request: Request) -> JSONResponse:
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > PAIR_BODY_MAX):
            remote.pairing.fail()
            return JSONResponse({"error": "body too large"}, 400, headers=headers)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > PAIR_BODY_MAX:
                remote.pairing.fail()
                return JSONResponse({"error": "body too large"}, 400, headers=headers)
        status, payload = remote.pair(body, peer(request))
        return JSONResponse(payload, status, headers=headers)

    @app.get("/hello")
    async def hello(request: Request) -> JSONResponse:
        auth = remote.authenticate("GET", request_path(request.scope), request.headers, peer(request))
        if auth.device is None:
            return JSONResponse({"error": auth.reason, "final": auth.final}, 401,
                                headers=refusal_headers(auth))
        return JSONResponse({"serverName": remote.name, "serverId": remote.identity.server_id,
                             "version": PROTOCOL_VERSION,
                             "phone": "real" if isinstance(server.phone, RealPhone) else "fake"},
                            headers=headers)

    def refusal_headers(auth: AuthResult) -> dict:
        # The reason as a token and the Mac's clock, so the phone can say "check the date and time".
        return {**headers, "X-Rose-Error": auth.code, "X-Rose-Server-Time": str(now_ms(remote.now))}

    async def refuse(ws: WebSocket, auth: AuthResult) -> None:
        """Refuse an upgrade before accept: 403 for an unknown or revoked device (pair again),
        409 for a refusal that can clear up by itself (clock skew, nonce, garbled request)."""

        if not auth.final:
            with contextlib.suppress(RuntimeError):  # the server lacks the denial-response extension
                await ws.send_denial_response(JSONResponse({"error": auth.reason, "final": False},
                                                           RETRY_STATUS, headers=refusal_headers(auth)))
                return
        await ws.close(code=1008)  # the client sees HTTP 403

    def guarded(handler: Callable[[WebSocket], Awaitable[None]]):
        async def route(ws: WebSocket) -> None:
            auth = remote.authenticate("GET", request_path(ws.scope), ws.headers, peer(ws))
            if auth.device is None:
                await refuse(ws, auth)
                return
            with remote.track(auth.device, ws, peer(ws)):
                await handler(ws)
        return route

    async def phone(ws: WebSocket) -> None:
        await server.serve_phone(ws, authenticated=True)

    app.websocket("/ws/audio")(guarded(server.serve_audio))
    app.websocket("/ws/events")(guarded(server.serve_events))
    app.websocket("/ws/phone")(guarded(phone))
    builder = getattr(server, "app_builder", None)
    if builder is not None:  # only on a host whose owner enabled the app builder (docs/APP-BUILDER.md)
        from .app_builder.routes import add_routes
        add_routes(app, remote, builder, peer, refusal_headers, request_path)
    workspace_tasks = getattr(server, "workspace_tasks", None)
    if workspace_tasks is not None:  # only on a host whose owner enabled page tasks (docs/APP-BUILDER.md §4.3)
        from .workspace_routes import add_routes as add_workspace_routes
        add_workspace_routes(app, remote, workspace_tasks, peer, refusal_headers, request_path)
    return app


class _DenialNoise(logging.Filter):
    """uvicorn's websockets-sansio protocol logs "ASGI callable returned without completing
    handshake" after a denial response (our 409) even though it sent it; drop just that line."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "ASGI callable returned without completing handshake."


def remote_http_protocol() -> type:
    """uvicorn's h11 protocol for the remote listener, with two limits it lacks: a connection must
    finish each request's headers within HEADER_TIMEOUT_S (no idle or trickling sockets), and one
    IP address may hold at most MAX_PER_PEER connections. Without them, 64 idle TLS connections
    from any LAN or tailnet host would fill uvicorn's limit and lock the paired phone out (503)."""

    from uvicorn.protocols.http.h11_impl import H11Protocol

    error_log = logging.getLogger("uvicorn.error")
    if not any(isinstance(f, _DenialNoise) for f in error_log.filters):
        error_log.addFilter(_DenialNoise())

    class RemoteH11(H11Protocol):
        def connection_made(self, transport: Any) -> None:  # type: ignore[override]
            super().connection_made(transport)
            self._deadline: asyncio.TimerHandle | None = None
            self._armed_scope: Any = None
            ip = self.client[0] if self.client else None
            if ip is not None:
                same = sum(1 for c in self.connections if (getattr(c, "client", None) or (None,))[0] == ip)
                if same > MAX_PER_PEER:
                    _log(f"refused a connection from {ip}: {MAX_PER_PEER} already open")
                    transport.abort()  # not close(): a TLS close waits for the peer's close_notify
                    return
            self._arm()

        def _arm(self) -> None:
            self._disarm()
            self._armed_scope = self.scope
            self._deadline = self.loop.call_later(HEADER_TIMEOUT_S, self._too_slow)

        def _disarm(self) -> None:
            if getattr(self, "_deadline", None) is not None:
                self._deadline.cancel()
                self._deadline = None

        def _too_slow(self) -> None:
            self._deadline = None
            idle = self.cycle is None or self.cycle.response_complete
            if self.scope is self._armed_scope and idle and not self.transport.is_closing():
                self.transport.abort()  # no request headers in time

        def handle_events(self) -> None:
            super().handle_events()
            if getattr(self, "_deadline", None) is not None and self.scope is not self._armed_scope:
                self._disarm()  # a request's headers arrived

        def on_response_complete(self) -> None:
            super().on_response_complete()
            if not self.transport.is_closing():
                self._arm()  # the next request on this keep-alive connection gets the same deadline

        def connection_lost(self, exc: Exception | None) -> None:
            self._disarm()
            super().connection_lost(exc)

    return RemoteH11


class LoopbackOnly:
    """ASGI middleware for the dashboard app: a loopback Host on every request, and an Origin that
    is absent or loopback. The dashboard has no login, so this is what stops a web page in the
    Mac's browser (cross-site websocket, DNS rebinding) from driving the phone or reading its
    screen through /ws/phone, /ws/audio, /ws/events or /api/*."""

    def __init__(self, app: Any, port: int):
        self.app = app
        self.hosts = loopback_hosts(port)
        self.origins = {f"http://{h}" for h in self.hosts}

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] in ("http", "websocket"):
            hdrs = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = hdrs.get("origin")
            if hdrs.get("host") not in self.hosts or (origin is not None and origin not in self.origins):
                peer = (scope.get("client") or ("?",))[0]
                _log(f"dashboard refused {scope['type']} {scope.get('path')} from {peer} "
                     "(not a loopback Host/Origin)")
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})  # before accept: HTTP 403
                else:
                    await JSONResponse({"ok": False, "error": "this Mac's dashboard only"}, 403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def loopback_hosts(port: int) -> set[str]:
    """Host header values that name this Mac's loopback dashboard."""

    names = ("127.0.0.1", "localhost", "[::1]")
    return {f"{n}:{port}" for n in names} | ({*names} if port == 80 else set())


def add_pairing_api(app: FastAPI, remote: Remote | None, port: int) -> None:
    """Localhost-only pairing management on the dashboard app (/api/pair/*)."""

    allowed_hosts = loopback_hosts(port)
    allowed_origins = {f"http://{h}" for h in allowed_hosts}
    headers = {"Cache-Control": "no-store"}

    def refuse(request: Request) -> JSONResponse | None:
        # The dashboard is loopback-only; a Host or Origin check also stops a web page
        # (DNS rebinding, cross-site fetch) from minting codes through the user's browser.
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"ok": False, "error": "pairing is managed from this Mac only"}, 403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse({"ok": False, "error": "cross-origin request refused"}, 403)
        if remote is None:
            return JSONResponse({"ok": False, "error": "the remote listener is off (--no-remote)"}, 503)
        if not remote.listening:
            return JSONResponse({"ok": False, "error": f"the remote listener could not use port {remote.port}"},
                                503)
        return None

    @app.post("/api/pair/new")
    async def pair_new(request: Request) -> JSONResponse:
        if (r := refuse(request)) is not None:
            return r
        try:
            payload = await asyncio.to_thread(remote.new_invite)
        except LookupError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, 503, headers=headers)
        return JSONResponse(payload, headers=headers)

    @app.get("/api/pair/status")
    async def pair_status(request: Request) -> JSONResponse:
        if (r := refuse(request)) is not None:
            return r
        out = remote.pairing.status()
        dev = remote.devices.get(out["pairedDeviceId"]) if out["pairedDeviceId"] else None
        out["pairedDeviceName"] = dev.name if dev else None
        return JSONResponse(out, headers=headers)

    @app.get("/api/pair/devices")
    async def pair_devices(request: Request) -> JSONResponse:
        if (r := refuse(request)) is not None:
            return r
        return JSONResponse({"devices": remote.devices.listing()}, headers=headers)

    @app.post("/api/pair/revoke")
    async def pair_revoke(request: Request) -> JSONResponse:
        if (r := refuse(request)) is not None:
            return r
        try:
            body = await request.json()
        except ValueError:
            body = None
        device_id = body.get("deviceId") if isinstance(body, dict) else None
        if not isinstance(device_id, str) or not _HEX64.fullmatch(device_id):
            return JSONResponse({"ok": False, "error": "deviceId must be 64 lowercase hex"}, 400, headers=headers)
        if not remote.revoke(device_id):
            return JSONResponse({"ok": False, "error": "unknown device"}, 404, headers=headers)
        return JSONResponse({"ok": True}, headers=headers)


def bind_socket(port: int, host: str = "0.0.0.0") -> socket.socket:
    """Bind the listener's socket up front so a busy port is a warning, not a crash."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock
