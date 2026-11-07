"""Offline checks for the Yogi replacement recovery. Fake control plane and in-memory guest only."""
import base64
import copy
import datetime
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

_TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TEST_DIR))
if not (_TEST_DIR / "yogi_replacement_recover.py").is_file():
    sys.path.insert(0, str(_TEST_DIR.parent))
import yogi_replacement_recover as recover  # noqa: E402
import yogi_replacement_recover_guest as guest  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 7, 4, 0, tzinfo=UTC)
ENV = {"PATH": "/usr/bin:/bin"}
BOOT = "11111111-1111-1111-1111-111111111111"
BOOT2 = "22222222-2222-2222-2222-222222222222"


def blob(kind, payload):
    raw = kind.encode()
    return len(raw).to_bytes(4, "big") + raw + payload


def public_line(kind, payload, comment):
    return f"{kind} {base64.b64encode(blob(kind, payload)).decode()} {comment}"


def tokens(*values):
    stream = iter(values)
    return lambda: next(stream)


def token_source():
    return tokens("ab" * 16, "cd" * 16)


def instance(name, zone, number, status="TERMINATED", fingerprint="fp-base", items=None, disk_name=None,
             service_accounts=None, nat=None, scheduling=None):
    disk_name = disk_name or name
    link = f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}/instances/{name}"
    disk = f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}/disks/{disk_name}"
    body = {
        "id": number, "name": name,
        "zone": f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}",
        "status": status, "selfLink": link,
        "metadata": {"fingerprint": fingerprint,
                     "items": [{"key": key, "value": value} for key, value in (items or recover.BASE_METADATA).items()]},
        "scheduling": scheduling or {"automaticRestart": True, "maxRunDuration": {"seconds": "2700"},
                                    "instanceTerminationAction": "STOP"},
        "serviceAccounts": [] if service_accounts is None else service_accounts,
        "networkInterfaces": [{"networkIP": "10.1.0.4",
                               "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default"}],
        "tags": {"items": []},
        "disks": [{"boot": True, "source": disk}],
    }
    if nat:
        body["networkInterfaces"][0]["accessConfigs"] = [{"natIP": nat}]
    return body


def disk(name, zone, number, user, size="2000"):
    return {"id": number, "name": name, "sizeGb": str(size), "status": "READY",
            "zone": f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}",
            "users": [f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}/instances/{user}"]}


def _operation(operation_type, status, name="operation-1794026000000-65fa8387d4384-e9bed427-00000001",
              target=recover.INSTANCE_ID, error=None):
    body = {
        "kind": "compute#operation",
        "id": "8000000000000000001",
        "name": name,
        "zone": f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{recover.ZONE}",
        "operationType": operation_type,
        "targetLink": (f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{recover.ZONE}"
                       f"/instances/{recover.INSTANCE}"),
        "targetId": str(target),
        "status": status,
        "selfLink": (f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{recover.ZONE}"
                     f"/operations/{name}"),
    }
    if error:
        body["error"] = {"errors": [{"code": error, "message": error}]}
        body["httpErrorStatusCode"] = 400
    return body


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class World:
    def __init__(self):
        self.fingerprint = "fp-base"
        self.items = dict(recover.BASE_METADATA)
        self.replacement_status = "TERMINATED"
        self.original_status = "TERMINATED"
        self.replacement_id = recover.INSTANCE_ID
        self.nat = None
        self.service_accounts = []
        self.scheduling = {"automaticRestart": True, "maxRunDuration": {"seconds": "2700"},
                           "instanceTerminationAction": "STOP"}
        self.accounts = [{"account": recover.OPERATOR_ACCOUNT, "status": "ACTIVE"}]
        self.firewalls = [{"name": "allow-iap-ssh", "direction": "INGRESS", "disabled": False, "priority": 1000,
                           "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default",
                           "sourceRanges": [recover.IAP_RANGE], "targetTags": [],
                           "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]}]
        self.snapshots = {
            "preserve": {"id": recover.PRESERVE_SNAPSHOT_ID, "name": recover.PRESERVE_SNAPSHOT, "status": "READY"},
            "prekey": {"id": recover.PREKEY_SNAPSHOT_ID, "name": recover.PREKEY_SNAPSHOT, "status": "READY"},
        }
        self.cursor = "40"
        self.pending = ""
        self.bad_cursor = False
        self.attribute = {"absent": True}
        self.fail_sets = []
        self.fail_describe = 0
        self.start_mode = "ok"
        self.stop_mode = "ok"
        self.leave_running = 0
        self.boot_guest = None
        self.requests = []
        self.on_start = None
        self.iam_missing = {}
        self.op_serial = 0
        self.jobs = {}

    def _replacement(self):
        return instance(recover.INSTANCE, recover.ZONE, self.replacement_id, self.replacement_status,
                        self.fingerprint, self.items, recover.DISK_NAME, self.service_accounts, self.nat,
                        self.scheduling)

    def _apply_items(self, items):
        self.fingerprint += "x"
        self.items = dict(items)

    def __call__(self, request):
        recover.guard_request(request)
        self.requests.append(copy.deepcopy(request))
        kind, target = request["kind"], request.get("target")
        if kind == "auth-list":
            return list(self.accounts)
        if kind == "describe-instance" and target == "replacement":
            if self.fail_describe:
                self.fail_describe -= 1
                raise recover.TransportError("exit")
            return self._replacement()
        if kind == "describe-instance" and target == "original":
            return instance(recover.ORIGINAL_INSTANCE, recover.ORIGINAL_ZONE, recover.ORIGINAL_INSTANCE_ID,
                            self.original_status, "fp-original", recover.BASE_METADATA, recover.ORIGINAL_INSTANCE)
        if kind == "describe-disk" and target == "replacement":
            return disk(recover.DISK_NAME, recover.ZONE, recover.DISK_ID, recover.INSTANCE, recover.DISK_SIZE_GB)
        if kind == "describe-disk" and target == "original":
            return disk(request["name"], recover.ORIGINAL_ZONE, recover.ORIGINAL_DISK_ID, recover.ORIGINAL_INSTANCE, "2000")
        if kind == "describe-snapshot":
            return copy.deepcopy(self.snapshots[target])
        if kind == "list-firewalls":
            return copy.deepcopy(self.firewalls)
        if kind == "set-metadata":
            return self._set(request)
        if kind in ("operation-wait", "operation-get"):
            return self._wait(request["name"])
        if kind == "serial":
            return self._serial(request["start"])
        if kind == "guest-attribute":
            return copy.deepcopy(self.attribute)
        if kind == "test-iam":
            missing = self.iam_missing.get(target, ())
            return {"permissions": [item for item in request["permissions"] if item not in missing]}
        if kind == "start":
            return self._start()
        if kind == "stop":
            return self._stop()
        raise AssertionError(kind)

    def _alloc_name(self):
        self.op_serial += 1
        return f"operation-1794026000000-65fa8387d4384-e9bed427-{self.op_serial:08x}"

    def _operation_body(self, name, operation_type, status, error=None, target_id=None):
        target = recover.INSTANCE_ID if target_id is None else str(target_id)
        body = {
            "kind": "compute#operation",
            "id": str(8000000000000000000 + self.op_serial),
            "name": name,
            "zone": f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{recover.ZONE}",
            "operationType": operation_type,
            "targetLink": (f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{recover.ZONE}"
                           f"/instances/{recover.INSTANCE}"),
            "targetId": target,
            "status": status,
            "selfLink": (f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{recover.ZONE}"
                         f"/operations/{name}"),
        }
        if error:
            body["error"] = {"errors": [{"code": error, "message": error}]}
            body["httpErrorStatusCode"] = 400
        return body

    def _begin(self, operation_type, effect, statuses=("PENDING", "DONE"), error=None, interrupt=False):
        name = self._alloc_name()
        job = {"type": operation_type, "effect": effect, "statuses": list(statuses), "error": error,
               "applied": False, "interrupt": interrupt, "mismatch": False}
        self.jobs[name] = job
        if statuses[0] == "DONE":
            self._finish(job, "DONE")
        return {"httpStatus": 200, "body": self._operation_body(name, operation_type, statuses[0],
                                                                error if statuses[0] == "DONE" else None)}

    def _finish(self, job, status):
        if status == "DONE" and not job["applied"]:
            job["applied"] = True
            if job["error"] is None and job["effect"]:
                job["effect"]()

    def _wait(self, name):
        job = self.jobs.get(name)
        if job is None:
            return {"httpStatus": 404, "body": {"error": {"code": 404, "status": "NOT_FOUND"}}}
        if job["interrupt"]:
            job["interrupt"] = False
            raise KeyboardInterrupt()
        if job["mismatch"]:
            job["mismatch"] = False
            other = self._alloc_name()
            return {"httpStatus": 200, "body": self._operation_body(other, job["type"], "DONE")}
        if len(job["statuses"]) > 1:
            job["statuses"].pop(0)
        status = job["statuses"][0]
        self._finish(job, status)
        return {"httpStatus": 200, "body": self._operation_body(
            name, job["type"], status, job["error"] if status == "DONE" else None)}

    def _set(self, request):
        mode = self.fail_sets.pop(0) if self.fail_sets else "ok"

        def apply_items():
            if request["fingerprint"] == self.fingerprint:
                self._apply_items(request["items"])

        if mode == "timeout":
            raise recover.TransportError("timeout")
        if mode == "readback":
            self.fail_describe += 1
            return self._begin("setMetadata", apply_items)
        if mode == "internal":
            return {"httpStatus": 500, "body": {"error": {"code": 13, "status": "INTERNAL"}}}
        if mode == "unparseable":
            return {"httpStatus": 200, "body": None}
        if mode == "pending":
            return self._begin("setMetadata", apply_items, statuses=("PENDING",))
        if mode == "running-then-done":
            return self._begin("setMetadata", apply_items, statuses=("PENDING", "RUNNING", "DONE"))
        if mode == "divergent":
            self._apply_items(dict(self.items, **{"ssh-keys": "other"}))
            return {"httpStatus": 500, "body": {"error": {"code": 13, "status": "INTERNAL"}}}
        if mode == "applied-internal":
            self._apply_items(request["items"])
            return {"httpStatus": 500, "body": {"error": {"code": 13, "status": "INTERNAL"}}}
        if mode == "done-error":
            return self._begin("setMetadata", apply_items, statuses=("PENDING", "DONE"), error="CONDITION_NOT_MET")
        if mode == "wait-mismatch":
            response = self._begin("setMetadata", apply_items)
            self.jobs[response["body"]["name"]]["mismatch"] = True
            return response
        if mode == "wait-interrupt":
            return self._begin("setMetadata", apply_items, interrupt=True)
        if mode == "wrong-target":
            name = self._alloc_name()
            return {"httpStatus": 200, "body": self._operation_body(name, "setMetadata", "PENDING", target_id="1")}
        if request["fingerprint"] != self.fingerprint:
            return {"httpStatus": 412, "body": {"error": {"code": 412, "status": "conditionNotMet"}}}
        return self._begin("setMetadata", apply_items)

    def _serial(self, start):
        if self.bad_cursor:
            return {"contents": "STALE", "start": "9", "next": "9"}
        if int(start) == 0:
            return {"contents": "OLD serial line\n", "start": "0", "next": self.cursor}
        if str(start) != str(self.cursor):
            return {"contents": self.pending, "start": "0", "next": self.cursor}
        text, self.pending = self.pending, ""
        if text:
            self.cursor = str(int(self.cursor) + len(text.encode()))
        return {"contents": text, "start": str(start), "next": self.cursor}

    def _start(self):
        def up():
            self.replacement_status = "RUNNING"
            if self.on_start:
                self.on_start()
            if self.boot_guest:
                self.pending = self.boot_guest(self.items) or ""

        if self.start_mode == "timeout-down":
            raise recover.TransportError("timeout")
        if self.start_mode == "timeout-up":
            self.replacement_status = "RUNNING"
            raise recover.TransportError("timeout")
        if self.start_mode == "interrupt":
            self.replacement_status = "RUNNING"
            raise KeyboardInterrupt()
        if self.start_mode == "pending":
            return self._begin("start", up, statuses=("PENDING",))
        if self.start_mode == "done-error":
            return self._begin("start", up, statuses=("PENDING", "DONE"), error="ZONE_RESOURCE_POOL_EXHAUSTED")
        return self._begin("start", up)

    def _stop(self):
        def down():
            if self.leave_running:
                self.leave_running -= 1
                self.replacement_status = "RUNNING"
            else:
                self.replacement_status = "TERMINATED"

        if self.stop_mode == "pending":
            return self._begin("stop", down, statuses=("PENDING",))
        return self._begin("stop", down)

    def kinds(self):
        return [request["kind"] for request in self.requests]

    def targets(self, kind):
        return [request.get("target") for request in self.requests if request["kind"] == kind]


class DictHost:
    def __init__(self, binding, guest_path, boot=BOOT):
        self.record, self.guest_path, self.boot = binding, str(guest_path), boot
        self.marker = None
        self.log, self.emitted = [], []
        self.chmod_calls = self.generate_calls = self.commit_calls = 0
        self.hostkey_puts = []
        self.publish_fails = False
        self.identity = dict(guest.EXPECTED_IDENTITY)
        self.unit = {"kind": "regular", "mode": 0o664, "uid": 0, "gid": 0, "size": 347, "nlink": 1}
        self.runner_state = {"active": "inactive", "enabled": "disabled", "process": False}
        self.names = list(guest.INVENTORY)
        self.fail_commit = None
        self.same_fingerprint = False
        self.prev = {}
        self.prev_public = {}
        self.auth = {path: None for path in guest.AUTH_PATHS}
        self.account_entry = None
        self.account_fail = False
        self.removed_account = False
        self.prior_unrecognized = 0
        self.private = "PRIVATE-KEY-MATERIAL-SENTINEL"
        self.public = {}
        payloads = {
            "ed25519": (b"\x00\x00\x00\x20" + bytes(range(32)), b"\x00\x00\x00\x20" + bytes(range(32, 64))),
            "ecdsa": (b"\x00\x00\x00\x08nistp256" + bytes(range(8)), b"\x00\x00\x00\x08nistp256" + bytes(range(8, 16))),
            "rsa": (b"\x00\x00\x00\x10" + bytes(range(16)), b"\x00\x00\x00\x10" + bytes(range(16, 32))),
        }
        for alg, (old, new) in payloads.items():
            kind = guest.PUBLIC_TYPES[alg]
            self.public[guest.KEY_NAMES[alg] + ".pub"] = public_line(kind, old, "clone-baseline")
            self.public[guest.KEY_NAMES[alg] + ".roseos-new.pub"] = public_line(kind, new, "roseos-yogi-recover")

    def binding(self):
        return dict(self.record)

    def self_sha256(self):
        return recover.sha256_bytes(Path(self.guest_path).read_bytes())

    def boot_id(self):
        return self.boot

    def marker_load(self):
        return None if self.marker is None else json.loads(json.dumps(self.marker))

    def marker_store(self, obj):
        self.marker = json.loads(json.dumps(obj))

    def instance_identity(self):
        return dict(self.identity)

    def unit_stat(self):
        return None if self.unit is None else dict(self.unit)

    def unit_chmod(self):
        self.chmod_calls += 1
        self.log.append("chmod")
        if not self.unit or self.unit.get("mode") != 0o664:
            raise guest.GuestRefused("chmod-precondition")
        self.unit = {"kind": "regular", "mode": 0o644, "uid": 0, "gid": 0, "size": 347, "nlink": 1}
        return dict(self.unit)

    def runner(self):
        return dict(self.runner_state)

    def ssh_names(self):
        return list(self.names)

    def ssh_stat(self, name):
        if name.endswith(".pub") and name not in self.public:
            return None
        if name.endswith(".pub"):
            return {"kind": "regular", "mode": 0o644, "uid": 0, "gid": 0, "size": 80, "nlink": 1}
        return {"kind": "regular", "mode": 0o600, "uid": 0, "gid": 0, "size": 400, "nlink": 1}

    def ssh_public(self, name):
        if name not in self.public:
            raise OSError("baseline")
        return self.public[name]

    def publish_hostkey_attributes(self, writes):
        self.hostkey_puts = list(writes)
        for key_type, blob in writes:
            if any(token in blob for token in ("BEGIN", "PRIVATE", "OPENSSH", " ")):
                raise guest.GuestRefused("private-attribute")
        return not self.publish_fails

    def ssh_generate(self, algorithm):
        self.generate_calls += 1
        self.log.append(("generate", algorithm))
        if self.same_fingerprint:
            return self.public[guest.KEY_NAMES[algorithm] + ".pub"]
        return self.public[guest.KEY_NAMES[algorithm] + ".roseos-new.pub"]

    def ssh_preserve(self, algorithm):
        self.log.append(("preserve", algorithm))
        live = guest.KEY_NAMES[algorithm] + ".pub"
        self.prev[algorithm] = self.public[live]

    def ssh_commit(self, algorithm):
        self.commit_calls += 1
        self.log.append(("commit", algorithm))
        if self.fail_commit == algorithm:
            raise OSError("rotate-partial")
        live = guest.KEY_NAMES[algorithm] + ".pub"
        self.public[live] = self.public[guest.KEY_NAMES[algorithm] + ".roseos-new.pub"]

    def ssh_restore(self, algorithm):
        if algorithm not in self.prev:
            return False
        self.public[guest.KEY_NAMES[algorithm] + ".pub"] = self.prev[algorithm]
        return True

    def ssh_drop_prev_private(self, algorithm):
        if algorithm in self.prev:
            self.prev_public[algorithm] = self.prev[algorithm]
        self.log.append(("drop-private", algorithm))

    def ssh_discard_temp(self):
        self.log.append("discard")
        return {"removed": 0, "skippedSymlink": 0}

    def auth_lines(self, path):
        if path not in self.auth:
            raise guest.GuestRefused("auth-path")
        lines = self.auth[path]
        return None if lines is None else list(lines)

    def auth_write(self, path, lines):
        self.auth[path] = list(lines)

    def account(self):
        return None if self.account_entry is None else dict(self.account_entry)

    def account_remove(self):
        if self.account_fail:
            raise OSError("userdel")
        self.removed_account = True
        self.account_entry = None
        return "removed"

    def emit(self, line):
        self.emitted.append(line)

    def prior_journal(self):
        return self.prior_unrecognized


def load_rendered(path):
    spec = importlib.util.spec_from_file_location("rendered_" + recover.sha256_bytes(path.read_bytes())[:12], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.op = Path(self.temp.name) / "op"
        self.world = World()
        self.clock = Clock()
        self.plan = recover.plan("Mac operator", self.op, now=NOW, token=token_source(), revision="a" * 40,
                                env=ENV)

    def run_guest(self, host=None):
        module = load_rendered(self.op / "guest.py")
        binding = json.loads((self.op / "binding.json").read_text())
        host = host or DictHost(binding, self.op / "guest.py")
        receipt = module.run(host)
        return module, host, receipt

    def boot(self, items):
        module = load_rendered(self.op / "guest.py")
        binding = json.loads(items[recover.BINDING_KEY])
        host = DictHost(binding, self.op / "guest.py")
        self.world.host = host
        module.run(host)
        self.assertTrue(host.emitted)
        return host.emitted[-1] + "\n"

    def preflight(self):
        return recover.preflight(self.op, recover.INSTANCE_ID, True, self.world, now=NOW, env=ENV)

    def apply(self, **kwargs):
        kwargs.setdefault("now", NOW)
        kwargs.setdefault("clock", self.clock)
        kwargs.setdefault("sleep", self.clock.sleep)
        kwargs.setdefault("env", ENV)
        return recover.apply(self.op, recover.INSTANCE_ID, True, self.world, **kwargs)


class PlanTest(Base):
    def test_plan_is_a_fresh_dry_run_and_keeps_historical_ids_out_of_the_binding(self):
        self.assertTrue(self.plan["dryRun"])
        self.assertFalse(self.plan["executed"] or self.plan["liveRecovery"] or self.plan["hostKeyPinned"])
        self.assertFalse(self.plan["historicalReferenceReplayed"])
        self.assertEqual(self.plan["historicalReference"]["slackTs"], "1794022474.195909")
        self.assertEqual(self.plan["historicalReference"]["hashStatus"], "recorded-from-relay-not-recomputed")
        self.assertEqual(self.plan["historicalReference"]["contentStatus"], "not-reviewed")
        banned = recover.historical_hashes()
        self.assertNotIn(self.plan["payloadSha256"], banned)
        self.assertEqual(self.plan["deadlines"]["policySeconds"], 2700)
        self.assertEqual(self.plan["deadlines"]["workBudgetSeconds"] + self.plan["deadlines"]["cleanupReserveSeconds"], 2700)
        self.assertEqual(self.plan["readOnly"]["originalInstanceId"], recover.ORIGINAL_INSTANCE_ID)
        self.assertEqual(self.plan["readOnly"]["originalDiskId"], recover.ORIGINAL_DISK_ID)
        other = recover.plan("Mac operator", Path(self.temp.name) / "op2", now=NOW,
                             token=tokens("12" * 16, "34" * 16), revision="b" * 40, env=ENV)
        self.assertNotEqual(other["operationId"], self.plan["operationId"])
        self.assertNotIn("@@OP@@", (self.op / "guest.py").read_text())
        self.assertNotIn("@@NONCE@@", (self.op / "guest.py").read_text())
        text = (ROOT_TEXT())
        self.assertNotIn("ssh-keyscan", text)
        self.assertNotIn("gcloud compute ssh", text)
        self.assertNotIn("/setCommonInstanceMetadata", text)
        self.assertNotIn("setCommonInstanceMetadata(", text)

    def test_actions_context_and_bad_confirmation_are_refused(self):
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "no", env={"GITHUB_ACTIONS": "true"})
        with self.assertRaises(recover.Refused):
            recover.preflight(self.op, "1", True, self.world, now=NOW, env=ENV)
        dry = recover.apply(self.op, recover.INSTANCE_ID, False, self.world, now=NOW, env=ENV)
        self.assertTrue(dry["dryRun"])
        self.assertEqual(self.world.requests, [])

    def test_direct_route_stays_unbound(self):
        try:
            import yogi_direct
            import yogi_ota as yogi
        except ImportError:
            self.skipTest("full repository route modules are not in this deployment bundle")
        cfg = yogi.config()
        self.assertEqual(cfg["builder"]["direct"]["status"], "unbound")
        self.assertIsNone(cfg["builder"]["direct"]["hostKey"])
        self.assertEqual(yogi_direct.GITHUB_CONTEXT, recover.GITHUB_CONTEXT)
        self.assertEqual((recover.PROJECT, recover.ZONE, recover.INSTANCE, recover.INSTANCE_ID),
                         (yogi.PROJECT, yogi.ZONE, yogi.INSTANCE, yogi.INSTANCE_ID))


def ROOT_TEXT():
    return (Path(recover.__file__).read_text() + Path(guest.__file__).read_text())


class ControlTest(Base):
    def test_happy_path_repairs_rotates_restores_metadata_and_stops(self):
        self.preflight()
        self.world.boot_guest = self.boot
        result = self.apply()
        self.assertEqual(result["exit"], 0, result["result"])
        self.assertEqual(result["result"], "procedure-complete-guest-reported")
        self.assertFalse(result["guestAuthenticated"] or result["hostKeyPinned"] or result["liveRecovery"])
        self.assertEqual(result["stop"], "stopped")
        self.assertEqual(result["restore"], "clean")
        self.assertEqual(self.world.replacement_status, "TERMINATED")
        self.assertEqual(self.world.items, recover.BASE_METADATA)
        self.assertEqual(self.world.targets("start"), ["replacement"])
        self.assertEqual(self.world.targets("stop"), ["replacement"])
        self.assertEqual(self.world.targets("set-metadata"), ["replacement", "replacement"])
        self.assertEqual(self.world.targets("describe-instance").count("original") > 0, True)
        self.assertNotIn("original", self.world.targets("start") + self.world.targets("stop") + self.world.targets("set-metadata"))
        self.assertNotIn(self.world.host.private, json.dumps(result))
        keys = result["guestReceipt"]["hostKeys"]
        self.assertTrue(keys["distinctFromClonedBaseline"])
        self.assertFalse(result["guestReceipt"]["originalVmAccessed"])
        self.assertFalse(result["guestReceipt"]["snapshotsUsed"])
        for alg in guest.ALGORITHMS:
            self.assertNotEqual(keys["baseline"][alg], keys["rotated"][alg])
        self.assertEqual(self.clock.sleeps, [180])
        self.assertNotIn("#!/bin/bash", json.dumps(result["requests"]))

    def test_target_mismatches_do_not_mutate(self):
        cases = []
        world = World()
        world.replacement_id = "1234567890123456789"
        cases.append(world)
        world = World()
        world.original_status = "RUNNING"
        cases.append(world)
        world = World()
        world.nat = "203.0.113.9"
        cases.append(world)
        world = World()
        world.service_accounts = [{"email": "x@example.invalid"}]
        cases.append(world)
        world = World()
        world.scheduling = {"automaticRestart": False, "maxRunDuration": {"seconds": "2700"},
                            "instanceTerminationAction": "STOP"}
        cases.append(world)
        world = World()
        world.items = dict(recover.BASE_METADATA, **{"ssh-keys": "present"})
        cases.append(world)
        world = World()
        world.snapshots["prekey"]["status"] = "CREATING"
        cases.append(world)
        world = World()
        world.firewalls = []
        cases.append(world)
        world = World()
        world.accounts = [{"account": "other@example.invalid", "status": "ACTIVE"}]
        cases.append(world)
        for world in cases:
            with self.subTest(world=world.kinds() if False else world.replacement_id):
                report = recover.preflight(self.op, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
                self.assertNotEqual(report["result"], "preflight-ok")
                self.assertNotIn("set-metadata", world.kinds())
                self.assertNotIn("start", world.kinds())

    def test_stale_fingerprint_and_replay_and_expired_preflight(self):
        self.preflight()
        self.world.fingerprint = "fp-other"
        stale = self.apply()
        self.assertEqual(stale["result"], "stale-fingerprint")
        self.assertNotIn("set-metadata", self.world.kinds())
        with self.assertRaises(recover.Refused):
            self.apply()
        fresh = Path(self.temp.name) / "fresh"
        recover.plan("Mac operator", fresh, now=NOW, token=tokens("ef" * 16, "11" * 16),
                     revision="c" * 40, env=ENV)
        world = World()
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        with self.assertRaises(recover.Refused):
            recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW + datetime.timedelta(minutes=21), env=ENV)
        self.assertNotIn("set-metadata", world.kinds())

    def test_metadata_internal_error_is_not_retried(self):
        self.preflight()
        self.world.fail_sets = ["internal"]
        result = self.apply()
        self.assertEqual(result["metadata"], "not-applied")
        self.assertEqual(result["restore"], "already-base")
        self.assertEqual(self.world.targets("set-metadata"), ["replacement"])
        self.assertNotIn("start", self.world.kinds())
        self.assertEqual(result["stop"], "not-started")
        self.assertEqual(result["metadataError"], "INTERNAL")

    def test_ambiguous_applied_metadata_is_restored_and_not_booted(self):
        self.preflight()
        self.world.fail_sets = ["applied-internal"]
        result = self.apply()
        self.assertEqual(result["metadata"], "ambiguous-applied")
        self.assertEqual(len(self.world.targets("set-metadata")), 2)
        self.assertEqual(self.world.items, recover.BASE_METADATA)
        self.assertNotIn("start", self.world.kinds())
        self.assertEqual(self.world.replacement_status, "TERMINATED")

    def test_unparseable_pending_and_divergent_metadata(self):
        self.preflight()
        self.world.fail_sets = ["unparseable"]
        result = self.apply()
        self.assertEqual(result["metadata"], "not-applied")
        self.assertEqual(len(self.world.targets("set-metadata")), 1)
        fresh = self._fresh("12")
        world = World()
        world.fail_sets = ["pending"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(), sleep=lambda s: None, env=ENV)
        self.assertEqual(result["metadata"], "pending-timeout")
        self.assertEqual(world.targets("set-metadata"), ["replacement"])
        self.assertGreaterEqual(world.kinds().count("operation-wait"), 1)
        self.assertNotIn("start", world.kinds())
        fresh = self._fresh("13")
        world = World()
        world.fail_sets = ["divergent"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(), sleep=lambda s: None, env=ENV)
        self.assertEqual(result["restore"], "already-base")
        self.assertEqual(len(world.targets("set-metadata")), 1)
        self.assertEqual(world.items["ssh-keys"], "other")

    def test_readback_failure_does_not_send_a_second_set(self):
        self.preflight()
        self.world.fail_sets = ["readback"]
        result = self.apply()
        self.assertEqual(result["metadata"], "readback-failed")
        self.assertEqual(result["restore"], "not-attempted-readback-failed")
        self.assertEqual(len(self.world.targets("set-metadata")), 1)
        self.assertNotIn("start", self.world.kinds())

    def test_malicious_serial_attribute_and_stale_cursor_still_stop_after_start(self):
        self.preflight()
        self.world.boot_guest = lambda items: guest.PREFIX + "{not-json}\n"
        result = self.apply()
        self.assertEqual(result["guestRead"], "serial-json")
        evidence = json.loads((self.op / "serial-evidence.json").read_text())
        self.assertEqual(evidence["reason"], "json")
        self.assertNotIn("{not-json}", (self.op / "serial-evidence.json").read_text())
        self.assertEqual(stat.S_IMODE((self.op / "serial-evidence.json").stat().st_mode), 0o600)
        self.assertTrue(evidence["receiptLines"][0]["redacted"])
        self.assertEqual(evidence["receiptLines"][0]["sha256"], recover.sha256_bytes(b"{not-json}"))
        self.assertEqual(result["stop"], "stopped")
        self.assertEqual(self.world.replacement_status, "TERMINATED")
        self.assertEqual(self.world.items, recover.BASE_METADATA)
        self.assertEqual(result["exit"], 1)
        fresh = self._fresh("14")
        world = World()
        world.attribute = {"value": "{\"no\":1}"}

        def good(items, op=fresh, box=world):
            module = load_rendered(Path(op) / "guest.py")
            host = DictHost(json.loads(items[recover.BINDING_KEY]), Path(op) / "guest.py")
            module.run(host)
            return host.emitted[-1] + "\n"
        world.boot_guest = good
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(), sleep=Clock().sleep, env=ENV)
        self.assertEqual(result["guestRead"], "attribute-mismatch")
        self.assertEqual(result["stop"], "stopped")
        fresh = self._fresh("15")
        world = World()
        world.bad_cursor = True
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        clock = Clock()
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=clock, sleep=clock.sleep, env=ENV)
        self.assertEqual(result["guestRead"], "stale-cursor")
        self.assertEqual(result["stop"], "stopped")
        self.assertEqual(world.replacement_status, "TERMINATED")
        self.assertNotIn("startup-script", world.items)
        self.assertFalse(result["serialReceiptTrustedForPin"])

    def test_start_timeout_still_stops_when_the_vm_came_up(self):
        self.preflight()
        self.world.start_mode = "timeout-up"
        result = self.apply()
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["stop"], "stopped")
        self.assertNotEqual(result["result"], "procedure-complete-guest-reported")
        self.assertEqual(self.world.replacement_status, "TERMINATED")

    def test_automatic_restart_gets_one_more_stop_and_then_gives_up(self):
        self.preflight()
        self.world.boot_guest = self.boot
        self.world.leave_running = 1
        result = self.apply()
        self.assertEqual(result["stop"], "stopped-after-restart")
        self.assertEqual(self.world.targets("stop"), ["replacement", "replacement"])
        self.assertEqual(result["exit"], 0)
        fresh = self._fresh("16")
        world = World()
        world.leave_running = 5
        world.boot_guest = lambda items, op=fresh: self._emit(op, items)
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        clock = Clock()
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=clock, sleep=clock.sleep, env=ENV)
        self.assertEqual(result["stop"], "stop-unconfirmed")
        self.assertEqual(len(world.targets("stop")), 2)
        self.assertEqual(result["exit"], 1)
        self.assertEqual(world.replacement_status, "RUNNING")

    def test_work_budget_stops_without_another_serial_poll(self):
        self.preflight()

        def jump():
            self.clock.now = recover.WORK_BUDGET_SECONDS + 5
        self.world.on_start = jump
        result = self.apply()
        self.assertEqual(result["guestRead"], "budget")
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(result["stop"], "stopped")
        self.assertLessEqual(self.world.kinds().count("serial"), 1)

    def test_cleanup_restores_and_stops_without_starting(self):
        self.preflight()
        self.world.fail_sets = ["applied-internal"]
        self.apply()
        script = (self.op / "startup-script.sh").read_text()
        binding_text = (self.op / "binding.json").read_text().strip()
        self.world.replacement_status = "RUNNING"
        self.world.items = {"enable-oslogin": "FALSE", "block-project-ssh-keys": "TRUE",
                            "startup-script": script, recover.BINDING_KEY: binding_text, "ssh-keys": "keep-me"}
        self.world.fingerprint = "fp-left"
        before = len(self.world.targets("start"))
        result = recover.cleanup(self.op, recover.INSTANCE_ID, True, self.world, now=NOW, env=ENV)
        self.assertEqual(result["exit"], 0)
        self.assertEqual(self.world.items["ssh-keys"], "keep-me")
        self.assertNotIn("startup-script", self.world.items)
        self.assertTrue(result["startupScriptRemoved"])
        self.assertTrue(result["preservedIdsMatch"])
        self.assertEqual(result["preserved"]["original"]["id"], recover.ORIGINAL_INSTANCE_ID)
        self.assertEqual(result["preserved"]["originalDisk"]["id"], recover.ORIGINAL_DISK_ID)
        self.assertEqual(result["preserved"]["preserve"]["id"], recover.PRESERVE_SNAPSHOT_ID)
        self.assertEqual(result["preserved"]["prekey"]["id"], recover.PREKEY_SNAPSHOT_ID)
        self.assertEqual(self.world.replacement_status, "TERMINATED")
        self.assertEqual(len(self.world.targets("start")), before)
        self.assertNotIn("original", self.world.targets("stop") + self.world.targets("set-metadata"))

    def test_interrupt_after_start_still_stops_and_removes_our_metadata(self):
        self.preflight()
        self.world.start_mode = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.apply()
        self.assertEqual(self.world.replacement_status, "TERMINATED")
        self.assertNotIn("startup-script", self.world.items)
        self.assertIn("stop", self.world.kinds())
        self.assertTrue((self.op / "outcome.json").is_file() or (self.op / "outcome-unwritten.json").is_file())

    def test_cleanup_stops_a_running_replacement_without_started_json(self):
        fresh = self._fresh("21")
        world = World()
        world.replacement_status = "RUNNING"
        result = recover.cleanup(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        self.assertEqual(world.replacement_status, "TERMINATED")
        self.assertIn("stop", world.kinds())
        self.assertNotIn("original", world.targets("stop"))
        self.assertEqual(result["stop"], "stopped")
        self.assertFalse((fresh / "started.json").exists())

    def test_cleanup_leaves_a_stopped_base_vm_alone(self):
        result = recover.cleanup(self.op, recover.INSTANCE_ID, True, self.world, now=NOW, env=ENV)
        self.assertEqual(result["stop"], "not-started")
        self.assertNotIn("stop", self.world.kinds())
        self.assertNotIn("set-metadata", self.world.kinds())

    def test_foreign_metadata_value_is_not_erased(self):
        script = (self.op / "startup-script.sh").read_text()
        binding_text = recover.binding_canonical(self.plan["operationId"], self.plan["nonce"], self.plan["payloadSha256"])
        self.world.items = {"enable-oslogin": "FALSE", "block-project-ssh-keys": "TRUE",
                            "startup-script": "operator-script", "ssh-keys": "keep-me"}
        kind, _after = recover.restore_metadata(self.world, script, binding_text)
        self.assertEqual(kind, "skipped-foreign-value")
        self.assertEqual(self.world.items["startup-script"], "operator-script")
        self.assertEqual(self.world.items["ssh-keys"], "keep-me")
        self.assertNotIn("set-metadata", self.world.kinds())

    def test_schedule_accepts_integer_seconds_and_rejects_flag_syntax(self):
        self.assertIsNone(recover.scheduling_problem({"automaticRestart": True, "maxRunDuration": {"seconds": 2700},
                                                     "instanceTerminationAction": "STOP"}))
        self.assertEqual(recover.scheduling_problem({"automaticRestart": True, "maxRunDuration": {"seconds": "2700s"},
                                                    "instanceTerminationAction": "STOP"}), "max-run-duration")
        self.assertLessEqual(sum(recover.SERIAL_DELAYS) + 240 * 2, recover.CLEANUP_RESERVE_SECONDS + recover.WORK_BUDGET_SECONDS)

    def test_key_path_and_impersonation_are_refused(self):
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "keys", revision="e" * 40,
                         env={"PATH": "/usr/bin", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/key.json"})
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "imp", revision="e" * 40,
                         env={"PATH": "/usr/bin", "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT": recover.OPERATOR_ACCOUNT})

    def test_export_bundle_plans_without_the_repository(self):
        bundle = Path(self.temp.name) / "bundle"
        artifacts = recover.export_bundle(bundle)
        listed = (bundle / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(listed), 5)
        for line in listed:
            digest, name = line.split("  ")
            self.assertEqual(digest, artifacts[name])
        op = Path(self.temp.name) / "from-bundle"
        result = subprocess.run([sys.executable, str(bundle / "yogi_replacement_recover.py"), "plan",
                                 "--operator", "Mac operator", "--out", str(op)], capture_output=True, text=True,
                                env=dict(ENV))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((op / "SHA256SUMS").is_file())
        self.assertNotIn("@@OP@@", (op / "guest.py").read_text())
        self.assertNotIn("@@NONCE@@", (op / "guest.py").read_text())

    def test_preflight_execute_is_read_only_and_checks_permissions(self):
        report = self.preflight()
        self.assertTrue(report["cloudReadOnly"])
        self.assertFalse(report["mutated"])
        self.assertEqual(report["result"], "preflight-ok")
        self.assertFalse(set(self.world.kinds()) & {"start", "stop", "set-metadata"})
        self.assertIn("test-iam", self.world.kinds())
        blocked = World()
        blocked.iam_missing = {"replacement": ["compute.instances.setMetadata"]}
        refused = recover.preflight(self.op, recover.INSTANCE_ID, True, blocked, now=NOW, env=ENV)
        self.assertEqual(refused["result"], "preflight-blocked")
        self.assertIn("missing-permission:compute.instances.setMetadata", refused["problems"])
        self.assertNotIn("start", blocked.kinds())
        local = recover.preflight(self.op, recover.INSTANCE_ID, False, blocked, now=NOW, env=ENV)
        self.assertEqual(local["result"], "dry-run")
        self.assertFalse(local["cloudReadOnly"])

    def test_inspect_payload_does_not_repair_a_0644_unit(self):
        op = Path(self.temp.name) / "inspect-op"
        recover.plan("Mac operator", op, now=NOW, token=tokens("ab" * 16, "cd" * 16), revision="a" * 40,
                     env=ENV, mode="inspect")
        script = (op / "guest.py").read_text()
        for banned in ("ssh-keygen", "userdel", "fchmod", "os.chmod", "BEGIN OPENSSH"):
            self.assertNotIn(banned, script)
        root = Path(self.temp.name) / "inspect-root"
        unit = root / guest.UNIT_PATH.lstrip("/")
        unit.parent.mkdir(parents=True)
        unit.write_text("u" * 347)
        unit.chmod(0o644)
        ssh = root / "etc/ssh"
        ssh.mkdir()
        (ssh / "sshd_config").write_text("KEEP")
        (ssh / "ssh_host_ed25519_key.pub").write_text(public_line("ssh-ed25519", b"\x00\x00\x00\x20" + bytes(range(32)), "clone") + "\n")
        auth = root / "root/.ssh"
        auth.mkdir(parents=True)
        keys = auth / "authorized_keys"
        keys.write_text("ssh-ed25519 AAAA someone-else\n")
        (root / "proc").mkdir()
        binding = op / "binding.json"
        identity = Path(self.temp.name) / "identity.json"
        identity.write_text(json.dumps({"project": recover.PROJECT, "zone": recover.ZONE,
                                        "instance": recover.INSTANCE, "instanceId": recover.INSTANCE_ID}))
        before = keys.read_bytes()
        mode = unit.stat().st_mode
        result = subprocess.run([sys.executable, str(op / "guest.py"), "--root", str(root), str(binding),
                                 str(identity), BOOT], capture_output=True, text=True)
        self.assertEqual(keys.read_bytes(), before)
        self.assertEqual(unit.stat().st_mode, mode)
        self.assertEqual((ssh / "sshd_config").read_text(), "KEEP")
        self.assertIn(guest.PREFIX, result.stdout)
        receipt = json.loads(result.stdout.strip().split(guest.PREFIX, 1)[1])
        self.assertEqual(receipt["purpose"], "inspect")
        self.assertEqual(receipt["unit"]["action"], "not-modified")
        self.assertEqual(receipt["unit"]["beforeMode"], "0644")
        self.assertEqual(receipt["unit"]["problem"], "owner")
        self.assertIn('elif before["mode"] == 0o644:', script)
        self.assertIn('problem = "already-0644"', script)
        self.assertEqual(receipt["journal"]["entries"], 0)
        self.assertEqual(receipt["hostKeys"]["rotated"], {})
        self.assertEqual(guest.validate_shape(receipt, json.loads(binding.read_text())), [])
        self.assertEqual(receipt["cleanup"]["linesRemoved"], 0)
        self.assertGreater(receipt["cleanup"]["unidentifiedAuthorizedKeyLines"], 0)
        self.assertFalse(guest.success_problems(receipt) == [] or receipt["purpose"] == "repair")

    def _fresh(self, mark):
        path = Path(self.temp.name) / ("op" + mark)
        recover.plan("Mac operator", path, now=NOW, token=tokens(mark * 16, "aa" * 16),
                     revision="d" * 40, env=ENV)
        return path

    def _emit(self, op, items):
        module = load_rendered(Path(op) / "guest.py")
        host = DictHost(json.loads(items[recover.BINDING_KEY]), Path(op) / "guest.py")
        module.run(host)
        return host.emitted[-1] + "\n"


class GuestTest(Base):
    def test_unit_chmod_only_when_every_precondition_matches(self):
        _module, host, receipt = self.run_guest()
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["unit"]["action"], "chmod-0644")
        self.assertEqual(host.chmod_calls, 1)
        self.assertEqual(host.generate_calls, 3)
        cases = [
            ("mode", {"mode": 0o644}),
            ("owner", {"uid": 1}),
            ("size", {"size": 346}),
            ("nlink", {"nlink": 2}),
            ("symlink", {"kind": "symlink"}),
            ("missing", None),
        ]
        for name, change in cases:
            with self.subTest(name=name):
                binding = json.loads((self.op / "binding.json").read_text())
                host = DictHost(binding, self.op / "guest.py")
                if change is None:
                    host.unit = None
                else:
                    host.unit.update(change)
                _module, host, receipt = self.run_guest(host)
                self.assertEqual(host.chmod_calls, 0)
                self.assertEqual(host.generate_calls, 0)
                self.assertNotEqual(receipt["status"], "complete")
                if name == "mode":
                    self.assertEqual(receipt["unit"]["problem"], "already-0644")
                    self.assertEqual(receipt["unit"]["action"], "not-modified")
                    self.assertEqual(receipt["cleanup"]["linesRemoved"], 0)
        binding = json.loads((self.op / "binding.json").read_text())
        for runner in ({"active": "active"}, {"enabled": "enabled"}, {"process": True}):
            host = DictHost(binding, self.op / "guest.py")
            host.runner_state.update(runner)
            _module, host, receipt = self.run_guest(host)
            self.assertEqual(host.chmod_calls, 0)
            self.assertIn(receipt["unit"]["problem"], ("runner-active", "runner-enabled", "runner-process"))

    def test_rotation_is_named_distinct_and_does_not_continue_after_partial(self):
        binding = json.loads((self.op / "binding.json").read_text())
        host = DictHost(binding, self.op / "guest.py")
        host.names.append("ssh_host_extra_key")
        _module, host, receipt = self.run_guest(host)
        self.assertEqual(host.generate_calls, 0)
        self.assertEqual(receipt["unit"]["problem"], "unknown-host-key")
        self.assertEqual(host.chmod_calls, 1)
        host = DictHost(binding, self.op / "guest.py")
        host.same_fingerprint = True
        _module, host, receipt = self.run_guest(host)
        self.assertEqual(host.commit_calls, 0)
        self.assertEqual(receipt["unit"]["problem"], "rotate-same")
        self.assertNotIn(host.private, json.dumps(receipt))
        host = DictHost(binding, self.op / "guest.py")
        host.fail_commit = "ecdsa"
        _module, host, receipt = self.run_guest(host)
        self.assertEqual(receipt["status"], "partial")
        self.assertTrue(receipt["hostKeys"]["rolledBack"])
        self.assertEqual(host.public[guest.KEY_NAMES["ed25519"] + ".pub"], host.prev["ed25519"])
        self.assertEqual(host.generate_calls, 2)
        module, host, second = self.run_guest(host)
        self.assertEqual(host.generate_calls, 2)
        self.assertEqual(host.chmod_calls, 1)
        self.assertEqual(second["unit"]["problem"], "reexecution-partial")
        host = DictHost(binding, self.op / "guest.py")
        module, host, receipt = self.run_guest(host)
        host.boot = BOOT2
        again = module.run(host)
        self.assertTrue(again["reexecution"])
        self.assertEqual(again["previousBootId"], BOOT)
        self.assertEqual(host.generate_calls, 3)
        self.assertEqual(again["hostKeys"]["rotated"], receipt["hostKeys"]["rotated"])

    def test_cleanup_removes_only_the_fresh_comment_and_the_exact_temporary_account(self):
        binding = json.loads((self.op / "binding.json").read_text())
        host = DictHost(binding, self.op / "guest.py")
        own = f"ssh-ed25519 AAAA roseos-yogi-recover-{binding['operationId']}"
        other = "ssh-ed25519 AAAA operator-kept"
        foreign = "ssh-ed25519 AAAA roseos-yogi-recover-" + "ff" * 16
        host.auth[guest.AUTH_PATHS[0]] = [other, own, "# comment", foreign]
        host.account_entry = {"name": guest.TEMP_ACCOUNT, "uid": 60001, "gid": 60001,
                              "home": guest.TEMP_HOME, "shell": "/usr/sbin/nologin"}
        _module, host, receipt = self.run_guest(host)
        self.assertEqual(host.auth[guest.AUTH_PATHS[0]], [other, "# comment", foreign])
        self.assertTrue(host.removed_account)
        self.assertEqual(receipt["status"], "partial")
        self.assertGreater(receipt["cleanup"]["unidentifiedAuthorizedKeyLines"], 0)
        kept = {"name": "pixelbuild", "uid": 2000, "gid": 2000, "home": "/home/pixelbuild", "shell": "/bin/bash"}
        self.assertFalse(guest.account_removable(kept))
        host = DictHost(binding, self.op / "guest.py")
        host.account_entry = kept
        _module, host, receipt = self.run_guest(host)
        self.assertFalse(host.removed_account)
        self.assertEqual(receipt["cleanup"]["temporaryAccount"], "preserved-mismatch")
        host = DictHost(binding, self.op / "guest.py")
        host.account_entry = {"name": guest.TEMP_ACCOUNT, "uid": 60001, "gid": 60001, "home": guest.TEMP_HOME,
                              "shell": "/usr/sbin/nologin"}
        host.account_fail = True
        _module, host, receipt = self.run_guest(host)
        self.assertEqual(receipt["cleanup"]["temporaryAccount"], "remove-failed")
        self.assertEqual(receipt["status"], "partial")

    def test_linux_host_refuses_symlinks_and_keeps_unrelated_ssh_files(self):
        root = Path(self.temp.name) / "root"
        unit = root / guest.UNIT_PATH.lstrip("/")
        unit.parent.mkdir(parents=True)
        secret = root / "secret"
        secret.write_text("SECRET-UNIT")
        unit.symlink_to(secret)
        host = guest.LinuxHost(root=str(root))
        self.assertEqual(host.unit_stat()["kind"], "symlink")
        with self.assertRaises(guest.GuestRefused):
            host.unit_chmod()
        self.assertEqual(secret.read_text(), "SECRET-UNIT")
        ssh = root / "etc/ssh"
        ssh.mkdir(parents=True)
        keep = ssh / "sshd_config"
        keep.write_text("KEEP")
        (ssh / "ssh_host_ed25519_key.roseos-new").write_text("temp")
        (ssh / "ssh_host_ecdsa_key.roseos-new").symlink_to(keep)
        discarded = host.ssh_discard_temp()
        self.assertFalse((ssh / "ssh_host_ed25519_key.roseos-new").exists())
        self.assertEqual(keep.read_text(), "KEEP")
        self.assertTrue((ssh / "ssh_host_ecdsa_key.roseos-new").is_symlink())
        self.assertGreater(discarded["skippedSymlink"], 0)
        module = load_rendered(self.op / "guest.py")
        disk = Path(self.temp.name) / "disk"
        ssh = disk / "etc/ssh"
        ssh.mkdir(parents=True)
        private = ssh / "ssh_host_ed25519_key"
        public = ssh / "ssh_host_ed25519_key.pub"
        private.write_text("OLD-PRIVATE")
        public.write_text("ssh-ed25519 AAAA clone\n")
        private.chmod(0o600)
        rendered = module.LinuxHost(root=str(disk))
        rendered.ssh_preserve("ed25519")
        private.write_text("NEW-PRIVATE")
        self.assertTrue(rendered.ssh_restore("ed25519"))
        self.assertEqual(private.read_text(), "OLD-PRIVATE")
        rendered.ssh_drop_prev_private("ed25519")
        self.assertFalse((ssh / ("ssh_host_ed25519_key.roseos-prev-" + module.OPERATION_ID)).exists())
        self.assertTrue((ssh / ("ssh_host_ed25519_key.roseos-prev-" + module.OPERATION_ID + ".pub")).is_file())
        (ssh / "ssh_host_rsa_key").symlink_to(private)
        with self.assertRaises(module.GuestRefused):
            rendered.ssh_preserve("rsa")
        auth = disk / "root/.ssh"
        auth.mkdir(parents=True)
        keys = auth / "authorized_keys"
        keys.write_text("ssh-ed25519 AAAA keep\nssh-ed25519 AAAA roseos-yogi-recover-" + module.OPERATION_ID + "\n")
        other = disk / "home/pixelbuild/secret"
        other.parent.mkdir(parents=True)
        other.write_text("STAY")
        rendered.auth_write("/root/.ssh/authorized_keys", ["ssh-ed25519 AAAA keep"])
        evidence = Path(str(keys) + ".roseos-prev-" + module.OPERATION_ID)
        self.assertIn("roseos-yogi-recover-", evidence.read_text())
        self.assertEqual(other.read_text(), "STAY")
        calls = []
        rendered.userdel_fn = calls.append
        rendered.passwd_fn = lambda name: {"name": module.TEMP_ACCOUNT, "uid": 60001, "gid": 60001,
                                           "home": module.TEMP_HOME, "shell": "/usr/sbin/nologin"}
        self.assertEqual(rendered.account_remove(), "removed-home-leftover")
        self.assertEqual(calls, [module.TEMP_ACCOUNT])
        self.assertNotIn('"-r"', Path(guest.__file__).read_text().split("def account_remove", 1)[1].split("def emit", 1)[0])

    def test_partial_cleanup_still_stops(self):
        def boot(items):
            module = load_rendered(self.op / "guest.py")
            host = DictHost(json.loads(items[recover.BINDING_KEY]), self.op / "guest.py")
            host.auth[guest.AUTH_PATHS[0]] = ["ssh-ed25519 AAAA kept-unrelated"]
            module.run(host)
            return host.emitted[-1] + "\n"
        self.preflight()
        self.world.boot_guest = boot
        result = self.apply()
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["guestRead"], "guest-incomplete")
        self.assertEqual(result["stop"], "stopped")
        self.assertEqual(self.world.items, recover.BASE_METADATA)
        self.assertGreater(result["guestReceipt"]["cleanup"]["unidentifiedAuthorizedKeyLines"], 0)

    def test_real_guest_receipt_survives_serial_framing_without_weakening_the_binding(self):
        module = load_rendered(self.op / "guest.py")
        binding = json.loads((self.op / "binding.json").read_text())
        host = DictHost(binding, self.op / "guest.py")
        receipt = module.run(host)
        line = host.emitted[-1]
        self.assertEqual(len(host.emitted), 1)
        self.assertLessEqual(len(line), 8000)
        self.assertEqual(guest.parse_serial(line + "\n", binding)["operationId"], binding["operationId"])
        lead = "Nov  7 08:47:58 roseos google_metadata_script_runner[214]: startup-script: "
        wrapped = []
        body = line
        width = 80
        for offset in range(0, len(body), width):
            wrapped.append(lead + body[offset:offset + width])
        boot = "\n".join([
            "[    0.000000] Linux version 6.8.0-roseos",
            "cloud-init[211]: startup-script: Cloud-init v. 24.1 finished",
            "sshd[240]: Server listening on 0.0.0.0 port 22.",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "b3BlbnNzaC1rZXktdjEAAAAA",
            "-----END OPENSSH PRIVATE KEY-----",
        ])
        framed = boot + "\r\n" + "\r\n".join(wrapped) + "\r\n" + line + "\r\n" + boot + "\r\n"
        parsed = guest.parse_serial(framed, binding)
        self.assertEqual(parsed, receipt)
        saved = recover.serial_evidence(binding, framed, "parsed", receipt=parsed)
        encoded = json.dumps(saved)
        self.assertNotIn("BEGIN OPENSSH", encoded)
        self.assertNotIn("Linux version", encoded)
        self.assertNotIn("Server listening", encoded)
        self.assertEqual(saved["acceptedReceipt"]["operationId"], binding["operationId"])
        self.assertEqual(saved["acceptedReceipt"]["nonce"], binding["nonce"])
        self.assertTrue(saved["receiptLines"][0]["redacted"])
        other = json.loads(guest.canonical(receipt))
        other["bootId"] = "33333333-3333-3333-3333-333333333333"
        foreign = guest.PREFIX + guest.canonical(other)
        with self.assertRaises(guest.Malformed) as raised:
            guest.parse_serial(line + "\n" + foreign + "\n", binding)
        self.assertEqual(raised.exception.code, "sequence")
        huge = guest.PREFIX + ("{" * 8001)
        with self.assertRaises(guest.Malformed) as raised:
            guest.parse_serial(huge + "\n", binding)
        self.assertEqual(raised.exception.code, "oversize")
        fragment = line[len(guest.PREFIX):][:40]
        window = lead + guest.PREFIX + fragment + "\n"
        record = recover.serial_evidence(binding, window, "json")
        self.assertNotIn(fragment, json.dumps(record))
        self.assertEqual(record["receiptLines"][0]["reason"], "json")
        self.assertEqual(record["receiptLines"][0]["length"], 40)
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "spent-serial", now=NOW,
                         token=tokens("20ef6008b1ef78137d201de13176af42", "cd" * 16), revision="a" * 40, env=ENV)

    def test_rotate_only_requires_the_inspected_keys_and_does_not_chmod(self):
        host = DictHost(json.loads((self.op / "binding.json").read_text()), self.op / "guest.py")
        baseline = {}
        for alg, name in guest.KEY_NAMES.items():
            _kind, fingerprint = guest.parse_public(host.ssh_public(name + ".pub"))
            baseline[alg] = fingerprint
        evidence = {
            "schema": guest.RECEIPT_SCHEMA, "purpose": "inspect", "operationId": "11" * 16, "nonce": "22" * 16,
            "payloadSha256": "33" * 32, "instanceId": recover.INSTANCE_ID, "bootId": BOOT,
            "reexecution": False, "previousBootId": "none", "originalVmAccessed": False, "snapshotsUsed": False,
            "status": "complete",
            "unit": {"action": "not-modified", "problem": "already-0644", "beforeMode": "0644", "beforeSize": 347,
                     "beforeUid": 0, "beforeGid": 0, "afterMode": "0644", "afterSize": 347, "afterUid": 0, "afterGid": 0},
            "runner": {"active": "inactive", "enabled": "disabled", "process": False},
            "hostKeys": {"baseline": baseline, "rotated": {}, "distinctFromClonedBaseline": False,
                         "unknownHostKeyCount": 0, "rolledBack": False},
            "cleanup": {"linesRemoved": 0, "unidentifiedAuthorizedKeyLines": 0, "temporaryAccount": "absent",
                        "tempFilesRemoved": 0, "accessCleanup": "ok"},
            "journal": {"entries": 2, "readable": 1, "phases": ["complete"], "hasCompleteReceipt": True,
                        "unrecognized": 1},
        }
        path = Path(self.temp.name) / "inspect-evidence.json"
        path.write_text(json.dumps(evidence))
        commitment = recover.baseline_commitment(baseline)
        out = Path(self.temp.name) / "rotate-op"
        plan = recover.plan("Mac operator", out, now=NOW, token=tokens("44" * 16, "55" * 16), revision="a" * 40,
                            env=ENV, mode="rotate", inspect_receipt=path,
                            confirm_inspect_operation=evidence["operationId"], confirm_baseline=commitment)
        self.assertFalse(plan["hostKeyPinned"])
        self.assertFalse(plan["repairsGuest"])
        self.assertTrue(plan["rotatesHostKeys"])
        self.assertEqual(plan["inspectEvidence"]["role"], "guest-evidence-not-authority")
        self.assertTrue(plan["inspectEvidence"]["priorCompletePhaseIsNotAuthority"])
        module = load_rendered(out / "guest.py")
        host = DictHost(json.loads((out / "binding.json").read_text()), out / "guest.py")
        host.unit["mode"] = 0o644
        host.prior_unrecognized = 1
        receipt = module.run(host)
        self.assertEqual(host.chmod_calls, 0)
        self.assertEqual(host.generate_calls, 3)
        self.assertEqual(host.unit["mode"], 0o644)
        self.assertEqual(len(host.hostkey_puts), 3)
        self.assertTrue(host.marker.get("hostkeyAttributesPublished"))
        self.assertTrue(all("PRIVATE" not in blob and " " not in blob for _key, blob in host.hostkey_puts))
        self.assertNotIn("PRIVATE", json.dumps(receipt))
        identity = plan["hostIdentity"]
        self.assertFalse(identity["pinned"])
        self.assertFalse(identity["iapAuthenticatesHostKey"])
        self.assertNotIn("blocker", identity)
        self.assertFalse(identity["guestAttributes"]["enabledOnThisInstance"])
        self.assertFalse(identity["guestAttributes"]["requiresGuestServiceAccount"])
        self.assertFalse(identity["nextPhase"]["executedByThisTool"])
        self.assertNotIn("enable-guest-attributes", recover.BASE_METADATA)
        self.assertEqual(receipt["purpose"], "rotate")
        self.assertEqual(receipt["unit"]["action"], "not-modified")
        self.assertEqual(receipt["hostKeys"]["baseline"], baseline)
        self.assertEqual(guest.rotate_success_problems(receipt, baseline), [])
        self.assertGreaterEqual(receipt["journal"]["unrecognized"], 1)
        self.assertNotIn("BEGIN OPENSSH", json.dumps(receipt))
        framed = "\n".join([
            "[    0.000000] Linux version 6.8.0-roseos",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            host.emitted[-1],
            "sshd[240]: Server listening on 0.0.0.0 port 22.",
        ]) + "\n"
        self.assertEqual(module.parse_serial(framed, json.loads((out / "binding.json").read_text())), receipt)
        saved = recover.serial_evidence(json.loads((out / "binding.json").read_text()), framed, "parsed", receipt=receipt)
        self.assertNotIn("BEGIN OPENSSH", json.dumps(saved))
        self.assertNotIn("Linux version", json.dumps(saved))
        failed = DictHost(json.loads((out / "binding.json").read_text()), out / "guest.py")
        failed.unit["mode"] = 0o644
        failed.publish_fails = True
        failed_receipt = module.run(failed)
        self.assertEqual(guest.rotate_success_problems(failed_receipt, baseline), [])
        self.assertFalse(failed.marker.get("hostkeyAttributesPublished"))
        self.assertEqual(failed.unit["mode"], 0o644)
        self.assertEqual(failed.generate_calls, 3)
        failed.publish_fails = False
        failed.hostkey_puts = []
        failed.boot = "01234567-89ab-cdef-0123-456789abcdef"
        again = module.run(failed)
        self.assertEqual(failed.generate_calls, 3)
        self.assertEqual(guest.rotate_success_problems(again, baseline), [])
        self.assertTrue(failed.marker.get("hostkeyAttributesPublished"))
        self.assertEqual(len(failed.hostkey_puts), 3)
        live = dict(evidence)
        live["operationId"] = "638e6e9039e41b7531697bb45066a616"
        live["hostKeys"] = dict(evidence["hostKeys"], baseline={
            "ed25519": "SHA256:7q5hBI1kjRbG/OjjOFOj+5URxaJPujYAjZ92S/xyrHs",
            "ecdsa": "SHA256:ddKB95EKzKYE6UlZE0rVIqqgKQ3Le3ZKhh7nmSKTrTc",
            "rsa": "SHA256:uV/VcrkukvQqcWntQbRl1cKauw9f1viUCc5vsVu7nqM",
        })
        live_path = Path(self.temp.name) / "live-inspect.json"
        live_path.write_text(json.dumps(live))
        live_commitment = recover.baseline_commitment(live["hostKeys"]["baseline"])
        refused = Path(self.temp.name) / "rotate-refused"
        recover.plan("Mac operator", refused, now=NOW, token=tokens("66" * 16, "77" * 16), revision="a" * 40,
                     env=ENV, mode="rotate", inspect_receipt=live_path,
                     confirm_inspect_operation=live["operationId"], confirm_baseline=live_commitment)
        other = DictHost(json.loads((refused / "binding.json").read_text()), refused / "guest.py")
        other.unit["mode"] = 0o644
        other.prior_unrecognized = 1
        stopped = load_rendered(refused / "guest.py").run(other)
        self.assertEqual(other.chmod_calls, 0)
        self.assertEqual(other.generate_calls, 0)
        self.assertEqual(other.hostkey_puts, [])
        self.assertEqual(stopped["unit"]["problem"], "baseline")
        self.assertEqual(other.unit["mode"], 0o644)
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "spent-inspect", now=NOW,
                         token=tokens("638e6e9039e41b7531697bb45066a616", "89" * 16), revision="a" * 40, env=ENV)
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "stale-op", now=NOW,
                         token=tokens("98" * 16, "97" * 16), revision="a" * 40, env=ENV, mode="rotate",
                         inspect_receipt=path, confirm_inspect_operation="ab" * 16, confirm_baseline=commitment)
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "stale-baseline", now=NOW,
                         token=tokens("96" * 16, "95" * 16), revision="a" * 40, env=ENV, mode="rotate",
                         inspect_receipt=path, confirm_inspect_operation=evidence["operationId"],
                         confirm_baseline="ab" * 32)
        for index, (mutated, label) in enumerate((
                ({"runner": {"active": "inactive", "enabled": "enabled", "process": False}}, "runner"),
                ({"hostKeys": dict(evidence["hostKeys"], unknownHostKeyCount=1)}, "unknown"),
                ({"unit": dict(evidence["unit"], problem="nlink")}, "hardlink"),
        )):
            bad = json.loads(json.dumps(evidence))
            bad.update(mutated)
            bad_path = Path(self.temp.name) / ("bad-" + label + ".json")
            bad_path.write_text(json.dumps(bad))
            with self.assertRaises(recover.Refused):
                recover.plan("Mac operator", Path(self.temp.name) / ("plan-" + label), now=NOW,
                             token=tokens(f"{80 + index:02d}" * 16, f"{90 + index:02d}" * 16),
                             revision="a" * 40, env=ENV, mode="rotate",
                             inspect_receipt=bad_path, confirm_inspect_operation=bad["operationId"],
                             confirm_baseline=commitment)
        disk = DictHost(json.loads((out / "binding.json").read_text()), out / "guest.py")
        disk.unit = {"kind": "regular", "mode": 0o644, "uid": 0, "gid": 0, "size": 347, "nlink": 2}
        stopped = module.run(disk)
        self.assertEqual(disk.generate_calls, 0)
        self.assertEqual(disk.chmod_calls, 0)
        self.assertEqual(stopped["unit"]["problem"], "nlink")
        disk = DictHost(json.loads((out / "binding.json").read_text()), out / "guest.py")
        disk.unit["mode"] = 0o644
        disk.names.append("ssh_host_extra_key")
        stopped = module.run(disk)
        self.assertEqual(disk.generate_calls, 0)
        self.assertEqual(stopped["unit"]["problem"], "unknown-host-key")
        disk = DictHost(json.loads((out / "binding.json").read_text()), out / "guest.py")
        disk.unit["mode"] = 0o644
        disk.runner_state = {"active": "inactive", "enabled": "enabled", "process": False}
        stopped = module.run(disk)
        self.assertEqual(disk.generate_calls, 0)
        self.assertEqual(stopped["unit"]["problem"], "runner-enabled")

    def test_hostkey_attributes_match_serial_only_as_consistency(self):
        host = DictHost(json.loads((self.op / "binding.json").read_text()), self.op / "guest.py")
        lines, fingerprints = {}, {}
        for alg, name in guest.KEY_NAMES.items():
            lines[alg] = host.public[name + ".roseos-new.pub"]
            _kind, fingerprints[alg] = guest.parse_public(lines[alg])
        puts = []

        class IO:
            def publish_hostkey_attributes(self, writes):
                puts.extend(writes)
                return True

        self.assertTrue(guest.publish_public_hostkeys(IO(), lines))
        self.assertTrue(all(" " not in blob and "PRIVATE" not in blob for _key, blob in puts))
        items = [{"namespace": "hostkeys", "key": key, "value": blob} for key, blob in puts]
        observed = recover.hostkey_consistency({"queryValue": {"items": items}}, fingerprints)
        self.assertTrue(observed["consistentWithSerial"])
        self.assertFalse(observed["pinned"])
        self.assertFalse(observed["honestGuest"])
        self.assertFalse(observed["iapAuthenticatesHostKey"])
        self.assertFalse(observed["requiresGuestServiceAccount"])
        self.assertEqual(observed["binding"], "control-plane-read-of-this-instance-metadata-server")
        short = dict(fingerprints)
        short["ed25519"] = "SHA256:" + "A" * 43
        self.assertFalse(recover.hostkey_consistency({"queryValue": {"items": items}}, short)["consistentWithSerial"])
        self.assertFalse(recover.hostkey_consistency({"queryValue": {"items": items[:2]}}, fingerprints)["pinned"])
        self.assertFalse(recover.hostkey_consistency(
            {"queryValue": {"items": items[:2]}}, fingerprints)["consistentWithSerial"])
        with self.assertRaises(recover.Refused):
            recover.hostkey_consistency(
                {"queryValue": {"items": [{"namespace": "hostkeys", "key": "ssh-ed25519",
                                           "value": "-----BEGIN OPENSSH PRIVATE KEY-----"}]}},
                fingerprints)
        with self.assertRaises(guest.GuestRefused):
            guest.publish_public_hostkeys(IO(), dict(lines, ed25519="-----BEGIN OPENSSH PRIVATE KEY-----"))
        phase = recover.hostkey_observation_phase()
        self.assertFalse(phase["executedByThisTool"])
        self.assertFalse(phase["iapAuthenticatesHostKey"])
        self.assertEqual(phase["gcloudComputeSsh"], "refused")
        self.assertIn("start-iap-tunnel", phase["laterSsh"])
        self.assertNotIn("compute ssh", phase["laterSsh"])
        self.assertNotIn("compute.instances.getGuestAttributes", recover.IAM_REQUIRED["replacement"])


class CommandTest(Base):
    def test_argv_stays_on_the_replacement_and_metadata_is_one_http_body(self):
        self.assertTrue(recover.START_URL.endswith(
            "/projects/protocols-465723/zones/us-west2-b/instances/roseos-yogi-builder-20261105-b/start"))
        self.assertTrue(recover.STOP_URL.endswith(
            "/projects/protocols-465723/zones/us-west2-b/instances/roseos-yogi-builder-20261105-b/stop"))
        with self.assertRaises(recover.Refused):
            recover.gcloud_argv({"kind": "start", "target": "replacement"})
        with self.assertRaises(recover.Refused):
            recover.gcloud_argv({"kind": "start", "target": "original"})
        with self.assertRaises(recover.Refused):
            recover.gcloud_argv({"kind": "stop", "target": "original"})
        with self.assertRaises(recover.Refused):
            recover.guard_request({"kind": "delete-snapshot", "target": "prekey"})
        body = recover.metadata_body("fp-base", dict(recover.BASE_METADATA, **{
            "startup-script": "echo hi\n", recover.BINDING_KEY: "binding"}))
        self.assertEqual(body["fingerprint"], "fp-base")
        self.assertEqual([item["key"] for item in body["items"][:2]], list(recover.BASE_ORDER))
        self.assertTrue(recover.METADATA_URL.endswith(
            "/projects/protocols-465723/zones/us-west2-b/instances/roseos-yogi-builder-20261105-b/setMetadata"))
        parsed = recover.interpret_metadata_http(500, {"error": {"code": 13, "status": "INTERNAL"}})
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["disposition"], "submission-error")
        self.assertEqual(parsed["errorCode"], "INTERNAL")
        incomplete = recover.interpret_metadata_http(200, {"status": "DONE", "targetId": recover.INSTANCE_ID})
        self.assertFalse(incomplete["ok"])
        self.assertEqual(incomplete["disposition"], "malformed")
        done = recover.interpret_operation(200, _operation("setMetadata", "DONE"), "setMetadata")
        self.assertTrue(done["ok"])
        self.assertEqual(done["disposition"], "done")
        self.assertEqual(recover.classify_metadata(
            done, (recover.BASE_METADATA, "fp-base"),
            (dict(recover.BASE_METADATA, **{"startup-script": "s", recover.BINDING_KEY: "b"}), "fp-next"),
            dict(recover.BASE_METADATA, **{"startup-script": "s", recover.BINDING_KEY: "b"})), "clean")
        self.assertEqual(recover.classify_metadata(
            parsed, (recover.BASE_METADATA, "fp-base"), (recover.BASE_METADATA, "fp-base"),
            {"startup-script": "s"}), "not-applied")
        self.assertEqual(recover.interpret_metadata_http(200, None)["disposition"], "malformed")
        self.assertEqual(recover.interpret_metadata_http(500, None)["disposition"], "malformed")
        accepted = recover.interpret_operation(200, _operation("setMetadata", "RUNNING"), "setMetadata")
        self.assertEqual(accepted["disposition"], "accepted")
        self.assertFalse(accepted["ok"])
        listing = recover.gcloud_argv({"kind": "list-firewalls", "target": "none"})
        self.assertEqual(listing, ["gcloud", "compute", "firewall-rules", "list",
                                   "--project=protocols-465723", "--format=json", "--quiet"])
        self.assertNotIn("sourceRanges", " ".join(listing))
        self.assertNotIn("--filter", listing)

    def test_firewall_admission_and_iam_null_are_not_grants(self):
        instance = {"networkInterfaces": [{"network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/default"}],
                    "tags": {"items": []}}
        allow = {"name": "allow-iap", "direction": "INGRESS", "disabled": False, "priority": 1000,
                 "network": instance["networkInterfaces"][0]["network"], "sourceRanges": [recover.IAP_RANGE],
                 "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]}
        self.assertEqual(recover.firewall_problems([allow], instance), [])
        self.assertEqual(recover.firewall_problems([{**allow, "disabled": True}], instance), ["iap-firewall"])
        self.assertEqual(recover.firewall_problems([{**allow, "direction": "EGRESS"}], instance), ["iap-firewall"])
        self.assertEqual(recover.firewall_problems([{**allow, "targetTags": ["not-this-vm"]}], instance), ["iap-firewall"])
        self.assertEqual(recover.firewall_problems([{**allow, "network": "https://www.googleapis.com/compute/v1/projects/protocols-465723/global/networks/other"}], instance), ["iap-firewall"])
        denied = {**allow, "name": "deny-ssh", "priority": 100, "allowed": [], "denied": [{"IPProtocol": "tcp", "ports": ["22"]}]}
        self.assertEqual(recover.firewall_problems([allow, denied], instance), ["iap-firewall-denied"])
        self.assertEqual(recover.firewall_problems({"items": [allow]}, instance), ["iap-firewall"])
        self.assertEqual(recover.attribute_text({
            "kind": "compute#guestAttributes", "variableValue": "{\"a\":1}",
            "queryValue": {"items": [{"namespace": "roseos-yogi-recover", "key": "receipt", "value": "{\"a\":1}"}]},
        }), "{\"a\":1}")
        self.assertIsNone(recover.attribute_text({"variableValue": "one", "value": "two"}))
        self.assertIsNone(recover.attribute_text([{"value": "one"}, {"value": "two"}]))
        with self.assertRaises(guest.Malformed):
            recover.read_new(lambda _request: {"kind": "compute#instance", "contents": "", "start": "0", "next": "1"}, "0")
        self.assertEqual(recover.iam_url("project"),
                         "https://cloudresourcemanager.googleapis.com/v1/projects/protocols-465723:testIamPermissions")
        self.assertNotIn("compute.googleapis.com", recover.iam_url("project"))
        self.assertTrue(recover.iam_url("replacement").startswith("https://compute.googleapis.com/compute/v1/"))
        self.assertTrue(recover.iam_url("replacement").endswith("/instances/roseos-yogi-builder-20261105-b/testIamPermissions"))
        self.assertIsNone(recover.permissions_from_iam_body(None))
        self.assertIsNone(recover.permissions_from_iam_body({"permissions": None}))
        self.assertEqual(recover.permissions_from_iam_body({"permissions": []}), [])
        self.assertIn("permission-check-failed", recover.iam_problems({"project": None, "replacement": [], "original": []}))
        seen = {}

        class Response:
            def __init__(self, status, body):
                self.status, self._body = status, body

            def read(self, _limit):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, timeout):
            seen.setdefault("urls", []).append(request.full_url)
            seen["calls"] = seen.get("calls", 0) + 1
            body = b"null" if seen["calls"] == 1 else b'{"error":{"status":"INTERNAL"}}'
            status = 200 if seen["calls"] == 1 else 500
            return Response(status, body)

        def run(_argv, **_kwargs):
            return type("Proc", (), {"returncode": 0, "stdout": "token\n", "stderr": ""})()

        transport = recover.RealTransport(run=run, opener=opener)
        request = {"kind": "test-iam", "target": "project", "permissions": list(recover.IAM_REQUIRED["project"])}
        with self.assertRaises(recover.TransportError):
            transport(request)
        with self.assertRaises(recover.TransportError):
            transport(request)
        self.assertEqual(seen["calls"], 2)
        self.assertEqual(seen["urls"], [recover.iam_url("project"), recover.iam_url("project")])
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "spent", now=NOW,
                         token=tokens("e75b0fd782e43f585c621c74f95bfa90", "ab" * 16), revision="a" * 40, env=ENV)

    def test_metadata_http_null_and_internal_are_one_call(self):
        """A null body or HTTP error is not a successful setMetadata, and that call is not repeated."""
        bodies = [b"null", b'{"error":{"code":500,"status":"INTERNAL"}}']
        statuses = [200, 500]

        class Response:
            def __init__(self, status, body):
                self.status, self._body = status, body

            def read(self, _limit):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        calls = {"n": 0}

        def opener(_request, timeout):
            index = calls["n"]
            calls["n"] += 1
            return Response(statuses[index], bodies[index])

        def run(_argv, **_kwargs):
            return type("Proc", (), {"returncode": 0, "stdout": "token\n", "stderr": ""})()

        transport = recover.RealTransport(run=run, opener=opener)
        request = {"kind": "set-metadata", "target": "replacement", "fingerprint": "fp-base",
                   "items": dict(recover.BASE_METADATA)}
        first = transport(request)
        second = transport(request)
        self.assertEqual(calls["n"], 2)
        first_view = recover.interpret_operation(first["httpStatus"], first["body"], "setMetadata")
        second_view = recover.interpret_operation(second["httpStatus"], second["body"], "setMetadata")
        self.assertEqual(first_view["disposition"], "malformed")
        self.assertEqual(first_view["errorCode"], "unparseable")
        self.assertEqual(second_view["disposition"], "submission-error")
        self.assertEqual(second_view["errorCode"], "INTERNAL")
        seen = []

        def once(inner):
            seen.append(inner["kind"])
            if inner["kind"] == "set-metadata":
                return {"httpStatus": 500, "body": {"error": {"code": 13, "status": "INTERNAL"}}}
            if inner["kind"] == "describe-instance":
                return instance(recover.INSTANCE, recover.ZONE, recover.INSTANCE_ID)
            raise AssertionError(inner["kind"])

        attempt, after = recover.one_set(once, "fp-base", dict(recover.BASE_METADATA))
        self.assertEqual(seen, ["set-metadata", "describe-instance"])
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["disposition"], "submission-error")
        self.assertEqual(attempt["errorCode"], "INTERNAL")
        self.assertIsNotNone(after)

    def test_accepted_pending_operation_is_waited_then_classified(self):
        name = "operation-1794026000001-65fa8387d4384-e9bed427-00000002"
        other = "operation-1794026000002-65fa8387d4384-e9bed427-00000003"
        pending = _operation("setMetadata", "PENDING", name)
        running = _operation("setMetadata", "RUNNING", name)
        done = _operation("setMetadata", "DONE", name)
        failed = _operation("setMetadata", "DONE", name, error="CONDITION_NOT_MET")
        phases = {"set": [running, done]}

        class Response:
            def __init__(self, status, body):
                self.status, self._body = status, body

            def read(self, _limit):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.get_method(), request.data, timeout))
            if request.full_url.endswith("/setMetadata"):
                body = phases["set"].pop(0)
            elif request.full_url.endswith("/wait"):
                body = phases["set"].pop(0)
            else:
                raise AssertionError(request.full_url)
            return Response(200, json.dumps(body).encode())

        def run(_argv, **_kwargs):
            return type("Proc", (), {"returncode": 0, "stdout": "token\n", "stderr": ""})()

        transport = recover.RealTransport(run=run, opener=opener)
        clock = Clock()
        attempt = recover.complete_operation(
            transport, {"httpStatus": 200, "body": pending}, "setMetadata",
            recover.Deadline(clock, 30), clock.sleep)
        self.assertTrue(attempt["ok"])
        self.assertEqual(attempt["disposition"], "done")
        self.assertEqual(attempt["name"], name)
        self.assertEqual([item[1] for item in calls], ["POST", "POST"])
        self.assertTrue(calls[0][0].endswith("/setMetadata") is False)
        self.assertTrue(calls[0][0].endswith("/operations/" + name + "/wait"))
        self.assertIsNone(calls[0][2])
        self.assertTrue(calls[1][0].endswith("/operations/" + name + "/wait"))
        self.assertIsNone(calls[1][2])
        self.assertNotIn("setMetadata", calls[1][0])

        def mismatch_opener(request, timeout):
            calls.append(request.full_url)
            return Response(200, json.dumps(_operation("setMetadata", "DONE", other)).encode())

        calls.clear()
        mismatch = recover.RealTransport(run=run, opener=mismatch_opener)
        bad = recover.complete_operation(
            mismatch, {"httpStatus": 200, "body": pending}, "setMetadata",
            recover.Deadline(Clock(), 30), lambda _seconds: None)
        self.assertEqual(bad["disposition"], "mismatch")
        self.assertEqual(bad["errorCode"], "handle")
        self.assertEqual(calls, [recover.operation_url(name, True)])

        refused = recover.complete_operation(
            transport, {"httpStatus": 500, "body": {"error": {"code": 13, "status": "INTERNAL"}}},
            "setMetadata", recover.Deadline(Clock(), 30), lambda _seconds: None)
        self.assertEqual(refused["disposition"], "submission-error")
        self.assertEqual(refused["errorCode"], "INTERNAL")
        self.assertFalse(refused["ok"])

        missing = recover.complete_operation(
            transport, {"httpStatus": 200, "body": None}, "setMetadata",
            recover.Deadline(Clock(), 30), lambda _seconds: None)
        self.assertEqual(missing["disposition"], "malformed")
        self.assertEqual(missing["errorCode"], "unparseable")

        errored = recover.complete_operation(
            lambda request: {"httpStatus": 200, "body": failed} if request["kind"] == "operation-wait"
            else (_ for _ in ()).throw(AssertionError(request["kind"])),
            {"httpStatus": 200, "body": pending}, "setMetadata",
            recover.Deadline(Clock(), 30), lambda _seconds: None)
        self.assertEqual(errored["disposition"], "done-error")
        self.assertFalse(errored["ok"])

        def hang(request):
            calls.append(request["kind"])
            if request["kind"] != "operation-wait":
                raise AssertionError(request["kind"])
            return {"httpStatus": 200, "body": pending}

        calls.clear()
        hung = recover.complete_operation(
            hang, {"httpStatus": 200, "body": pending}, "setMetadata",
            recover.Deadline(Clock(), 180), lambda _seconds: None)
        self.assertEqual(hung["disposition"], "pending-timeout")
        self.assertEqual(calls, ["operation-wait", "operation-wait", "operation-wait"])
        self.assertNotIn("set-metadata", calls)

    def test_live_pending_metadata_waits_and_a_rollback_wait_can_succeed(self):
        self.preflight()
        self.world.fail_sets = ["running-then-done"]
        self.world.boot_guest = self.boot
        result = self.apply()
        self.assertEqual(result["metadata"], "clean")
        self.assertEqual(result["exit"], 0)
        self.assertIn("operation-wait", self.world.kinds())
        self.assertEqual(self.world.targets("set-metadata"), ["replacement", "replacement"])
        self.assertEqual(self.world.items, recover.BASE_METADATA)
        self.assertNotIn(self.world.requests[0].get("name"), recover.SPENT_ZONE_OPERATIONS)

        def plan_op(mark):
            path = Path(self.temp.name) / ("op" + mark)
            recover.plan("Mac operator", path, now=NOW, token=tokens(mark * 16, "aa" * 16),
                         revision="d" * 40, env=ENV)
            return path

        def emit(op, items):
            module = load_rendered(Path(op) / "guest.py")
            host = DictHost(json.loads(items[recover.BINDING_KEY]), Path(op) / "guest.py")
            module.run(host)
            return host.emitted[-1] + "\n"

        fresh = plan_op("31")
        world = World()
        world.fail_sets = ["wait-mismatch"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(),
                               sleep=lambda _seconds: None, env=ENV)
        self.assertEqual(result["metadata"], "mismatch")
        self.assertEqual(world.targets("set-metadata"), ["replacement"])
        self.assertEqual(world.kinds().count("operation-wait"), 1)
        self.assertNotIn("start", world.kinds())
        fresh = plan_op("32")
        world = World()
        world.fail_sets = ["wrong-target"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(),
                               sleep=lambda _seconds: None, env=ENV)
        self.assertEqual(result["metadata"], "mismatch")
        self.assertNotIn("operation-wait", world.kinds())
        fresh = plan_op("33")
        world = World()
        world.fail_sets = ["done-error"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(),
                               sleep=lambda _seconds: None, env=ENV)
        self.assertEqual(result["metadata"], "not-applied")
        self.assertEqual(world.kinds().count("operation-wait"), 1)
        self.assertNotIn("start", world.kinds())
        fresh = plan_op("34")
        world = World()
        world.fail_sets = ["wait-interrupt"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        with self.assertRaises(KeyboardInterrupt):
            recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(),
                          sleep=lambda _seconds: None, env=ENV)
        self.assertNotIn("start", world.kinds())
        self.assertEqual(world.replacement_status, "TERMINATED")
        fresh = plan_op("35")
        world = World()
        world.start_mode = "pending"
        world.boot_guest = lambda items, op=fresh: emit(op, items)
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        clock = Clock()
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=clock, sleep=lambda _s: None,
                               env=ENV)
        self.assertEqual(result["result"], "start-unconfirmed")
        self.assertEqual(result["stop"], "stopped")
        self.assertNotIn("startup-script", world.items)
        self.assertEqual(world.replacement_status, "TERMINATED")
        fresh = plan_op("36")
        world = World()
        world.stop_mode = "pending"
        world.boot_guest = lambda items, op=fresh: emit(op, items)
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(),
                               sleep=lambda _s: None, env=ENV)
        self.assertEqual(result["exit"], 1)
        self.assertNotEqual(result["result"], "procedure-complete-guest-reported")
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "spent-live", now=NOW,
                         token=tokens("5cf6fe9a62880b89ee6e7e822a0379d2", "ab" * 16), revision="a" * 40, env=ENV)
        with self.assertRaises(recover.Refused):
            recover.operation_url(next(iter(recover.SPENT_ZONE_OPERATIONS)), True)

    def test_bundle_unittest_and_archive_reject_bytecode(self):
        if os.environ.get("YOGI_RECOVER_BUNDLE_TEST") == "1":
            self.skipTest("the exported bundle is already running this suite")
        bundle = Path(self.temp.name) / "fresh-bundle"
        recover.export_bundle(bundle)
        env = {"PATH": ENV["PATH"], "PYTHONDONTWRITEBYTECODE": "1", "YOGI_RECOVER_BUNDLE_TEST": "1"}
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_yogi_replacement_recover"],
                                cwd=bundle, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse((bundle / "__pycache__").exists())
        recover.self_check(bundle)
        tar_path = Path(self.temp.name) / "bundle.tar.gz"
        second = Path(self.temp.name) / "bundle-again.tar.gz"
        recover.pack_bundle(bundle, tar_path)
        recover.pack_bundle(bundle, second)
        self.assertEqual(tar_path.read_bytes(), second.read_bytes())
        import tarfile
        with tarfile.open(tar_path) as archive:
            names = archive.getnames()
        self.assertTrue(names)
        self.assertFalse(any(name.endswith(".pyc") or "__pycache__" in name for name in names))
        extra = bundle / "notes.txt"
        extra.write_text("not in the manifest\n")
        with self.assertRaises(recover.Refused):
            recover.self_check(bundle)
        extra.unlink()
        cache = bundle / "__pycache__"
        cache.mkdir()
        (cache / "yogi_replacement_recover_guest.cpython-312.pyc").write_bytes(b"not-bytecode")
        with self.assertRaises(recover.Refused):
            recover.self_check(bundle)

    def test_historical_collision_is_refused_without_reading_the_packet(self):
        banned = next(iter(recover.historical_hashes()))
        with self.assertRaises(recover.Refused):
            recover.reject_historical("ab" * 16, "cd" * 16, banned)
        self.assertNotIn("inspect_guest", Path(guest.__file__).read_text())
        self.assertIn("recorded-from-relay-not-recomputed", Path(recover.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
