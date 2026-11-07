"""Offline checks for the Yogi replacement recovery. Fake control plane and in-memory guest only."""
import base64
import copy
import datetime
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yogi_ota as yogi  # noqa: E402
import yogi_direct  # noqa: E402
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
        "networkInterfaces": [{"networkIP": "10.1.0.4"}],
        "disks": [{"boot": True, "source": disk}],
    }
    if nat:
        body["networkInterfaces"][0]["accessConfigs"] = [{"natIP": nat}]
    return body


def disk(name, zone, number, user, size="2000"):
    return {"id": number, "name": name, "sizeGb": str(size), "status": "READY",
            "zone": f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}",
            "users": [f"https://www.googleapis.com/compute/v1/projects/{recover.PROJECT}/zones/{zone}/instances/{user}"]}


def attempt(ok=True, code="", status="DONE", target=recover.INSTANCE_ID, http=200):
    return {"ok": ok, "unparseable": code == "unparseable", "httpStatus": http, "operationStatus": status,
            "errorCode": code, "targetId": target}


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
        self.firewalls = [{"name": "allow-iap-ssh", "direction": "INGRESS", "disabled": False,
                           "sourceRanges": [recover.IAP_RANGE],
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
        self.leave_running = 0
        self.boot_guest = None
        self.requests = []
        self.on_start = None

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
        if kind == "serial":
            return self._serial(request["start"])
        if kind == "guest-attribute":
            return copy.deepcopy(self.attribute)
        if kind == "start":
            return self._start()
        if kind == "stop":
            return self._stop()
        raise AssertionError(kind)

    def _set(self, request):
        mode = self.fail_sets.pop(0) if self.fail_sets else "ok"
        if mode == "timeout":
            raise recover.TransportError("timeout")
        if mode == "readback":
            self.fail_describe += 1
            return {"attempt": attempt(False, "INTERNAL", "none", "", 500)}
        if mode == "internal":
            return {"attempt": attempt(False, "INTERNAL", "none", "", 500)}
        if mode == "unparseable":
            return {"attempt": attempt(False, "unparseable", "none", "", 200)}
        if mode == "pending":
            return {"attempt": attempt(False, "not-done", "RUNNING", recover.INSTANCE_ID, 200)}
        if mode == "divergent":
            self._apply_items(dict(self.items, **{"ssh-keys": "other"}))
            return {"attempt": attempt(False, "INTERNAL", "none", "", 500)}
        if mode == "applied-internal":
            self._apply_items(request["items"])
            return {"attempt": attempt(False, "INTERNAL", "none", "", 500)}
        if request["fingerprint"] != self.fingerprint:
            return {"attempt": attempt(False, "fingerprint", "DONE", recover.INSTANCE_ID, 412)}
        self._apply_items(request["items"])
        return {"attempt": attempt()}

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
        if self.start_mode == "timeout-down":
            raise recover.TransportError("timeout")
        if self.start_mode == "timeout-up":
            self.replacement_status = "RUNNING"
            raise recover.TransportError("timeout")
        self.replacement_status = "RUNNING"
        if self.on_start:
            self.on_start()
        if self.boot_guest:
            self.pending = self.boot_guest(self.items) or ""
        return {"requested": True}

    def _stop(self):
        if self.leave_running:
            self.leave_running -= 1
            self.replacement_status = "RUNNING"
            return {"requested": True}
        self.replacement_status = "TERMINATED"
        return {"requested": True}

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
        self.identity = dict(guest.EXPECTED_IDENTITY)
        self.unit = {"kind": "regular", "mode": 0o664, "uid": 0, "gid": 0, "size": 347, "nlink": 1}
        self.runner_state = {"active": "inactive", "enabled": "disabled", "process": False}
        self.names = list(guest.INVENTORY)
        self.fail_commit = None
        self.same_fingerprint = False
        self.auth = {path: None for path in guest.AUTH_PATHS}
        self.account_entry = None
        self.account_fail = False
        self.removed_account = False
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

    def ssh_generate(self, algorithm):
        self.generate_calls += 1
        self.log.append(("generate", algorithm))
        if self.same_fingerprint:
            return self.public[guest.KEY_NAMES[algorithm] + ".pub"]
        return self.public[guest.KEY_NAMES[algorithm] + ".roseos-new.pub"]

    def ssh_commit(self, algorithm):
        self.commit_calls += 1
        self.log.append(("commit", algorithm))
        if self.fail_commit == algorithm:
            raise OSError("rotate-partial")
        live = guest.KEY_NAMES[algorithm] + ".pub"
        self.public[live] = self.public[guest.KEY_NAMES[algorithm] + ".roseos-new.pub"]

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
        self.assertNotIn("@@", (self.op / "guest.py").read_text())
        text = (ROOT_TEXT())
        self.assertNotIn("ssh-keyscan", text)
        self.assertNotIn("gcloud compute ssh", text)
        self.assertNotIn("setCommonInstanceMetadata", text)

    def test_actions_context_and_bad_confirmation_are_refused(self):
        with self.assertRaises(recover.Refused):
            recover.plan("Mac operator", Path(self.temp.name) / "no", env={"GITHUB_ACTIONS": "true"})
        with self.assertRaises(recover.Refused):
            recover.preflight(self.op, "1", True, self.world, now=NOW, env=ENV)
        dry = recover.apply(self.op, recover.INSTANCE_ID, False, self.world, now=NOW, env=ENV)
        self.assertTrue(dry["dryRun"])
        self.assertEqual(self.world.requests, [])

    def test_direct_route_stays_unbound(self):
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
        self.assertEqual(result["restore"], "not-needed")
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
        self.assertEqual(result["metadata"], "not-applied")
        self.assertNotIn("start", world.kinds())
        fresh = self._fresh("13")
        world = World()
        world.fail_sets = ["divergent"]
        recover.preflight(fresh, recover.INSTANCE_ID, True, world, now=NOW, env=ENV)
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(), sleep=lambda s: None, env=ENV)
        self.assertEqual(result["restore"], "skipped-unknown-keys")
        self.assertEqual(len(world.targets("set-metadata")), 1)
        self.assertIn("ssh-keys", world.items)

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
        self.assertEqual(result["guestRead"], "malicious")
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
        result = recover.apply(fresh, recover.INSTANCE_ID, True, world, now=NOW, clock=Clock(), sleep=lambda s: None, env=ENV)
        self.assertEqual(result["result"], "serial-cursor-failed")
        self.assertNotIn("start", world.kinds())
        self.assertEqual(world.items, recover.BASE_METADATA)

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
        self.world.replacement_status = "RUNNING"
        self.world.items = {"enable-oslogin": "FALSE", "block-project-ssh-keys": "TRUE",
                            "startup-script": "left", recover.BINDING_KEY: "left"}
        self.world.fingerprint = "fp-left"
        before = len(self.world.targets("start"))
        result = recover.cleanup(self.op, recover.INSTANCE_ID, True, self.world, now=NOW, env=ENV)
        self.assertEqual(result["exit"], 0)
        self.assertEqual(self.world.items, recover.BASE_METADATA)
        self.assertEqual(self.world.replacement_status, "TERMINATED")
        self.assertEqual(len(self.world.targets("start")), before)

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


class CommandTest(Base):
    def test_argv_stays_on_the_replacement_and_metadata_is_one_http_body(self):
        start = recover.gcloud_argv({"kind": "start", "target": "replacement"})
        self.assertEqual(start[4], recover.INSTANCE)
        self.assertNotIn("ssh", start)
        self.assertNotIn("--async", start)
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
        parsed = recover.interpret_metadata_http(500, {"error": {"code": 500, "status": "INTERNAL"}})
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["errorCode"], "INTERNAL")
        done = recover.interpret_metadata_http(200, {"status": "DONE", "targetId": recover.INSTANCE_ID})
        self.assertTrue(done["ok"])
        self.assertEqual(recover.classify_metadata(
            done, (recover.BASE_METADATA, "fp-base"),
            (dict(recover.BASE_METADATA, **{"startup-script": "s", recover.BINDING_KEY: "b"}), "fp-next"),
            dict(recover.BASE_METADATA, **{"startup-script": "s", recover.BINDING_KEY: "b"})), "clean")
        self.assertEqual(recover.classify_metadata(
            parsed, (recover.BASE_METADATA, "fp-base"), (recover.BASE_METADATA, "fp-base"),
            {"startup-script": "s"}), "not-applied")

    def test_historical_collision_is_refused_without_reading_the_packet(self):
        banned = next(iter(recover.historical_hashes()))
        with self.assertRaises(recover.Refused):
            recover.reject_historical("ab" * 16, "cd" * 16, banned)
        self.assertNotIn("inspect_guest", Path(guest.__file__).read_text())
        self.assertIn("recorded-from-relay-not-recomputed", Path(recover.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
