#!/usr/bin/env python3
"""Guest half of the Yogi replacement-builder recovery.

The control plane renders this file once per fresh operation, replacing the operation-id and nonce placeholders, and sends that rendered
text as the instance startup-script. This template is not a spent binding. It does not SSH, scan host keys, read the
original VM, or restore a snapshot.

On the guest it may chmod one unit file when every precondition matches, rotate three named host keys, and remove one
exact authorized_keys comment plus one exact temporary account. It writes a single public receipt line.
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
TOP_KEYS = {
    "schema", "operationId", "nonce", "payloadSha256", "instanceId", "bootId", "reexecution", "previousBootId",
    "originalVmAccessed", "snapshotsUsed", "status", "unit", "runner", "hostKeys", "cleanup",
}
UNIT_KEYS = {
    "action", "problem", "beforeMode", "beforeSize", "beforeUid", "beforeGid", "afterMode", "afterSize", "afterUid",
    "afterGid",
}
UNIT_PROBLEMS = {
    "none", "missing", "symlink", "kind", "mode", "owner", "size", "nlink", "runner-active", "runner-enabled",
    "runner-process", "chmod", "identity", "unknown-host-key", "baseline", "rotate-same", "rotate-partial",
    "reexecution-partial", "cleanup",
}
ACCOUNT_STATES = {"absent", "removed", "preserved-mismatch", "remove-failed", "removed-home-leftover"}
FORBIDDEN = ("BEGIN ", "PRIVATE", "OPENSSH", "-----")


class GuestRefused(Exception):
    pass


class Malformed(Exception):
    pass


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


def _mode_token(value):
    return value == "unknown" or bool(re.fullmatch(r"[0-7]{4}", value or ""))


def _fp_map(value):
    if not isinstance(value, dict) or set(value) - set(ALGORITHMS):
        return False
    return all(isinstance(item, str) and FP.fullmatch(item) for item in value.values())


def validate_shape(receipt, binding):
    """Structural gate for a receipt. A well-formed lie can still pass; this only rejects the wrong shape."""
    problems = binding_problems(binding)
    if not isinstance(receipt, dict) or set(receipt) != TOP_KEYS:
        return ["receipt-keys"]
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
            "linesRemoved", "unidentifiedAuthorizedKeyLines", "temporaryAccount", "tempFilesRemoved", "accessCleanup"}:
        return ["cleanup"]
    if cleanup["temporaryAccount"] not in ACCOUNT_STATES or cleanup["accessCleanup"] not in ("ok", "failed"):
        return ["cleanup"]
    for key in ("linesRemoved", "unidentifiedAuthorizedKeyLines", "tempFilesRemoved"):
        if not isinstance(cleanup[key], int) or cleanup[key] < 0 or cleanup[key] > 100000:
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
    if receipt["status"] != "complete" or unit["action"] != "chmod-0644" or unit["problem"] != "none":
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


def parse_serial(contents, binding):
    found = []
    for line in str(contents).splitlines():
        idx = line.find(PREFIX)
        if idx < 0:
            continue
        payload = line[idx + len(PREFIX):].strip()
        if len(payload) > 8000:
            raise Malformed("oversize")
        try:
            receipt = json.loads(payload)
        except ValueError:
            raise Malformed("json") from None
        if validate_shape(receipt, binding):
            raise Malformed("shape")
        found.append(receipt)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    if len(found) == 2 and found[0]["reexecution"] is False and found[1]["reexecution"] is True \
            and found[0]["hostKeys"]["baseline"] == found[1]["hostKeys"]["baseline"] \
            and found[0]["hostKeys"]["rotated"] == found[1]["hostKeys"]["rotated"]:
        return found[1]
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
    if record.get("mode") != UNIT_BEFORE:
        return "mode"
    if record.get("size") != UNIT_SIZE:
        return "size"
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
        "schema": RECEIPT_SCHEMA, "operationId": binding["operationId"], "nonce": binding["nonce"],
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
                    "tempFilesRemoved": 0, "accessCleanup": "ok"},
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


def cleanup_access(io, operation_id):
    removed = unidentified = 0
    failed = False
    for path in AUTH_PATHS:
        try:
            lines = io.auth_lines(path)
        except (GuestRefused, OSError):
            failed = True
            continue
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
    return {"linesRemoved": removed, "unidentifiedAuthorizedKeyLines": unidentified,
            "temporaryAccount": account_state, "tempFilesRemoved": temp_removed,
            "accessCleanup": "failed" if failed else "ok"}


def _emit(io, receipt, binding):
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


def run(io):
    """One guest pass. A marker other than a finished complete receipt never continues a rotation."""
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
        rolled = _rollback(io)
        receipt["status"] = "partial"
        receipt["unit"]["problem"] = code
        receipt["hostKeys"]["baseline"] = baseline
        receipt["hostKeys"]["rotated"] = {}
        receipt["hostKeys"]["rolledBack"] = rolled
        receipt["hostKeys"]["distinctFromClonedBaseline"] = False
        receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
        io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                         "receipt": receipt})
        return _emit(io, receipt, binding)
    for alg in ALGORITHMS:
        io.ssh_drop_prev_private(alg)
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
    return _emit(io, receipt, binding)


def _rollback(io):
    """Copy any preserved previous keys back over the live names. Previous public files stay."""
    rolled = False
    for alg in ALGORITHMS:
        try:
            if io.ssh_restore(alg):
                rolled = True
            io.ssh_drop_prev_private(alg)
        except (GuestRefused, OSError):
            rolled = True
    return rolled


def _rerun(io, marker, binding, boot):
    if not isinstance(marker, dict) or marker.get("operationId") != OPERATION_ID or marker.get("nonce") != NONCE:
        return None
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
            for alg in ALGORITHMS:
                try:
                    io.ssh_drop_prev_private(alg)
                except (GuestRefused, OSError):
                    receipt["status"] = "partial"
            if int(discarded.get("skippedSymlink") or 0) > 0:
                receipt["status"] = "partial"
                receipt["cleanup"] = dict(receipt["cleanup"], accessCleanup="failed")
            return _emit(io, receipt, binding)
    # A crash or reboot must not generate another key. Restore any preserved previous pair.
    rolled = _rollback(io)
    try:
        io.ssh_discard_temp()
    except (GuestRefused, OSError):
        rolled = True
    receipt = _skeleton(binding, boot, "partial")
    receipt["reexecution"] = False
    receipt["unit"]["problem"] = "reexecution-partial"
    baseline = marker.get("baseline") if isinstance(marker.get("baseline"), dict) else {}
    receipt["hostKeys"]["baseline"] = {k: v for k, v in baseline.items() if k in ALGORITHMS and isinstance(v, str) and FP.fullmatch(v)}
    receipt["hostKeys"]["rotated"] = {}
    receipt["hostKeys"]["rolledBack"] = rolled
    receipt["hostKeys"]["distinctFromClonedBaseline"] = False
    receipt["cleanup"] = cleanup_access(io, OPERATION_ID)
    io.marker_store({"phase": "partial", "operationId": OPERATION_ID, "nonce": NONCE, "baseline": baseline,
                     "receipt": receipt})
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

    def ssh_restore(self, algorithm):
        if algorithm not in ALGORITHMS or not HEX32.fullmatch(OPERATION_ID):
            return False
        restored = False
        for suffix in ("", ".pub"):
            prev = KEY_NAMES[algorithm] + ".roseos-prev-" + OPERATION_ID + suffix
            if not os.path.lexists(self._real("/etc/ssh/" + prev)):
                continue
            self._copy_ssh(prev, KEY_NAMES[algorithm] + suffix, overwrite=True)
            restored = True
        return restored

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
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        try:
            fd = os.open("/dev/ttyS0", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)


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
