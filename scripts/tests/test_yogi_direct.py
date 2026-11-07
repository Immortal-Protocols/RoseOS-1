"""Offline checks of the Yogi direct operator route. Fake gcloud/ssh, temporary git repositories and local shells only:
no VM, SSH connection, runner, network, key or phone."""
import base64
import copy
import datetime
import hashlib
import io
import json
import os
import shlex
import time
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yogi_direct as direct  # noqa: E402
import yogi_ota as yogi  # noqa: E402
import yogi_possession  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 7, 3, 0, tzinfo=UTC)
BLOB = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + bytes(range(32))  # synthetic public key blob (test only)
KEY = base64.b64encode(BLOB).decode()
FINGERPRINT = 'SHA256:' + base64.b64encode(hashlib.sha256(BLOB).digest()).decode().rstrip('=')
TUPLE = {'project': yogi.PROJECT, 'zone': yogi.ZONE, 'instance': yogi.INSTANCE, 'instanceId': yogi.INSTANCE_ID}
ORIGINAL_ID = '8812446529823929967'


def host_key(**changes):
    value = {'type': 'ssh-ed25519', 'key': KEY, 'sha256': FINGERPRINT, 'instanceId': yogi.INSTANCE_ID,
             'observedVia': 'serial-console-output', 'distinctFromOriginal': True, 'provenance': 'synthetic'}
    value.update(changes)
    return value


def ready_config(root):
    """The committed config with synthetic stand-ins for every direct dependency (test only)."""
    cfg = copy.deepcopy(yogi.config())
    b = cfg['builder']
    b.update(root=str(root), retainedSource=str(root/'src'), attempts=str(root/'attempts'),
             lock=str(root/'yogi-build.lock'), coverDisplayInput=str(root/'inputs/cover.json'),
             bootstrapState=str(root/'signing-bootstrap-state'))
    b['direct'].update(status='ready', route='external-ip', sshUser='yogi-operator', hostKey=host_key(),
                       runnerService='actions.runner.synthetic.service',
                       operatorAccess={'compute': 'synthetic', 'ssh': 'synthetic', 'paths': 'synthetic',
                                       'provenance': 'synthetic'},
                       operationRoot=str(root/'direct'), lock=str(root/'yogi-direct.lock'))
    return cfg


def describe_json(name=yogi.INSTANCE, zone=yogi.ZONE, number=yogi.INSTANCE_ID, status='TERMINATED', ip='203.0.113.7'):
    return json.dumps({'id': number, 'name': name, 'zone': f'projects/{yogi.PROJECT}/zones/{zone}', 'status': status,
                       'selfLink': f'https://compute.googleapis.com/compute/v1/projects/{yogi.PROJECT}/zones/{zone}/instances/{name}',
                       'networkInterfaces': [{'accessConfigs': [{'natIP': ip}]}]})


class Fake:
    """Scripted gcloud and ssh. Every call is logged; ssh commands are classified by what they are."""
    def __init__(self, states=('TERMINATED', 'RUNNING'), original='TERMINATED', probe=None, remote=None,
                 ssh_rc=0, install_rc=0, start_rc=0, describe_id=yogi.INSTANCE_ID):
        self.states, self.original, self.log = list(states), original, []
        self.probe = probe if probe is not None else {'builder': TUPLE, 'runnerProcess': False, 'active': 'inactive',
                                                      'enabled': 'disabled'}
        self.remote = remote or {'status': {'state': 'not-installed'}, 'alive': False}
        self.ssh_rc, self.install_rc, self.start_rc, self.describe_id = ssh_rc, install_rc, start_rc, describe_id

    def kind(self, argv):
        if argv[0] == 'ssh':
            command = argv[-1]
            if shlex.quote(direct.PROBE) in command:
                return 'probe'
            if shlex.quote(direct.STATUS_PROBE) in command:
                return 'status'
            if command.startswith('set -eu'):
                return 'install'
            if command.startswith('kill -TERM'):
                return 'kill'
            return 'ssh-other'
        return 'gcloud-' + argv[3]

    def __call__(self, argv, **kwargs):
        kind = self.kind(argv)
        self.log.append((kind, argv, kwargs))
        out = ''
        rc = 0
        if kind == 'gcloud-describe':
            if argv[4] == yogi.INSTANCE:
                state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
                out = describe_json(status=state, number=self.describe_id)
            else:
                out = describe_json(name=argv[4], zone='us-west2-a', number=ORIGINAL_ID, status=self.original)
        elif kind == 'gcloud-start':
            rc = self.start_rc
        elif kind.startswith('gcloud'):
            rc = 0
        elif self.ssh_rc:
            rc = self.ssh_rc
        elif kind == 'probe':
            out = json.dumps(self.probe)
        elif kind == 'status':
            out = json.dumps(self.remote)
        elif kind == 'install':
            rc = self.install_rc
        if argv[0] == 'ssh':
            return SimpleNamespace(returncode=rc, stdout=out.encode(), stderr=b'')
        return SimpleNamespace(returncode=rc, stdout=out, stderr='')

    def kinds(self):
        return [k for k, _, _ in self.log]


def ticking(step=60):
    """A monotonic test clock that advances on every read, so any poll loop is bounded."""
    state = {'now': 0}

    def clock():
        state['now'] += step
        return state['now']
    return clock


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = ready_config(self.root/'srv')
        self.repo = self.root/'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'config', 'user.name', 'test')
        (self.repo/'scripts').mkdir()
        (self.repo/'scripts/yogi_direct.py').write_text('# synthetic reviewed source\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'reviewed')
        self.source = git(self.repo, 'rev-parse', 'HEAD')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', self.source)
        self.identity = self.root/'operator_key'
        self.identity.write_text('synthetic private key placeholder (never read)\n')
        self.env = {'PATH': os.environ.get('PATH', '')}

    def plan(self, stage='check', **kwargs):
        kwargs.setdefault('out', self.root/'op')
        return direct.plan(self.cfg, stage, kwargs.pop('source', self.source), kwargs.pop('operator', 'Mac operator'),
                           repo=self.repo, now=NOW, env=self.env, **kwargs)

    def started(self, fake=None):
        manifest = self.plan()
        fake = fake or Fake()
        direct.start(self.cfg, self.root/'op', run=fake, sleep=lambda s: None, clock=ticking(), env=self.env, now=NOW)
        return manifest


class ContractTest(unittest.TestCase):
    def test_committed_tree_keeps_the_direct_route_unbound_and_the_runner_route_unchanged(self):
        cfg = yogi.config()
        d = cfg['builder']['direct']
        self.assertEqual(d['status'], 'unbound')
        for key in ('route', 'sshUser', 'hostKey', 'runnerService', 'operatorAccess'):
            self.assertIsNone(d[key])  # unverified dependencies, never defaults
        for stage in direct.STAGES:
            joined = ' | '.join(direct.blockers(cfg, stage))
            for name in ('builder.direct.status', 'builder.direct.route', 'builder.direct.sshUser',
                         'builder.direct.hostKey', 'builder.direct.runnerService', 'builder.direct.operatorAccess'):
                self.assertIn(name, joined)
        self.assertIn('signed app inputs are not bound', ' '.join(direct.blockers(cfg, 'compile')))
        # The runner route's gates are untouched by this route.
        self.assertEqual(cfg['builder']['status'], 'runner-unregistered')
        self.assertIsNone(cfg['builder']['evidence']['runner']['organizationEligibility'])
        self.assertIn('runner.organizationEligibility', yogi.EVIDENCE_FIELDS)
        for stage in ('key-bootstrap', 'bootstrap', 'ota'):
            joined = ' '.join(yogi.blockers(cfg, stage))
            self.assertIn('runner.organizationEligibility', joined)
            self.assertIn('builder.status=runner-unregistered', joined)
        self.assertFalse(cfg['publicationEnabled'] or cfg['automationEnabled'])
        self.assertEqual(cfg['builder']['retired']['instanceId'], ORIGINAL_ID)

    def test_signing_install_staging_ota_and_publication_are_refused_even_when_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = ready_config(Path(temp))
            for stage in direct.NOT_DIRECT:
                with self.subTest(stage=stage):
                    self.assertEqual(len(direct.blockers(cfg, stage)), 1)
                    with self.assertRaisesRegex(yogi.Refused, 'not available on the direct route'):
                        direct.guard(cfg, stage)
            self.assertIn('OIDC is never forwarded', direct.NOT_DIRECT['sign'])

    def test_host_key_pin_must_be_independently_bound_and_distinct_from_the_original(self):
        self.assertIsNone(direct.host_key_problem(host_key()))
        for changes, reason in (({'sha256': 'SHA256:other'}, 'does not match'), ({'type': 'ssh-rsa'}, 'ssh-ed25519'),
                                ({'instanceId': ORIGINAL_ID}, 'numeric instance ID'),
                                ({'observedVia': 'first-use'}, 'observedVia'), ({'observedVia': 'ssh-keyscan'}, 'observedVia'),
                                ({'observedVia': 'guest-hostname'}, 'observedVia'),
                                ({'distinctFromOriginal': None}, 'restored'), ({'provenance': ''}, 'provenance'),
                                ({'key': 'not base64!'}, 'valid OpenSSH')):
            with self.subTest(changes=changes):
                self.assertIn(reason, direct.host_key_problem(host_key(**changes)))
        self.assertIn('not recorded', direct.host_key_problem(None))

    def test_paths_lock_and_original_vm_must_be_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = ready_config(Path(temp))
            self.assertEqual(direct.blockers(cfg, 'check'), [])
            for change, reason in ((('direct', 'lock', cfg['builder']['lock']), 'own absolute path'),
                                   (('direct', 'operationRoot', '/tmp/elsewhere'), 'operationRoot'),
                                   (('retired', 'instanceId', None), 'retired.instanceId'),
                                   (('direct', 'sshUser', 'root; rm'), 'sshUser'),
                                   (('direct', 'route', 'any'), 'route')):
                with self.subTest(change=change):
                    broken = copy.deepcopy(cfg)
                    broken['builder'][change[0]][change[1]] = change[2]
                    self.assertTrue(any(reason in b for b in direct.blockers(broken, 'check')))


class ManualOnlyTest(Base):
    def test_every_operator_and_guest_entry_refuses_inside_a_github_actions_job(self):
        for name in direct.GITHUB_CONTEXT:
            env = dict(self.env, **{name: 'synthetic'})
            fake = Fake()
            with self.subTest(name=name):
                with self.assertRaisesRegex(yogi.Refused, 'manual operator only'):
                    direct.plan(self.cfg, 'check', self.source, 'op', self.root/f'op-{name}', repo=self.repo, env=env)
                for call in (lambda: direct.start(self.cfg, self.root/'none', run=fake, env=env),
                             lambda: direct.launch(self.cfg, self.root/'none', self.identity, run=fake, env=env),
                             lambda: direct.status(self.cfg, self.root/'none', self.identity, run=fake, env=env),
                             lambda: direct.cancel(self.cfg, self.root/'none', self.identity, run=fake, env=env),
                             lambda: direct.cleanup(self.cfg, self.root/'none', self.identity, run=fake, env=env),
                             lambda: direct.agent(self.root/'none', 'a'*64, cfg=self.cfg, env=env)):
                    with self.assertRaisesRegex(yogi.Refused, 'manual operator only'):
                        call()
                self.assertEqual(fake.log, [])
                self.assertFalse((self.root/f'op-{name}').exists())


class PlanTest(Base):
    def test_plan_binds_reviewed_source_builder_stage_unique_id_and_expiry(self):
        manifest = self.plan()
        op = self.root/'op'
        self.assertEqual(manifest['builder'], TUPLE)
        self.assertEqual(manifest['source'], self.source)
        self.assertEqual(manifest['sourceArchiveSha256'], direct.sha_file(op/'source.tar'))
        self.assertRegex(manifest['operationId'], '^[0-9a-f]{32}$')
        self.assertEqual(manifest['expiresAt'], '2026-11-07T15:00:00Z')
        self.assertIn('not a GitHub identity', manifest['operator']['note'])
        self.assertEqual(direct.load(self.cfg, op, NOW)[0], manifest)
        other = self.plan(out=self.root/'op2')
        self.assertNotEqual(other['operationId'], manifest['operationId'])
        with self.assertRaises(FileExistsError):  # one directory per operation
            self.plan()
        with tarfile.open(op/'source.tar') as tar:
            names = tar.getnames()
        self.assertIn('scripts/yogi_direct.py', names)

    def test_unreviewed_dirty_wrong_or_short_source_refuses_before_writing(self):
        (self.repo/'scripts/yogi_direct.py').write_text('# local edit\n')
        with self.assertRaisesRegex(yogi.Refused, 'uncommitted'):
            self.plan(out=self.root/'dirty')
        git(self.repo, 'checkout', '-q', '--', '.')
        (self.repo/'extra').write_text('x')
        git(self.repo, 'add', 'extra')
        git(self.repo, 'commit', '-qm', 'unreviewed')
        with self.assertRaisesRegex(yogi.Refused, 'not at --source'):
            self.plan(out=self.root/'behind')
        with self.assertRaisesRegex(yogi.Refused, 'not on origin/main'):
            self.plan(out=self.root/'unmerged', source=git(self.repo, 'rev-parse', 'HEAD'))
        with self.assertRaisesRegex(yogi.Refused, '40-hex'):
            self.plan(out=self.root/'short', source=self.source[:12])
        with self.assertRaisesRegex(yogi.Refused, 'operator'):
            self.plan(out=self.root/'anon', operator='')
        for name in ('dirty', 'behind', 'unmerged', 'short', 'anon'):
            self.assertFalse((self.root/name).exists())

    def test_compile_inputs_must_be_exactly_the_committed_artifact(self):
        archive = self.root/'inputs.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('apps/voice.apk', b'synthetic')
        digest = 'sha256:' + direct.sha_file(archive)
        record_value = {'export': {'artifact': {'digest': digest}}}
        with mock.patch.object(yogi, 'app_record', lambda cfg: record_value):
            manifest = self.plan('compile', out=self.root/'c1', inputs=archive)
            self.assertEqual(manifest['inputs']['sha256'], digest.removeprefix('sha256:'))
            record_value['export']['artifact']['digest'] = 'sha256:' + 'f'*64
            with self.assertRaisesRegex(yogi.Refused, 'does not match the committed app-inputs artifact digest'):
                self.plan('compile', out=self.root/'c2', inputs=archive)
            with self.assertRaisesRegex(yogi.Refused, 'needs --inputs'):
                self.plan('compile', out=self.root/'c3')
        with self.assertRaisesRegex(yogi.Refused, 'only compile takes --inputs'):
            self.plan('check', out=self.root/'c4', inputs=archive)
        self.assertFalse(any((self.root/n).exists() for n in ('c2', 'c3', 'c4')))

    def test_changed_files_foreign_builder_or_expired_operation_refuse(self):
        self.plan()
        op = self.root/'op'
        later = NOW + datetime.timedelta(hours=13)
        with self.assertRaisesRegex(yogi.Refused, 'expired'):
            direct.load(self.cfg, op, later)
        self.assertTrue(direct.load(self.cfg, op, later, allow_expired=True))
        manifest = json.loads((op/'manifest.json').read_text())
        manifest['builder']['instanceId'] = ORIGINAL_ID
        (op/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(yogi.Refused, 'exact replacement builder'):
            direct.load(self.cfg, op, NOW)
        self.plan(out=self.root/'op2')
        with open(self.root/'op2/source.tar', 'ab') as f:
            f.write(b'\0')
        with self.assertRaisesRegex(yogi.Refused, 'source archive changed'):
            direct.load(self.cfg, self.root/'op2', NOW)


class ControlTest(Base):
    def test_record_is_durable_before_start_and_only_a_terminated_vm_is_started(self):
        fake = Fake()
        written = []
        real_fsync = os.fsync
        with mock.patch.object(direct.os, 'fsync', lambda fd: (written.append(len(fake.log)), real_fsync(fd))):
            self.started(fake)
        self.assertEqual(fake.kinds(), ['gcloud-describe', 'gcloud-describe', 'gcloud-start', 'gcloud-describe'])
        self.assertEqual(written, [2])  # fsynced after the two describes, before the START request
        record = json.loads((self.root/'op/ownership.json').read_text())
        self.assertTrue(record['startedByThisOperation'])
        self.assertEqual(record['operation']['source'], self.source)
        with self.assertRaises(FileExistsError):  # never a second START for one operation
            direct.start(self.cfg, self.root/'op', run=Fake(), sleep=lambda s: None, clock=ticking(), env=self.env, now=NOW)

    def test_running_vm_is_never_adopted_and_original_must_be_terminated(self):
        self.plan()
        fake = Fake(states=('RUNNING',))
        with self.assertRaisesRegex(yogi.Refused, 'not adopted'):
            direct.start(self.cfg, self.root/'op', run=fake, env=self.env, now=NOW)
        self.assertNotIn('gcloud-start', fake.kinds())
        self.assertFalse(json.loads((self.root/'op/ownership.json').read_text())['startedByThisOperation'])
        self.plan(out=self.root/'op2')
        fake = Fake(original='RUNNING')
        with self.assertRaisesRegex(yogi.Refused, 'original Yogi VM is RUNNING'):
            direct.start(self.cfg, self.root/'op2', run=fake, env=self.env, now=NOW)
        self.assertFalse((self.root/'op2/ownership.json').exists())
        self.assertNotIn('gcloud-start', fake.kinds())

    def test_wrong_numeric_identity_or_uncertain_start_requests_nothing_further(self):
        self.plan()
        fake = Fake(describe_id='1')
        with self.assertRaisesRegex(yogi.Refused, 'identity mismatch'):
            direct.start(self.cfg, self.root/'op', run=fake, env=self.env, now=NOW)
        self.assertEqual(fake.kinds(), ['gcloud-describe', 'gcloud-describe'])
        self.assertFalse((self.root/'op/ownership.json').exists())
        self.plan(out=self.root/'op2')
        fake = Fake(start_rc=1)
        with self.assertRaisesRegex(yogi.Refused, 'uncertain'):
            direct.start(self.cfg, self.root/'op2', run=fake, env=self.env, now=NOW)
        self.assertEqual(fake.kinds().count('gcloud-start'), 1)
        self.assertTrue(json.loads((self.root/'op2/ownership.json').read_text())['startedByThisOperation'])


class SshTest(Base):
    def test_ssh_trusts_only_the_pin_and_forwards_nothing(self):
        session = direct.Session(self.cfg, '203.0.113.7', self.identity, run=Fake())
        self.addCleanup(session.close)
        argv = ' '.join(session.argv)
        for required in ('-F /dev/null', 'StrictHostKeyChecking=yes', 'GlobalKnownHostsFile=/dev/null',
                         f'HostKeyAlias=yogi-{yogi.INSTANCE_ID}', 'HostKeyAlgorithms=ssh-ed25519', 'UpdateHostKeys=no',
                         'ForwardAgent=no', 'ForwardX11=no', 'ClearAllForwardings=yes', 'PermitLocalCommand=no',
                         'BatchMode=yes', 'IdentitiesOnly=yes', '-l yogi-operator'):
            self.assertIn(required, argv)
        for forbidden in ('accept-new', 'StrictHostKeyChecking=no', 'SendEnv', 'SetEnv', '-A '):
            self.assertNotIn(forbidden, argv)
        self.assertEqual(session.known.read_text(), f'yogi-{yogi.INSTANCE_ID} ssh-ed25519 {KEY}\n')
        self.assertEqual(set(session.env), {'PATH', 'HOME'})
        self.cfg['builder']['direct']['route'] = 'iap'
        iap = direct.Session(self.cfg, None, self.identity, run=Fake())
        self.addCleanup(iap.close)
        proxy = [o for o in iap.argv if o.startswith('ProxyCommand=')][0]
        self.assertIn(f'start-iap-tunnel {yogi.INSTANCE} 22 --listen-on-stdin --project={yogi.PROJECT} --zone={yogi.ZONE}',
                      proxy)
        self.cfg['builder']['direct']['route'] = 'external-ip'
        with self.assertRaisesRegex(yogi.Refused, 'external address'):
            direct.Session(self.cfg, None, self.identity, run=Fake())

    def proxy_run(self, environ):
        """Run the session's ProxyCommand as ssh would (/bin/sh -c, ssh's environment, %% unescaped) against a fake
        gcloud that reports only what it was given. Synthetic paths; nothing is read."""
        fakebin = self.root/'fakebin'
        fakebin.mkdir(exist_ok=True)
        (fakebin/'gcloud').write_text('#!/bin/sh\nenv | sort > "$(dirname "$0")/gcloud.env"\nprintf "%s\\n" "$@" > '
                                      '"$(dirname "$0")/gcloud.args"\n')
        (fakebin/'gcloud').chmod(0o755)
        self.cfg['builder']['direct']['route'] = 'iap'
        session = direct.Session(self.cfg, None, self.identity, run=Fake(), gcloud='gcloud', environ=environ)
        self.addCleanup(session.close)
        proxy = [o for o in session.argv if o.startswith('ProxyCommand=')][0][len('ProxyCommand='):]
        env = dict(session.env, PATH=f'{fakebin}:{session.env["PATH"]}')
        subprocess.run(['/bin/sh', '-c', proxy.replace('%%', '%')], env=env, check=True, timeout=30)
        seen = dict(line.split('=', 1) for line in (fakebin/'gcloud.env').read_text().splitlines() if '=' in line)
        return session, seen, (fakebin/'gcloud.args').read_text().splitlines()

    def test_iap_tunnel_gets_the_operators_gcloud_config_location_and_nothing_else(self):
        operator_home = str(self.root/'synthetic operator home')
        environ = {'HOME': operator_home, 'PATH': os.environ['PATH'], 'CLOUDSDK_PYTHON': '/usr/bin/python3',
                   'GOOGLE_APPLICATION_CREDENTIALS': '/synthetic/adc.json', 'CLOUDSDK_CORE_ACCOUNT': 'someone',
                   'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE': '/synthetic/token', 'HTTPS_PROXY': 'http://proxy.invalid',
                   'SSH_AUTH_SOCK': '/synthetic/agent.sock'}
        session, seen, args = self.proxy_run(environ)
        self.assertEqual(seen['CLOUDSDK_CONFIG'], operator_home + '/.config/gcloud')
        self.assertEqual(seen['CLOUDSDK_PYTHON'], '/usr/bin/python3')
        self.assertEqual(seen['HOME'], session.temp.name)  # gcloud still runs with ssh's temporary HOME
        for name in ('GOOGLE_APPLICATION_CREDENTIALS', 'CLOUDSDK_CORE_ACCOUNT', 'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE',
                     'HTTPS_PROXY', 'SSH_AUTH_SOCK'):
            self.assertNotIn(name, seen)
        self.assertEqual(set(session.env), {'PATH', 'HOME'})  # ssh itself (and so the guest) gets nothing new
        self.assertEqual(args, ['compute', 'start-iap-tunnel', yogi.INSTANCE, '22', '--listen-on-stdin',
                                f'--project={yogi.PROJECT}', f'--zone={yogi.ZONE}', '--verbosity=warning'])
        argv = ' '.join(session.argv)
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn(f'HostKeyAlias=yogi-{yogi.INSTANCE_ID}', argv)

    def test_explicit_config_with_spaces_or_percent_reaches_gcloud_exactly(self):
        config = str(self.root/'cfg 100%done/gcloud')
        session, seen, _ = self.proxy_run({'HOME': '/synthetic/home', 'CLOUDSDK_CONFIG': config,
                                           'PATH': os.environ['PATH']})
        self.assertEqual(seen['CLOUDSDK_CONFIG'], config)
        self.assertNotIn('CLOUDSDK_PYTHON', seen)
        proxy = [o for o in session.argv if o.startswith('ProxyCommand=')][0]
        self.assertIn('100%%done', proxy)

    def test_iap_config_location_must_be_absolute_and_external_ip_route_has_no_proxy(self):
        self.cfg['builder']['direct']['route'] = 'iap'
        for environ in ({}, {'HOME': 'relative'}, {'CLOUDSDK_CONFIG': 'relative/gcloud'},
                        {'HOME': '/h', 'CLOUDSDK_CONFIG': '/a\nb'}, {'HOME': '/h', 'CLOUDSDK_PYTHON': 'python3'}):
            with self.subTest(environ=environ):
                with self.assertRaisesRegex(yogi.Refused, 'absolute path'):
                    direct.Session(self.cfg, None, self.identity, run=Fake(), environ=environ)
        self.cfg['builder']['direct']['route'] = 'external-ip'
        session = direct.Session(self.cfg, '203.0.113.7', self.identity, run=Fake(),
                                 environ={'HOME': '/h', 'CLOUDSDK_CONFIG': '/c'})
        self.addCleanup(session.close)
        self.assertFalse(any('ProxyCommand' in o or 'CLOUDSDK' in o for o in session.argv))

    def test_historical_active_but_disabled_runner_is_still_refused(self):
        # Receipt correction (Chief, 2026-11-06): the last guest observation before STOP was active/running but
        # disabled. The route requires inactive/disabled and no runner process; that shape must refuse.
        observed = {'builder': TUPLE, 'runnerProcess': True, 'active': 'active', 'enabled': 'disabled'}
        self.assertFalse(direct.quiesced(observed))
        self.assertFalse(direct.quiesced(dict(observed, runnerProcess=False)))
        self.assertTrue(direct.quiesced(dict(observed, runnerProcess=False, active='inactive')))

    def test_host_verification_precedes_any_transfer(self):
        self.started()
        for fake, reason in ((Fake(states=('RUNNING',), ssh_rc=255), 'host key did not match'),
                             (Fake(states=('RUNNING',), probe={'builder': dict(TUPLE, instanceId=ORIGINAL_ID),
                                                               'runnerProcess': False, 'active': 'inactive',
                                                               'enabled': 'disabled'}), 'not the exact replacement'),
                             (Fake(states=('RUNNING',), probe={'builder': {'error': 'URLError'}}), 'not the exact'),
                             (Fake(states=('RUNNING',), probe={'builder': TUPLE, 'runnerProcess': True,
                                                               'active': 'inactive', 'enabled': 'disabled'}), 'quiesced'),
                             (Fake(states=('RUNNING',), probe={'builder': TUPLE, 'runnerProcess': False,
                                                               'active': 'active', 'enabled': 'disabled'}), 'quiesced'),
                             (Fake(states=('RUNNING',), probe={'builder': TUPLE, 'runnerProcess': False,
                                                               'active': 'inactive', 'enabled': 'enabled'}), 'quiesced'),
                             (Fake(states=('RUNNING',), original='RUNNING'), 'original Yogi VM'),
                             (Fake(states=('STOPPING',)), 'nothing sent')):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(yogi.Refused, reason):
                    direct.launch(self.cfg, self.root/'op', self.identity, run=fake, env=self.env, now=NOW)
                self.assertNotIn('install', fake.kinds())
                self.assertTrue(all(kwargs.get('input') is None for _, _, kwargs in fake.log))
                self.assertFalse((self.root/'op/launched.json').exists())

    def test_launch_sends_once_only_for_its_own_started_vm(self):
        self.plan()
        with self.assertRaisesRegex(yogi.Refused, 'did not start'):
            direct.launch(self.cfg, self.root/'op', self.identity, run=Fake(states=('RUNNING',)), env=self.env, now=NOW)
        direct.start(self.cfg, self.root/'op', run=Fake(), sleep=lambda s: None, clock=ticking(), env=self.env, now=NOW)
        fake = Fake(states=('RUNNING',))
        result = direct.launch(self.cfg, self.root/'op', self.identity, run=fake, env=self.env, now=NOW)
        self.assertTrue(result['launched'])
        self.assertEqual([k for k in fake.kinds() if not k.startswith('gcloud')], ['probe', 'install'])
        sent = [kwargs['input'] for kind, _, kwargs in fake.log if kind == 'install'][0]
        self.assertEqual(hashlib.sha256(sent).hexdigest(), result['bundleSha256'])
        with tarfile.open(fileobj=io.BytesIO(sent)) as tar:
            self.assertEqual(sorted(tar.getnames()), ['manifest.json', 'source.tar'])
        for kind, argv, kwargs in fake.log:
            if kind.startswith('gcloud'):
                continue
            self.assertFalse(any(k.startswith(('GITHUB', 'ACTIONS')) for k in kwargs['env']))
        with self.assertRaisesRegex(yogi.Refused, 'never re-sent'):
            direct.launch(self.cfg, self.root/'op', self.identity, run=Fake(states=('RUNNING',)), env=self.env, now=NOW)

    def test_install_failure_or_lost_connection_is_never_retried(self):
        self.started()
        fake = Fake(states=('RUNNING',), install_rc=1)
        with self.assertRaisesRegex(yogi.Refused, 'refused the operation bundle'):
            direct.launch(self.cfg, self.root/'op', self.identity, run=fake, env=self.env, now=NOW)
        self.assertTrue((self.root/'op/launched.json').exists())
        with self.assertRaisesRegex(yogi.Refused, 'never re-sent'):
            direct.launch(self.cfg, self.root/'op', self.identity, run=Fake(states=('RUNNING',)), env=self.env, now=NOW)


class InstallScriptTest(Base):
    """Runs the guest install script in a local shell with a fake setsid, against a temporary operation root."""
    def install(self, manifest, data, digest=None):
        op = self.root/'op'
        manifest_sha = direct.load(self.cfg, op, NOW)[1]
        script = direct.install_script(self.cfg, manifest, manifest_sha, digest or hashlib.sha256(data).hexdigest())
        fakebin = self.root/'bin'
        fakebin.mkdir(exist_ok=True)
        (fakebin/'setsid').write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/setsid.args"\n')
        (fakebin/'setsid').chmod(0o755)
        result = subprocess.run(['bash', '-c', script], input=data, capture_output=True,
                                env={'PATH': f'{fakebin}:/usr/bin:/bin'}, timeout=60)
        for _ in range(100):  # the agent is launched in the background; give the fake setsid time to record
            if result.returncode or (fakebin/'setsid.args').exists():
                break
            time.sleep(0.05)
        return result, fakebin

    def test_verified_bundle_is_unpacked_and_the_agent_launched_from_the_reviewed_source(self):
        manifest = self.plan()
        data = direct.bundle(self.root/'op', manifest)
        result, fakebin = self.install(manifest, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        d = Path(self.cfg['builder']['direct']['operationRoot'])/manifest['operationId']
        self.assertTrue((d/'src/scripts/yogi_direct.py').is_file())
        args = (fakebin/'setsid.args').read_text()
        self.assertIn(f'{d}/src/scripts/yogi_direct.py agent --op-dir {d} --manifest-sha', args)

    def test_replayed_or_corrupt_bundle_is_refused_before_anything_runs(self):
        manifest = self.plan()
        data = direct.bundle(self.root/'op', manifest)
        self.assertEqual(self.install(manifest, data)[0].returncode, 0)
        (self.root/'bin/setsid.args').unlink()
        result, fakebin = self.install(manifest, data)  # the same operation again
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((fakebin/'setsid.args').exists())
        partial = Path(self.cfg['builder']['direct']['operationRoot'])/('0'*32)
        self.plan(out=self.root/'op-p', token=lambda: '0'*32)
        os.rename(self.root/'op', self.root/'op-first')
        os.rename(self.root/'op-p', self.root/'op')
        pending = json.loads((self.root/'op/manifest.json').read_text())
        partial.mkdir()  # an earlier, interrupted delivery of the same operation left only its directory
        result, fakebin = self.install(pending, direct.bundle(self.root/'op', pending))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((partial/'bundle.tar').exists())
        self.assertFalse((fakebin/'setsid.args').exists())
        os.rename(self.root/'op', self.root/'op-p')
        os.rename(self.root/'op-first', self.root/'op')
        self.plan(out=self.root/'op-b')
        os.rename(self.root/'op', self.root/'op-a')
        os.rename(self.root/'op-b', self.root/'op')
        other = json.loads((self.root/'op/manifest.json').read_text())
        data = direct.bundle(self.root/'op', other)
        result, fakebin = self.install(other, data[:-1] + b'x', digest=hashlib.sha256(data).hexdigest())
        self.assertNotEqual(result.returncode, 0)
        d = Path(self.cfg['builder']['direct']['operationRoot'])/other['operationId']
        self.assertFalse((d/'in').exists())
        self.assertFalse((fakebin/'setsid.args').exists())


class StatusCancelCleanupTest(Base):
    def remote(self, state, alive, **extra):
        return {'status': dict({'schema': direct.STATUS, 'operationId': self.manifest['operationId'], 'state': state,
                                'pid': 4242, 'pgid': 4242}, **extra), 'alive': alive}

    def setUp(self):
        super().setUp()
        self.manifest = self.started()

    def cleanup(self, fake, abandon=False):
        return direct.cleanup(self.cfg, self.root/'op', self.identity, abandon=abandon, run=fake,
                              sleep=lambda s: None, clock=ticking(), env=self.env, now=NOW)

    def test_status_reports_lost_and_refuses_another_operations_status(self):
        fake = Fake(states=('RUNNING',), remote=self.remote('running', False))
        self.assertEqual(direct.status(self.cfg, self.root/'op', self.identity, run=fake, env=self.env)['state'], 'lost')
        foreign = self.remote('done', False)
        foreign['status']['operationId'] = 'f'*32
        with self.assertRaisesRegex(yogi.Refused, 'not this operation'):
            direct.status(self.cfg, self.root/'op', self.identity, run=Fake(states=('RUNNING',), remote=foreign),
                          env=self.env)

    def test_cancel_signals_only_a_live_running_agent_group(self):
        fake = Fake(states=('RUNNING',), remote=self.remote('running', True))
        direct.cancel(self.cfg, self.root/'op', self.identity, run=fake, env=self.env)
        kill = [argv[-1] for kind, argv, _ in fake.log if kind == 'kill']
        self.assertEqual(kill, ['kill -TERM -- -4242'])
        for remote in (self.remote('done', False), self.remote('running', False), self.remote('running', True, pgid=1)):
            fake = Fake(states=('RUNNING',), remote=remote)
            with self.assertRaisesRegex(yogi.Refused, 'nothing to cancel'):
                direct.cancel(self.cfg, self.root/'op', self.identity, run=fake, env=self.env)
            self.assertNotIn('kill', fake.kinds())

    def test_cleanup_never_stops_under_a_running_agent_or_without_its_own_record(self):
        fake = Fake(states=('RUNNING',), remote=self.remote('running', True))
        with self.assertRaisesRegex(yogi.Refused, 'still running'):
            self.cleanup(fake)
        self.assertNotIn('gcloud-stop', fake.kinds())
        fake = Fake(states=('RUNNING',), ssh_rc=255)  # unreachable: unverifiable
        with self.assertRaisesRegex(yogi.Refused, '--abandon'):
            self.cleanup(fake)
        self.assertNotIn('gcloud-stop', fake.kinds())
        record = json.loads((self.root/'op/ownership.json').read_text())
        record['operation']['id'] = 'f'*32
        (self.root/'op/ownership.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(yogi.Refused, 'not this operation'):
            self.cleanup(Fake(states=('RUNNING',)))

    def test_cleanup_stops_after_a_terminal_agent_or_an_explicit_abandon(self):
        for remote in ('done', 'failed', 'cancelled', 'lost'):
            with self.subTest(remote=remote):
                (self.root/'op/cleanup.json').unlink(missing_ok=True)
                fake = Fake(states=('RUNNING', 'RUNNING', 'STOPPING', 'TERMINATED'),
                            remote=self.remote('running' if remote == 'lost' else remote, False))
                self.assertEqual(self.cleanup(fake), 'TERMINATED')
                self.assertEqual(fake.kinds().count('gcloud-stop'), 1)
                self.assertEqual(json.loads((self.root/'op/cleanup.json').read_text())['remote'], remote)
        (self.root/'op/cleanup.json').unlink()
        fake = Fake(states=('RUNNING', 'STOPPING', 'TERMINATED'), ssh_rc=255)
        self.assertEqual(self.cleanup(fake, abandon=True), 'TERMINATED')
        self.assertEqual(json.loads((self.root/'op/cleanup.json').read_text())['remote'], 'abandoned (unverified)')
        fake = Fake(states=('TERMINATED',))
        self.assertEqual(self.cleanup(fake), 'TERMINATED')
        self.assertNotIn('gcloud-stop', fake.kinds())

    def test_cleanup_of_a_vm_this_operation_did_not_start_stops_nothing(self):
        self.plan(out=self.root/'op2')
        with self.assertRaisesRegex(yogi.Refused, 'not adopted'):
            direct.start(self.cfg, self.root/'op2', run=Fake(states=('RUNNING',)), env=self.env, now=NOW)
        fake = Fake(states=('RUNNING',))
        self.assertIn('nothing to stop', direct.cleanup(self.cfg, self.root/'op2', self.identity, run=fake,
                                                        env=self.env, now=NOW))
        self.plan(out=self.root/'never-started')  # no ownership record at all
        self.assertIn('nothing to stop', direct.cleanup(self.cfg, self.root/'never-started', self.identity, run=fake,
                                                        env=self.env, now=NOW))
        self.assertEqual(fake.log, [])


class AgentTest(Base):
    def setUp(self):
        super().setUp()
        self.manifest = self.plan()
        self.op_dir = Path(self.cfg['builder']['direct']['operationRoot'])/self.manifest['operationId']
        (self.op_dir/'in').mkdir(parents=True)
        for name in ('manifest.json', 'source.tar'):
            (self.op_dir/'in'/name).write_bytes((self.root/'op'/name).read_bytes())
        self.sha = direct.load(self.cfg, self.root/'op', NOW)[1]
        self.meta = {'project/project-id': yogi.PROJECT, 'instance/zone': 'projects/1/zones/us-west2-b',
                     'instance/name': yogi.INSTANCE, 'instance/id': yogi.INSTANCE_ID}
        self.runner = {'runnerProcess': False, 'active': 'inactive', 'enabled': 'disabled'}

    def agent(self, steps, sha=None, now=lambda: NOW, **kwargs):
        return direct.agent(self.op_dir, sha or self.sha, cfg=self.cfg, env=self.env, now=now,
                            metadata=lambda key: self.meta[key], runner=lambda: dict(self.runner), steps=steps, **kwargs)

    def status(self):
        return json.loads((self.op_dir/'status.json').read_text())

    def test_steps_run_under_the_direct_lock_with_identity_rechecked_and_status_persisted(self):
        seen = []
        state = self.agent([('one', lambda: seen.append(1) or 'a'), ('two', lambda: seen.append(2) or 'b')])
        self.assertEqual(state['state'], 'done')
        self.assertEqual([s['result'] for s in self.status()['steps']], ['a', 'b'])
        self.assertEqual(seen, [1, 2])
        with self.assertRaisesRegex(yogi.Refused, 'never re-run'):
            self.agent([('one', lambda: 'a')])

    def test_wrong_manifest_host_or_expired_operation_refuses_before_any_step(self):
        ran = []
        steps = [('one', lambda: ran.append(1))]
        with self.assertRaisesRegex(yogi.Refused, 'differs from the one sent'):
            self.agent(steps, sha='0'*64)
        self.assertEqual(self.status()['state'], 'refused')
        (self.op_dir/'status.json').unlink()
        with self.assertRaisesRegex(yogi.Refused, 'expired'):
            self.agent(steps, now=lambda: NOW + datetime.timedelta(hours=13))
        (self.op_dir/'status.json').unlink()
        self.meta['instance/id'] = ORIGINAL_ID
        state = self.agent(steps)
        self.assertEqual((state['state'], ran), ('failed', []))
        self.assertIn('exact replacement tuple', state['error'])

    def test_runner_appearing_mid_operation_stops_before_the_next_step(self):
        def first():
            self.runner['active'] = 'active'
            return 'first'
        ran = []
        state = self.agent([('one', first), ('two', lambda: ran.append(2))])
        self.assertEqual(state['state'], 'failed')
        self.assertIn('not quiesced', state['error'])
        self.assertEqual(ran, [])

    def test_a_second_concurrent_operation_is_refused_by_the_direct_lock(self):
        import fcntl
        lock = os.open(self.cfg['builder']['direct']['lock'], os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with self.assertRaisesRegex(yogi.Refused, 'direct lock'):
            self.agent([('one', lambda: 1)])
        self.assertEqual(self.status()['state'], 'refused')

    def test_an_unexpected_step_error_is_recorded_failed_not_left_running(self):
        def step():
            raise TypeError('synthetic')
        state = self.agent([('one', step), ('two', lambda: 2)])
        self.assertEqual((state['state'], state['error']), ('failed', 'TypeError'))
        self.assertEqual(self.status()['state'], 'failed')

    def test_sigterm_records_cancelled(self):
        import signal

        def step():
            os.kill(os.getpid(), signal.SIGTERM)
            return 'unreached'
        state = self.agent([('one', step), ('two', lambda: 2)])
        self.assertEqual(state['state'], 'cancelled')
        self.assertEqual(self.status()['steps'], [])

    def test_check_stage_runs_the_existing_readiness_with_the_direct_identity(self):
        calls = {}
        real = yogi.readiness

        def fake_readiness(cfg, out, **kwargs):
            calls['identity'] = kwargs['identity'](cfg)
            return {'passed': True}
        with mock.patch.object(yogi, 'readiness', fake_readiness):
            state = self.agent(None)
        self.assertEqual(state['state'], 'done')
        self.assertEqual([c[0] for c in calls['identity']],
                         ['host identity is the exact replacement tuple',
                          'direct operation: runner quiesced (no runner process; unit inactive and disabled)'])
        self.assertIs(yogi.readiness, real)


class DirectReadinessTest(unittest.TestCase):
    def test_readiness_with_a_direct_identity_never_records_a_runner_name_and_refuses_first(self):
        inspected = []

        def identity(cfg):
            raise yogi.Refused('direct operation refused: host identity is not the exact replacement tuple')
        with tempfile.TemporaryDirectory() as temp:
            cfg = ready_config(Path(temp))
            with self.assertRaisesRegex(yogi.Refused, 'not the exact replacement tuple'):
                yogi.readiness(cfg, Path(temp)/'out', identity=identity, which=lambda t: inspected.append(t),
                               free=lambda p: inspected.append(p))
            self.assertEqual(inspected, [])
            self.assertFalse((Path(temp)/'out').exists())

    def test_allocation_records_the_operation_not_a_run(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = ready_config(Path(temp))
            Path(cfg['builder']['attempts']).mkdir(parents=True)
            record = yogi.allocate(cfg, None, None, 'a'*40, now=NOW, operation='b'*32)
            self.assertEqual((record['runId'], record['runAttempt'], record['directOperation']), (None, None, 'b'*32))
            self.assertNotIn('directOperation', yogi.allocate(cfg, '5', '1', 'a'*40, now=NOW))


class UnzipTest(unittest.TestCase):
    def test_unsafe_entries_refuse(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for index, (name, attrs) in enumerate((('../escape', None), ('/abs', None), ('link', 0o120777 << 16))):
                with self.subTest(name=name):
                    archive = temp/f'{index}.zip'
                    with zipfile.ZipFile(archive, 'w') as z:
                        info = zipfile.ZipInfo(name)
                        if attrs:
                            info.external_attr = attrs
                        z.writestr(info, b'x')
                    with self.assertRaisesRegex(yogi.Refused, 'unsafe entry'):
                        direct.safe_unzip(archive, temp/f'out-{index}')
            good = temp/'good.zip'
            with zipfile.ZipFile(good, 'w') as z:
                z.writestr('apps/a.apk', b'x')
            self.assertTrue((direct.safe_unzip(good, temp/'good')/'apps/a.apk').is_file())


from test_yogi_possession import PossessionTest  # noqa: E402,F401
from test_yogi_possess_install import PossessInstallTest, PossessSshdTest  # noqa: E402,F401


if __name__ == '__main__':
    unittest.main()
