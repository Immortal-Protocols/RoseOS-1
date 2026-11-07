#!/usr/bin/env python3
"""Fail-closed recovery plan for the Yogi replacement builder only.

Offline by default. `plan` writes a fresh operation and does not call a cloud API. `preflight`, `apply` and `cleanup`
contact the control plane only with `--execute` on the operator computer. This module does not SSH, scan host keys,
change project metadata or IAM, restore a snapshot, or start the original VM.

The guest receipt is evidence the procedure asked for. It is not a trusted host-key pin: the guest writes the serial
line, and this repository has no trusted fingerprint of that guest. Historical proposal hashes are reference
identifiers from an unrecovered packet. They are not bindings and were not recomputed here.

Exit status: 0 procedure finished or dry-run, 1 refused or halted, 2 usage.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import yogi_replacement_recover_guest as guest  # noqa: E402

PROJECT = "protocols-465723"
ZONE = "us-west2-b"
INSTANCE = "roseos-yogi-builder-20261105-b"
INSTANCE_ID = "2720796557371812468"
DISK_NAME = "roseos-yogi-builder-20261105-b"
DISK_ID = "938743673401685238"
DISK_SIZE_GB = "2000"
ORIGINAL_ZONE = "us-west2-a"
ORIGINAL_INSTANCE = "roseos-yogi-builder-20261103-a"
ORIGINAL_INSTANCE_ID = "8812446529823929967"
ORIGINAL_DISK_ID = "6647020458376394351"
PRESERVE_SNAPSHOT = "yogi-preserve-20261105-2252"
PRESERVE_SNAPSHOT_ID = "8970504972743521862"
PREKEY_SNAPSHOT = "yogi-b-prekey-20261107-030026"
PREKEY_SNAPSHOT_ID = "1721727283888335524"
OPERATOR_ACCOUNT = "grok-bots@protocols-465723.iam.gserviceaccount.com"
IAP_RANGE = "35.235.240.0/20"
BINDING_KEY = "roseos-yogi-recover-binding"
BASE_METADATA = {"enable-oslogin": "FALSE", "block-project-ssh-keys": "TRUE"}
BASE_ORDER = ("enable-oslogin", "block-project-ssh-keys")
OUR_KEYS = ("startup-script", BINDING_KEY)
POLICY_SECONDS = 45 * 60
CLEANUP_RESERVE_SECONDS = 10 * 60
WORK_BUDGET_SECONDS = POLICY_SECONDS - CLEANUP_RESERVE_SECONDS
SERIAL_DELAYS = (180,)
DOCS = {
    "setMetadata": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/setMetadata",
    "metadataKeepExisting": "https://docs.cloud.google.com/compute/docs/metadata/setting-custom-metadata",
    "startupScript": "https://docs.cloud.google.com/compute/docs/instances/startup-scripts/linux",
    "serialPort": "https://docs.cloud.google.com/compute/docs/troubleshooting/viewing-serial-port-output",
    "guestAttributes": "https://docs.cloud.google.com/compute/docs/metadata/manage-guest-attributes",
    "maxRunDuration": "https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime",
    "scheduling": "https://docs.cloud.google.com/compute/docs/instances/setting-instance-scheduling-options",
}
PLAN_TTL = datetime.timedelta(hours=12)
PREFLIGHT_TTL = datetime.timedelta(minutes=20)
METADATA_URL = ("https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/instances/"
                "roseos-yogi-builder-20261105-b/setMetadata")
PLAN_SCHEMA = "roseos.yogi-replacement-recover-plan.v1"
PREFLIGHT_SCHEMA = "roseos.yogi-replacement-recover-preflight.v1"
OUTCOME_SCHEMA = "roseos.yogi-replacement-recover-outcome.v1"
GITHUB_CONTEXT = ("GITHUB_ACTIONS", "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                  "ACTIONS_RUNTIME_TOKEN", "ACTIONS_RUNTIME_URL", "ACTIONS_CACHE_URL", "GITHUB_TOKEN")
OPERATOR = re.compile(r"[A-Za-z0-9 ._@-]{1,80}")
HEX32 = re.compile(r"[0-9a-f]{32}")
SHA256 = re.compile(r"[0-9a-f]{64}")
DISK_NAME_RE = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
FINGERPRINT_RE = re.compile(r"[A-Za-z0-9+/=_-]{1,128}")
COMMIT = re.compile(r"[0-9a-f]{40}")
LIMITATIONS = (
    "internal-error-cause-unknown",
    "no-trusted-guest-fingerprint",
    "cloned-baseline-is-the-replacement-disk",
    "historical-packet-not-retrieved",
    "unidentified-authorized-keys-are-preserved",
    "snapshot-restore-is-not-performed",
    "ssh-is-not-used",
    "guest-attributes-are-not-trusted",
    "serial-output-is-guest-written",
    "unknown-host-key-names-stop-rotation",
    "unit-hardlink-or-unexpected-mode-is-not-changed",
    "metadata-write-is-single-attempt",
    "automatic-restart-can-reexecute-until-stop",
)
HISTORICAL = {
    "schema": "roseos.yogi-replacement-recover-historical-reference.v1",
    "relay": "Chief, relayed by Saba",
    "slackTs": "1794022474.195909",
    "location": "former operator Mac, reported off",
    "retrieval": "unconfirmed",
    "hashStatus": "recorded-from-relay-not-recomputed",
    "contentStatus": "not-reviewed",
    "operatorReactivated": False,
    "role": "historical reference identifiers; not bindings and not inputs of this tool",
    "base": "/Users/nathanspaun/Documents/Codex/2026-10-27/task-3/yogi-setup-20261105/replacement-b-001/",
    "files": (
        {"path": "inspection-proposal-v3/inspect_guest.py",
         "sha256": "c9d612938c1d13f7febd88a3a68c4863adfaee855ce97a1d0657e1b10acc3b5c"},
        {"path": "inspection-proposal-v3/inspection_contract.py",
         "sha256": "36b9d271c07b6987ed1a8dc48a3f3d05b7cd89a7dda66236d7a8872f12d6fd1b"},
        {"path": "inspection-adapter-v4/adapter.py",
         "sha256": "f842121b35a079697dc3484caca576088f71e00004ec893ba7557d9b5f404ebe"},
        {"path": "inspection-adapter-v4/inspection_core.py",
         "sha256": "2ae1d58dd0f212198bb4fc81432c20df7cfbad6619cc8936c4b07d8eda65cfe8"},
        {"path": "hostkey-rotation-proposal-v3/rotate_guest.py",
         "sha256": "624dd95c7bfc7f3a671628746257bcf798111f88de78665a8710cc8b1489db73"},
        {"path": "yogi-identity-bound-v4.tar.gz",
         "sha256": "8608f2c2c8315f648dc7d3fa0fbcbbff393911d748b4c9a9b264b2e0f3ff58bf",
         "indexSha256": "21677d17c97dc9d2d045392dba4d4db703855b0b49e095d96488493fd3495e02",
         "indexFiles": 38},
    ),
}


class Refused(Exception):
    pass


class TransportError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def manual_context(env):
    found = sorted(key for key in GITHUB_CONTEXT if key in env)
    if found:
        raise Refused("recovery is manual operator only and refuses inside a GitHub Actions job")
    if env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise Refused("GOOGLE_APPLICATION_CREDENTIALS is set; use the active gcloud account and do not pass a key path")
    if env.get("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"):
        raise Refused("service-account impersonation is set; the active gcloud account must be the operator account")


def historical_hashes():
    found = set()
    for row in HISTORICAL["files"]:
        found.add(row["sha256"])
        if row.get("indexSha256"):
            found.add(row["indexSha256"])
    return found


def reject_historical(operation_id, nonce, payload_sha):
    banned = historical_hashes()
    if operation_id in banned or nonce in banned or payload_sha in banned:
        raise Refused("historical identifier is not a binding")


def binding_canonical(operation_id, nonce, payload_sha):
    return json.dumps({"nonce": nonce, "operationId": operation_id, "payloadSha256": payload_sha,
                       "schema": guest.BINDING_SCHEMA}, sort_keys=True, separators=(",", ":"))


def render_guest(operation_id, nonce, template):
    if template.count("@@OP@@") != 1 or template.count("@@NONCE@@") != 1:
        raise Refused("guest template placeholders are not unique")
    if not HEX32.fullmatch(operation_id or "") or not HEX32.fullmatch(nonce or "") or operation_id == nonce:
        raise Refused("binding generator failed")
    rendered = template.replace("@@OP@@", operation_id).replace("@@NONCE@@", nonce)
    if "@@" in rendered:
        raise Refused("guest template placeholder remains")
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def startup_script(guest_text, guest_sha, operation_id):
    if "\nEND_GUEST\n" in "\n" + guest_text or guest_text.startswith("END_GUEST"):
        raise Refused("guest text collides with the heredoc sentinel")
    if not guest_text.endswith("\n"):
        guest_text += "\n"
    lines = [
        "#!/bin/bash",
        "# roseos yogi replacement recovery. Reviewed payload. No download and no ssh.",
        "set -eu",
        "set -o pipefail",
        "umask 077",
        f"op={shlex.quote(operation_id)}",
        '/usr/bin/install -d -m 0700 -- "/var/lib/roseos-yogi-recover/$op"',
        'cat > "/var/lib/roseos-yogi-recover/$op/guest.py" <<\'END_GUEST\'',
        guest_text.rstrip("\n"),
        "END_GUEST",
        f'printf "%s  %s\\n" {shlex.quote(guest_sha)} "/var/lib/roseos-yogi-recover/$op/guest.py" | /usr/bin/sha256sum -c --quiet -',
        'exec /usr/bin/python3 -I "/var/lib/roseos-yogi-recover/$op/guest.py" --guest',
        "",
    ]
    return "\n".join(lines)


def metadata_body(fingerprint, items):
    """setMetadata replaces the whole map. Every key in `items` is sent; none are dropped here."""
    if not FINGERPRINT_RE.fullmatch(fingerprint or ""):
        raise Refused("metadata fingerprint is not usable")
    if not isinstance(items, dict) or any(not isinstance(value, str) for value in items.values()):
        raise Refused("metadata items are outside the recovery set")
    for key in items:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,127}", key):
            raise Refused("metadata items are outside the recovery set")
    for key, value in BASE_METADATA.items():
        if key in items and items[key] != value:
            raise Refused("preserved metadata value differs")
    ordered_keys = [key for key in BASE_ORDER + OUR_KEYS if key in items]
    ordered_keys += sorted(key for key in items if key not in ordered_keys)
    ordered = [{"key": key, "value": items[key]} for key in ordered_keys]
    return {"fingerprint": fingerprint, "items": ordered}


def removal_items(items, script, binding_text):
    """Drop this operation's startup-script and binding only when the values match exactly.

    Every other key, including a key added after our write, is copied through. A different value
    in either of our key names is left untouched and no write is offered.
    """
    if not isinstance(items, dict) or any(not isinstance(value, str) for value in items.values()):
        return None
    for key, value in BASE_METADATA.items():
        if items.get(key) != value:
            return None
    if items.get("startup-script") not in (None, script):
        return None
    if items.get(BINDING_KEY) not in (None, binding_text):
        return None
    drop = set()
    if items.get("startup-script") == script:
        drop.add("startup-script")
    if items.get(BINDING_KEY) == binding_text:
        drop.add(BINDING_KEY)
    return {key: value for key, value in items.items() if key not in drop}


def startup_script_absent(view):
    return isinstance(view, dict) and "startup-script" not in view["items"] and BINDING_KEY not in view["items"]


def interpret_metadata_http(status, body):
    attempt = {"ok": False, "unparseable": False, "httpStatus": status, "operationStatus": "none",
               "errorCode": "", "targetId": ""}
    if not isinstance(body, dict):
        attempt["unparseable"] = True
        attempt["errorCode"] = "unparseable"
        return attempt
    if status != 200:
        error = body.get("error")
        if isinstance(error, dict):
            attempt["errorCode"] = str(error.get("status") or error.get("code") or "http")[:40]
        else:
            attempt["errorCode"] = "http"
        return attempt
    operation = str(body.get("status") or "")
    attempt["operationStatus"] = operation or "missing"
    attempt["targetId"] = str(body.get("targetId") or "")
    if body.get("error"):
        attempt["errorCode"] = "operation-error"
        return attempt
    if operation != "DONE" or attempt["targetId"] != INSTANCE_ID:
        attempt["errorCode"] = "not-done" if operation != "DONE" else "target"
        return attempt
    attempt["ok"] = True
    return attempt


def metadata_view(instance):
    if not isinstance(instance, dict):
        return None
    meta = instance.get("metadata")
    if not isinstance(meta, dict) or not FINGERPRINT_RE.fullmatch(str(meta.get("fingerprint") or "")):
        return None
    items = {}
    for entry in meta.get("items") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            return None
        if entry["key"] in items or not isinstance(entry.get("value"), str):
            return None
        items[entry["key"]] = entry["value"]
    return {"fingerprint": meta["fingerprint"], "items": items}


def classify_metadata(attempt, before, after, intended):
    """One readback. `clean` is the only result that may boot the guest."""
    if before is None or after is None or not isinstance(attempt, dict):
        return "readback-failed"
    before_items, before_fp = before
    after_items, after_fp = after
    clean = attempt.get("ok") is True and attempt.get("targetId") == INSTANCE_ID and not attempt.get("errorCode")
    if clean and after_items == intended and after_fp != before_fp and before_items != intended:
        return "clean"
    if after_items == before_items and after_fp == before_fp:
        return "not-applied"
    if after_items == intended:
        return "ambiguous-applied"
    return "divergent"


def restore_allowed(items):
    """True when a preserving removal can be attempted. Unknown keys do not forbid it."""
    return removal_items(items, items.get("startup-script"), items.get(BINDING_KEY)) is not None


def instance_problems(seen, name, zone, number):
    if not isinstance(seen, dict):
        return ["instance-missing"]
    link = f"/projects/{PROJECT}/zones/{zone}/instances/{name}"
    problems = []
    if str(seen.get("id")) != number:
        problems.append("instance-id")
    if seen.get("name") != name:
        problems.append("instance-name")
    if str(seen.get("zone") or "").rstrip("/").rsplit("/", 1)[-1] != zone:
        problems.append("instance-zone")
    if not str(seen.get("selfLink") or "").endswith(link):
        problems.append("instance-link")
    return problems


def external_address(instance):
    for nic in instance.get("networkInterfaces") or []:
        if not isinstance(nic, dict):
            return True
        for access in nic.get("accessConfigs") or []:
            if isinstance(access, dict) and access.get("natIP"):
                return True
        for access in nic.get("ipv6AccessConfigs") or []:
            if isinstance(access, dict) and access.get("externalIpv6"):
                return True
    return False


def scheduling_problem(scheduling):
    """REST shape: scheduling.maxRunDuration.seconds is an int64, not a gcloud duration flag."""
    if not isinstance(scheduling, dict) or scheduling.get("automaticRestart") is not True:
        return "automatic-restart"
    duration = scheduling.get("maxRunDuration") if isinstance(scheduling.get("maxRunDuration"), dict) else {}
    seconds = duration.get("seconds")
    if seconds not in (2700, "2700"):
        return "max-run-duration"
    if duration.get("nanos", 0) not in (0, "0", None):
        return "max-run-duration"
    if scheduling.get("instanceTerminationAction") != "STOP":
        return "termination-action"
    return None


def boot_disk_name(instance, zone):
    boots = [disk for disk in instance.get("disks") or [] if isinstance(disk, dict) and disk.get("boot") is True]
    if len(boots) != 1:
        return None
    source = str(boots[0].get("source") or "")
    marker = f"/projects/{PROJECT}/zones/{zone}/disks/"
    if marker not in source:
        return None
    name = source.rsplit("/", 1)[-1]
    if not DISK_NAME_RE.fullmatch(name):
        return None
    return name


def disk_problems(disk, name, zone, number, size=None, user=None):
    if not isinstance(disk, dict):
        return ["disk-missing"]
    problems = []
    if str(disk.get("id")) != number:
        problems.append("disk-id")
    if disk.get("name") != name:
        problems.append("disk-name")
    if str(disk.get("zone") or "").rstrip("/").rsplit("/", 1)[-1] != zone:
        problems.append("disk-zone")
    if size is not None and str(disk.get("sizeGb")) != str(size):
        problems.append("disk-size")
    if user is not None:
        users = [str(item).rstrip("/").rsplit("/", 1)[-1] for item in disk.get("users") or []]
        if users != [user]:
            problems.append("disk-users")
    return problems


def snapshot_problems(snap, name, number):
    if not isinstance(snap, dict):
        return ["snapshot-missing"]
    problems = []
    if snap.get("name") != name or str(snap.get("id")) != number:
        problems.append("snapshot-id")
    if snap.get("status") != "READY":
        problems.append("snapshot-status")
    return problems


def firewall_problems(rules):
    if not isinstance(rules, list):
        return ["iap-firewall"]
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("disabled") is True:
            continue
        if rule.get("direction") not in (None, "INGRESS"):
            continue
        if IAP_RANGE not in (rule.get("sourceRanges") or []):
            continue
        for entry in rule.get("allowed") or []:
            if not isinstance(entry, dict) or entry.get("IPProtocol") not in ("tcp", "6"):
                continue
            ports = entry.get("ports")
            if ports in (None, []) or (isinstance(ports, list) and "22" in ports):
                return []
    return ["iap-firewall"]


def account_problems(listing):
    if isinstance(listing, dict):
        listing = listing.get("accounts")
    if not isinstance(listing, list):
        return ["account"]
    active = [row for row in listing if isinstance(row, dict) and row.get("status") == "ACTIVE"]
    if len(active) != 1 or active[0].get("account") != OPERATOR_ACCOUNT:
        return ["account"]
    return []


def guard_request(request):
    if not isinstance(request, dict) or set(request) - {"kind", "target", "name", "zone", "start", "timeout",
                                                       "fingerprint", "items"}:
        raise Refused("control-plane request is outside the recovery set")
    kind, target = request.get("kind"), request.get("target", "none")
    if kind == "auth-list" and target == "none":
        return request
    if kind == "list-firewalls" and target == "none":
        return request
    if kind == "describe-snapshot" and target in ("preserve", "prekey"):
        return request
    if kind == "describe-instance" and target in ("replacement", "original"):
        return request
    if kind == "describe-disk" and target == "replacement" and request.get("name", DISK_NAME) == DISK_NAME \
            and request.get("zone", ZONE) == ZONE:
        return request
    if kind == "describe-disk" and target == "original" and request.get("zone") == ORIGINAL_ZONE \
            and DISK_NAME_RE.fullmatch(request.get("name") or ""):
        return request
    if kind in ("serial", "guest-attribute", "start", "stop") and target == "replacement":
        return request
    if kind == "set-metadata" and target == "replacement" and "items" in request and "fingerprint" in request:
        return request
    if target == "original":
        raise Refused("original VM and disk are read-only")
    raise Refused("control-plane request is outside the recovery set")


def gcloud_argv(request):
    guard_request(request)
    kind, project = request["kind"], f"--project={PROJECT}"
    if kind == "set-metadata":
        raise Refused("setMetadata is one HTTP call, not a gcloud argv")
    if kind == "auth-list":
        return ["gcloud", "auth", "list", "--format=json", "--quiet"]
    if kind == "list-firewalls":
        return ["gcloud", "compute", "firewall-rules", "list", project, "--format=json", "--quiet",
                "--filter=direction=INGRESS AND sourceRanges:35.235.240.0/20"]
    if kind == "describe-snapshot":
        name = PRESERVE_SNAPSHOT if request["target"] == "preserve" else PREKEY_SNAPSHOT
        return ["gcloud", "compute", "snapshots", "describe", name, project, "--format=json", "--quiet"]
    if kind == "describe-instance":
        name, zone = (INSTANCE, ZONE) if request["target"] == "replacement" else (ORIGINAL_INSTANCE, ORIGINAL_ZONE)
        return ["gcloud", "compute", "instances", "describe", name, project, f"--zone={zone}", "--format=json", "--quiet"]
    if kind == "describe-disk":
        name = DISK_NAME if request["target"] == "replacement" else request["name"]
        zone = ZONE if request["target"] == "replacement" else ORIGINAL_ZONE
        return ["gcloud", "compute", "disks", "describe", name, project, f"--zone={zone}", "--format=json", "--quiet"]
    if kind == "serial":
        return ["gcloud", "compute", "instances", "get-serial-port-output", INSTANCE, project, f"--zone={ZONE}",
                "--port=1", f"--start={int(request['start'])}", "--format=json", "--quiet"]
    if kind == "guest-attribute":
        return ["gcloud", "compute", "instances", "get-guest-attributes", INSTANCE, project, f"--zone={ZONE}",
                "--query-path=roseos-yogi-recover/receipt", "--format=json", "--quiet"]
    if kind in ("start", "stop"):
        return ["gcloud", "compute", "instances", kind, INSTANCE, project, f"--zone={ZONE}", "--quiet"]
    raise Refused("control-plane request is outside the recovery set")


def token_argv():
    return ["gcloud", "auth", "print-access-token"]


class RealTransport:
    """Operator gcloud plus one urllib setMetadata. The access token stays in this process and off argv."""

    def __init__(self, run=subprocess.run, opener=urllib.request.urlopen):
        self.run, self.opener = run, opener

    def _token(self):
        try:
            result = self.run(token_argv(), capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise TransportError("token") from None
        token = result.stdout.strip() if result.returncode == 0 else ""
        if not token or any(char in token for char in "\n\r\t "):
            raise TransportError("token")
        return token

    def _set(self, request):
        body = metadata_body(request["fingerprint"], request["items"])
        token = self._token()
        http_request = urllib.request.Request(
            METADATA_URL, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with self.opener(http_request, timeout=60) as response:
                raw, status = response.read(1 << 20), getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            raw, status = error.read(1 << 20), error.code
        except Exception:
            raise TransportError("timeout") from None
        try:
            parsed = json.loads(raw.decode())
        except (ValueError, UnicodeError):
            parsed = None
        return {"attempt": interpret_metadata_http(status, parsed)}

    def __call__(self, request):
        guard_request(request)
        if request["kind"] == "set-metadata":
            return self._set(request)
        try:
            result = self.run(gcloud_argv(request), capture_output=True, text=True, timeout=request.get("timeout", 120))
        except subprocess.TimeoutExpired:
            raise TransportError("timeout") from None
        if request["kind"] == "guest-attribute" and result.returncode:
            return {"absent": True}
        if request["kind"] in ("start", "stop"):
            if result.returncode:
                raise TransportError("exit")
            return {"requested": True}
        if result.returncode:
            raise TransportError("exit")
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise TransportError("unparseable") from None


def logged(transport, log):
    def call(request):
        guard_request(request)
        entry = {"kind": request["kind"], "target": request.get("target", "none")}
        if request["kind"] == "set-metadata":
            entry["keys"] = sorted(request["items"])
            entry["fingerprint"] = request["fingerprint"]
        log.append(entry)
        return transport(request)
    return call


def collect(call):
    errors = []

    def grab(request):
        try:
            return call(request)
        except TransportError:
            errors.append(request["kind"] + ":" + request.get("target", "none"))
            return None

    snapshot = {"account": grab({"kind": "auth-list", "target": "none"}),
                "replacement": grab({"kind": "describe-instance", "target": "replacement"}),
                "original": grab({"kind": "describe-instance", "target": "original"}),
                "replacementDisk": grab({"kind": "describe-disk", "target": "replacement", "name": DISK_NAME,
                                         "zone": ZONE}),
                "originalDisk": None, "snapshots": {}, "firewalls": grab({"kind": "list-firewalls", "target": "none"}),
                "errors": errors}
    original = snapshot["original"]
    if original and not instance_problems(original, ORIGINAL_INSTANCE, ORIGINAL_ZONE, ORIGINAL_INSTANCE_ID):
        name = boot_disk_name(original, ORIGINAL_ZONE)
        if name:
            snapshot["originalDisk"] = grab({"kind": "describe-disk", "target": "original", "name": name,
                                             "zone": ORIGINAL_ZONE})
        else:
            errors.append("original-boot-disk")
    elif original:
        errors.append("original-identity")
    for target, _name in (("preserve", PRESERVE_SNAPSHOT), ("prekey", PREKEY_SNAPSHOT)):
        snapshot["snapshots"][target] = grab({"kind": "describe-snapshot", "target": target})
    return snapshot


def apply_problems(snapshot):
    problems = list(account_problems(snapshot.get("account")))
    problems += [f"replacement-{item}" for item in instance_problems(
        snapshot.get("replacement"), INSTANCE, ZONE, INSTANCE_ID)]
    replacement = snapshot.get("replacement") if isinstance(snapshot.get("replacement"), dict) else {}
    if replacement.get("status") != "TERMINATED":
        problems.append("replacement-status")
    if replacement and external_address(replacement):
        problems.append("external-ip")
    if replacement and (replacement.get("serviceAccounts") or []):
        problems.append("service-account")
    schedule = scheduling_problem(replacement.get("scheduling") if replacement else None)
    if schedule:
        problems.append(schedule)
    view = metadata_view(replacement) if replacement else None
    if view is None or view["items"] != BASE_METADATA:
        problems.append("metadata")
    if replacement and boot_disk_name(replacement, ZONE) != DISK_NAME:
        problems.append("boot-disk")
    problems += [f"replacement-{item}" for item in disk_problems(
        snapshot.get("replacementDisk"), DISK_NAME, ZONE, DISK_ID, DISK_SIZE_GB, INSTANCE)]
    problems += [f"original-{item}" for item in instance_problems(
        snapshot.get("original"), ORIGINAL_INSTANCE, ORIGINAL_ZONE, ORIGINAL_INSTANCE_ID)]
    original = snapshot.get("original") if isinstance(snapshot.get("original"), dict) else {}
    if original and original.get("status") != "TERMINATED":
        problems.append("original-status")
    original_name = boot_disk_name(original, ORIGINAL_ZONE) if original else None
    problems += [f"original-{item}" for item in disk_problems(
        snapshot.get("originalDisk"), original_name or "", ORIGINAL_ZONE, ORIGINAL_DISK_ID, user=ORIGINAL_INSTANCE)]
    problems += [f"preserve-{item}" for item in snapshot_problems(
        (snapshot.get("snapshots") or {}).get("preserve"), PRESERVE_SNAPSHOT, PRESERVE_SNAPSHOT_ID)]
    problems += [f"prekey-{item}" for item in snapshot_problems(
        (snapshot.get("snapshots") or {}).get("prekey"), PREKEY_SNAPSHOT, PREKEY_SNAPSHOT_ID)]
    problems += firewall_problems(snapshot.get("firewalls"))
    if snapshot.get("errors"):
        problems.append("read-failed")
    return problems


def replacement_identity_ok(instance):
    return isinstance(instance, dict) and not instance_problems(instance, INSTANCE, ZONE, INSTANCE_ID)


class Deadline:
    def __init__(self, clock, seconds):
        self.clock, self.limit = clock, clock() + seconds

    def remaining(self):
        return self.limit - self.clock()

    def expired(self):
        return self.remaining() <= 0


def blank_attempt(code):
    return {"ok": False, "unparseable": True, "httpStatus": 0, "operationStatus": "none", "errorCode": code,
            "targetId": ""}


def one_set(call, fingerprint, items):
    try:
        response = call({"kind": "set-metadata", "target": "replacement", "fingerprint": fingerprint, "items": items,
                         "timeout": 60})
        attempt = response["attempt"] if isinstance(response, dict) else blank_attempt("unparseable")
    except (TransportError, KeyError, TypeError):
        attempt = blank_attempt("timeout")
    try:
        after = metadata_view(call({"kind": "describe-instance", "target": "replacement"}))
    except TransportError:
        after = None
    return attempt, after


def restore_metadata(call, script, binding_text):
    """One preserving removal of this operation's exact metadata values, then one readback. No second write."""
    try:
        current = metadata_view(call({"kind": "describe-instance", "target": "replacement"}))
    except TransportError:
        return "readback-failed", None
    if current is None:
        return "readback-failed", None
    kept = removal_items(current["items"], script, binding_text)
    if kept is None:
        return "skipped-foreign-value", current
    if kept == current["items"]:
        return "already-base", current
    attempt, after = one_set(call, current["fingerprint"], kept)
    kind = classify_metadata(attempt, (current["items"], current["fingerprint"]),
                             None if after is None else (after["items"], after["fingerprint"]), kept)
    return kind, after


def ensure_stopped(call, start_attempted):
    def status():
        try:
            instance = call({"kind": "describe-instance", "target": "replacement"})
        except TransportError:
            return None
        if not replacement_identity_ok(instance):
            return "mismatch"
        return instance.get("status")

    state = status()
    if state == "mismatch":
        return "identity-mismatch"
    if state == "TERMINATED" and not start_attempted:
        return "not-started"
    # Two stop requests, each followed by one read. The second covers one automaticRestart.
    for label in ("stopped", "stopped-after-restart"):
        try:
            call({"kind": "stop", "target": "replacement", "timeout": 240})
        except TransportError:
            pass
        state = status()
        if state == "TERMINATED":
            return label
        if state == "mismatch":
            return "identity-mismatch"
    return "stop-unconfirmed"


def capture_cursor(call):
    try:
        response = call({"kind": "serial", "target": "replacement", "start": 0, "timeout": 60})
    except TransportError:
        raise Refused("serial cursor read failed") from None
    if not isinstance(response, dict) or str(response.get("start")) != "0":
        raise Refused("serial cursor is not the start of a fresh read")
    nxt = str(response.get("next") or "")
    if not re.fullmatch(r"[0-9]{1,12}", nxt):
        raise Refused("serial cursor is not the start of a fresh read")
    return nxt


def read_new(call, cursor):
    response = call({"kind": "serial", "target": "replacement", "start": int(cursor), "timeout": 60})
    if not isinstance(response, dict) or str(response.get("start")) != str(cursor):
        raise guest.Malformed("cursor")
    nxt = str(response.get("next") or "")
    if not re.fullmatch(r"[0-9]{1,12}", nxt) or int(nxt) < int(cursor):
        raise guest.Malformed("cursor")
    return str(response.get("contents") or ""), nxt


def collect_receipt(call, binding, deadline, sleep):
    """Read serial only after the instance is RUNNING. The first read may include older bytes.

    A receipt is accepted only when it matches this operation's nonce. Output from before this
    operation cannot contain that nonce. A follow-up read uses the returned byte cursor. The
    serial console is still guest-written and is not a pin.
    """
    if deadline.expired():
        return None, "budget"
    chunks = []
    cursor = "0"
    try:
        text, cursor = read_new(call, cursor)
    except guest.Malformed:
        return None, "stale-cursor"
    except TransportError:
        return None, "serial-failed"
    chunks.append(text)
    try:
        receipt = guest.parse_serial("".join(chunks), binding)
    except guest.Malformed:
        return None, "malicious"
    if receipt is not None:
        return receipt, "read"
    for delay in SERIAL_DELAYS:
        if deadline.expired():
            return None, "budget"
        wait = min(delay, max(0, int(deadline.remaining())))
        if wait:
            sleep(wait)
        if deadline.expired():
            return None, "budget"
        try:
            text, cursor = read_new(call, cursor)
        except guest.Malformed:
            return None, "stale-cursor"
        except TransportError:
            return None, "serial-failed"
        chunks.append(text)
        try:
            receipt = guest.parse_serial("".join(chunks), binding)
        except guest.Malformed:
            return None, "malicious"
        if receipt is not None:
            return receipt, "read"
    return None, "absent"


def attribute_state(call, receipt):
    try:
        response = call({"kind": "guest-attribute", "target": "replacement", "timeout": 30})
    except TransportError:
        return "failed"
    if not isinstance(response, dict) or response.get("absent") is True:
        return "absent"
    try:
        parsed = json.loads(response.get("value"))
    except (TypeError, ValueError):
        return "mismatch"
    return "matched" if parsed == receipt else "mismatch"


def exclusive_json(path, obj):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def head_revision(repo):
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    revision = result.stdout.strip()
    if result.returncode or not COMMIT.fullmatch(revision):
        raise Refused("source revision is not a commit")
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True)
    if dirty.returncode or dirty.stdout.strip():
        raise Refused("source worktree has uncommitted changes")
    return revision


def plan(operator, out, now=None, token=None, revision=None, repo=ROOT, template=None, env=None):
    manual_context(env if env is not None else os.environ)
    now = now or now_utc()
    if not isinstance(operator, str) or not OPERATOR.fullmatch(operator):
        raise Refused("operator name is empty or uses unsupported characters")
    if revision is None:
        try:
            revision = head_revision(Path.cwd())
        except Refused:
            revision = "unbound"
    elif revision != "unbound" and not COMMIT.fullmatch(revision):
        raise Refused("source revision is not a commit")
    token = token or (lambda: __import__("secrets").token_hex(16))
    operation_id, nonce = token(), token()
    if not HEX32.fullmatch(operation_id or "") or not HEX32.fullmatch(nonce or "") or operation_id == nonce:
        raise Refused("binding generator failed")
    template = template if template is not None else (HERE / "yogi_replacement_recover_guest.py").read_text()
    rendered = render_guest(operation_id, nonce, template)
    payload_sha = sha256_bytes(rendered.encode())
    reject_historical(operation_id, nonce, payload_sha)
    script = startup_script(rendered, payload_sha, operation_id)
    binding = {"schema": guest.BINDING_SCHEMA, "operationId": operation_id, "nonce": nonce,
               "payloadSha256": payload_sha}
    out = Path(out)
    out.mkdir(mode=0o700)
    (out / "guest.py").write_text(rendered)
    (out / "startup-script.sh").write_text(script)
    (out / "binding.json").write_text(binding_canonical(operation_id, nonce, payload_sha) + "\n")
    document = {
        "schema": PLAN_SCHEMA, "dryRun": True, "executed": False, "liveRecovery": False, "hostKeyPinned": False,
        "claim": "procedure-evidence-only", "operationId": operation_id, "nonce": nonce,
        "payloadSha256": payload_sha, "startupScriptSha256": sha256_bytes(script.encode()),
        "templateSha256": sha256_bytes(template.encode()), "sourceRevision": revision, "operator": operator,
        "createdAt": stamp(now), "expiresAt": stamp(now + PLAN_TTL),
        "binding": binding, "deadlines": {
            "policySeconds": POLICY_SECONDS, "workBudgetSeconds": WORK_BUDGET_SECONDS,
            "cleanupReserveSeconds": CLEANUP_RESERVE_SECONDS, "serialDelaysSeconds": list(SERIAL_DELAYS),
            "metadataAttempts": 1, "stopAttempts": 2},
        "target": {"project": PROJECT, "zone": ZONE, "instance": INSTANCE, "instanceId": INSTANCE_ID,
                   "disk": DISK_NAME, "diskId": DISK_ID, "diskSizeGb": DISK_SIZE_GB},
        "readOnly": {"originalInstance": ORIGINAL_INSTANCE, "originalZone": ORIGINAL_ZONE,
                     "originalInstanceId": ORIGINAL_INSTANCE_ID, "originalDiskId": ORIGINAL_DISK_ID,
                     "snapshots": [{"name": PRESERVE_SNAPSHOT, "id": PRESERVE_SNAPSHOT_ID},
                                   {"name": PREKEY_SNAPSHOT, "id": PREKEY_SNAPSHOT_ID}]},
        "metadataBefore": BASE_METADATA, "metadataAdded": list(OUR_KEYS),
        "unit": {"path": guest.UNIT_PATH, "beforeMode": "0664", "afterMode": "0644", "size": guest.UNIT_SIZE,
                 "owner": "root:root", "runner": "inactive/disabled"},
        "hostKeys": {"inventory": list(guest.INVENTORY), "rotate": list(guest.ALGORITHMS),
                     "baseline": "public fingerprints read on this boot of the replacement disk"},
        "cleanup": {"authorizedKeyComment": "roseos-yogi-recover-<operationId>",
                    "temporaryAccount": guest.TEMP_ACCOUNT, "paths": list(guest.AUTH_PATHS)},
        "route": "gce-metadata-startup-script-and-bounded-serial-read",
        "docs": DOCS, "operatorAccount": OPERATOR_ACCOUNT,
        "permissionsExpected": ["compute.instances.get", "compute.instances.setMetadata", "compute.instances.start",
                                "compute.instances.stop", "compute.instances.getSerialPortOutput",
                                "compute.disks.get", "compute.snapshots.get", "compute.firewalls.list"],
        "permissionsRefused": ["compute.instances.setMetadata on the original", "setCommonInstanceMetadata",
                               "setIamPolicy", "snapshots.useReadOnly", "instances.delete"],
        "limitations": list(LIMITATIONS), "historicalReference": HISTORICAL,
        "historicalReferenceReplayed": False,
    }
    (out / "plan.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    names = ("binding.json", "guest.py", "plan.json", "startup-script.sh")
    sums = "".join(f"{sha256_bytes((out / name).read_bytes())}  {name}\n" for name in names)
    (out / "SHA256SUMS").write_text(sums)
    document["artifacts"] = {name: sha256_bytes((out / name).read_bytes()) for name in names}
    return document


def load_operation(path, now=None, allow_expired=False):
    path = Path(path)
    sums = {}
    try:
        for line in (path / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            if not SHA256.fullmatch(digest) or name not in ("binding.json", "guest.py", "plan.json", "startup-script.sh"):
                raise ValueError
            sums[name] = digest
            if sha256_bytes((path / name).read_bytes()) != digest:
                raise ValueError
    except (OSError, ValueError):
        raise Refused("operation checksums do not match") from None
    if set(sums) != {"binding.json", "guest.py", "plan.json", "startup-script.sh"}:
        raise Refused("operation checksums do not match")
    try:
        document = json.loads((path / "plan.json").read_text())
        binding = json.loads((path / "binding.json").read_text())
    except (OSError, ValueError):
        raise Refused("operation plan is unreadable") from None
    if document.get("schema") != PLAN_SCHEMA or binding != document.get("binding"):
        raise Refused("operation plan does not match its binding")
    if guest.binding_problems(binding):
        raise Refused("operation binding is not usable")
    rendered = (path / "guest.py").read_text()
    script = (path / "startup-script.sh").read_text()
    if sha256_bytes(rendered.encode()) != binding["payloadSha256"] or binding["operationId"] not in rendered:
        raise Refused("rendered guest does not match the binding")
    if sha256_bytes(script.encode()) != document.get("startupScriptSha256") or binding["payloadSha256"] not in script:
        raise Refused("startup script does not match the binding")
    reject_historical(binding["operationId"], binding["nonce"], binding["payloadSha256"])
    if not allow_expired and (now or now_utc()) >= parse_stamp(document["expiresAt"]):
        raise Refused("operation plan has expired")
    return {"path": path, "plan": document, "binding": binding, "guest": rendered, "script": script}


def confirm_instance(value):
    if value != INSTANCE_ID:
        raise Refused("confirm-instance does not match the replacement numeric id")


def outcome_shell(**fields):
    base = {"schema": OUTCOME_SCHEMA, "dryRun": False, "executed": True, "liveRecovery": False, "hostKeyPinned": False,
            "guestAuthenticated": False, "serialReceiptTrustedForPin": False, "distinctFromOriginalVerified": False,
            "claim": "procedure-evidence-only", "historicalReferenceReplayed": False}
    base.update(fields)
    return base


def preflight(path, confirm, execute=False, transport=None, now=None, env=None):
    manual_context(env if env is not None else os.environ)
    confirm_instance(confirm)
    loaded = load_operation(path, now)
    if not execute:
        return outcome_shell(dryRun=True, executed=False, result="dry-run", operationId=loaded["binding"]["operationId"])
    log = []
    snapshot = collect(logged(transport, log))
    problems = apply_problems(snapshot)
    view = metadata_view(snapshot.get("replacement")) if isinstance(snapshot.get("replacement"), dict) else None
    moment = now or now_utc()
    record = {"schema": PREFLIGHT_SCHEMA, "operationId": loaded["binding"]["operationId"],
              "nonce": loaded["binding"]["nonce"], "payloadSha256": loaded["binding"]["payloadSha256"],
              "fingerprint": None if view is None else view["fingerprint"], "observedAt": stamp(moment),
              "expiresAt": stamp(moment + PREFLIGHT_TTL), "problems": problems,
              "replacementStatus": (snapshot.get("replacement") or {}).get("status") if isinstance(snapshot.get("replacement"), dict) else None,
              "originalStatus": (snapshot.get("original") or {}).get("status") if isinstance(snapshot.get("original"), dict) else None,
              "requests": log}
    (loaded["path"] / "preflight.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return outcome_shell(result="preflight-ok" if not problems else "preflight-blocked",
                         operationId=loaded["binding"]["operationId"], problems=problems, requests=log,
                         fingerprint=record["fingerprint"])


def load_preflight(path, binding, now):
    try:
        record = json.loads((Path(path) / "preflight.json").read_text())
    except (OSError, ValueError):
        raise Refused("preflight record is missing") from None
    if record.get("schema") != PREFLIGHT_SCHEMA or record.get("operationId") != binding["operationId"] \
            or record.get("nonce") != binding["nonce"] or record.get("payloadSha256") != binding["payloadSha256"]:
        raise Refused("preflight record is for a different binding")
    if record.get("problems"):
        raise Refused("preflight record is not clear")
    if not isinstance(record.get("fingerprint"), str):
        raise Refused("preflight record has no metadata fingerprint")
    if (now or now_utc()) >= parse_stamp(record["expiresAt"]):
        raise Refused("preflight record has expired")
    return record


def finish(path, document, code):
    exclusive_json(Path(path) / "outcome.json", document)
    document["exit"] = code
    return document


def _write_outcome(op, document, code):
    try:
        return finish(op, document, code)
    except OSError:
        document["outcomeWrite"] = "failed"
        document["exit"] = code
        try:
            (Path(op) / "outcome-unwritten.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        except OSError:
            pass
        return document


def observed_ids(snapshot):
    def ident(obj):
        if not isinstance(obj, dict):
            return None
        return {"id": str(obj.get("id")), "name": obj.get("name"), "status": obj.get("status")}
    shots = snapshot.get("snapshots") or {}
    return {"original": ident(snapshot.get("original")), "originalDisk": ident(snapshot.get("originalDisk")),
            "preserve": ident(shots.get("preserve")), "prekey": ident(shots.get("prekey"))}


def ids_match(observed):
    return (observed["original"] or {}).get("id") == ORIGINAL_INSTANCE_ID \
        and (observed["originalDisk"] or {}).get("id") == ORIGINAL_DISK_ID \
        and (observed["preserve"] or {}).get("id") == PRESERVE_SNAPSHOT_ID \
        and (observed["prekey"] or {}).get("id") == PREKEY_SNAPSHOT_ID \
        and (observed["preserve"] or {}).get("status") == "READY" \
        and (observed["prekey"] or {}).get("status") == "READY"


def apply(path, confirm, execute=False, transport=None, now=None, clock=None, sleep=None, env=None):
    manual_context(env if env is not None else os.environ)
    confirm_instance(confirm)
    moment = now or now_utc()
    loaded = load_operation(path, moment)
    binding = loaded["binding"]
    if not execute:
        return outcome_shell(dryRun=True, executed=False, result="dry-run", operationId=binding["operationId"])
    op = loaded["path"]
    if (op / "outcome.json").exists() or (op / "started.json").exists():
        raise Refused("operation was already started; plan a new one")
    pre = load_preflight(op, binding, moment)
    log = []
    call = logged(transport, log)
    script = loaded["script"]
    binding_text = binding_canonical(binding["operationId"], binding["nonce"], binding["payloadSha256"])
    state = {"marker": False, "start": False, "sealed": False}

    def seal(fields):
        if state["sealed"]:
            return fields
        if not state["marker"]:
            fields.setdefault("restore", "not-attempted")
            fields.setdefault("stop", "not-started")
            fields["startupScriptRemoved"] = False
            state["sealed"] = True
            return _write_outcome(op, outcome_shell(**fields), 1 if fields.get("result") != "procedure-complete-guest-reported" else 0)
        if fields.get("metadata") == "readback-failed":
            restore, after = "not-attempted-readback-failed", None
        else:
            try:
                restore, after = restore_metadata(call, script, binding_text)
            except Exception:
                restore, after = "seal-failed", None
        try:
            stop = ensure_stopped(call, state["start"])
        except Exception:
            stop = "seal-failed"
        state["sealed"] = True
        fields["restore"] = restore
        fields["stop"] = stop
        removed = startup_script_absent(after) or restore == "already-base"
        fields["startupScriptRemoved"] = bool(removed)
        fields["metadataKeysAfter"] = sorted(after["items"]) if isinstance(after, dict) else None
        fields["startupScriptRemovalProof"] = "control-plane-readback" if removed else "absent"
        guest_ok = fields.pop("guestOk", False)
        reason = fields.get("guestRead")
        stopped = stop in ("stopped", "stopped-after-restart")
        restored = restore in ("clean", "already-base") and removed
        if guest_ok and restored and stopped and fields.get("metadata") == "clean":
            fields["result"] = "procedure-complete-guest-reported"
            code = 0
        else:
            fields.setdefault("result", "halted-" + str(reason))
            if fields["result"] == "procedure-complete-guest-reported":
                fields["result"] = "halted-stop"
            code = 1
        return _write_outcome(op, outcome_shell(**fields), code)

    def body():
        snapshot = collect(call)
        problems = apply_problems(snapshot)
        view = metadata_view(snapshot.get("replacement")) if isinstance(snapshot.get("replacement"), dict) else None
        preserved = observed_ids(snapshot)
        if problems or view is None:
            return seal({"result": "preflight-mismatch", "operationId": binding["operationId"], "problems": problems,
                         "requests": log, "metadata": "not-attempted", "preserved": preserved})
        if not ids_match(preserved):
            return seal({"result": "preserved-id-mismatch", "operationId": binding["operationId"], "requests": log,
                         "metadata": "not-attempted", "preserved": preserved})
        if view["fingerprint"] != pre["fingerprint"] or view["items"] != BASE_METADATA:
            return seal({"result": "stale-fingerprint", "operationId": binding["operationId"], "requests": log,
                         "metadata": "not-attempted", "preserved": preserved})
        intended = dict(BASE_METADATA)
        intended["startup-script"] = script
        intended[BINDING_KEY] = binding_text
        exclusive_json(op / "started.json", {"operationId": binding["operationId"], "nonce": binding["nonce"],
                                            "fingerprint": view["fingerprint"], "at": stamp(moment)})
        state["marker"] = True
        attempt, after = one_set(call, view["fingerprint"], intended)
        kind = classify_metadata(attempt, (view["items"], view["fingerprint"]),
                                 None if after is None else (after["items"], after["fingerprint"]), intended)
        if kind != "clean":
            return seal({"result": "metadata-" + kind, "operationId": binding["operationId"], "requests": log,
                         "metadata": kind, "metadataError": attempt.get("errorCode"), "preserved": preserved})
        clock_fn = clock or __import__("time").monotonic
        sleep_fn = sleep or __import__("time").sleep
        work = Deadline(clock_fn, WORK_BUDGET_SECONDS)
        if work.expired() or work.remaining() < 30:
            return seal({"result": "budget-before-start", "operationId": binding["operationId"], "requests": log,
                         "metadata": "clean", "preserved": preserved})
        state["start"] = True
        try:
            call({"kind": "start", "target": "replacement", "timeout": min(240, int(work.remaining()))})
        except TransportError:
            pass
        try:
            started = call({"kind": "describe-instance", "target": "replacement"})
        except TransportError:
            started = None
        if not isinstance(started, dict) or not replacement_identity_ok(started) or started.get("status") != "RUNNING":
            return seal({"result": "start-unconfirmed", "operationId": binding["operationId"], "requests": log,
                         "metadata": "clean", "preserved": preserved})
        try:
            receipt, reason = collect_receipt(call, binding, work, sleep_fn)
        except Refused:
            receipt, reason = None, "serial-cursor-failed"
        attribute = None
        guest_ok = False
        if receipt is not None and reason == "read":
            attribute = attribute_state(call, receipt)
            if attribute == "mismatch":
                reason = "attribute-mismatch"
            elif attribute == "failed":
                reason = "attribute-failed"
            elif guest.success_problems(receipt):
                reason = "guest-incomplete"
            else:
                guest_ok = True
        return seal({"operationId": binding["operationId"], "requests": log, "metadata": "clean",
                     "guestOk": guest_ok, "guestRead": reason, "guestReceipt": receipt if isinstance(receipt, dict) else None,
                     "guestAttribute": attribute, "preserved": preserved,
                     "distinctFromOriginalVerified": False, "serialReceiptTrustedForPin": False})

    try:
        return body()
    except KeyboardInterrupt:
        if not state["sealed"]:
            try:
                seal({"result": "interrupted", "operationId": binding["operationId"], "requests": log,
                      "guestRead": "interrupted", "metadata": "unknown"})
            except Exception:
                try:
                    ensure_stopped(call, True)
                except Exception:
                    pass
        raise


def cleanup(path, confirm, execute=False, transport=None, now=None, env=None):
    """Discover the replacement from the control plane. started.json is not required to stop or to remove our keys."""
    manual_context(env if env is not None else os.environ)
    confirm_instance(confirm)
    loaded = load_operation(path, now, allow_expired=True)
    if not execute:
        return outcome_shell(dryRun=True, executed=False, result="dry-run",
                             operationId=loaded["binding"]["operationId"])
    log = []
    call = logged(transport, log)
    snapshot = collect(call)
    preserved = observed_ids(snapshot)
    instance = snapshot.get("replacement")
    script = loaded["script"]
    binding_text = binding_canonical(loaded["binding"]["operationId"], loaded["binding"]["nonce"],
                                     loaded["binding"]["payloadSha256"])
    if not replacement_identity_ok(instance):
        stop = ensure_stopped(call, True) if instance is None else "identity-mismatch"
        document = outcome_shell(result="cleanup-identity-mismatch" if instance is not None else "cleanup-read-failed",
                                 operationId=loaded["binding"]["operationId"], requests=log, restore="not-attempted",
                                 stop=stop, preserved=preserved, preservedIdsMatch=ids_match(preserved),
                                 startupScriptRemoved=False, exit=1)
    else:
        restore, after = restore_metadata(call, script, binding_text)
        stop = ensure_stopped(call, instance.get("status") != "TERMINATED")
        removed = startup_script_absent(after) or restore in ("already-base", "clean")
        code = 0 if restore in ("clean", "already-base") and removed and stop in (
            "stopped", "stopped-after-restart", "not-started") and ids_match(preserved) else 1
        document = outcome_shell(result="cleanup", operationId=loaded["binding"]["operationId"], requests=log,
                                 restore=restore, stop=stop, preserved=preserved, preservedIdsMatch=ids_match(preserved),
                                 startupScriptRemoved=bool(removed),
                                 startupScriptRemovalProof="control-plane-readback" if removed else "absent",
                                 metadataKeysAfter=sorted(after["items"]) if isinstance(after, dict) else None, exit=code)
    try:
        (loaded["path"] / "cleanup.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    except OSError:
        document["outcomeWrite"] = "failed"
    return document


def export_bundle(out):
    """Copy the runnable operator files and a sha256 manifest. No cloud call and no git checkout required to run them."""
    out = Path(out)
    out.mkdir(mode=0o700)
    mapping = {
        "yogi_replacement_recover.py": HERE / "yogi_replacement_recover.py",
        "yogi_replacement_recover_guest.py": HERE / "yogi_replacement_recover_guest.py",
        "YOGI-REPLACEMENT-RECOVER.md": HERE.parent / "docs" / "YOGI-REPLACEMENT-RECOVER.md",
    }
    readme = (
        "Run from this directory. No repository checkout is required.\n"
        "python3 yogi_replacement_recover.py plan --operator NAME --out DIR\n"
        "Review the operation directory, then preflight and apply only with --execute "
        "and --confirm-instance 2720796557371812468.\n"
        "SHA256SUMS lists every file in this directory except itself.\n"
    )
    (out / "README.md").write_text(readme)
    for name, source in mapping.items():
        shutil.copyfile(source, out / name)
    names = ("README.md", "YOGI-REPLACEMENT-RECOVER.md", "yogi_replacement_recover.py",
             "yogi_replacement_recover_guest.py")
    sums = "".join(f"{sha256_bytes((out / name).read_bytes())}  {name}\n" for name in names)
    (out / "SHA256SUMS").write_text(sums)
    return {name: sha256_bytes((out / name).read_bytes()) for name in names + ("SHA256SUMS",)}


def main(argv, transport=None, now=None, clock=None, sleep=None, env=None, token=None, revision=None):
    parser = argparse.ArgumentParser(description="Yogi replacement-builder recovery plan")
    sub = parser.add_subparsers(dest="command", required=True)
    planned = sub.add_parser("plan")
    planned.add_argument("--operator", required=True)
    planned.add_argument("--out", required=True)
    exported = sub.add_parser("export-bundle")
    exported.add_argument("--out", required=True)
    for name in ("preflight", "apply", "cleanup"):
        command = sub.add_parser(name)
        command.add_argument("--op", required=True)
        command.add_argument("--confirm-instance", required=True)
        command.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "export-bundle":
            result, code = {"dryRun": True, "executed": False, "artifacts": export_bundle(args.out)}, 0
        elif args.command == "plan":
            document = plan(args.operator, args.out, now=now, token=token, revision=revision, env=env)
            result, code = {"dryRun": True, "executed": False, "operationId": document["operationId"],
                            "payloadSha256": document["payloadSha256"],
                            "startupScriptSha256": document["startupScriptSha256"],
                            "artifacts": document["artifacts"], "historicalReferenceReplayed": False}, 0
        elif args.command == "preflight":
            result = preflight(args.op, args.confirm_instance, args.execute, transport or RealTransport(), now, env)
            code = 0 if result["result"] in ("dry-run", "preflight-ok") else 1
        elif args.command == "apply":
            result = apply(args.op, args.confirm_instance, args.execute, transport or RealTransport(), now, clock, sleep, env)
            code = result.get("exit", 0 if result.get("dryRun") else 1)
        else:
            result = cleanup(args.op, args.confirm_instance, args.execute, transport or RealTransport(), now, env)
            code = result.get("exit", 0 if result.get("result") in ("dry-run", "nothing-started") else 1)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
