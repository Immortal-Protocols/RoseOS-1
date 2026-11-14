#!/usr/bin/env python3
"""Guest half of the Yogi replacement-builder recovery.

The control plane renders this file once per fresh operation, replacing the operation-id and nonce placeholders, and sends that rendered
text as the instance startup-script. This template is not a spent binding. It does not SSH, scan host keys, read the
original VM, or restore a snapshot.

On the guest it may chmod one unit file when every precondition matches, rotate three named host keys, and remove one
exact authorized_keys comment plus one exact temporary account. It writes a single public receipt line. Purpose
rotate-publish, and only that purpose, then PUTs the new public blobs to hostkeys/ on this instance's metadata
server. That write uses no service account. A failed PUT does not roll the keys back and is not retried. Repair
and plain rotate do not publish.
"""
import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request

RECEIPT_SCHEMA = "roseos.yogi-replacement-recover-receipt.v1"
BINDING_SCHEMA = "roseos.yogi-replacement-recover-binding.v1"
PREFIX = "ROSEOS-YOGI-RECOVER-RECEIPT "
PROJECT = "protocols-465723"
ZONE = "us-west2-b"
INSTANCE = "roseos-yogi-builder-20261105-b"
INSTANCE_ID = "2720796557371812468"
OPERATION_ID = "@@OP@@"
NONCE = "@@NONCE@@"
GUEST_PURPOSE = "@@PURPOSE@@"
BOUND_ED25519 = "@@FP_ED25519@@"
BOUND_ECDSA = "@@FP_ECDSA@@"
BOUND_RSA = "@@FP_RSA@@"
UNIT_PATH = ("/etc/systemd/system/"
             "actions.runner.Immortal-Protocols-RoseOS-1.roseos-yogi-builder-20261105-b.service")
UNIT_NAME = "actions.runner.Immortal-Protocols-RoseOS-1.roseos-yogi-builder-20261105-b.service"
UNIT_SIZE = 347
UNIT_BEFORE = 0o664
UNIT_AFTER = 0o644
ALGORITHMS = ("ed25519", "ecdsa", "rsa")
KEY_NAMES = {"ed25519": "ssh_host_ed25519_key", "ecdsa": "ssh_host_ecdsa_key", "rsa": "ssh_host_rsa_key"}
PUBLIC_TYPES = {"ed25519": "ssh-ed25519", "ecdsa": "ecdsa-sha2-nistp256", "rsa": "ssh-rsa"}
INVENTORY = (
    "ssh_host_ed25519_key", "ssh_host_ed25519_key.pub",
    "ssh_host_ecdsa_key", "ssh_host_ecdsa_key.pub",
    "ssh_host_rsa_key", "ssh_host_rsa_key.pub",
    "ssh_host_dsa_key", "ssh_host_dsa_key.pub",
)
AUTH_PATHS = ("/root/.ssh/authorized_keys", "/home/pixelbuild/.ssh/authorized_keys")
TEMP_ACCOUNT = "roseos-yogi-recover"
TEMP_HOME = "/var/lib/roseos-yogi-recover/account"
TEMP_SHELLS = {"/usr/sbin/nologin", "/bin/false"}
HEX32 = re.compile(r"[0-9a-f]{32}")
SHA256 = re.compile(r"[0-9a-f]{64}")
FP = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
BOOT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
EXPECTED_IDENTITY = {"project": PROJECT, "zone": ZONE, "instance": INSTANCE, "instanceId": INSTANCE_ID}
JOURNAL_PHASES = {"started", "unit-done", "baseline", "refused", "partial", "complete", "unrecognized"}
JOURNAL_KEYS = {"entries", "readable", "phases", "hasCompleteReceipt", "unrecognized"}
TOP_KEYS = {
    "schema", "purpose", "operationId", "nonce", "payloadSha256", "instanceId", "bootId", "reexecution",
    "previousBootId", "originalVmAccessed", "snapshotsUsed", "status", "unit", "runner", "hostKeys", "cleanup",
    "journal",
}
UNIT_KEYS = {
    "action", "problem", "beforeMode", "beforeSize", "beforeUid", "beforeGid", "afterMode", "afterSize", "afterUid",
    "afterGid",
}
UNIT_PROBLEMS = {
    "none", "missing", "symlink", "kind", "mode", "owner", "size", "nlink", "runner-active", "runner-enabled",
    "runner-process", "chmod", "identity", "unknown-host-key", "baseline", "rotate-same", "rotate-partial",
    "reexecution-partial", "cleanup", "already-0644", "hostkey-publish", "pair",
}
ACCOUNT_STATES = {"absent", "removed", "preserved-mismatch", "remove-failed", "removed-home-leftover"}
FORBIDDEN = ("BEGIN ", "PRIVATE", "OPENSSH", "-----")


class GuestRefused(Exception):
    pass


class Malformed(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail if isinstance(detail, str) and re.fullmatch(r"[a-z0-9-]{0,40}", detail) else ""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def binding_problems(value):
    if not isinstance(value, dict) or set(value) != {"schema", "operationId", "nonce", "payloadSha256"}:
        return ["binding-keys"]
    if value.get("schema") != BINDING_SCHEMA or not HEX32.fullmatch(value.get("operationId") or ""):
        return ["binding-operation"]
    if not HEX32.fullmatch(value.get("nonce") or "") or value["nonce"] == value["operationId"]:
        return ["binding-nonce"]
    if not SHA256.fullmatch(value.get("payloadSha256") or ""):
        return ["binding-payload"]
    return []


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


_UNIT_ACTIVE = {"active", "inactive", "failed", "unknown", "activating"}
_UNIT_ENABLED = {"enabled", "disabled", "static", "masked", "indirect", "unknown"}
def _service_unit(value):
    return isinstance(value, dict) and set(value) == {"active", "enabled"} \
        and value["active"] in _UNIT_ACTIVE and value["enabled"] in _UNIT_ENABLED


def _service_problems(value):
    if not isinstance(value, dict) or set(value) != {
            "ssh", "sshd", "guestAgent", "listen", "port22", "config"}:
        return ["services"]
    if not _service_unit(value["ssh"]) or not _service_unit(value["sshd"]) or not _service_unit(value["guestAgent"]):
        return ["services"]
    listen = value["listen"]
    if not isinstance(listen, list) or len(listen) > 4:
        return ["services"]
    for item in listen:
        if not isinstance(item, str) or not re.fullmatch(r"[0-9A-Za-z:.\[\]]{2,60}:22", item):
            return ["services"]
    if value["port22"] not in ("listening", "absent", "unreadable"):
        return ["services"]
    config = value["config"]
    if not isinstance(config, dict) or set(config) != {
            "scope", "port", "listen", "passwordAuth", "permitRoot", "pubkey", "matchBlocks"}:
        return ["services"]
    if config.get("scope") != "root-file-static":
        return ["services"]
    if config["port"] not in ("22", "other", "absent", "unreadable"):
        return ["services"]
    if config["listen"] not in ("any", "localhost", "other", "absent", "unreadable"):
        return ["services"]
    for key in ("passwordAuth", "permitRoot", "pubkey"):
        if config[key] not in ("yes", "no", "unset", "unreadable", "prohibit-password"):
            return ["services"]
    if not isinstance(config["matchBlocks"], int) or not 0 <= config["matchBlocks"] <= 20:
        return ["services"]
    return []


def _mode_token(value):
    return value == "unknown" or bool(re.fullmatch(r"[0-7]{4}", value or ""))


def _fp_map(value):
    if not isinstance(value, dict) or set(value) - set(ALGORITHMS):
        return False
    return all(isinstance(item, str) and FP.fullmatch(item) for item in value.values())


def validate_shape(receipt, binding):
    """Structural gate for a receipt. A well-formed lie can still pass; this only rejects the wrong shape."""
    problems = binding_problems(binding)
    if not isinstance(receipt, dict):
        return ["receipt-keys"]
    extra = set(receipt) - TOP_KEYS
    if extra - {"services"} or TOP_KEYS - set(receipt):
        return ["receipt-keys"]
    if "services" in receipt and _service_problems(receipt["services"]):
        return ["services"]
    if receipt.get("purpose") not in ("repair", "inspect", "rotate", "rotate-publish"):
        return ["purpose"]
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["operationId"] != binding["operationId"]:
        return ["receipt-binding"]
    if receipt["nonce"] != binding["nonce"] or receipt["payloadSha256"] != binding["payloadSha256"]:
        return ["receipt-binding"]
    if receipt["instanceId"] != INSTANCE_ID or receipt["originalVmAccessed"] is not False:
        return ["receipt-identity"]
    if receipt["snapshotsUsed"] is not False or receipt["status"] not in ("complete", "partial", "refused"):
        return ["receipt-status"]
    if not isinstance(receipt["reexecution"], bool) or not BOOT.fullmatch(receipt.get("bootId") or ""):
        return ["receipt-boot"]
    previous = receipt["previousBootId"]
    if receipt["reexecution"]:
        if not BOOT.fullmatch(previous or "") or previous == receipt["bootId"]:
            return ["receipt-boot"]
    elif previous != "none":
        return ["receipt-boot"]
    unit, runner = receipt["unit"], receipt["runner"]
    if not isinstance(unit, dict) or set(unit) != UNIT_KEYS or unit["action"] not in ("chmod-0644", "not-modified"):
        return ["unit"]
    if unit["problem"] not in UNIT_PROBLEMS or not _mode_token(unit["beforeMode"]) or not _mode_token(unit["afterMode"]):
        return ["unit"]
    for key in ("beforeSize", "beforeUid", "beforeGid", "afterSize", "afterUid", "afterGid"):
        if not isinstance(unit[key], int) or unit[key] < -1 or unit[key] > 10 ** 12:
            return ["unit"]
    if set(runner) != {"active", "enabled", "process"} or not isinstance(runner["process"], bool):
        return ["runner"]
    if runner["active"] not in ("inactive", "active", "failed", "unchecked", "activating"):
        return ["runner"]
    if runner["enabled"] not in ("disabled", "enabled", "static", "masked", "indirect", "unchecked"):
        return ["runner"]
    keys = receipt["hostKeys"]
    if not isinstance(keys, dict) or set(keys) != {
            "baseline", "rotated", "distinctFromClonedBaseline", "unknownHostKeyCount", "rolledBack"}:
        return ["hostkeys"]
    if keys["distinctFromClonedBaseline"] not in (True, False) or keys["rolledBack"] not in (True, False):
        return ["hostkeys"]
    if not _fp_map(keys["baseline"]) or not _fp_map(keys["rotated"]):
        return ["hostkeys"]
    if not isinstance(keys["unknownHostKeyCount"], int) or not 0 <= keys["unknownHostKeyCount"] <= 1000:
        return ["hostkeys"]
    cleanup = receipt["cleanup"]
    if not isinstance(cleanup, dict) or set(cleanup) != {
            "linesRemoved", "unidentifiedAuthorizedKeyLines", "temporaryAccount", "tempFilesRemoved",
            "accessCleanup", "possessMarkerLines", "authPathsRead", "possessComment", "possessMaterial"}:
        return ["cleanup"]
    if cleanup["temporaryAccount"] not in ACCOUNT_STATES or cleanup["accessCleanup"] not in ("ok", "failed"):
        return ["cleanup"]
    for key in ("linesRemoved", "unidentifiedAuthorizedKeyLines", "tempFilesRemoved"):
        if not isinstance(cleanup[key], int) or cleanup[key] < 0 or cleanup[key] > 100000:
            return ["cleanup"]
    journal = receipt.get("journal")
    if not isinstance(journal, dict) or set(journal) != JOURNAL_KEYS:
        return ["journal"]
    if not isinstance(journal["entries"], int) or not 0 <= journal["entries"] <= 20:
        return ["journal"]
    if not isinstance(journal["readable"], int) or not 0 <= journal["readable"] <= journal["entries"]:
        return ["journal"]
    phases = journal["phases"]
    if not isinstance(phases, list) or len(phases) > 20 or any(item not in JOURNAL_PHASES for item in phases):
        return ["journal"]
    if not isinstance(journal["hasCompleteReceipt"], bool):
        return ["journal"]
    if not isinstance(journal["unrecognized"], int) or not 0 <= journal["unrecognized"] <= 20:
        return ["journal"]
    if not isinstance(cleanup.get("possessMarkerLines"), int) or not -1 <= cleanup["possessMarkerLines"] <= 1000:
        return ["cleanup"]
    if not isinstance(cleanup.get("authPathsRead"), int) or not 0 <= cleanup["authPathsRead"] <= 2:
        return ["cleanup"]
    if not isinstance(cleanup.get("possessComment"), str) or len(cleanup["possessComment"]) > 80:
        return ["cleanup"]
    if not isinstance(cleanup.get("possessMaterial"), str) or len(cleanup["possessMaterial"]) > 2000:
        return ["cleanup"]
    for text in _strings(receipt):
        if len(text) > 80 or any(token in text for token in FORBIDDEN):
            return ["receipt-text"]
    if problems:
        return problems
    return []


def success_problems(receipt):
    """Empty only for a guest-reported complete repair. This is not a host-key pin."""
    unit, runner, keys, cleanup = receipt["unit"], receipt["runner"], receipt["hostKeys"], receipt["cleanup"]
    problems = []
    if receipt.get("purpose") != "repair" or receipt["status"] != "complete" or unit["action"] != "chmod-0644" \
            or unit["problem"] != "none":
        problems.append("status")
    if (unit["beforeMode"], unit["afterMode"]) != ("0664", "0644"):
        problems.append("mode")
    if (unit["beforeSize"], unit["afterSize"]) != (UNIT_SIZE, UNIT_SIZE):
        problems.append("size")
    if (unit["beforeUid"], unit["beforeGid"], unit["afterUid"], unit["afterGid"]) != (0, 0, 0, 0):
        problems.append("owner")
    if runner != {"active": "inactive", "enabled": "disabled", "process": False}:
        problems.append("runner")
    if set(keys["baseline"]) != set(ALGORITHMS) or set(keys["rotated"]) != set(ALGORITHMS):
        problems.append("rotation")
    if keys["unknownHostKeyCount"] != 0 or keys["distinctFromClonedBaseline"] is not True or keys["rolledBack"] is not False:
        problems.append("distinct")
    for alg in ALGORITHMS:
        if keys["baseline"].get(alg) == keys["rotated"].get(alg):
            problems.append("distinct")
    if cleanup["unidentifiedAuthorizedKeyLines"] != 0 or cleanup["accessCleanup"] != "ok":
        problems.append("cleanup")
    if cleanup["temporaryAccount"] not in ("absent", "removed"):
        problems.append("account")
    return problems


def rotate_success_problems(receipt, expected_baseline, purpose="rotate"):
    """Empty only when this boot rotated keys and did not change the unit mode.

    The expected baseline is the operator-reviewed inspect evidence. Matching it is not a host-key pin
    and not a comparison with the original VM. purpose rotate-publish is the same rotation plus a public
    hostkeys publish; a partial publish is not success.
    """
    if not isinstance(receipt, dict) or receipt.get("purpose") != purpose:
        return ["purpose"]
    unit = receipt.get("unit") or {}
    runner = receipt.get("runner") or {}
    keys = receipt.get("hostKeys") or {}
    cleanup = receipt.get("cleanup") or {}
    problems = []
    if receipt.get("status") != "complete" or unit.get("action") != "not-modified" or unit.get("problem") != "none":
        problems.append("status")
    if (unit.get("beforeMode"), unit.get("afterMode")) != ("0644", "0644"):
        problems.append("mode")
    if (unit.get("beforeSize"), unit.get("afterSize")) != (UNIT_SIZE, UNIT_SIZE):
        problems.append("size")
    if (unit.get("beforeUid"), unit.get("beforeGid"), unit.get("afterUid"), unit.get("afterGid")) != (0, 0, 0, 0):
        problems.append("owner")
    if runner != {"active": "inactive", "enabled": "disabled", "process": False}:
        problems.append("runner")
    if not isinstance(expected_baseline, dict) or set(expected_baseline) != set(ALGORITHMS):
        problems.append("baseline")
    elif keys.get("baseline") != expected_baseline or set(keys.get("rotated") or {}) != set(ALGORITHMS):
        problems.append("baseline")
    else:
        for alg in ALGORITHMS:
            if not FP.fullmatch(expected_baseline[alg]) or keys["rotated"].get(alg) == expected_baseline[alg]:
                problems.append("distinct")
    if keys.get("unknownHostKeyCount") != 0 or keys.get("distinctFromClonedBaseline") is not True \
            or keys.get("rolledBack") is not False:
        problems.append("distinct")
    if cleanup.get("linesRemoved") != 0 or cleanup.get("tempFilesRemoved") != 0 \
            or cleanup.get("unidentifiedAuthorizedKeyLines") != 0 or cleanup.get("accessCleanup") != "ok" \
            or cleanup.get("temporaryAccount") != "absent":
        problems.append("cleanup")
    return problems


def _bound_baseline():
    return {"ed25519": BOUND_ED25519, "ecdsa": BOUND_ECDSA, "rsa": BOUND_RSA}


def journal_summary(marker):
    """Public summary of one local marker. It does not copy the marker body."""
    if not marker:
        return {"entries": 0, "readable": 0, "phases": [], "hasCompleteReceipt": False, "unrecognized": 0}
    phase = marker.get("phase") if isinstance(marker, dict) else None
    if phase in JOURNAL_PHASES and phase != "unrecognized":
        phases, unrecognized = [phase], 0
    else:
        phases, unrecognized = ["unrecognized"], 1
    receipt = marker.get("receipt") if isinstance(marker, dict) else None
    complete = phase == "complete" and isinstance(receipt, dict) and receipt.get("status") == "complete"
    return {"entries": 1, "readable": 1, "phases": phases, "hasCompleteReceipt": bool(complete),
            "unrecognized": unrecognized}


def parse_serial(contents, binding, final=True):
    """Accept one receipt from a serial console capture.

    A syslog prefix may precede the receipt. CRLF is one line. A console that wraps a long line and
    repeats that prefix is joined back into one JSON value. Two byte-for-byte equal receipts are one
    observation. A second different receipt is still a sequence failure. An unfinished tail waits
    unless this is the last read.
    """
    text = str(contents).replace("\r\n", "\n").replace("\r", "\n")
    closed = text.endswith("\n")
    parts = text.split("\n")
    if closed:
        parts = parts[:-1]
    found = []
    index = 0
    while index < len(parts):
        line = parts[index]
        at = line.find(PREFIX)
        if at < 0:
            index += 1
            continue
        lead = line[:at]
        payload = line[at + len(PREFIX):]
        cursor = index + 1
        while True:
            if len(payload) > 8000:
                raise Malformed("oversize")
            try:
                receipt = json.loads(payload.strip())
                break
            except ValueError:
                if cursor >= len(parts):
                    if not closed and not final:
                        return None
                    raise Malformed("json") from None
                nxt = parts[cursor]
                if PREFIX in nxt:
                    raise Malformed("json")
                if lead and nxt.startswith(lead):
                    nxt = nxt[len(lead):]
                payload += nxt
                cursor += 1
        problems = validate_shape(receipt, binding)
        if problems:
            raise Malformed("shape", problems[0])
        found.append(receipt)
        index = cursor
    unique = []
    for receipt in found:
        if not unique or unique[-1] != receipt:
            unique.append(receipt)
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    if len(unique) == 2 and unique[0]["reexecution"] is False and unique[1]["reexecution"] is True \
            and unique[0]["hostKeys"]["baseline"] == unique[1]["hostKeys"]["baseline"] \
            and unique[0]["hostKeys"]["rotated"] == unique[1]["hostKeys"]["rotated"]:
        return unique[1]
    raise Malformed("sequence")


def unit_problem(record):
    if record is None:
        return "missing"
    if record.get("kind") == "symlink":
        return "symlink"
    if record.get("kind") != "regular":
        return "kind"
    if record.get("nlink") != 1:
        return "nlink"
    if record.get("uid") != 0 or record.get("gid") != 0:
        return "owner"
    if record.get("size") != UNIT_SIZE:
        return "size"
    if record.get("mode") == UNIT_AFTER:
        return "already-0644"
    if record.get("mode") != UNIT_BEFORE:
        return "mode"
    return None


def unit_after_problem(record):
    if not record or record.get("kind") != "regular" or record.get("nlink") != 1:
        return "chmod"
    if record.get("uid") != 0 or record.get("gid") != 0 or record.get("mode") != UNIT_AFTER:
        return "chmod"
    if record.get("size") != UNIT_SIZE:
        return "chmod"
    return None


def runner_problem(runner):
    if runner.get("process") is not False:
        return "runner-process"
    if runner.get("active") != "inactive":
        return "runner-active"
    if runner.get("enabled") != "disabled":
        return "runner-enabled"
    return None


def publish_public_hostkeys(io, lines_by_alg):
    """PUT each new public blob to hostkeys/<openssh-type> on this instance's metadata server.

    The value is the base64 blob only, matching the guest-attribute key the guest environment uses.
    Private key files are not read. A metadata rejection (guest attributes disabled) returns False.
    That does not roll the new keys back and does not prove the guest is honest.
    """
    if set(lines_by_alg) != set(ALGORITHMS):
        raise GuestRefused("public-type")
    puts = []
    for alg in ALGORITHMS:
        line = lines_by_alg[alg]
        key_type, _fingerprint = parse_public(line)
        if key_type != PUBLIC_TYPES[alg]:
            raise GuestRefused("public-type")
        blob = str(line).strip().split()[1]
        if not re.fullmatch(r"[A-Za-z0-9+/]+=*", blob):
            raise GuestRefused("public-blob")
        if any(token in blob or token in key_type for token in ("BEGIN", "PRIVATE", "OPENSSH")):
            raise GuestRefused("private-attribute")
        puts.append((key_type, blob))
    return bool(io.publish_hostkey_attributes(puts))


def _u32(value):
    return int(value).to_bytes(4, "big")


def _ssh_bytes(payload):
    if isinstance(payload, str):
        payload = payload.encode()
    return _u32(len(payload)) + payload


def _take_bytes(blob, offset):
    if offset + 4 > len(blob):
        raise GuestRefused("pair")
    length = int.from_bytes(blob[offset:offset + 4], "big")
    offset += 4
    if length < 0 or length > 1 << 20 or offset + length > len(blob):
        raise GuestRefused("pair")
    return blob[offset:offset + length], offset + length


def _pem_body(blob):
    try:
        text = blob.decode("ascii")
    except UnicodeError:
        raise GuestRefused("pair") from None
    body = []
    inside = False
    for line in text.splitlines():
        if line.startswith("-----BEGIN ") and line.endswith(" PRIVATE KEY-----"):
            inside = True
            continue
        if line.startswith("-----END ") and line.endswith(" PRIVATE KEY-----"):
            break
        if inside and line.strip():
            body.append(line.strip())
    if not body:
        raise GuestRefused("pair")
    try:
        return base64.b64decode("".join(body), validate=True)
    except ValueError:
        raise GuestRefused("pair") from None


def _public_wire_from_private_section(section):
    """Public wire blob from the private section. Secret fields are not returned."""
    try:
        check_a = int.from_bytes(section[:4], "big")
        check_b = int.from_bytes(section[4:8], "big")
    except (IndexError, TypeError):
        raise GuestRefused("pair") from None
    if len(section) < 8 or check_a != check_b:
        raise GuestRefused("pair")
    key_type, offset = _take_bytes(section, 8)
    if key_type == b"ssh-ed25519":
        public, _offset = _take_bytes(section, offset)
        _secret, _offset = _take_bytes(section, _offset)
        return _ssh_bytes(key_type) + _ssh_bytes(public)
    if key_type.startswith(b"ecdsa-sha2-"):
        curve, offset = _take_bytes(section, offset)
        point, offset = _take_bytes(section, offset)
        _secret, _offset = _take_bytes(section, offset)
        return _ssh_bytes(key_type) + _ssh_bytes(curve) + _ssh_bytes(point)
    if key_type == b"ssh-rsa":
        modulus, offset = _take_bytes(section, offset)
        exponent, _offset = _take_bytes(section, offset)
        return _ssh_bytes(key_type) + _ssh_bytes(exponent) + _ssh_bytes(modulus)
    raise GuestRefused("pair")


def public_fingerprint_from_private(blob):
    """Fingerprint of the public key carried in an unencrypted OpenSSH private key.

    The return value is the key type and the SHA256 fingerprint. Private scalars are not returned.
    """
    try:
        raw = _pem_body(blob)
    except GuestRefused:
        raise
    magic = b"openssh-key-v1\x00"
    if not raw.startswith(magic):
        raise GuestRefused("pair")
    offset = len(magic)
    cipher, offset = _take_bytes(raw, offset)
    kdf, offset = _take_bytes(raw, offset)
    _options, offset = _take_bytes(raw, offset)
    if cipher != b"none" or kdf != b"none" or offset + 4 > len(raw):
        raise GuestRefused("pair")
    count = int.from_bytes(raw[offset:offset + 4], "big")
    offset += 4
    if count != 1:
        raise GuestRefused("pair")
    header_public, offset = _take_bytes(raw, offset)
    private_section, _offset = _take_bytes(raw, offset)
    derived = _public_wire_from_private_section(private_section)
    if derived != header_public:
        raise GuestRefused("pair")
    key_type, _rest = _take_bytes(derived, 0)
    try:
        name = key_type.decode("ascii")
    except UnicodeError:
        raise GuestRefused("pair") from None
    line = name + " " + base64.b64encode(derived).decode("ascii")
    return parse_public(line)


def parse_public(line):
    parts = str(line).strip().split()
    if len(parts) < 2 or parts[0] not in PUBLIC_TYPES.values():
        raise GuestRefused("public-type")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        length = int.from_bytes(blob[:4], "big")
        embedded = blob[4:4 + length].decode()
    except (ValueError, UnicodeError, IndexError):
        raise GuestRefused("public-blob") from None
    if embedded != parts[0] or not 8 <= len(blob) <= 4096:
        raise GuestRefused("public-blob")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return parts[0], fingerprint


def line_disposition(line, operation_id):
    """remove only an exact three-field line whose comment is this operation's fresh marker."""
    if line is None:
        return "keep"
    if line == "" or line.startswith("#"):
        return "keep"
    parts = line.split()
    comment = "roseos-yogi-recover-" + operation_id
    if len(parts) == 3 and parts[0] in PUBLIC_TYPES.values() and parts[2] == comment and HEX32.fullmatch(operation_id):
        return "remove"
    return "unidentified"


def account_removable(entry):
    if not isinstance(entry, dict):
        return False
    try:
        uid, gid = int(entry.get("uid")), int(entry.get("gid"))
    except (TypeError, ValueError):
        return False
    return entry.get("name") == TEMP_ACCOUNT and uid >= 60000 and gid >= 60000 and uid != 0 \
        and entry.get("home") == TEMP_HOME and entry.get("shell") in TEMP_SHELLS


def _mode_name(mode):
    if not isinstance(mode, int) or mode < 0:
        return "unknown"
    return f"{mode & 0o777:04o}"


def _public_runner(runner):
    active = runner.get("active")
    enabled = runner.get("enabled")
    if active not in ("inactive", "active", "failed", "unchecked", "activating"):
        active = "failed"
    if enabled not in ("disabled", "enabled", "static", "masked", "indirect", "unchecked"):
        enabled = "enabled"
    return {"active": active, "enabled": enabled, "process": runner.get("process") is True}


def _skeleton(binding, boot, status):
    return {
        "schema": RECEIPT_SCHEMA, "purpose": "repair", "operationId": binding["operationId"], "nonce": binding["nonce"],
        "payloadSha256": binding["payloadSha256"], "instanceId": INSTANCE_ID, "bootId": boot,
        "reexecution": False, "previousBootId": "none", "originalVmAccessed": False, "snapshotsUsed": False,
        "status": status,
        "unit": {"action": "not-modified", "problem": "none", "beforeMode": "unknown", "beforeSize": -1,
                 "beforeUid": -1, "beforeGid": -1, "afterMode": "unknown", "afterSize": -1, "afterUid": -1,
                 "afterGid": -1},
        "runner": {"active": "unchecked", "enabled": "unchecked", "process": False},
        "hostKeys": {"baseline": {}, "rotated": {}, "distinctFromClonedBaseline": False, "unknownHostKeyCount": 0,
                     "rolledBack": False},
        "cleanup": {"linesRemoved": 0, "unidentifiedAuthorizedKeyLines": 0, "temporaryAccount": "absent",
                    "tempFilesRemoved": 0, "accessCleanup": "ok", "possessMarkerLines": 0,
                    "authPathsRead": 2, "possessComment": "roseos-yogi-possess-",
                    "possessMaterial": "roseos-yogi-possess-material-unset"},
        "journal": {"entries": 0, "readable": 0, "phases": [], "hasCompleteReceipt": False, "unrecognized": 0},
    }


def _fill_unit(receipt, before, after, problem, action):
    if before:
        receipt["unit"].update(beforeMode=_mode_name(before.get("mode")), beforeSize=int(before.get("size", -1)),
                               beforeUid=int(before.get("uid", -1)), beforeGid=int(before.get("gid", -1)))
    if after:
        receipt["unit"].update(afterMode=_mode_name(after.get("mode")), afterSize=int(after.get("size", -1)),
                               afterUid=int(after.get("uid", -1)), afterGid=int(after.get("gid", -1)))
    receipt["unit"]["problem"] = problem
    receipt["unit"]["action"] = action


def _private_problem(record):
    if not record or record.get("kind") != "regular" or record.get("uid") != 0 or record.get("nlink") != 1:
        return "baseline"
    if record.get("mode") not in (0o600, 0o640):
        return "baseline"
    return None


def _public_stat_problem(record):
    if not record or record.get("kind") != "regular" or record.get("uid") != 0 or record.get("nlink") != 1:
        return "baseline"
    if record.get("mode") != 0o644:
        return "baseline"
    return None


def possess_marker_lines(lines, comment):
    """How many lines contain this exact possession comment. The line text is not returned."""
    if not isinstance(comment, str) or not comment:
        return 0
    count = 0
    for line in lines or []:
        if isinstance(line, str) and comment in line:
            count += 1
    return count


def cleanup_access(io, operation_id):
    removed = unidentified = possess = paths = 0
    failed = False
    comment = "roseos-yogi-possess-"
    for path in AUTH_PATHS:
        try:
            lines = io.auth_lines(path)
        except (GuestRefused, OSError):
            failed = True
            continue
        paths += 1
        if lines is None:
            continue
        kept, changed = [], False
        for line in lines:
            action = line_disposition(line, operation_id)
            if action == "remove":
                removed += 1
                changed = True
            else:
                if action == "unidentified":
                    unidentified += 1
                kept.append(line)
            possess += possess_marker_lines([line], comment)
        if changed:
            try:
                io.auth_write(path, kept)
            except (GuestRefused, OSError):
                failed = True
    account_state = "absent"
    try:
        entry = io.account()
        if entry is None:
            account_state = "absent"
        elif not account_removable(entry):
            account_state = "preserved-mismatch"
        else:
            account_state = io.account_remove()
            if account_state not in ("removed", "removed-home-leftover"):
                account_state = "remove-failed"
    except (GuestRefused, OSError):
        account_state = "remove-failed"
    temp_removed = 0
    try:
        discarded = io.ssh_discard_temp()
        temp_removed = int(discarded["removed"])
        if int(discarded["skippedSymlink"]) > 0:
            failed = True
    except (GuestRefused, KeyError, TypeError, ValueError):
        failed = True
    if failed or paths != len(AUTH_PATHS):
        possess = -1
    return {"linesRemoved": removed, "unidentifiedAuthorizedKeyLines": unidentified,
            "temporaryAccount": account_state, "tempFilesRemoved": temp_removed,
            "accessCleanup": "failed" if failed or paths != len(AUTH_PATHS) else "ok",
            "possessMarkerLines": possess, "authPathsRead": paths, "possessComment": comment,
            "possessMaterial": "roseos-yogi-possess-material-unset"}


def _emit(io, receipt, binding):
    prior_unrecognized = 0
    if isinstance(receipt.get("journal"), dict) and isinstance(receipt["journal"].get("unrecognized"), int):
        prior_unrecognized = receipt["journal"]["unrecognized"]
    try:
        receipt["journal"] = journal_summary(io.marker_load())
    except (GuestRefused, OSError, ValueError, TypeError):
        receipt["journal"] = journal_summary({"phase": "unrecognized"})
    if 0 <= prior_unrecognized <= 20 and prior_unrecognized > receipt["journal"]["unrecognized"]:
        receipt["journal"]["unrecognized"] = prior_unrecognized
    if validate_shape(receipt, binding):
        raise GuestRefused("receipt-shape")
    line = PREFIX + canonical(receipt)
    if len(line) > 8000:
        raise GuestRefused("receipt-size")
    io.emit(line)
    return receipt


def _boot(io):
    boot = io.boot_id()
    if not isinstance(boot, str) or not BOOT.fullmatch(boot):
        raise GuestRefused("boot-id")
    return boot


def _observe_keys(io):
    """Public fingerprints only. Does not generate, rename, or delete host keys."""
    unknown = 0
    for name in io.ssh_names():
        if name.startswith("ssh_host_") and name not in INVENTORY and ".roseos-prev-" not in name \
                and ".roseos-new" not in name:
            unknown += 1
    baseline = {}
    for alg in ALGORITHMS:
        try:
            key_type, fingerprint = parse_public(io.ssh_public(KEY_NAMES[alg] + ".pub"))
        except (GuestRefused, OSError):
            continue
        if key_type == PUBLIC_TYPES[alg] and FP.fullmatch(fingerprint):
            baseline[alg] = fingerprint
    return baseline, unknown


def _observe_access(io, operation_id):
    """Count authorized_keys lines and the temporary account. Does not rewrite either."""
    unidentified = possess = paths = 0
    failed = False
    comment = "roseos-yogi-possess-"
    for path in AUTH_PATHS:
        try:
            lines = io.auth_lines(path)
        except (GuestRefused, OSError):
            failed = True
            continue
        paths += 1
        if not lines:
            continue
        for line in lines:
            if line_disposition(line, operation_id) == "unidentified":
                unidentified += 1
            possess += possess_marker_lines([line], comment)
    if failed or paths != len(AUTH_PATHS):
        possess = -1
    account_state = "absent"
    try:
        entry = io.account()
        if entry is not None:
            account_state = "preserved-mismatch"
    except (GuestRefused, OSError):
        failed = True
        account_state = "preserved-mismatch"
    return {"linesRemoved": 0, "unidentifiedAuthorizedKeyLines": unidentified, "temporaryAccount": account_state,
            "tempFilesRemoved": 0, "accessCleanup": "failed" if failed or paths != len(AUTH_PATHS) else "ok",
            "possessMarkerLines": possess, "authPathsRead": paths, "possessComment": comment,
            "possessMaterial": "roseos-yogi-possess-material-unset"}


def _rotate_refuse(io, binding, boot, before, runner, problem, baseline=None, unknown=0):
    receipt = _skeleton(binding, boot, "refused")
    receipt["purpose"] = "rotate"
    _fill_unit(receipt, before if isinstance(before, dict) else None, before if isinstance(before, dict) else None,
               problem, "not-modified")
    if isinstance(before, dict) and before.get("mode") == UNIT_AFTER:
        receipt["unit"]["beforeMode"] = receipt["unit"]["afterMode"] = "0644"
    receipt["runner"] = _public_runner(runner) if isinstance(runner, dict) else receipt["runner"]
    if isinstance(baseline, dict):
        receipt["hostKeys"]["baseline"] = {k: v for k, v in baseline.items() if k in ALGORITHMS and FP.fullmatch(v or "")}
    receipt["hostKeys"]["unknownHostKeyCount"] = unknown
    receipt["cleanup"] = _observe_access(io, OPERATION_ID)
    try:
        receipt["journal"]["unrecognized"] = min(20, int(io.prior_journal()))
    except (GuestRefused, OSError, TypeError, ValueError):
        receipt["journal"]["unrecognized"] = 1
    io.marker_store({"phase": "refused", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
    return _emit(io, receipt, binding)


def run_rotate(io):
    """Rotate the three named host keys only. The unit mode is not changed in either direction."""
    try:
        binding = io.binding()
    except GuestRefused:
        return None
    if binding_problems(binding) or binding["operationId"] != OPERATION_ID or binding["nonce"] != NONCE:
        return None
    try:
        if io.self_sha256() != binding["payloadSha256"]:
            return None
        boot = _boot(io)
    except GuestRefused:
        return None
    marker = io.marker_load()
    if marker:
        return _rerun(io, marker, binding, boot)
    io.marker_store({"phase": "started", "operationId": OPERATION_ID, "nonce": NONCE})
    try:
        identity = io.instance_identity()
    except GuestRefused:
        identity = None
    try:
        before = io.unit_stat()
        runner = io.runner()
    except GuestRefused:
        before, runner = None, {}
    if identity != EXPECTED_IDENTITY:
        return _rotate_refuse(io, binding, boot, before, runner, "identity")
    if not isinstance(before, dict):
        return _rotate_refuse(io, binding, boot, before, runner, "mode")
    unit_state = unit_problem(before)
    runner_state = runner_problem(runner) if isinstance(runner, dict) else "runner-active"
    if unit_state != "already-0644" or runner_state:
        problem = runner_state if unit_state == "already-0644" and runner_state else (unit_state or "mode")
        return _rotate_refuse(io, binding, boot, before, runner, problem)
    expected = _bound_baseline()
    if any(not FP.fullmatch(expected[alg]) for alg in ALGORITHMS):
        return _rotate_refuse(io, binding, boot, before, runner, "baseline")
    baseline, unknown = _observe_keys(io)
    access = _observe_access(io, OPERATION_ID)
    if unknown or baseline != expected or access["unidentifiedAuthorizedKeyLines"] or access["temporaryAccount"] != "absent" \
            or access["accessCleanup"] != "ok":
        problem = "unknown-host-key" if unknown else ("cleanup" if access["unidentifiedAuthorizedKeyLines"] or access["temporaryAccount"] != "absent" or access["accessCleanup"] != "ok" else "baseline")
        return _rotate_refuse(io, binding, boot, before, runner, problem, baseline, unknown)
    io.marker_store({"phase": "baseline", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline})
    rotated = {}
    public_lines = {}
    try:
        for alg in ALGORITHMS:
            key_type, fingerprint = parse_public(io.ssh_generate(alg))
            if key_type != PUBLIC_TYPES[alg] or fingerprint == baseline[alg]:
                io.ssh_discard_temp()
                raise GuestRefused("rotate-same")
            io.ssh_preserve(alg)
            io.ssh_commit(alg)
            public_line = io.ssh_public(KEY_NAMES[alg] + ".pub")
            key_type, confirmed = parse_public(public_line)
            if key_type != PUBLIC_TYPES[alg] or confirmed != fingerprint or confirmed == baseline[alg]:
                raise GuestRefused("rotate-partial")
            rotated[alg] = confirmed
            public_lines[alg] = public_line
    except (GuestRefused, OSError) as error:
        code = str(error) if str(error) in ("rotate-same", "rotate-partial") else "rotate-partial"
        outcome = _rollback(io)
        receipt = _skeleton(binding, boot, "partial")
        receipt["purpose"] = "rotate-publish" if GUEST_PURPOSE == "rotate-publish" else "rotate"
        _fill_unit(receipt, before, before, "pair" if outcome == "partial" else code, "not-modified")
        receipt["unit"]["beforeMode"] = receipt["unit"]["afterMode"] = "0644"
        receipt["runner"] = _public_runner(runner)
        receipt["hostKeys"]["baseline"] = baseline
        receipt["hostKeys"]["rotated"] = {}
        receipt["hostKeys"]["rolledBack"] = outcome == "restored"
        receipt["cleanup"] = access
        io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                         "receipt": receipt})
        _drop_backups(io)
        return _emit(io, receipt, binding)
    receipt = _skeleton(binding, boot, "complete")
    _fill_unit(receipt, before, before, "none", "not-modified")
    receipt["unit"]["beforeMode"] = receipt["unit"]["afterMode"] = "0644"
    receipt["runner"] = _public_runner(runner)
    receipt["hostKeys"]["baseline"] = baseline
    receipt["hostKeys"]["rotated"] = rotated
    receipt["hostKeys"]["rolledBack"] = False
    receipt["hostKeys"]["distinctFromClonedBaseline"] = True
    receipt["hostKeys"]["unknownHostKeyCount"] = 0
    receipt["cleanup"] = access
    try:
        receipt["journal"]["unrecognized"] = min(20, int(io.prior_journal()))
    except (GuestRefused, OSError, TypeError, ValueError):
        receipt["journal"]["unrecognized"] = 1
    receipt["purpose"] = "rotate-publish" if GUEST_PURPOSE == "rotate-publish" else "rotate"
    if GUEST_PURPOSE == "rotate-publish" and not _publish_rotated_hostkeys(io, public_lines):
        receipt["status"] = "partial"
        receipt["unit"]["problem"] = "hostkey-publish"
        io.marker_store({"phase": "publish-failed", "operationId": OPERATION_ID, "nonce": NONCE,
                         "baseline": baseline, "rotated": rotated, "receipt": receipt})
        _drop_backups(io)
        return _emit(io, receipt, binding)
    io.marker_store({"phase": "complete", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                     "rotated": rotated, "receipt": receipt})
    _drop_backups(io)
    return _emit(io, receipt, binding)


def _emit_publish_failure(io, marker, binding, boot):
    """Re-emit the partial receipt. Do not generate, publish, or roll the new keys back."""
    prior = marker.get("receipt")
    if not isinstance(prior, dict) or validate_shape(prior, binding) or prior.get("status") != "partial":
        return None
    if (prior.get("unit") or {}).get("problem") != "hostkey-publish":
        return None
    if prior.get("bootId") == boot:
        return None
    receipt = json.loads(canonical(prior))
    receipt["reexecution"] = True
    receipt["previousBootId"] = prior["bootId"]
    receipt["bootId"] = boot
    return _emit(io, receipt, binding)


def _publish_rotated_hostkeys(io, lines_by_alg):
    """Best-effort public publish. Failure leaves the rotated files in place."""
    try:
        return bool(publish_public_hostkeys(io, lines_by_alg))
    except (GuestRefused, OSError):
        return False


def run(io):
    """One guest pass. Repair still stops before rotation when the unit is already mode 0644.

    Rotate is a separate purpose. It requires that mode and does not change it.
    """
    if GUEST_PURPOSE in ("rotate", "rotate-publish"):
        return run_rotate(io)
    if GUEST_PURPOSE != "repair":
        return None
    try:
        binding = io.binding()
    except GuestRefused:
        return None
    if binding_problems(binding) or binding["operationId"] != OPERATION_ID or binding["nonce"] != NONCE:
        return None
    try:
        if io.self_sha256() != binding["payloadSha256"]:
            return None
        boot = _boot(io)
    except GuestRefused:
        return None
    marker = io.marker_load()
    if marker:
        return _rerun(io, marker, binding, boot)
    io.marker_store({"phase": "started", "operationId": OPERATION_ID, "nonce": NONCE})
    try:
        identity = io.instance_identity()
    except GuestRefused:
        identity = None
    if identity != EXPECTED_IDENTITY:
        receipt = _skeleton(binding, boot, "refused")
        receipt["unit"]["problem"] = "identity"
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "refused", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
        return _emit(io, receipt, binding)
    try:
        before = io.unit_stat()
        runner = io.runner()
    except GuestRefused:
        receipt = _skeleton(binding, boot, "refused")
        receipt["unit"]["problem"] = "missing"
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "refused", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
        return _emit(io, receipt, binding)
    problem = unit_problem(before) or runner_problem(runner)
    if problem == "already-0644" and runner_problem(runner) is None:
        baseline, unknown = _observe_keys(io)
        receipt = _skeleton(binding, boot, "refused")
        _fill_unit(receipt, before, None, "already-0644", "not-modified")
        receipt["runner"] = _public_runner(runner)
        receipt["hostKeys"]["baseline"] = baseline
        receipt["hostKeys"]["unknownHostKeyCount"] = unknown
        receipt["cleanup"] = _observe_access(io, OPERATION_ID)
        io.marker_store({"phase": "refused", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
        return _emit(io, receipt, binding)
    if problem:
        receipt = _skeleton(binding, boot, "refused")
        _fill_unit(receipt, before if isinstance(before, dict) else None, None, problem, "not-modified")
        receipt["runner"] = _public_runner(runner)
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        if receipt["cleanup"]["accessCleanup"] != "ok" or receipt["cleanup"]["unidentifiedAuthorizedKeyLines"]:
            receipt["status"] = "partial"
        io.marker_store({"phase": "refused", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
        return _emit(io, receipt, binding)
    try:
        after = io.unit_chmod()
    except GuestRefused:
        after = None
    if unit_after_problem(after):
        receipt = _skeleton(binding, boot, "partial")
        _fill_unit(receipt, before, after if isinstance(after, dict) else None, "chmod", "not-modified")
        receipt["runner"] = _public_runner(runner)
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
        return _emit(io, receipt, binding)
    io.marker_store({"phase": "unit-done", "operationId": OPERATION_ID, "nonce": NONCE})
    names = list(io.ssh_names())
    unknown = 0
    for name in names:
        if name.startswith("ssh_host_") and name not in INVENTORY:
            unknown += 1
    receipt = _skeleton(binding, boot, "refused")
    _fill_unit(receipt, before, after, "none", "chmod-0644")
    receipt["runner"] = _public_runner(runner)
    receipt["hostKeys"]["unknownHostKeyCount"] = unknown
    if unknown:
        receipt["unit"]["problem"] = "unknown-host-key"
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "refused", "operationId": OPERATION_ID, "nonce": NONCE, "receipt": receipt})
        return _emit(io, receipt, binding)
    baseline = {}
    try:
        for alg in ALGORITHMS:
            private = io.ssh_stat(KEY_NAMES[alg])
            public_stat = io.ssh_stat(KEY_NAMES[alg] + ".pub")
            if _private_problem(private) or _public_stat_problem(public_stat):
                raise GuestRefused("baseline")
            key_type, fingerprint = parse_public(io.ssh_public(KEY_NAMES[alg] + ".pub"))
            if key_type != PUBLIC_TYPES[alg]:
                raise GuestRefused("baseline")
            baseline[alg] = fingerprint
    except (GuestRefused, OSError):
        receipt["unit"]["problem"] = "baseline"
        receipt["hostKeys"]["baseline"] = {k: v for k, v in baseline.items() if FP.fullmatch(v or "")}
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                         "receipt": receipt})
        return _emit(io, receipt, binding)
    io.marker_store({"phase": "baseline", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline})
    rotated = {}
    try:
        for alg in ALGORITHMS:
            key_type, fingerprint = parse_public(io.ssh_generate(alg))
            if key_type != PUBLIC_TYPES[alg] or fingerprint == baseline[alg]:
                io.ssh_discard_temp()
                raise GuestRefused("rotate-same")
            io.ssh_preserve(alg)
            io.ssh_commit(alg)
            key_type, confirmed = parse_public(io.ssh_public(KEY_NAMES[alg] + ".pub"))
            if key_type != PUBLIC_TYPES[alg] or confirmed != fingerprint or confirmed == baseline[alg]:
                raise GuestRefused("rotate-partial")
            rotated[alg] = confirmed
    except (GuestRefused, OSError) as error:
        code = str(error) if str(error) in ("rotate-same", "rotate-partial") else "rotate-partial"
        outcome = _rollback(io)
        receipt["status"] = "partial"
        receipt["unit"]["problem"] = "pair" if outcome == "partial" else code
        receipt["hostKeys"]["baseline"] = baseline
        receipt["hostKeys"]["rotated"] = {}
        receipt["hostKeys"]["rolledBack"] = outcome == "restored"
        receipt["hostKeys"]["distinctFromClonedBaseline"] = False
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                         "receipt": receipt})
        _drop_backups(io)
        return _emit(io, receipt, binding)
    receipt["status"] = "complete"
    receipt["hostKeys"]["baseline"] = baseline
    receipt["hostKeys"]["rotated"] = rotated
    receipt["hostKeys"]["rolledBack"] = False
    receipt["hostKeys"]["distinctFromClonedBaseline"] = True
    receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
    if receipt["cleanup"]["accessCleanup"] != "ok" or receipt["cleanup"]["unidentifiedAuthorizedKeyLines"] \
            or receipt["cleanup"]["temporaryAccount"] not in ("absent", "removed"):
        receipt["status"] = "partial"
        receipt["unit"]["problem"] = "cleanup"
        receipt["hostKeys"]["distinctFromClonedBaseline"] = True
    phase = "complete" if receipt["status"] == "complete" else "partial"
    io.marker_store({"phase": phase, "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                     "rotated": rotated, "receipt": receipt})
    _drop_backups(io)
    return _emit(io, receipt, binding)


def restore_backup_pairs(io):
    """Restore a backup only when both halves are one valid pair. Never copy a single half.

    Returns restored, unchanged, or partial. Does not delete the backup private key and does not
    generate a new key.
    """
    restored = False
    partial = False
    for alg in ALGORITHMS:
        try:
            backup = io.pair_status(alg, "backup")
            live = io.pair_status(alg, "live")
        except (GuestRefused, OSError):
            partial = True
            continue
        if backup == "match":
            try:
                same = live == "match" and io.pairs_agree(alg)
            except (GuestRefused, OSError):
                same = False
            if same:
                continue
            try:
                copied = io.restore_pair(alg) and io.pair_status(alg, "live") == "match"
            except (GuestRefused, OSError):
                copied = False
            if copied:
                restored = True
            else:
                partial = True
            continue
        if live != "match":
            partial = True
    if partial:
        return "partial"
    return "restored" if restored else "unchanged"


def _drop_backups(io):
    """Drop backup private keys only after the caller has stored a durable journal."""
    for alg in ALGORITHMS:
        io.ssh_drop_prev_private(alg)


def _rollback(io):
    """Restore complete backup pairs. The backup private key stays until the journal is stored."""
    return restore_backup_pairs(io)


def _rerun(io, marker, binding, boot):
    if not isinstance(marker, dict) or marker.get("operationId") != OPERATION_ID or marker.get("nonce") != NONCE:
        return None
    if marker.get("phase") == "publish-failed":
        return _emit_publish_failure(io, marker, binding, boot)
    if marker.get("phase") == "complete" and isinstance(marker.get("receipt"), dict):
        prior = marker["receipt"]
        if validate_shape(prior, binding) or prior.get("status") != "complete":
            marker = {"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE}
        else:
            receipt = json.loads(canonical(prior))
            receipt["reexecution"] = True
            receipt["previousBootId"] = prior["bootId"]
            receipt["bootId"] = boot
            discarded = {"removed": 0, "skippedSymlink": 0}
            try:
                discarded = io.ssh_discard_temp()
            except GuestRefused:
                discarded = {"removed": 0, "skippedSymlink": 1}
            try:
                _drop_backups(io)
            except (GuestRefused, OSError):
                receipt["status"] = "partial"
            if int(discarded.get("skippedSymlink") or 0) > 0:
                receipt["status"] = "partial"
                receipt["cleanup"] = dict(receipt["cleanup"], accessCleanup="failed")
            return _emit(io, receipt, binding)
    # A crash or reboot must not generate another key. Restore only a complete backup pair.
    outcome = _rollback(io)
    try:
        io.ssh_discard_temp()
    except (GuestRefused, OSError):
        outcome = "partial"
    receipt = _skeleton(binding, boot, "partial")
    receipt["purpose"] = "rotate-publish" if GUEST_PURPOSE == "rotate-publish" else (
        "rotate" if GUEST_PURPOSE == "rotate" else "repair")
    receipt["reexecution"] = False
    receipt["unit"]["problem"] = "pair" if outcome == "partial" else "reexecution-partial"
    baseline = marker.get("baseline") if isinstance(marker.get("baseline"), dict) else {}
    receipt["hostKeys"]["baseline"] = {k: v for k, v in baseline.items() if k in ALGORITHMS and isinstance(v, str) and FP.fullmatch(v)}
    receipt["hostKeys"]["rotated"] = {}
    receipt["hostKeys"]["rolledBack"] = outcome == "restored"
    receipt["hostKeys"]["distinctFromClonedBaseline"] = False
    if GUEST_PURPOSE in ("rotate", "rotate-publish"):
        receipt["cleanup"] = _observe_access(io, OPERATION_ID)
    else:
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
    io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                     "receipt": receipt})
    try:
        _drop_backups(io)
    except (GuestRefused, OSError):
        pass
    return _emit(io, receipt, binding)


def stat_record(st):
    if stat.S_ISLNK(st.st_mode):
        kind = "symlink"
    elif stat.S_ISREG(st.st_mode):
        kind = "regular"
    else:
        kind = "other"
    return {"kind": kind, "mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid, "size": st.st_size,
            "nlink": st.st_nlink}


class LinuxHost:
    """Rooted host operations. Paths are a fixed allowlist. Symlinks are not followed."""

    def __init__(self, root="/", guest_path=None, metadata=None, systemctl_fn=None, processes_fn=None, boot_id_fn=None,
                 passwd_fn=None, userdel_fn=None, keygen_fn=None):
        self.root, self.guest_path = root, guest_path or __file__
        self.metadata, self.systemctl_fn, self.processes_fn = metadata, systemctl_fn, processes_fn
        self.boot_id_fn, self.passwd_fn, self.userdel_fn, self.keygen_fn = boot_id_fn, passwd_fn, userdel_fn, keygen_fn

    def _real(self, path):
        if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
            raise GuestRefused("path")
        if self.root == "/":
            return path
        return self.root.rstrip("/") + path

    def _marker_dir(self):
        return self._real("/var/lib/roseos-yogi-recover/" + OPERATION_ID)

    def instance_identity(self):
        if self.metadata:
            return self.metadata("identity")
        def read(path):
            request = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/" + path,
                headers={"Metadata-Flavor": "Google"})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
                return response.read(4096).decode().strip()
        zone = read("instance/zone").rsplit("/", 1)[-1]
        return {"project": read("project/project-id"), "zone": zone, "instance": read("instance/name"),
                "instanceId": read("instance/id")}

    def binding(self):
        if self.metadata:
            return self.metadata("binding")
        request = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/attributes/roseos-yogi-recover-binding",
            headers={"Metadata-Flavor": "Google"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
            return json.loads(response.read(8192).decode())

    def self_sha256(self):
        digest = hashlib.sha256()
        with open(self.guest_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def boot_id(self):
        if self.boot_id_fn:
            return self.boot_id_fn()
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def marker_load(self):
        path = self._marker_dir() + "/state.json"
        if not os.path.lexists(path):
            return None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return {"phase": "corrupt", "operationId": OPERATION_ID, "nonce": NONCE}
        try:
            data = os.read(fd, 65537)
        finally:
            os.close(fd)
        if len(data) > 65536:
            return {"phase": "corrupt", "operationId": OPERATION_ID, "nonce": NONCE}
        try:
            return json.loads(data.decode())
        except (ValueError, UnicodeError):
            return {"phase": "corrupt", "operationId": OPERATION_ID, "nonce": NONCE}

    def marker_store(self, obj):
        directory = self._marker_dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        target = directory + "/state.json"
        temporary = directory + "/state.json.new"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, (canonical(obj) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temporary, target)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def prior_journal(self):
        """Count other operation markers that are not a readable known phase. Their bytes are not returned."""
        base = self._real("/var/lib/roseos-yogi-recover")
        unrecognized = 0
        allowed = {"started", "unit-done", "baseline", "refused", "partial", "complete"}
        try:
            names = os.listdir(base)
        except OSError:
            return 0
        for name in names:
            if unrecognized >= 20:
                break
            if name == OPERATION_ID or not HEX32.fullmatch(name):
                continue
            path = base + "/" + name + "/state.json"
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            except OSError:
                unrecognized += 1
                continue
            try:
                data = os.read(fd, 65537)
            finally:
                os.close(fd)
            if len(data) > 65536:
                unrecognized += 1
                continue
            try:
                marker = json.loads(data.decode())
            except (ValueError, UnicodeError):
                unrecognized += 1
                continue
            phase = marker.get("phase") if isinstance(marker, dict) else None
            if phase not in allowed:
                unrecognized += 1
        return unrecognized

    def _stat_path(self, path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            if error.errno == errno.ELOOP:
                return {"kind": "symlink", "mode": 0, "uid": -1, "gid": -1, "size": -1, "nlink": -1}
            if error.errno == errno.ENOENT:
                return None
            raise GuestRefused("stat") from None
        try:
            return stat_record(os.fstat(fd))
        finally:
            os.close(fd)

    def unit_stat(self):
        return self._stat_path(self._real(UNIT_PATH))

    def unit_chmod(self):
        try:
            fd = os.open(self._real(UNIT_PATH), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise GuestRefused("symlink") from None
            raise GuestRefused("chmod-precondition") from None
        try:
            record = stat_record(os.fstat(fd))
            if unit_problem(record):
                raise GuestRefused("chmod-precondition")
            os.fchmod(fd, UNIT_AFTER)
            return stat_record(os.fstat(fd))
        finally:
            os.close(fd)

    def runner(self):
        def verb(name):
            if self.systemctl_fn:
                return self.systemctl_fn(name)
            result = subprocess.run(["/bin/systemctl", name, UNIT_NAME], capture_output=True, text=True, timeout=20)
            text = (result.stdout or result.stderr).strip()
            return text.splitlines()[0] if text else "unknown"
        if self.processes_fn:
            process = bool(self.processes_fn())
        else:
            process = False
            for pid in os.listdir("/proc"):
                if not pid.isdigit():
                    continue
                try:
                    raw = open(f"/proc/{pid}/cmdline", "rb").read(4096)
                except OSError:
                    continue
                text = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
                if "Runner.Listener" in text or "Runner.Worker" in text:
                    process = True
                    break
        return {"active": verb("is-active"), "enabled": verb("is-enabled"), "process": process}

    def ssh_names(self):
        path = self._real("/etc/ssh")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            return sorted(os.listdir(fd))
        finally:
            os.close(fd)

    def _ssh_name(self, name):
        allowed = set(INVENTORY)
        for alg in ALGORITHMS:
            allowed.add(KEY_NAMES[alg] + ".roseos-new")
            allowed.add(KEY_NAMES[alg] + ".roseos-new.pub")
            if HEX32.fullmatch(OPERATION_ID):
                allowed.add(KEY_NAMES[alg] + ".roseos-prev-" + OPERATION_ID)
                allowed.add(KEY_NAMES[alg] + ".roseos-prev-" + OPERATION_ID + ".pub")
        if name not in allowed:
            raise GuestRefused("ssh-name")
        return name

    def _copy_ssh(self, source_name, dest_name, overwrite):
        source = self._real("/etc/ssh/" + self._ssh_name(source_name))
        dest = self._real("/etc/ssh/" + self._ssh_name(dest_name))
        if os.path.lexists(dest):
            info = os.lstat(dest)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise GuestRefused("prev-symlink")
            if not overwrite:
                return False
        try:
            src = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise GuestRefused("symlink") from None
            raise GuestRefused("ssh-file") from None
        try:
            info = os.fstat(src)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
                raise GuestRefused("ssh-file")
            data = os.read(src, info.st_size)
            mode = stat.S_IMODE(info.st_mode)
            uid, gid = info.st_uid, info.st_gid
        finally:
            os.close(src)
        if os.path.lexists(dest):
            out = os.open(dest, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC)
        else:
            out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
        try:
            os.write(out, data)
            os.fchmod(out, mode)
            try:
                os.fchown(out, uid, gid)
            except OSError:
                pass
            os.fsync(out)
        finally:
            os.close(out)
        return True

    def ssh_preserve(self, algorithm):
        if algorithm not in ALGORITHMS or not HEX32.fullmatch(OPERATION_ID):
            raise GuestRefused("algorithm")
        for suffix in ("", ".pub"):
            self._copy_ssh(KEY_NAMES[algorithm] + suffix,
                           KEY_NAMES[algorithm] + ".roseos-prev-" + OPERATION_ID + suffix, overwrite=False)

    def _read_regular(self, name, limit):
        fd = os.open(self._real("/etc/ssh/" + self._ssh_name(name)), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise GuestRefused("pair")
            return os.read(fd, info.st_size)
        finally:
            os.close(fd)

    def _pair_names(self, algorithm, place):
        base = KEY_NAMES[algorithm]
        if place == "live":
            return base, base + ".pub"
        backup = base + ".roseos-prev-" + OPERATION_ID
        return backup, backup + ".pub"

    def pair_status(self, algorithm, place):
        """match, mismatch, or absent. Private bytes are not returned."""
        if algorithm not in ALGORITHMS or not HEX32.fullmatch(OPERATION_ID):
            return "mismatch"
        private_name, public_name = self._pair_names(algorithm, place)
        private_path = self._real("/etc/ssh/" + private_name)
        public_path = self._real("/etc/ssh/" + public_name)
        private_exists = os.path.lexists(private_path)
        public_exists = os.path.lexists(public_path)
        if not private_exists and not public_exists:
            return "absent"
        if not private_exists or not public_exists:
            return "mismatch"
        try:
            private_type, private_fp = public_fingerprint_from_private(self._read_regular(private_name, 1 << 20))
            public_type, public_fp = parse_public(self.ssh_public(public_name))
        except (GuestRefused, OSError):
            return "mismatch"
        if private_type == public_type and private_fp == public_fp:
            return "match"
        return "mismatch"

    def pairs_agree(self, algorithm):
        """True when the live and backup public fingerprints are the same. No key bytes are returned."""
        _live_private, live_public = self._pair_names(algorithm, "live")
        _backup_private, backup_public = self._pair_names(algorithm, "backup")
        try:
            _live_type, live_fp = parse_public(self.ssh_public(live_public))
            _backup_type, backup_fp = parse_public(self.ssh_public(backup_public))
        except (GuestRefused, OSError):
            return False
        return live_fp == backup_fp

    def restore_pair(self, algorithm):
        """Copy both backup halves, private first. A single half is not copied."""
        if algorithm not in ALGORITHMS or not HEX32.fullmatch(OPERATION_ID):
            return False
        if self.pair_status(algorithm, "backup") != "match":
            return False
        private_name, public_name = self._pair_names(algorithm, "backup")
        live_private, live_public = self._pair_names(algorithm, "live")
        self._copy_ssh(private_name, live_private, overwrite=True)
        self._copy_ssh(public_name, live_public, overwrite=True)
        return self.pair_status(algorithm, "live") == "match"

    def ssh_restore(self, algorithm):
        return self.restore_pair(algorithm)

    def ssh_drop_prev_private(self, algorithm):
        if algorithm not in ALGORITHMS or not HEX32.fullmatch(OPERATION_ID):
            return
        path = self._real("/etc/ssh/" + KEY_NAMES[algorithm] + ".roseos-prev-" + OPERATION_ID)
        if not os.path.lexists(path):
            return
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise GuestRefused("prev-symlink")
        os.unlink(path)

    def ssh_stat(self, name):
        return self._stat_path(self._real("/etc/ssh/" + self._ssh_name(name)))

    def ssh_public(self, name):
        name = self._ssh_name(name)
        if not name.endswith(".pub"):
            raise GuestRefused("ssh-name")
        fd = os.open(self._real("/etc/ssh/" + name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            data = os.read(fd, 4096)
            if os.read(fd, 1):
                raise GuestRefused("public-size")
        finally:
            os.close(fd)
        return data.decode()

    def publish_hostkey_attributes(self, writes):
        """PUT only a public blob. The private key file is not opened."""
        if self.metadata:
            return bool(self.metadata("publish-hostkeys", writes))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key_type, blob in writes:
            if key_type not in PUBLIC_TYPES.values():
                raise GuestRefused("public-type")
            if not isinstance(blob, str) or not re.fullmatch(r"[A-Za-z0-9+/]+=*", blob):
                raise GuestRefused("public-blob")
            if any(token in blob for token in ("BEGIN", "PRIVATE", "OPENSSH", " ", "\n")):
                raise GuestRefused("private-attribute")
            request = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/hostkeys/" + key_type,
                data=blob.encode("ascii"), method="PUT", headers={"Metadata-Flavor": "Google"})
            try:
                with opener.open(request, timeout=5) as response:
                    response.read(256)
            except (OSError, urllib.error.URLError, ValueError):
                return False
        return True

    def ssh_generate(self, algorithm):
        if algorithm not in ALGORITHMS:
            raise GuestRefused("algorithm")
        private = self._real("/etc/ssh/" + KEY_NAMES[algorithm] + ".roseos-new")
        if os.path.lexists(private):
            raise GuestRefused("temp-exists")
        if self.keygen_fn:
            self.keygen_fn(algorithm, private)
        else:
            command = ["/usr/bin/ssh-keygen", "-q", "-t", algorithm, "-N", "", "-C", "roseos-yogi-recover", "-f", private]
            if algorithm == "ecdsa":
                command = ["/usr/bin/ssh-keygen", "-q", "-t", "ecdsa", "-b", "256", "-N", "", "-C",
                           "roseos-yogi-recover", "-f", private]
            elif algorithm == "rsa":
                command = ["/usr/bin/ssh-keygen", "-q", "-t", "rsa", "-b", "3072", "-N", "", "-C",
                           "roseos-yogi-recover", "-f", private]
            result = subprocess.run(command, capture_output=True, timeout=30)
            if result.returncode:
                raise GuestRefused("keygen")
        return self.ssh_public(KEY_NAMES[algorithm] + ".roseos-new.pub")

    def ssh_commit(self, algorithm):
        if algorithm not in ALGORITHMS:
            raise GuestRefused("algorithm")
        base = KEY_NAMES[algorithm]
        for suffix in ("", ".pub"):
            source = self._real("/etc/ssh/" + base + ".roseos-new" + suffix)
            dest = self._real("/etc/ssh/" + base + suffix)
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(source, dest)
        directory = os.open(self._real("/etc/ssh"), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def ssh_discard_temp(self):
        removed = skipped = 0
        for alg in ALGORITHMS:
            for suffix in ("", ".pub"):
                path = self._real("/etc/ssh/" + KEY_NAMES[alg] + ".roseos-new" + suffix)
                if not os.path.lexists(path):
                    continue
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode):
                    skipped += 1
                    continue
                if stat.S_ISREG(info.st_mode):
                    os.unlink(path)
                    removed += 1
                else:
                    skipped += 1
        return {"removed": removed, "skippedSymlink": skipped}

    def auth_lines(self, path):
        if path not in AUTH_PATHS:
            raise GuestRefused("auth-path")
        full = self._real(path)
        if not os.path.lexists(full):
            return None
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise GuestRefused("auth-symlink") from None
            raise GuestRefused("auth-open") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
                raise GuestRefused("auth-file")
            data = os.read(fd, info.st_size)
        finally:
            os.close(fd)
        return data.decode().splitlines()

    def auth_write(self, path, lines):
        if path not in AUTH_PATHS or not HEX32.fullmatch(OPERATION_ID):
            raise GuestRefused("auth-path")
        full = self._real(path)
        evidence = full + ".roseos-prev-" + OPERATION_ID
        if os.path.lexists(evidence):
            info = os.lstat(evidence)
            if not stat.S_ISREG(info.st_mode):
                raise GuestRefused("auth-symlink")
        else:
            src = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(src)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
                    raise GuestRefused("auth-file")
                original = os.read(src, info.st_size)
                mode = stat.S_IMODE(info.st_mode)
            finally:
                os.close(src)
            out = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
            try:
                os.write(out, original)
                os.fsync(out)
            finally:
                os.close(out)
        payload = ("\n".join(lines) + "\n").encode()
        temporary = full + ".roseos-new-" + OPERATION_ID
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(out, payload)
            os.fsync(out)
        finally:
            os.close(out)
        os.rename(temporary, full)

    def account(self):
        if self.passwd_fn:
            return self.passwd_fn(TEMP_ACCOUNT)
        path = self._real("/etc/passwd")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            raise GuestRefused("passwd") from None
        try:
            data = os.read(fd, 1 << 20).decode()
        finally:
            os.close(fd)
        for line in data.splitlines():
            parts = line.split(":")
            if len(parts) >= 7 and parts[0] == TEMP_ACCOUNT:
                return {"name": parts[0], "uid": int(parts[2]), "gid": int(parts[3]), "home": parts[5],
                        "shell": parts[6]}
        return None

    def account_remove(self):
        entry = self.account()
        if not account_removable(entry):
            raise GuestRefused("account")
        if self.userdel_fn:
            self.userdel_fn(TEMP_ACCOUNT)
        else:
            result = subprocess.run(["/usr/sbin/userdel", "--", TEMP_ACCOUNT], capture_output=True, timeout=30)
            if result.returncode:
                raise GuestRefused("userdel")
        leftover = False
        auth = self._real(TEMP_HOME + "/.ssh/authorized_keys")
        if os.path.lexists(auth):
            info = os.lstat(auth)
            if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                os.unlink(auth)
            else:
                leftover = True
        for directory in (TEMP_HOME + "/.ssh", TEMP_HOME):
            try:
                os.rmdir(self._real(directory))
            except OSError:
                leftover = True
        return "removed-home-leftover" if leftover else "removed"

    def emit(self, line):
        """One stdout line. The startup-script runner already copies stdout to the serial console.

        A second write to the serial device made parse_serial see two receipts and reject the sequence.
        The same public line is appended to this operation's local journal for a later inspect.
        """
        if "\n" in line or not line.startswith(PREFIX) or len(line) > 8000:
            raise GuestRefused("receipt-line")
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        try:
            directory = self._marker_dir()
            if not os.path.isdir(directory):
                return
            fd = os.open(directory + "/journal.log",
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError:
            return
        try:
            os.write(fd, (line + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)


INSPECT_TEMPLATE = r'''#!/usr/bin/env python3
"""Read-only Yogi replacement inventory. It does not change mode, keys, accounts, or authorized_keys."""
import base64
import errno
import hashlib
import json
import os
import re
import stat
import sys
import urllib.request

OPERATION_ID = "@@INSPECT_OP@@"
NONCE = "@@INSPECT_NONCE@@"
PREFIX = "ROSEOS-YOGI-RECOVER-RECEIPT "
PROJECT = "protocols-465723"
ZONE = "us-west2-b"
INSTANCE = "roseos-yogi-builder-20261105-b"
INSTANCE_ID = "2720796557371812468"
UNIT_PATH = "/etc/systemd/system/actions.runner.Immortal-Protocols-RoseOS-1.roseos-yogi-builder-20261105-b.service"
UNIT_NAME = "actions.runner.Immortal-Protocols-RoseOS-1.roseos-yogi-builder-20261105-b.service"
UNIT_SIZE = 347
PUBLIC = {"ed25519": "ssh-ed25519", "ecdsa": "ecdsa-sha2-nistp256", "rsa": "ssh-rsa"}
NAMES = {"ed25519": "ssh_host_ed25519_key.pub", "ecdsa": "ssh_host_ecdsa_key.pub", "rsa": "ssh_host_rsa_key.pub"}
INVENTORY = (
    "ssh_host_ed25519_key", "ssh_host_ed25519_key.pub", "ssh_host_ecdsa_key", "ssh_host_ecdsa_key.pub",
    "ssh_host_rsa_key", "ssh_host_rsa_key.pub", "ssh_host_dsa_key", "ssh_host_dsa_key.pub",
)
AUTH = ("/root/.ssh/authorized_keys", "/home/pixelbuild/.ssh/authorized_keys")
ACCOUNT = "roseos-yogi-recover"


def under(root, path):
    if not path.startswith("/") or ".." in path.split("/"):
        raise OSError("path")
    return path if root == "/" else root.rstrip("/") + path


def read_file(root, path, limit):
    fd = os.open(under(root, path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise OSError("file")
        return os.read(fd, info.st_size), info
    finally:
        os.close(fd)


def meta_text(path):
    request = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/" + path,
                                     headers={"Metadata-Flavor": "Google"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
        return response.read(8192).decode()


def fingerprint(line):
    parts = line.strip().split()
    if len(parts) < 2 or parts[0] not in PUBLIC.values():
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
        length = int.from_bytes(blob[:4], "big")
        embedded = blob[4:4 + length].decode()
    except (ValueError, UnicodeError, IndexError):
        return None
    if embedded != parts[0] or not 8 <= len(blob) <= 4096:
        return None
    value = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return parts[0], value


def emit(receipt):
    line = PREFIX + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def journal_summary(root):
    """Count local markers. The receipt does not copy marker bytes."""
    phases = []
    entries = readable = unrecognized = 0
    complete = False
    allowed = {"started", "unit-done", "baseline", "refused", "partial", "complete"}
    try:
        names = sorted(os.listdir(under(root, "/var/lib/roseos-yogi-recover")))
    except OSError:
        names = []
    for name in names:
        if entries >= 20 or not re.fullmatch(r"[0-9a-f]{32}", name):
            continue
        entries += 1
        path = under(root, "/var/lib/roseos-yogi-recover") + "/" + name + "/state.json"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            unrecognized += 1
            continue
        try:
            data = os.read(fd, 65537)
        finally:
            os.close(fd)
        if len(data) > 65536:
            unrecognized += 1
            continue
        try:
            marker = json.loads(data.decode())
        except (ValueError, UnicodeError):
            unrecognized += 1
            continue
        readable += 1
        phase = marker.get("phase") if isinstance(marker, dict) else None
        if phase in allowed:
            phases.append(phase)
        else:
            phases.append("unrecognized")
            unrecognized += 1
        stored = marker.get("receipt") if isinstance(marker, dict) else None
        if phase == "complete" and isinstance(stored, dict) and stored.get("status") == "complete":
            complete = True
    return {"entries": entries, "readable": readable, "phases": phases[:20],
            "hasCompleteReceipt": complete, "unrecognized": min(unrecognized, 20)}


def _unit_token(root, verb, name, allowed):
    if root != "/":
        return "unknown"
    try:
        import subprocess
        result = subprocess.run(["/bin/systemctl", verb, name], capture_output=True, text=True, timeout=10)
        text = (result.stdout or "").strip().splitlines()
        token = text[0] if text else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return token if token in allowed else "unknown"


def read_proc_table(root, name):
    """Read one proc net table. None means this file was not read.

    /proc/net is a symlink and its files report size 0. The final component is opened
    with O_NOFOLLOW and, on the live root only, a zero-size regular file is read up to
    a cap. That exception is not applied to read_file.
    """
    if name not in ("tcp", "tcp6"):
        return None
    if root == "/":
        path = "/proc/" + str(os.getpid()) + "/net/" + name
    else:
        path = under(root, "/proc/net/" + name)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > (1 << 20):
            return None
        if root == "/" and info.st_size == 0:
            return os.read(fd, 1 << 20)
        return os.read(fd, info.st_size)
    finally:
        os.close(fd)


def _listen_addresses(root):
    found = []
    read_ok = 0
    for name in ("tcp", "tcp6"):
        data = read_proc_table(root, name)
        if data is None:
            continue
        read_ok += 1
        for raw in data.decode(errors="replace").splitlines()[1:]:
            parts = raw.split()
            if len(parts) < 4 or parts[3] != "0A" or ":" not in parts[1]:
                continue
            address, port = parts[1].split(":", 1)
            if port.lower() != "0016":
                continue
            if name == "tcp" and len(address) == 8:
                try:
                    raw_ip = bytes.fromhex(address)
                except ValueError:
                    continue
                shown = ".".join(str(b) for b in raw_ip[::-1]) + ":22"
            elif name == "tcp6" and len(address) == 32:
                shown = "[ipv6]:22" if set(address) != {"0"} else "[::]:22"
            else:
                continue
            if shown not in found and len(found) < 4:
                found.append(shown)
    return found, read_ok


def _config_class(text):
    port, listen, password, permit, pubkey, matches = "absent", "absent", "unset", "unset", "unset", 0
    blocked = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0].lower()
        value = parts[1].lower() if len(parts) > 1 else ""
        if key == "include":
            continue
        if key == "match":
            matches += 1
            blocked = True
            continue
        if blocked:
            continue
        if key == "port":
            port = "22" if value == "22" else "other"
        elif key == "listenaddress":
            if value in ("0.0.0.0", "::", "*", "[::]"):
                listen = "any"
            elif value in ("127.0.0.1", "::1", "localhost"):
                listen = "localhost"
            else:
                listen = "other"
        elif key == "passwordauthentication":
            password = value if value in ("yes", "no") else "other"
        elif key == "permitrootlogin":
            permit = value if value in ("yes", "no", "prohibit-password") else "other"
        elif key == "pubkeyauthentication":
            pubkey = value if value in ("yes", "no") else "other"
    if password == "other":
        password = "unset"
    if permit == "other":
        permit = "unset"
    if pubkey == "other":
        pubkey = "unset"
    return {"scope": "root-file-static", "port": port, "listen": listen, "passwordAuth": password,
            "permitRoot": permit, "pubkey": pubkey, "matchBlocks": min(matches, 20)}


def read_services(root):
    """Read-only service facts. sshd_config scope is the root file only, not the effective config.

    Include drop-ins and Match blocks are not applied. No service is started and no key is read.
    """
    active = {"active", "inactive", "failed", "unknown", "activating"}
    enabled = {"enabled", "disabled", "static", "masked", "indirect", "unknown"}
    def unit(name):
        return {"active": _unit_token(root, "is-active", name, active),
                "enabled": _unit_token(root, "is-enabled", name, enabled)}
    listen, read_ok = _listen_addresses(root)
    try:
        data, _info = read_file(root, "/etc/ssh/sshd_config", 1 << 16)
        config = _config_class(data.decode(errors="replace"))
    except OSError:
        config = {"scope": "root-file-static", "port": "unreadable", "listen": "unreadable",
                  "passwordAuth": "unreadable", "permitRoot": "unreadable", "pubkey": "unreadable",
                  "matchBlocks": 0}
    if listen:
        port22 = "listening"
    elif read_ok == 2:
        port22 = "absent"
    else:
        port22 = "unreadable"
    return {"ssh": unit("ssh.service"), "sshd": unit("sshd.service"),
            "guestAgent": unit("google-guest-agent.service"), "listen": listen,
            "port22": port22, "config": config}


def main(argv):
    root = "/"
    binding = identity = boot = None
    args = argv[1:]
    if args[:1] == ["--root"]:
        root = args[1]
        binding = json.loads(open(args[2], encoding="utf-8").read())
        identity = json.loads(open(args[3], encoding="utf-8").read())
        boot = args[4]
    elif args == ["--guest"]:
        binding = json.loads(meta_text("instance/attributes/roseos-yogi-recover-binding"))
        identity = {"project": meta_text("project/project-id").strip(),
                    "zone": meta_text("instance/zone").strip().rsplit("/", 1)[-1],
                    "instance": meta_text("instance/name").strip(),
                    "instanceId": meta_text("instance/id").strip()}
        boot = open("/proc/sys/kernel/random/boot_id", encoding="utf-8").read().strip()
    else:
        return 2
    own = hashlib.sha256(open(__file__, "rb").read()).hexdigest()
    if binding.get("operationId") != OPERATION_ID or binding.get("nonce") != NONCE or binding.get("payloadSha256") != own:
        return 1
    expected = {"project": PROJECT, "zone": ZONE, "instance": INSTANCE, "instanceId": INSTANCE_ID}
    problem = "none"
    before = {"kind": "missing", "mode": 0, "uid": -1, "gid": -1, "size": -1, "nlink": -1}
    try:
        fd = os.open(under(root, UNIT_PATH), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        os.close(fd)
        kind = "regular" if stat.S_ISREG(info.st_mode) else "other"
        before = {"kind": kind, "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
                  "size": info.st_size, "nlink": info.st_nlink}
    except OSError as error:
        problem = "symlink" if error.errno == errno.ELOOP else "missing"
    if problem == "none":
        if before["kind"] != "regular":
            problem = "kind"
        elif before["nlink"] != 1:
            problem = "nlink"
        elif before["uid"] != 0 or before["gid"] != 0:
            problem = "owner"
        elif before["size"] != UNIT_SIZE:
            problem = "size"
        elif before["mode"] == 0o644:
            problem = "already-0644"
        elif before["mode"] != 0o664:
            problem = "mode"
    runner = {"active": "unchecked", "enabled": "unchecked", "process": False}
    if identity != expected:
        problem = "identity"
    else:
        try:
            def verb(name):
                import subprocess
                result = subprocess.run(["/bin/systemctl", name, UNIT_NAME], capture_output=True, text=True, timeout=20)
                text = (result.stdout or result.stderr).strip()
                return text.splitlines()[0] if text else "unknown"
            process = False
            for pid in os.listdir("/proc" if root == "/" else under(root, "/proc")):
                if not str(pid).isdigit():
                    continue
                try:
                    raw = open(("/proc/" if root == "/" else under(root, "/proc/")) + str(pid) + "/cmdline", "rb").read(4096)
                except OSError:
                    continue
                text = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
                if "Runner.Listener" in text or "Runner.Worker" in text:
                    process = True
            active = verb("is-active") if root == "/" else "inactive"
            enabled = verb("is-enabled") if root == "/" else "disabled"
            if active not in ("inactive", "active", "failed", "unchecked", "activating"):
                active = "failed"
            if enabled not in ("disabled", "enabled", "static", "masked", "indirect", "unchecked"):
                enabled = "enabled"
            runner = {"active": active, "enabled": enabled, "process": process}
        except OSError:
            runner = {"active": "unchecked", "enabled": "unchecked", "process": False}
    baseline = {}
    unknown = 0
    try:
        names = os.listdir(under(root, "/etc/ssh"))
    except OSError:
        names = []
    for name in names:
        if name.startswith("ssh_host_") and name not in INVENTORY and ".roseos-prev-" not in name and ".roseos-new" not in name:
            unknown += 1
    for alg, name in NAMES.items():
        try:
            data, _info = read_file(root, "/etc/ssh/" + name, 4096)
            parsed = fingerprint(data.decode())
        except (OSError, UnicodeError):
            continue
        if parsed and parsed[0] == PUBLIC[alg]:
            baseline[alg] = parsed[1]
    unidentified = possess = paths = 0
    needle = "@@POSSESS_COMMENT@@"
    material = "@@POSSESS_MATERIAL@@"
    unreadable = False
    for path in AUTH:
        try:
            data, _info = read_file(root, path, 1 << 20)
        except OSError as error:
            if getattr(error, "errno", None) == errno.ENOENT:
                paths += 1
                continue
            unreadable = True
            continue
        paths += 1
        for line in data.decode(errors="replace").splitlines():
            parts = line.split()
            if line and not line.startswith("#") and not (len(parts) == 3 and parts[2] == "roseos-yogi-recover-" + OPERATION_ID):
                unidentified += 1
            comment_hit = bool(needle) and needle in line
            blob_hit = bool(material) and material != "roseos-yogi-possess-material-unset" and material in line
            if comment_hit or blob_hit:
                possess += 1
    if unreadable or paths != len(AUTH):
        possess = -1
    account = "absent"
    try:
        data, _info = read_file(root, "/etc/passwd", 1 << 20)
        for line in data.decode(errors="replace").splitlines():
            if line.startswith(ACCOUNT + ":"):
                account = "preserved-mismatch"
    except OSError:
        pass
    mode = f"{before['mode'] & 0o777:04o}" if problem != "missing" else "unknown"
    receipt = {
        "schema": "roseos.yogi-replacement-recover-receipt.v1", "purpose": "inspect",
        "operationId": OPERATION_ID, "nonce": NONCE, "payloadSha256": own, "instanceId": INSTANCE_ID,
        "bootId": boot, "reexecution": False, "previousBootId": "none", "originalVmAccessed": False,
        "snapshotsUsed": False, "status": "refused" if problem == "identity" else "complete",
        "unit": {"action": "not-modified", "problem": problem, "beforeMode": mode, "beforeSize": before["size"],
                 "beforeUid": before["uid"], "beforeGid": before["gid"], "afterMode": mode,
                 "afterSize": before["size"], "afterUid": before["uid"], "afterGid": before["gid"]},
        "runner": runner,
        "hostKeys": {"baseline": baseline, "rotated": {}, "distinctFromClonedBaseline": False,
                     "unknownHostKeyCount": unknown, "rolledBack": False},
        "cleanup": {"linesRemoved": 0, "unidentifiedAuthorizedKeyLines": unidentified,
                    "temporaryAccount": account, "tempFilesRemoved": 0,
                    "accessCleanup": "failed" if unreadable or paths != len(AUTH) else "ok",
                    "possessMarkerLines": possess, "authPathsRead": paths, "possessComment": needle,
                    "possessMaterial": material},
        "journal": journal_summary(root),
        "services": read_services(root),
    }
    emit(receipt)
    return 0 if problem == "none" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def inspect_template():
    if any(token in INSPECT_TEMPLATE for token in ("ssh-keygen", "userdel", "fchmod", "os.chmod", "BEGIN OPENSSH")):
        raise GuestRefused("inspect template is not read-only")
    if INSPECT_TEMPLATE.count("@@INSPECT_OP@@") != 1 or INSPECT_TEMPLATE.count("@@INSPECT_NONCE@@") != 1:
        raise GuestRefused("inspect template placeholders are not unique")
    if INSPECT_TEMPLATE.count("@@POSSESS_COMMENT@@") != 1 or INSPECT_TEMPLATE.count("@@POSSESS_MATERIAL@@") != 1:
        raise GuestRefused("inspect template placeholders are not unique")
    return INSPECT_TEMPLATE


def main(argv):
    if argv != ["--guest"]:
        print("usage: yogi_replacement_recover_guest.py --guest", file=sys.stderr)
        return 2
    if not HEX32.fullmatch(OPERATION_ID) or not HEX32.fullmatch(NONCE):
        print("unrendered guest template", file=sys.stderr)
        return 1
    try:
        receipt = run(LinuxHost(guest_path=__file__))
    except GuestRefused:
        return 1
    if not receipt or receipt.get("status") != "complete":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
