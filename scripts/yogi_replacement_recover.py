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
import gzip
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
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
GUEST_ATTRIBUTES_KEY = "enable-guest-attributes"
HOSTKEY_PERMISSION = "compute.instances.getGuestAttributes"
POLICY_SECONDS = 45 * 60
CLEANUP_RESERVE_SECONDS = 10 * 60
WORK_BUDGET_SECONDS = POLICY_SECONDS - CLEANUP_RESERVE_SECONDS
SERIAL_DELAYS = (180,)
IAM_REQUIRED = {
    "replacement": ("compute.instances.get", "compute.instances.setMetadata", "compute.instances.start",
                    "compute.instances.stop", "compute.instances.getSerialPortOutput"),
    "original": ("compute.instances.get",),
    "project": ("compute.disks.get", "compute.snapshots.get", "compute.firewalls.list",
                "compute.zoneOperations.get"),
}
READ_ONLY_KINDS = {"auth-list", "describe-instance", "describe-disk", "describe-snapshot", "list-firewalls",
                   "test-iam", "serial", "guest-attribute"}
DOCS = {
    "setMetadata": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/setMetadata",
    "metadataKeepExisting": "https://docs.cloud.google.com/compute/docs/metadata/setting-custom-metadata",
    "startupScript": "https://docs.cloud.google.com/compute/docs/instances/startup-scripts/linux",
    "serialPort": "https://docs.cloud.google.com/compute/docs/troubleshooting/viewing-serial-port-output",
    "guestAttributes": "https://docs.cloud.google.com/compute/docs/metadata/manage-guest-attributes",
    "guestAttributeHostKeys": "https://docs.cloud.google.com/solutions/connecting-securely",
    "iapTcp": "https://docs.cloud.google.com/compute/docs/connect/ssh-using-iap",
    "instanceIdentity": "https://docs.cloud.google.com/compute/docs/instances/verifying-instance-identity",
    "maxRunDuration": "https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime",
    "scheduling": "https://docs.cloud.google.com/compute/docs/instances/setting-instance-scheduling-options",
    "testIamPermissions": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/testIamPermissions",
    "resourceManagerTestIamPermissions": "https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions",
    "zoneOperationsWait": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/zoneOperations/wait",
    "zoneOperationsGet": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/zoneOperations/get",
    "instancesStart": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/start",
    "instancesStop": "https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/stop",
}
PLAN_TTL = datetime.timedelta(hours=12)
PREFLIGHT_TTL = datetime.timedelta(minutes=20)
METADATA_URL = ("https://compute.googleapis.com/compute/v1/projects/protocols-465723/zones/us-west2-b/instances/"
                "roseos-yogi-builder-20261105-b/setMetadata")
START_URL = METADATA_URL[:METADATA_URL.rindex("/")] + "/start"
STOP_URL = METADATA_URL[:METADATA_URL.rindex("/")] + "/stop"
METADATA_WAIT_SECONDS = 180
START_WAIT_SECONDS = 180
STOP_WAIT_SECONDS = 180
PLAN_SCHEMA = "roseos.yogi-replacement-recover-plan.v1"
PREFLIGHT_SCHEMA = "roseos.yogi-replacement-recover-preflight.v1"
OUTCOME_SCHEMA = "roseos.yogi-replacement-recover-outcome.v1"
GITHUB_CONTEXT = ("GITHUB_ACTIONS", "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                  "ACTIONS_RUNTIME_TOKEN", "ACTIONS_RUNTIME_URL", "ACTIONS_CACHE_URL", "GITHUB_TOKEN")
SPENT_OPERATION_IDS = frozenset({
    "e75b0fd782e43f585c621c74f95bfa90",
    "5cf6fe9a62880b89ee6e7e822a0379d2",
    "20ef6008b1ef78137d201de13176af42",
    "638e6e9039e41b7531697bb45066a616",
})
SPENT_ZONE_OPERATIONS = frozenset({
    "operation-1794025967141-65fa8387d4384-e9bed427-7ed969ab",
    "operation-1794025969883-65fa838a71873-581e64cf-6e23ae4e",
    "operation-1794026872649-65fa86e763554-67c3c575-aa4d2c66",
    "operation-1794026874731-65fa86e95fb00-d547145e-45241669",
    "operation-1794027065214-65fa879f086ca-4cf61ef5-4480e200",
    "operation-1794027069962-65fa87a38f91f-6bc72969-2fdd5f07",
})
OPERATION_NAME = re.compile(r"operation-[0-9]{10,16}-[0-9a-f]{8,20}-[0-9a-f]{6,20}-[0-9a-f]{6,20}")
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
    "gcloud-compute-ssh-is-not-used",
    "iap-does-not-authenticate-the-ssh-host-key",
    "guest-attributes-do-not-prove-an-honest-guest",
    "serial-output-is-guest-written",
    "unknown-host-key-names-stop-rotation",
    "unit-hardlink-or-unexpected-mode-is-not-changed",
    "metadata-write-is-single-attempt",
    "pending-zone-operation-is-waited-not-resubmitted",
    "inspect-receipt-is-guest-evidence-not-a-pin",
    "urllib-metadata-client-is-not-a-fix-for-the-unknown-internal-error",
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
        raise Refused("GOOGLE_APPLICATION_CREDENTIALS is set in this process. For this command only, run it as "
                      "`env -u GOOGLE_APPLICATION_CREDENTIALS` so gcloud uses the already-active account. "
                      "Do not copy, print, or request a key file.")
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


def render_guest(operation_id, nonce, template, op_token="@@OP@@", nonce_token="@@NONCE@@", extra=None):
    if template.count(op_token) != 1 or template.count(nonce_token) != 1:
        raise Refused("guest template placeholders are not unique")
    if not HEX32.fullmatch(operation_id or "") or not HEX32.fullmatch(nonce or "") or operation_id == nonce:
        raise Refused("binding generator failed")
    rendered = template.replace(op_token, operation_id).replace(nonce_token, nonce)
    for token, value in (extra or {}).items():
        if template.count(token) != 1 or not isinstance(value, str) or "@@" in value:
            raise Refused("guest template placeholders are not unique")
        rendered = rendered.replace(token, value)
    if op_token in rendered or nonce_token in rendered or any(token in rendered for token in (extra or {})):
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


def removal_items(items, script, binding_text, guest_attributes_before=None, publish=False):
    """Drop this operation's startup-script and binding only when the values match exactly.

    Every other key is copied through. A rotate-publish submission may also have added
    enable-guest-attributes=TRUE. That key is removed only when this operation added it. A preexisting
    TRUE or FALSE is restored to that exact value. A different value in a preserved key is not written.
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
    kept = {key: value for key, value in items.items() if key not in drop}
    if not publish:
        return kept
    if guest_attributes_before == "unknown":
        return None
    current = kept.get(GUEST_ATTRIBUTES_KEY)
    if guest_attributes_before is None:
        if current == "TRUE":
            kept.pop(GUEST_ATTRIBUTES_KEY, None)
        elif current is not None:
            return None
        return kept
    if guest_attributes_before not in ("TRUE", "FALSE"):
        return None
    if current not in ("TRUE", "FALSE"):
        return None
    kept[GUEST_ATTRIBUTES_KEY] = guest_attributes_before
    return kept


def metadata_admission(items, mode):
    """Base keys must match. Only rotate-publish may see other keys, and it keeps them."""
    if not isinstance(items, dict):
        return ["metadata"]
    for key, value in BASE_METADATA.items():
        if items.get(key) != value:
            return ["metadata"]
    if "startup-script" in items or BINDING_KEY in items:
        return ["metadata"]
    if mode != "rotate-publish":
        return ["metadata"] if items != BASE_METADATA else []
    flag = items.get(GUEST_ATTRIBUTES_KEY)
    if flag is not None and flag not in ("TRUE", "FALSE"):
        return ["metadata"]
    for key, value in items.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,127}", key):
            return ["metadata"]
        if not isinstance(value, str) or len(value) > 32768:
            return ["metadata"]
    return []


def metadata_submission(items, script, binding_text, publish):
    """One setMetadata body. rotate-publish turns guest attributes on in this same map."""
    intended = dict(items)
    intended["startup-script"] = script
    intended[BINDING_KEY] = binding_text
    if publish:
        intended[GUEST_ATTRIBUTES_KEY] = "TRUE"
    return intended


def publication_context(path):
    """What this operation added, from started.json. Missing means do not touch the enable flag."""
    try:
        record = json.loads((Path(path) / "started.json").read_text())
    except (OSError, ValueError):
        return False, None
    if not isinstance(record, dict) or record.get("publishHostkeys") is not True:
        return False, None
    if "guestAttributesBefore" not in record:
        return True, "unknown"
    before = record.get("guestAttributesBefore")
    if before is not None and before not in ("TRUE", "FALSE"):
        return True, "unknown"
    return True, before


def startup_script_absent(view):
    return isinstance(view, dict) and "startup-script" not in view["items"] and BINDING_KEY not in view["items"]


def compute_resource_path(value):
    """Path after the Compute API prefix. Response links use either documented host."""
    if not isinstance(value, str):
        return None
    for prefix in ("https://www.googleapis.com/compute/v1/", "https://compute.googleapis.com/compute/v1/"):
        if value.startswith(prefix):
            return value[len(prefix):].rstrip("/")
    return None


def operation_url(name, wait):
    """Build our zone operation URL. The name is checked; a selfLink from the response is never followed."""
    if name in SPENT_ZONE_OPERATIONS or not OPERATION_NAME.fullmatch(name or ""):
        raise Refused("operation handle is not a fresh operation for this replacement")
    base = ("https://compute.googleapis.com/compute/v1/projects/" + PROJECT + "/zones/" + ZONE
            + "/operations/" + name)
    return base + "/wait" if wait else base


def _operation_error_code(body):
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("code") or "operation-error")[:40]
        return str(error.get("status") or error.get("code") or "http")[:40]
    code = body.get("httpErrorStatusCode") if isinstance(body, dict) else None
    if isinstance(code, int) and code >= 400:
        return "http-" + str(code)
    return ""


def interpret_operation(status, body, operation_type, expected_name=None):
    """Classify one Operation response. PENDING and RUNNING are accepted, not failures.

    `ok` is true only for a finished operation with no error and this replacement's identity.
    A non-200 response, a null body, or a body with no operation name is a submission or transport
    failure. Those do not produce a handle to wait on.
    """
    attempt = {"ok": False, "unparseable": False, "disposition": "submission-error", "httpStatus": status,
               "operationStatus": "none", "errorCode": "", "targetId": "", "name": "", "operationType": ""}
    if not isinstance(body, dict):
        attempt["unparseable"] = True
        attempt["errorCode"] = "unparseable"
        attempt["disposition"] = "malformed"
        return attempt
    if status != 200:
        attempt["errorCode"] = _operation_error_code(body) or "http"
        return attempt
    name = body.get("name")
    if not isinstance(name, str) or not OPERATION_NAME.fullmatch(name) or name in SPENT_ZONE_OPERATIONS:
        attempt["disposition"] = "malformed"
        attempt["errorCode"] = "no-operation"
        attempt["unparseable"] = "status" not in body and "name" not in body
        return attempt
    if expected_name is not None and name != expected_name:
        attempt["disposition"] = "mismatch"
        attempt["errorCode"] = "handle"
        attempt["name"] = name
        return attempt
    attempt["name"] = name
    attempt["operationType"] = str(body.get("operationType") or "")
    attempt["operationStatus"] = str(body.get("status") or "")
    attempt["targetId"] = str(body.get("targetId") or "")
    zone_path = compute_resource_path(body.get("zone"))
    self_path = compute_resource_path(body.get("selfLink"))
    target_path = compute_resource_path(body.get("targetLink"))
    expected_self = "projects/" + PROJECT + "/zones/" + ZONE + "/operations/" + name
    expected_zone = "projects/" + PROJECT + "/zones/" + ZONE
    expected_target = expected_zone + "/instances/" + INSTANCE
    problems = []
    if body.get("kind") != "compute#operation":
        problems.append("kind")
    if zone_path != expected_zone:
        problems.append("zone")
    if self_path != expected_self:
        problems.append("selfLink")
    if attempt["operationType"] != operation_type:
        problems.append("operationType")
    if attempt["targetId"] != INSTANCE_ID:
        problems.append("target")
    if target_path != expected_target:
        problems.append("targetLink")
    if body.get("region"):
        problems.append("region")
    if attempt["operationStatus"] not in ("PENDING", "RUNNING", "DONE"):
        problems.append("status")
    if problems:
        attempt["disposition"] = "mismatch"
        attempt["errorCode"] = problems[0]
        return attempt
    error_code = _operation_error_code(body)
    if error_code or body.get("error"):
        attempt["disposition"] = "done-error"
        attempt["errorCode"] = error_code or "operation-error"
        return attempt
    if attempt["operationStatus"] == "DONE":
        attempt["disposition"] = "done"
        attempt["ok"] = True
        return attempt
    attempt["disposition"] = "accepted"
    attempt["errorCode"] = ""
    return attempt


def interpret_metadata_http(status, body):
    """setMetadata responses are Operation resources. DONE is not required of the first response."""
    return interpret_operation(status, body, "setMetadata")


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
    clean = attempt.get("ok") is True and attempt.get("disposition") == "done" \
        and attempt.get("targetId") == INSTANCE_ID and not attempt.get("errorCode")
    if clean and after_items == intended and after_fp != before_fp and before_items != intended:
        return "clean"
    if after_items == before_items and after_fp == before_fp:
        if attempt.get("disposition") == "pending-timeout":
            return "pending-timeout"
        if attempt.get("disposition") == "mismatch":
            return "mismatch"
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


def firewall_list_argv(project):
    """No server-side sourceRanges filter.

    The operator preflight sent `direction=INGRESS AND sourceRanges:35.235.240.0/20` and Compute Engine rejected
    it as an invalid list filter. `gcloud compute firewall-rules list` forwards `--filter` to that API
    (https://cloud.google.com/compute/docs/reference/rest/v1/firewalls/list). This list is unfiltered. Direction,
    disabled, network, target tags, source, protocol, port, and priority are checked on the returned rules.
    """
    return ["gcloud", "compute", "firewall-rules", "list", project, "--format=json", "--quiet"]


def _network_name(value):
    if not isinstance(value, str) or not value or ".." in value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1]


def _covers_iap(ranges):
    try:
        needed = ipaddress.ip_network(IAP_RANGE, strict=False)
    except ValueError:
        return False
    for item in ranges or []:
        try:
            network = ipaddress.ip_network(item, strict=False)
        except (TypeError, ValueError):
            continue
        if needed.subnet_of(network):
            return True
    return False


def _tcp22(entries):
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        protocol = str(entry.get("IPProtocol") or "").lower()
        if protocol not in ("tcp", "6", "all"):
            continue
        ports = entry.get("ports")
        if ports in (None, []) or (isinstance(ports, list) and "22" in [str(port) for port in ports]):
            return True
    return False


def _priority(rule):
    value = rule.get("priority", 1000)
    if value is True or value is False:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 65535:
        return None
    return number


def firewall_problems(rules, instance):
    """Client-side IAP SSH admission. A missing or ambiguous winning rule is not treated as allowed."""
    if not isinstance(rules, list) or not isinstance(instance, dict):
        return ["iap-firewall"]
    nics = [nic for nic in instance.get("networkInterfaces") or [] if isinstance(nic, dict)]
    network = _network_name(nics[0].get("network")) if nics else None
    tags = [item for item in ((instance.get("tags") or {}).get("items") or []) if isinstance(item, str)]
    if not network:
        return ["iap-firewall-network"]
    matched = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("disabled") is True:
            continue
        if rule.get("direction") != "INGRESS":
            continue
        if _network_name(rule.get("network")) != network:
            continue
        targets = rule.get("targetTags") or []
        if targets and not set(targets) & set(tags):
            continue
        if not _covers_iap(rule.get("sourceRanges") or []):
            continue
        priority = _priority(rule)
        if priority is None:
            return ["iap-firewall-priority"]
        if rule.get("denied") and _tcp22(rule.get("denied")):
            matched.append((priority, "deny"))
        elif rule.get("allowed") and _tcp22(rule.get("allowed")) and not rule.get("denied"):
            matched.append((priority, "allow"))
    if not matched:
        return ["iap-firewall"]
    best = min(item[0] for item in matched)
    winners = [item[1] for item in matched if item[0] == best]
    if "deny" in winners or "allow" not in winners:
        return ["iap-firewall-denied"]
    return []


def iam_url(target):
    """Instance checks use Compute Engine. Project checks use Resource Manager.

    `POST https://compute.googleapis.com/compute/v1/projects/protocols-465723/testIamPermissions` returned null on
    the operator preflight. That Compute project URL is not the documented project IAM test. Resource Manager is
    `POST https://cloudresourcemanager.googleapis.com/v1/projects/{resource}:testIamPermissions`
    (https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions).
    Instance checks stay on
    https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/testIamPermissions.
    """
    if target == "replacement":
        return ("https://compute.googleapis.com/compute/v1/projects/" + PROJECT + "/zones/" + ZONE
                + "/instances/" + INSTANCE + "/testIamPermissions")
    if target == "original":
        return ("https://compute.googleapis.com/compute/v1/projects/" + PROJECT + "/zones/" + ORIGINAL_ZONE
                + "/instances/" + ORIGINAL_INSTANCE + "/testIamPermissions")
    if target == "project":
        return "https://cloudresourcemanager.googleapis.com/v1/projects/" + PROJECT + ":testIamPermissions"
    raise Refused("permission check target is outside the recovery set")


def permissions_from_iam_body(parsed):
    """A null body or a null permissions field is not a grant. Only an explicit list is a result."""
    if not isinstance(parsed, dict) or "permissions" not in parsed:
        return None
    granted = parsed.get("permissions")
    if not isinstance(granted, list) or any(not isinstance(item, str) for item in granted):
        return None
    return granted


def permissions_for(mode):
    required = {target: list(names) for target, names in IAM_REQUIRED.items()}
    if mode == "rotate-publish":
        required["replacement"].append(HOSTKEY_PERMISSION)
    return required


def permissions_accepted(target):
    base = list(IAM_REQUIRED[target])
    accepted = [base]
    if target == "replacement":
        accepted.append(base + [HOSTKEY_PERMISSION])
    return accepted


def iam_problems(found, mode="repair"):
    if not isinstance(found, dict):
        return ["permission-check-failed"]
    problems = []
    for target, required in permissions_for(mode).items():
        granted = found.get(target)
        if not isinstance(granted, list):
            problems.append("permission-check-failed")
            continue
        problems += ["missing-permission:" + name for name in required if name not in granted]
    return problems


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
                                                       "fingerprint", "items", "permissions", "operationType", "query"}:
        raise Refused("control-plane request is outside the recovery set")
    kind, target = request.get("kind"), request.get("target", "none")
    if "query" in request and (kind != "guest-attribute" or request.get("query") not in ("receipt", "hostkeys")):
        raise Refused("control-plane request is outside the recovery set")
    if kind in ("operation-wait", "operation-get") and target == "replacement" \
            and request.get("zone", ZONE) == ZONE and request.get("operationType") in ("setMetadata", "start", "stop") \
            and "items" not in request and "fingerprint" not in request \
            and OPERATION_NAME.fullmatch(request.get("name") or "") and request.get("name") not in SPENT_ZONE_OPERATIONS:
        return request
    if kind == "test-iam" and target in IAM_REQUIRED and isinstance(request.get("permissions"), list) \
            and request["permissions"] in permissions_accepted(target):
        return request
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
    if kind in ("set-metadata", "test-iam", "start", "stop", "operation-wait", "operation-get"):
        raise Refused("that request is one HTTP call, not a gcloud argv")
    if kind == "auth-list":
        return ["gcloud", "auth", "list", "--format=json", "--quiet"]
    if kind == "list-firewalls":
        return firewall_list_argv(project)
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
        query = "hostkeys/" if request.get("query") == "hostkeys" else "roseos-yogi-recover/receipt"
        return ["gcloud", "compute", "instances", "get-guest-attributes", INSTANCE, project, f"--zone={ZONE}",
                f"--query-path={query}", "--format=json", "--quiet"]
    raise Refused("control-plane request is outside the recovery set")


def token_argv():
    return ["gcloud", "auth", "print-access-token"]


class RealTransport:
    """Operator gcloud for reads. Mutations and operation waits are one fresh urllib call each.

    A wait or get posts or gets the zone operation URL only. It does not resend setMetadata, start, or stop.
    """

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

    def _http(self, url, method, payload, timeout):
        token = self._token()
        headers = {"Authorization": "Bearer " + token}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        http_request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener(http_request, timeout=timeout) as response:
                raw, status = response.read(1 << 20), getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            raw, status = error.read(1 << 20), error.code
        except Exception:
            raise TransportError("timeout") from None
        try:
            parsed = json.loads(raw.decode()) if raw else None
        except (ValueError, UnicodeError):
            parsed = None
        return {"httpStatus": status, "body": parsed}

    def _set(self, request):
        return self._http(METADATA_URL, "POST", metadata_body(request["fingerprint"], request["items"]),
                          min(60, int(request.get("timeout", 60))))

    def _action(self, request, url):
        return self._http(url, "POST", None, min(60, int(request.get("timeout", 60))))

    def _operation(self, request):
        waiting = request["kind"] == "operation-wait"
        timeout = min(150 if waiting else 30, max(1, int(request.get("timeout", 30))))
        return self._http(operation_url(request["name"], waiting), "POST" if waiting else "GET", None, timeout)

    def _iam(self, request):
        token = self._token()
        http_request = urllib.request.Request(
            iam_url(request["target"]), data=json.dumps({"permissions": list(request["permissions"])}).encode(),
            method="POST", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with self.opener(http_request, timeout=60) as response:
                raw, status = response.read(1 << 20), getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            raw, status = error.read(1 << 20), error.code
        except Exception:
            raise TransportError("timeout") from None
        if status != 200:
            raise TransportError("http")
        try:
            parsed = json.loads(raw.decode())
        except (ValueError, UnicodeError):
            raise TransportError("unparseable") from None
        granted = permissions_from_iam_body(parsed)
        if granted is None:
            raise TransportError("null-permissions")
        return {"permissions": granted}

    def __call__(self, request):
        guard_request(request)
        if request["kind"] == "set-metadata":
            return self._set(request)
        if request["kind"] == "start":
            return self._action(request, START_URL)
        if request["kind"] == "stop":
            return self._action(request, STOP_URL)
        if request["kind"] in ("operation-wait", "operation-get"):
            return self._operation(request)
        if request["kind"] == "test-iam":
            return self._iam(request)
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
        if request["kind"] in ("operation-wait", "operation-get"):
            entry["name"] = request["name"]
            entry["operationType"] = request["operationType"]
        if request["kind"] == "test-iam":
            entry["permissions"] = list(request.get("permissions") or [])
        log.append(entry)
        return transport(request)
    return call


def collect(call, mode="repair"):
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
    snapshot["iam"] = {}
    for target, permissions in permissions_for(mode).items():
        found = grab({"kind": "test-iam", "target": target, "permissions": list(permissions)})
        snapshot["iam"][target] = None if not isinstance(found, dict) else found.get("permissions")
    return snapshot


def apply_problems(snapshot, mode="repair"):
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
    if view is None or metadata_admission(view["items"], mode):
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
    problems += firewall_problems(snapshot.get("firewalls"), snapshot.get("replacement"))
    problems += iam_problems(snapshot.get("iam"), mode)
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


def blank_attempt(code, disposition="submission-error"):
    return {"ok": False, "unparseable": True, "disposition": disposition, "httpStatus": 0,
            "operationStatus": "none", "errorCode": code, "targetId": "", "name": "", "operationType": ""}


def complete_operation(call, response, operation_type, deadline, sleep):
    """Wait or get one accepted zone operation until DONE, the stage deadline, or a bad handle.

    zoneOperations.wait returns the current operation within about two minutes, which can still be
    PENDING or RUNNING. Another wait is the documented follow-up. It is not another mutation.
    """
    if not isinstance(response, dict) or "httpStatus" not in response:
        return blank_attempt("timeout")
    attempt = interpret_operation(response.get("httpStatus"), response.get("body"), operation_type)
    if attempt["disposition"] != "accepted":
        return attempt
    name = attempt["name"]
    stalls = 0
    while True:
        if deadline.expired():
            attempt["ok"] = False
            attempt["disposition"] = "pending-timeout"
            attempt["errorCode"] = "pending-timeout"
            return attempt
        remaining = deadline.remaining()
        kind = "operation-wait" if remaining >= 5 else "operation-get"
        timeout = min(150 if kind == "operation-wait" else 30, max(1, int(remaining)))
        before = remaining
        try:
            waited = call({"kind": kind, "target": "replacement", "name": name, "zone": ZONE,
                           "operationType": operation_type, "timeout": timeout})
        except TransportError:
            attempt["ok"] = False
            attempt["disposition"] = "pending-timeout"
            attempt["errorCode"] = "wait-timeout"
            return attempt
        if not isinstance(waited, dict) or waited.get("httpStatus") != 200:
            attempt["ok"] = False
            attempt["disposition"] = "pending-timeout"
            attempt["errorCode"] = "wait-http"
            return attempt
        nxt = interpret_operation(200, waited.get("body"), operation_type, name)
        if nxt["disposition"] == "malformed":
            attempt["ok"] = False
            attempt["disposition"] = "pending-timeout"
            attempt["errorCode"] = "wait-malformed"
            return attempt
        if nxt["disposition"] != "accepted":
            return nxt
        attempt = nxt
        pause = min(2.0, max(0.0, deadline.remaining() - 1))
        if pause:
            sleep(pause)
        if deadline.remaining() >= before - 0.001:
            stalls += 1
            if stalls >= 3:
                attempt["ok"] = False
                attempt["disposition"] = "pending-timeout"
                attempt["errorCode"] = "pending-timeout"
                return attempt


def submit_and_wait(call, request, operation_type, deadline, sleep):
    try:
        response = call(request)
    except TransportError:
        return blank_attempt("timeout")
    return complete_operation(call, response, operation_type, deadline, sleep)


def one_set(call, fingerprint, items, deadline=None, sleep=None):
    deadline = deadline or Deadline(time.monotonic, METADATA_WAIT_SECONDS)
    sleep = sleep or time.sleep
    attempt = submit_and_wait(
        call, {"kind": "set-metadata", "target": "replacement", "fingerprint": fingerprint, "items": items,
               "timeout": 60},
        "setMetadata", deadline, sleep)
    try:
        after = metadata_view(call({"kind": "describe-instance", "target": "replacement"}))
    except TransportError:
        after = None
    return attempt, after


def restore_metadata(call, script, binding_text, deadline=None, sleep=None, guest_attributes_before=None,
                     publish=False):
    """One preserving removal of this operation's exact metadata values, then one readback. No second write."""
    try:
        current = metadata_view(call({"kind": "describe-instance", "target": "replacement"}))
    except TransportError:
        return "readback-failed", None
    if current is None:
        return "readback-failed", None
    kept = removal_items(current["items"], script, binding_text, guest_attributes_before, publish)
    if kept is None:
        return "skipped-foreign-value", current
    if kept == current["items"]:
        return "already-base", current
    deadline = deadline or Deadline(time.monotonic, METADATA_WAIT_SECONDS)
    attempt, after = one_set(call, current["fingerprint"], kept, deadline, sleep or time.sleep)
    kind = classify_metadata(attempt, (current["items"], current["fingerprint"]),
                             None if after is None else (after["items"], after["fingerprint"]), kept)
    return kind, after


def ensure_stopped(call, start_attempted, clock=None, sleep=None):
    clock = clock or time.monotonic
    sleep = sleep or time.sleep

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
    # Two stop submissions, each waited on its own handle, then one read. The second covers one automaticRestart.
    for label in ("stopped", "stopped-after-restart"):
        submit_and_wait(call, {"kind": "stop", "target": "replacement", "timeout": 60}, "stop",
                        Deadline(clock, STOP_WAIT_SECONDS), sleep)
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
    if not isinstance(response, dict) or response.get("kind") not in (None, "compute#serialPortOutput") \
            or str(response.get("start")) != "0":
        raise Refused("serial cursor is not the start of a fresh read")
    nxt = str(response.get("next") or "")
    if not re.fullmatch(r"[0-9]{1,12}", nxt):
        raise Refused("serial cursor is not the start of a fresh read")
    return nxt


def read_new(call, cursor):
    response = call({"kind": "serial", "target": "replacement", "start": int(cursor), "timeout": 60})
    if not isinstance(response, dict) or response.get("kind") not in (None, "compute#serialPortOutput") \
            or str(response.get("start")) != str(cursor):
        raise guest.Malformed("cursor")
    nxt = str(response.get("next") or "")
    if not re.fullmatch(r"[0-9]{1,12}", nxt) or int(nxt) < int(cursor):
        raise guest.Malformed("cursor")
    return str(response.get("contents") or ""), nxt


def serial_evidence(binding, text, reason, detail="", receipt=None):
    """Allowlisted record of one serial window. Boot text and unfinished fragments are not stored."""
    lines = []
    for line in str(text).splitlines():
        if len(lines) >= 8:
            break
        at = line.find(guest.PREFIX)
        if at < 0:
            continue
        payload = line[at + len(guest.PREFIX):].strip()
        entry = {"length": len(payload), "sha256": sha256_bytes(payload.encode()), "redacted": True, "reason": "json"}
        try:
            parsed = json.loads(payload)
        except ValueError:
            lines.append(entry)
            continue
        if isinstance(parsed, dict) and not guest.validate_shape(parsed, binding):
            entry["redacted"] = False
            entry["reason"] = "parsed"
            entry["receipt"] = json.loads(guest.canonical(parsed))
        else:
            entry["reason"] = "shape"
        lines.append(entry)
    accepted = None
    if isinstance(receipt, dict) and not guest.validate_shape(receipt, binding):
        accepted = json.loads(guest.canonical(receipt))
    safe_reason = reason if re.fullmatch(r"[a-z0-9-]{1,40}", reason or "") else "unrecognized"
    safe_detail = detail if re.fullmatch(r"[a-z0-9-]{1,40}", detail or "") else ""
    return {"schema": "roseos.yogi-replacement-recover-serial-evidence.v1",
            "operationId": binding.get("operationId"), "reason": safe_reason, "detail": safe_detail,
            "totalBytes": len(str(text).encode()), "contentsSha256": sha256_bytes(str(text).encode()),
            "acceptedReceipt": accepted, "receiptLines": lines}


def save_serial_evidence(directory, binding, text, reason, detail="", receipt=None):
    if directory is None:
        return
    path = Path(directory) / "serial-evidence.json"
    if path.exists():
        return
    try:
        exclusive_json(path, serial_evidence(binding, text, reason, detail, receipt))
    except OSError:
        return


def collect_receipt(call, binding, deadline, sleep, evidence_dir=None):
    """Read serial only after the instance is RUNNING. The first read may include older bytes.

    A receipt is accepted only when it matches this operation's nonce. Output from before this
    operation cannot contain that nonce. A follow-up read uses the returned byte cursor. The
    serial console is still guest-written and is not a pin. An unparsed window is saved, without
    the raw console, before the caller classifies it.
    """
    if deadline.expired():
        return None, "budget"
    chunks = []
    cursor = "0"

    def consider(final):
        text = "".join(chunks)
        try:
            receipt = guest.parse_serial(text, binding, final=final)
        except guest.Malformed as error:
            save_serial_evidence(evidence_dir, binding, text, error.code, getattr(error, "detail", ""))
            return None, "serial-" + error.code
        if receipt is not None:
            save_serial_evidence(evidence_dir, binding, text, "parsed", receipt=receipt)
        return receipt, None

    try:
        text, cursor = read_new(call, cursor)
    except guest.Malformed:
        return None, "stale-cursor"
    except TransportError:
        return None, "serial-failed"
    chunks.append(text)
    receipt, failed = consider(False)
    if failed:
        return None, failed
    if receipt is not None:
        return receipt, "read"
    for index, delay in enumerate(SERIAL_DELAYS):
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
        receipt, failed = consider(index == len(SERIAL_DELAYS) - 1)
        if failed:
            return None, failed
        if receipt is not None:
            return receipt, "read"
    return None, "absent"


def attribute_text(response):
    """Guest-attribute text from the REST resource or the gcloud JSON projection.

    REST GuestAttributes uses variableValue and queryValue.items[].value
    (https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/getGuestAttributes).
    A flattened `{value}` or a one-element list of that object is the gcloud projection. Conflicting
    copies are not a match.
    """
    if isinstance(response, list):
        if len(response) != 1:
            return None
        response = response[0]
    if not isinstance(response, dict) or response.get("kind") not in (None, "compute#guestAttributes"):
        return None
    found = []
    if isinstance(response.get("variableValue"), str):
        found.append(response["variableValue"])
    if isinstance(response.get("value"), str):
        found.append(response["value"])
    query = response.get("queryValue")
    items = query.get("items") if isinstance(query, dict) else None
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("value"), str):
                found.append(item["value"])
    if not found or any(item != found[0] for item in found):
        return None
    return found[0]


def hostkey_consistency(response, serial_fingerprints):
    """Compare a getGuestAttributes hostkeys/ read with a serial receipt's public fingerprints.

    The API call is authenticated to this instance. The values were written by a process on that
    guest. Equality with the serial receipt is consistency between two guest publications. It is
    not a pin, not proof the guest is honest, and not an IAP result. IAP is not an input here:
    an IAP tunnel selects the instance and does not check the SSH host key.
    """
    if not isinstance(serial_fingerprints, dict) or set(serial_fingerprints) != set(guest.ALGORITHMS) \
            or any(not guest.FP.fullmatch(serial_fingerprints.get(alg) or "") for alg in guest.ALGORITHMS):
        raise Refused("serial fingerprints are not a public host-key set")
    if isinstance(response, dict) and response.get("absent") is True:
        return _hostkey_verdict(False, absent=True)
    items = _hostkey_items(response)
    found = {}
    by_alg = {}
    for item in items:
        namespace = item.get("namespace")
        key = item.get("key")
        value = item.get("value")
        if namespace not in (None, "hostkeys") or not isinstance(key, str) or not isinstance(value, str):
            raise Refused("hostkey attribute is not a public blob")
        if any(token in value for token in ("BEGIN", "PRIVATE", "OPENSSH", " ", "\n", "\r")):
            raise Refused("private-attribute")
        try:
            key_type, fingerprint = guest.parse_public(key + " " + value.strip())
        except guest.GuestRefused:
            raise Refused("hostkey attribute is not a public blob") from None
        if key_type != key:
            raise Refused("hostkey attribute is not a public blob")
        if key_type in found:
            raise Refused("hostkey attribute is not a public blob")
        found[key_type] = fingerprint
        matched = [alg for alg, kind in guest.PUBLIC_TYPES.items() if kind == key_type]
        if len(matched) != 1:
            raise Refused("hostkey attribute is not a public blob")
        by_alg[matched[0]] = fingerprint
    expected = {guest.PUBLIC_TYPES[alg]: serial_fingerprints[alg] for alg in guest.ALGORITHMS}
    return _hostkey_verdict(found == expected, fingerprints=by_alg)


def _hostkey_verdict(consistent, absent=False, fingerprints=None):
    return {
        "binding": "control-plane-read-of-this-instance-metadata-server",
        "namespace": "hostkeys",
        "consistentWithSerial": consistent,
        "absent": absent,
        "fingerprints": fingerprints or {},
        "serialRole": "consistency-only",
        "pinned": False,
        "honestGuest": False,
        "iapAuthenticatesHostKey": False,
        "requiresGuestServiceAccount": False,
    }


def _hostkey_items(response):
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        raise Refused("hostkey attribute is not a public blob")
    query = response.get("queryValue")
    items = query.get("items") if isinstance(query, dict) else None
    if isinstance(items, list):
        return items
    raise Refused("hostkey attribute is not a public blob")


def hostkey_observation_phase(mode):
    """rotate-publish enables guest attributes in the same setMetadata. Default rotate does neither."""
    publish = mode == "rotate-publish"
    return {
        "publishHostkeys": publish,
        "enable": "same-setMetadata-temporary-TRUE" if publish else "not-this-mode",
        "preservesForeignMetadata": publish,
        "restoresPreexistingEnableFlag": publish,
        "projectMetadata": "unchanged",
        "osLogin": "stays-FALSE",
        "preflightPermission": HOSTKEY_PERMISSION if publish else "not-required",
        "publishFailure": "partial-no-second-rotation-cleanup-and-stop" if publish else "not-this-mode",
        "read": "one-getGuestAttributes-hostkeys-on-this-instance" if publish else "not-this-mode",
        "serial": "consistency-with-operation-and-nonce",
        "pinned": False,
        "honestGuest": False,
        "iapAuthenticatesHostKey": False,
        "ssh": False,
        "gcloudComputeSsh": "refused",
        "privateKeyInAttributes": False,
        "tofu": False,
    }


def read_published_hostkeys(call, rotated, expected_baseline):
    """One getGuestAttributes of hostkeys/ on this instance. No retry and no SSH."""
    try:
        observed = call({"kind": "guest-attribute", "target": "replacement", "query": "hostkeys", "timeout": 30})
    except TransportError:
        return False, "hostkey-read-failed", "failed"
    try:
        verdict = hostkey_consistency(observed, rotated)
    except Refused:
        return False, "hostkey-malformed", "malformed"
    if verdict["absent"]:
        return False, "hostkey-absent", "absent"
    if isinstance(expected_baseline, dict) and verdict["fingerprints"] == expected_baseline:
        return False, "hostkey-stale", "stale"
    if not verdict["consistentWithSerial"]:
        return False, "hostkey-mismatch", "mismatch"
    return True, "read", "matched"


def attribute_state(call, receipt):
    try:
        response = call({"kind": "guest-attribute", "target": "replacement", "timeout": 30})
    except TransportError:
        return "failed"
    if isinstance(response, dict) and response.get("absent") is True:
        return "absent"
    text = attribute_text(response)
    if text is None:
        return "mismatch"
    try:
        parsed = json.loads(text)
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


def baseline_commitment(baseline):
    """SHA-256 of the three public fingerprints. This is not a host-key pin."""
    if not isinstance(baseline, dict):
        raise Refused("inspect baseline commitment does not match the reviewed receipt")
    payload = json.dumps({alg: baseline.get(alg) for alg in guest.ALGORITHMS}, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode())


def inspect_evidence_problems(receipt):
    """The inspect receipt is evidence the operator reviewed. It is not authority to pin a host key."""
    if not isinstance(receipt, dict):
        return ["inspect-evidence"]
    unit, runner, keys, cleanup = receipt.get("unit") or {}, receipt.get("runner") or {}, receipt.get("hostKeys") or {}, receipt.get("cleanup") or {}
    journal = receipt.get("journal") if isinstance(receipt.get("journal"), dict) else {}
    problems = []
    if receipt.get("purpose") != "inspect" or receipt.get("status") not in ("complete", "refused"):
        problems.append("inspect-evidence")
    if unit.get("action") != "not-modified" or unit.get("problem") != "already-0644":
        problems.append("inspect-unit")
    if (unit.get("beforeMode"), unit.get("afterMode")) != ("0644", "0644"):
        problems.append("inspect-mode")
    if (unit.get("beforeSize"), unit.get("afterSize")) != (guest.UNIT_SIZE, guest.UNIT_SIZE):
        problems.append("inspect-size")
    if (unit.get("beforeUid"), unit.get("beforeGid"), unit.get("afterUid"), unit.get("afterGid")) != (0, 0, 0, 0):
        problems.append("inspect-owner")
    if runner != {"active": "inactive", "enabled": "disabled", "process": False}:
        problems.append("inspect-runner")
    baseline = keys.get("baseline")
    if not isinstance(baseline, dict) or set(baseline) != set(guest.ALGORITHMS) \
            or any(not guest.FP.fullmatch(baseline.get(alg) or "") for alg in guest.ALGORITHMS):
        problems.append("inspect-baseline")
    if keys.get("rotated") not in ({},) or keys.get("distinctFromClonedBaseline") is not False \
            or keys.get("unknownHostKeyCount") != 0 or keys.get("rolledBack") is not False:
        problems.append("inspect-rotation")
    if cleanup.get("linesRemoved") != 0 or cleanup.get("tempFilesRemoved") != 0 \
            or cleanup.get("unidentifiedAuthorizedKeyLines") != 0 or cleanup.get("temporaryAccount") != "absent":
        problems.append("inspect-cleanup")
    if not isinstance(journal.get("unrecognized", 0), int) or not 0 <= journal.get("unrecognized", 0) <= 20:
        problems.append("inspect-journal")
    if not guest.HEX32.fullmatch(receipt.get("operationId") or ""):
        problems.append("inspect-evidence")
    return problems


def plan(operator, out, now=None, token=None, revision=None, repo=ROOT, template=None, env=None, mode="repair",
         inspect_receipt=None, confirm_inspect_operation=None, confirm_baseline=None, possess_comment=None):
    manual_context(env if env is not None else os.environ)
    now = now or now_utc()
    if mode not in ("repair", "inspect", "rotate", "rotate-publish"):
        raise Refused("mode must be repair, inspect, rotate, or rotate-publish")
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
    if operation_id in SPENT_OPERATION_IDS or nonce in SPENT_OPERATION_IDS:
        raise Refused("this operation id was already used and its preflight expired; plan a new binding")
    evidence = None
    if mode in ("rotate", "rotate-publish"):
        try:
            evidence = json.loads(Path(inspect_receipt).read_text())
        except (OSError, TypeError, ValueError):
            raise Refused("rotate requires the reviewed inspect receipt") from None
        evidence_problems = inspect_evidence_problems(evidence)
        if evidence_problems:
            raise Refused("inspect receipt is not a usable rotation baseline")
        if evidence["operationId"] in (operation_id, nonce):
            raise Refused("inspect evidence is not a new binding")
        if confirm_inspect_operation != evidence["operationId"]:
            raise Refused("inspect operation id does not match the reviewed receipt")
        if confirm_baseline != baseline_commitment(evidence["hostKeys"]["baseline"]):
            raise Refused("inspect baseline commitment does not match the reviewed receipt")
        raw_evidence = Path(inspect_receipt).read_text()
        if any(token in raw_evidence for token in ("BEGIN ", "PRIVATE", "OPENSSH")):
            raise Refused("inspect receipt contains key material")
    if template is None:
        if mode == "inspect":
            template = guest.inspect_template()
            op_token, nonce_token = "@@INSPECT_OP@@", "@@INSPECT_NONCE@@"
            extra = {"@@POSSESS_COMMENT@@": possess_comment or "roseos-yogi-possess-"}
        else:
            template = (HERE / "yogi_replacement_recover_guest.py").read_text()
            op_token, nonce_token = "@@OP@@", "@@NONCE@@"
            baseline = evidence["hostKeys"]["baseline"] if evidence else {}
            extra = {"@@PURPOSE@@": mode if mode in ("rotate", "rotate-publish") else "repair",
                     "@@FP_ED25519@@": baseline.get("ed25519", "unused"),
                     "@@FP_ECDSA@@": baseline.get("ecdsa", "unused"),
                     "@@FP_RSA@@": baseline.get("rsa", "unused")}
    else:
        op_token, nonce_token = ("@@INSPECT_OP@@", "@@INSPECT_NONCE@@") if mode == "inspect" else ("@@OP@@", "@@NONCE@@")
        extra = None
    rendered = render_guest(operation_id, nonce, template, op_token, nonce_token, extra)
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
        "schema": PLAN_SCHEMA, "mode": mode, "repairsGuest": mode == "repair",
        "rotatesHostKeys": mode in ("rotate", "rotate-publish"),
        "publishHostkeys": mode == "rotate-publish",
        "dryRun": True, "executed": False, "liveRecovery": False, "hostKeyPinned": False,
        "claim": "procedure-evidence-only", "operationId": operation_id, "nonce": nonce,
        "payloadSha256": payload_sha, "startupScriptSha256": sha256_bytes(script.encode()),
        "templateSha256": sha256_bytes(template.encode()), "sourceRevision": revision, "operator": operator,
        "createdAt": stamp(now), "expiresAt": stamp(now + PLAN_TTL),
        "binding": binding, "deadlines": {
            "policySeconds": POLICY_SECONDS, "workBudgetSeconds": WORK_BUDGET_SECONDS,
            "cleanupReserveSeconds": CLEANUP_RESERVE_SECONDS, "serialDelaysSeconds": list(SERIAL_DELAYS),
            "metadataAttempts": 1, "stopAttempts": 2,
            "metadataWaitSeconds": METADATA_WAIT_SECONDS, "startWaitSeconds": START_WAIT_SECONDS,
            "stopWaitSeconds": STOP_WAIT_SECONDS},
        "target": {"project": PROJECT, "zone": ZONE, "instance": INSTANCE, "instanceId": INSTANCE_ID,
                   "disk": DISK_NAME, "diskId": DISK_ID, "diskSizeGb": DISK_SIZE_GB},
        "readOnly": {"originalInstance": ORIGINAL_INSTANCE, "originalZone": ORIGINAL_ZONE,
                     "originalInstanceId": ORIGINAL_INSTANCE_ID, "originalDiskId": ORIGINAL_DISK_ID,
                     "snapshots": [{"name": PRESERVE_SNAPSHOT, "id": PRESERVE_SNAPSHOT_ID},
                                   {"name": PREKEY_SNAPSHOT, "id": PREKEY_SNAPSHOT_ID}]},
        "metadataBefore": BASE_METADATA,
        "metadataAdded": list(OUR_KEYS) + ([GUEST_ATTRIBUTES_KEY] if mode == "rotate-publish" else []),
        "unit": {"path": guest.UNIT_PATH,
                 "beforeMode": "0644" if mode in ("rotate", "rotate-publish") else "0664",
                 "afterMode": "0644", "size": guest.UNIT_SIZE,
                 "owner": "root:root", "runner": "inactive/disabled"},
        "hostKeys": {"inventory": list(guest.INVENTORY),
                     "rotate": list(guest.ALGORITHMS) if mode != "inspect" else [],
                     "baseline": "public fingerprints read on this boot of the replacement disk"},
        "inspectEvidence": None if evidence is None else {
            "role": "guest-evidence-not-authority", "operationId": evidence["operationId"],
            "baseline": evidence["hostKeys"]["baseline"],
            "baselineCommitment": baseline_commitment(evidence["hostKeys"]["baseline"]),
            "journalUnrecognized": evidence.get("journal", {}).get("unrecognized", 0),
            "priorCompletePhaseIsNotAuthority": True, "hostKeyPinned": False,
            "distinctFromOriginalVerified": False},
        "hostIdentity": {
            "pinned": False, "serialTrustedForPin": False, "repeatedGuestReportIsProof": False,
            "instanceIdentityToken": "unavailable-no-guest-service-account-and-does-not-carry-ssh-host-key",
            "privateKeyDelivery": "refused",
            "iapAuthenticatesHostKey": False,
            "gcloudComputeSsh": "refused-implicit-metadata-and-known-hosts-writes",
            "guestAttributes": {
                "requiresGuestServiceAccount": False,
                "enabledOnThisInstance": False,
                "namespace": "hostkeys",
                "publisher": "rotate-publish-public-blob-only" if mode == "rotate-publish" else "not-this-mode",
                "stockAgent": "version-dependent-publishes-on-generation-only",
                "controlPlaneRead": "getGuestAttributes-names-this-instance",
                "serialRole": "consistency-only",
                "networkWrongHost": "control-plane-binding-when-the-attribute-is-enabled-and-read-back",
                "compromisedGuest": "not-a-defense",
            },
            "nextPhase": hostkey_observation_phase(mode),
        },
        "cleanup": {"authorizedKeyComment": "roseos-yogi-recover-<operationId>",
                    "temporaryAccount": guest.TEMP_ACCOUNT, "paths": list(guest.AUTH_PATHS)},
        "route": "gce-metadata-startup-script-and-bounded-serial-read",
        "docs": DOCS, "operatorAccount": OPERATOR_ACCOUNT,
        "permissionsExpected": ["compute.instances.get", "compute.instances.setMetadata", "compute.instances.start",
                                "compute.instances.stop", "compute.instances.getSerialPortOutput",
                                "compute.disks.get", "compute.snapshots.get", "compute.firewalls.list"]
                               + ([HOSTKEY_PERMISSION] if mode == "rotate-publish" else []),
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
    if binding["operationId"] in SPENT_OPERATION_IDS or binding["nonce"] in SPENT_OPERATION_IDS:
        raise Refused("this operation id was already used and its preflight expired; plan a new binding")
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
        return outcome_shell(dryRun=True, executed=False, cloudReadOnly=False, mutated=False, result="dry-run",
                             operationId=loaded["binding"]["operationId"])
    log = []

    def readonly(request):
        if request.get("kind") not in READ_ONLY_KINDS:
            raise Refused("preflight is read-only and cannot start, stop, or set metadata")
        return transport(request)

    mode = loaded["plan"].get("mode", "repair")
    snapshot = collect(logged(readonly, log), mode)
    problems = apply_problems(snapshot, mode)
    view = metadata_view(snapshot.get("replacement")) if isinstance(snapshot.get("replacement"), dict) else None
    moment = now or now_utc()
    record = {"schema": PREFLIGHT_SCHEMA, "operationId": loaded["binding"]["operationId"],
              "nonce": loaded["binding"]["nonce"], "payloadSha256": loaded["binding"]["payloadSha256"],
              "cloudReadOnly": True, "mutated": False,
              "fingerprint": None if view is None else view["fingerprint"], "observedAt": stamp(moment),
              "expiresAt": stamp(moment + PREFLIGHT_TTL), "problems": problems,
              "replacementStatus": (snapshot.get("replacement") or {}).get("status") if isinstance(snapshot.get("replacement"), dict) else None,
              "originalStatus": (snapshot.get("original") or {}).get("status") if isinstance(snapshot.get("original"), dict) else None,
              "requests": log}
    (loaded["path"] / "preflight.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return outcome_shell(result="preflight-ok" if not problems else "preflight-blocked", cloudReadOnly=True,
                         mutated=False, operationId=loaded["binding"]["operationId"], problems=problems, requests=log,
                         fingerprint=record["fingerprint"], permissions=snapshot.get("iam"))


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


def inspect_reported(receipt):
    """An inspect receipt inventoried the guest and changed nothing. It is not a repair and not a pin."""
    if not isinstance(receipt, dict) or receipt.get("purpose") != "inspect" \
            or receipt.get("status") not in ("complete", "refused"):
        return False
    unit, keys, cleanup = receipt.get("unit") or {}, receipt.get("hostKeys") or {}, receipt.get("cleanup") or {}
    return unit.get("action") == "not-modified" and not keys.get("rotated") \
        and keys.get("rolledBack") is False and keys.get("distinctFromClonedBaseline") is False \
        and cleanup.get("linesRemoved") == 0 and cleanup.get("tempFilesRemoved") == 0 \
        and cleanup.get("temporaryAccount") in ("absent", "preserved-mismatch")


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
    clock_fn = clock or time.monotonic
    sleep_fn = sleep or time.sleep

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
            publish, attributes_before = publication_context(op)
            try:
                restore, after = restore_metadata(
                    call, script, binding_text, Deadline(clock_fn, METADATA_WAIT_SECONDS), sleep_fn,
                    attributes_before, publish)
            except Exception:
                restore, after = "seal-failed", None
        try:
            stop = ensure_stopped(call, state["start"], clock_fn, sleep_fn)
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
        mode = fields.pop("mode", "repair")
        stopped = stop in ("stopped", "stopped-after-restart")
        restored = restore in ("clean", "already-base") and removed
        if mode == "inspect" and guest_ok and restored and stopped and fields.get("metadata") == "clean":
            fields["result"] = "inspect-complete"
            fields["repairSucceeded"] = False
            code = 0
        elif mode == "rotate" and guest_ok and restored and stopped and fields.get("metadata") == "clean":
            fields["result"] = "rotate-complete-guest-reported"
            fields["repairSucceeded"] = False
            fields["rotationSucceeded"] = True
            fields["hostKeyPinned"] = False
            code = 0
        elif mode == "rotate-publish" and guest_ok and restored and stopped and fields.get("metadata") == "clean":
            fields["result"] = "rotate-publish-complete"
            fields["repairSucceeded"] = False
            fields["rotationSucceeded"] = True
            fields["hostkeysPublished"] = True
            fields["hostKeyPinned"] = False
            code = 0
        elif mode == "repair" and guest_ok and restored and stopped and fields.get("metadata") == "clean":
            fields["result"] = "procedure-complete-guest-reported"
            fields["repairSucceeded"] = True
            code = 0
        else:
            fields.setdefault("result", "halted-" + str(reason))
            fields["repairSucceeded"] = False
            if fields["result"] in ("procedure-complete-guest-reported", "inspect-complete",
                                    "rotate-complete-guest-reported", "rotate-publish-complete"):
                fields["result"] = "halted-stop"
            code = 1
        return _write_outcome(op, outcome_shell(**fields), code)

    def body():
        mode = loaded["plan"].get("mode")
        if mode not in ("inspect", "repair", "rotate", "rotate-publish"):
            raise Refused("operation mode is not inspect, repair, rotate, or rotate-publish")
        snapshot = collect(call, mode)
        problems = apply_problems(snapshot, mode)
        view = metadata_view(snapshot.get("replacement")) if isinstance(snapshot.get("replacement"), dict) else None
        preserved = observed_ids(snapshot)
        if problems or view is None:
            return seal({"result": "preflight-mismatch", "operationId": binding["operationId"], "problems": problems,
                         "requests": log, "metadata": "not-attempted", "preserved": preserved})
        if not ids_match(preserved):
            return seal({"result": "preserved-id-mismatch", "operationId": binding["operationId"], "requests": log,
                         "metadata": "not-attempted", "preserved": preserved})
        if view["fingerprint"] != pre["fingerprint"] or metadata_admission(view["items"], mode):
            return seal({"result": "stale-fingerprint", "operationId": binding["operationId"], "requests": log,
                         "metadata": "not-attempted", "preserved": preserved})
        publish = mode == "rotate-publish"
        intended = metadata_submission(view["items"], script, binding_text, publish)
        exclusive_json(op / "started.json", {"operationId": binding["operationId"], "nonce": binding["nonce"],
                                            "fingerprint": view["fingerprint"], "at": stamp(moment),
                                            "publishHostkeys": publish,
                                            "guestAttributesBefore": view["items"].get(GUEST_ATTRIBUTES_KEY)})
        state["marker"] = True
        attempt, after = one_set(call, view["fingerprint"], intended,
                                 Deadline(clock_fn, METADATA_WAIT_SECONDS), sleep_fn)
        kind = classify_metadata(attempt, (view["items"], view["fingerprint"]),
                                 None if after is None else (after["items"], after["fingerprint"]), intended)
        if kind != "clean":
            return seal({"result": "metadata-" + kind, "operationId": binding["operationId"], "requests": log,
                         "metadata": kind, "metadataError": attempt.get("errorCode"),
                         "operation": attempt.get("name") or None,
                         "operationStatus": attempt.get("operationStatus"), "preserved": preserved})
        work = Deadline(clock_fn, WORK_BUDGET_SECONDS)
        if work.expired() or work.remaining() < 30:
            return seal({"result": "budget-before-start", "operationId": binding["operationId"], "requests": log,
                         "metadata": "clean", "preserved": preserved})
        state["start"] = True
        start_budget = min(START_WAIT_SECONDS, max(1, int(work.remaining())))
        started_op = submit_and_wait(
            call, {"kind": "start", "target": "replacement", "timeout": min(60, start_budget)},
            "start", Deadline(clock_fn, start_budget), sleep_fn)
        try:
            started = call({"kind": "describe-instance", "target": "replacement"})
        except TransportError:
            started = None
        running = isinstance(started, dict) and replacement_identity_ok(started) and started.get("status") == "RUNNING"
        if not (started_op.get("ok") and started_op.get("operationType") == "start" and running):
            return seal({"result": "start-unconfirmed", "operationId": binding["operationId"], "requests": log,
                         "metadata": "clean", "operation": started_op.get("name") or None,
                         "operationStatus": started_op.get("operationStatus"), "preserved": preserved})
        try:
            receipt, reason = collect_receipt(call, binding, work, sleep_fn, op)
        except Refused:
            receipt, reason = None, "serial-cursor-failed"
        attribute = None
        guest_ok = False
        mode = loaded["plan"].get("mode")
        if mode not in ("inspect", "repair", "rotate", "rotate-publish"):
            raise Refused("operation mode is not inspect, repair, rotate, or rotate-publish")
        if mode == "inspect":
            guest_ok = bool(receipt) and reason == "read" and inspect_reported(receipt)
        elif mode == "rotate" and receipt is not None and reason == "read":
            expected = (loaded["plan"].get("inspectEvidence") or {}).get("baseline")
            attribute = attribute_state(call, receipt)
            if attribute == "mismatch":
                reason = "attribute-mismatch"
            elif attribute == "failed":
                reason = "attribute-failed"
            elif guest.rotate_success_problems(receipt, expected):
                reason = "guest-incomplete"
            else:
                guest_ok = True
        elif mode == "rotate-publish" and receipt is not None and reason == "read":
            expected = (loaded["plan"].get("inspectEvidence") or {}).get("baseline")
            if guest.rotate_success_problems(receipt, expected, purpose="rotate-publish") \
                    or receipt.get("operationId") != binding["operationId"] \
                    or receipt.get("nonce") != binding["nonce"]:
                reason = "guest-incomplete"
            elif work.expired():
                reason = "budget"
            else:
                guest_ok, reason, attribute = read_published_hostkeys(
                    call, receipt["hostKeys"]["rotated"], expected)
        elif receipt is not None and reason == "read":
            attribute = attribute_state(call, receipt)
            if attribute == "mismatch":
                reason = "attribute-mismatch"
            elif attribute == "failed":
                reason = "attribute-failed"
            elif guest.success_problems(receipt):
                reason = "guest-incomplete"
            else:
                guest_ok = True
        return seal({"operationId": binding["operationId"], "requests": log, "metadata": "clean", "mode": mode,
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
                    ensure_stopped(call, True, clock_fn, sleep_fn)
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
        stop = ensure_stopped(call, True, time.monotonic, time.sleep) if instance is None else "identity-mismatch"
        document = outcome_shell(result="cleanup-identity-mismatch" if instance is not None else "cleanup-read-failed",
                                 operationId=loaded["binding"]["operationId"], requests=log, restore="not-attempted",
                                 stop=stop, preserved=preserved, preservedIdsMatch=ids_match(preserved),
                                 startupScriptRemoved=False, exit=1)
    else:
        publish, attributes_before = publication_context(loaded["path"])
        restore, after = restore_metadata(call, script, binding_text, guest_attributes_before=attributes_before,
                                         publish=publish)
        stop = ensure_stopped(call, instance.get("status") != "TERMINATED", time.monotonic, time.sleep)
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


def bundle_unexpected(directory):
    directory = Path(directory)
    sums = directory / "SHA256SUMS"
    if not sums.is_file():
        raise Refused("bundle manifest is missing")
    listed = set()
    for line in sums.read_text().splitlines():
        digest, name = line.split("  ", 1)
        if "/" in name or name.startswith(".") or name in ("SHA256SUMS",) or not SHA256.fullmatch(digest):
            raise Refused("bundle manifest entry is not a single file name")
        if sha256_bytes((directory / name).read_bytes()) != digest:
            raise Refused("bundle manifest does not match " + name)
        listed.add(name)
    unexpected = []
    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory).as_posix()
        if path.is_dir():
            if path.name == "__pycache__" or any(part == "__pycache__" for part in path.parts):
                unexpected.append(rel)
            continue
        if rel == "SHA256SUMS":
            continue
        if rel.endswith((".pyc", ".pyo")) or "__pycache__" in rel.split("/") or rel not in listed:
            unexpected.append(rel)
    if unexpected:
        raise Refused("bundle contains files that are not in SHA256SUMS: " + ", ".join(unexpected))
    return sorted(listed)


def self_check(directory=None):
    """Offline check that the inspect payload cannot repair and that a bundle has only manifested files."""
    guest.inspect_template()
    if any(token in guest.INSPECT_TEMPLATE for token in ("ssh-keygen", "userdel", "fchmod", "os.chmod", "BEGIN OPENSSH")):
        raise Refused("inspect payload is not read-only")
    directory = Path(directory) if directory is not None else HERE
    checked = bundle_unexpected(directory) if (directory / "SHA256SUMS").is_file() else []
    return {"inspectRepairs": False, "secretsInInspect": False, "manifestFiles": checked}


def bundle_sources():
    """Repo layout and the flattened operator bundle both resolve. A bundle has no checkout."""
    test = HERE / "test_yogi_replacement_recover.py"
    if not test.is_file():
        test = HERE / "tests" / "test_yogi_replacement_recover.py"
    docs = HERE / "YOGI-REPLACEMENT-RECOVER.md"
    if not docs.is_file():
        docs = HERE.parent / "docs" / "YOGI-REPLACEMENT-RECOVER.md"
    return {
        "yogi_replacement_recover.py": HERE / "yogi_replacement_recover.py",
        "yogi_replacement_recover_guest.py": HERE / "yogi_replacement_recover_guest.py",
        "test_yogi_replacement_recover.py": test,
        "YOGI-REPLACEMENT-RECOVER.md": docs,
    }


def export_bundle(out):
    """Copy the runnable operator files, the runbook, the offline tests, and a sha256 manifest."""
    out = Path(out)
    out.mkdir(mode=0o700)
    mapping = bundle_sources()
    readme = (
        "Offline operator bundle. No repository checkout is required.\n"
        "\n"
        "Verify this directory without writing bytecode:\n"
        "  python3 -B yogi_replacement_recover.py self-check\n"
        "  python3 -B -m unittest test_yogi_replacement_recover\n"
        "\n"
        "Live read-only preflight. This process must not see a key-file variable and must not copy one.\n"
        "The gcloud account already active on this computer is grok-bots@protocols-465723.iam.gserviceaccount.com.\n"
        "  env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \\\n"
        "    python3 -B yogi_replacement_recover.py plan --mode repair --operator NAME --out DIR\n"
        "  env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \\\n"
        "    python3 -B yogi_replacement_recover.py preflight --op DIR \\\n"
        "    --confirm-instance 2720796557371812468 --execute\n"
        "\n"
        "preflight --execute only describes, lists firewalls with no server-side sourceRanges filter, and calls\n"
        "instances.testIamPermissions plus Resource Manager projects:testIamPermissions. It does not start the VM\n"
        "and it does not call setMetadata. Without --execute, preflight only rechecks the local plan.\n"
        "Do not run repair apply from this report. Spent operation ids include\n"
        "20ef6008b1ef78137d201de13176af42. Plan a new binding. The next guest pass is inspect only:\n"
        "  env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \\\n"
        "    python3 -B yogi_replacement_recover.py plan --mode inspect --operator NAME --out DIR\n"
        "Inspect does not chmod, rotate, or delete keys. It still uses one temporary metadata write and one start.\n"
    )
    (out / "README.md").write_text(readme)
    for name, source in mapping.items():
        shutil.copyfile(source, out / name)
    names = ("README.md", "YOGI-REPLACEMENT-RECOVER.md", "test_yogi_replacement_recover.py",
             "yogi_replacement_recover.py", "yogi_replacement_recover_guest.py")
    sums = "".join(f"{sha256_bytes((out / name).read_bytes())}  {name}\n" for name in names)
    (out / "SHA256SUMS").write_text(sums)
    bundle_unexpected(out)
    return {name: sha256_bytes((out / name).read_bytes()) for name in names + ("SHA256SUMS",)}


def pack_bundle(directory, tar_path):
    """Deterministic tar.gz of a clean bundle. Bytecode and unmanifested files are refused."""
    directory = Path(directory)
    bundle_unexpected(directory)
    tar_path = Path(tar_path)
    members = ["SHA256SUMS"]
    members += [line.split("  ", 1)[1] for line in (directory / "SHA256SUMS").read_text().splitlines()]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name in members:
            path = directory / name
            info = archive.gettarinfo(path, arcname="yogi-replacement-recover-bundle/" + name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    with tar_path.open("wb") as handle, gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as compressed:
        compressed.write(raw.getvalue())
    return tar_path


def main(argv, transport=None, now=None, clock=None, sleep=None, env=None, token=None, revision=None):
    parser = argparse.ArgumentParser(description="Yogi replacement-builder recovery plan")
    sub = parser.add_subparsers(dest="command", required=True)
    planned = sub.add_parser("plan")
    planned.add_argument("--operator", required=True)
    planned.add_argument("--out", required=True)
    planned.add_argument("--mode", choices=("repair", "inspect", "rotate", "rotate-publish"), default="repair")
    planned.add_argument("--inspect-receipt")
    planned.add_argument("--confirm-inspect-operation")
    planned.add_argument("--confirm-baseline")
    planned.add_argument("--possess-comment")
    sub.add_parser("self-check")
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
        elif args.command == "self-check":
            result, code = self_check(), 0
        elif args.command == "plan":
            document = plan(args.operator, args.out, now=now, token=token, revision=revision, env=env,
                            mode=args.mode, inspect_receipt=args.inspect_receipt,
                            confirm_inspect_operation=args.confirm_inspect_operation,
                            confirm_baseline=args.confirm_baseline,
                            possess_comment=args.possess_comment)
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
