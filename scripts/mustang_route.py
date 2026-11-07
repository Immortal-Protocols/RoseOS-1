#!/usr/bin/env python3
"""Dedicated Mustang route readiness.

The builder job has to run on the dedicated VM: it reads the guest metadata
server and the mounted data disk, then compiles there. Guard, controller,
publisher and cleanup stay on GitHub-hosted runners so STOP still runs when
that VM is down. Runner lifetime is not selected here.

This module prints bindings, blockers and reviewed commands. It does not start
a VM, register a runner, allocate a build, sign, or publish.
"""
import argparse
import json
import os
from pathlib import Path
import sys

import mustang_lane

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'os/build-only/mustang-runtime.json'
PEAK = ROOT/'os/mustang-ota/41761758623/producer/compile-completed.json'
BASELINE = ROOT/'os/mustang-delivery/41738359336/sign.json'
SIGNER = 'roseos-build-signer@protocols-465723.iam.gserviceaccount.com'
SECRETS = {'PIXEL_OS_SIGNING_KEYS_MUSTANG_V1': '1', 'PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1': '1'}
RUNNER_USER = 'mustang-runner'
EXCLUDED = ('frankel', 'blazer', 'rango')


def load(path):
    return json.loads(Path(path).read_text())


def compile_peak(root=ROOT):
    value = load(Path(root)/PEAK.relative_to(ROOT))
    peak = value.get('peakSampledBytes')
    if value.get('measure') != 'filesystem-available-drop' or type(peak) is not int or peak <= 0:
        raise ValueError('recorded Mustang compile peak is unavailable; refusing to guess a free-byte floor')
    return peak


def runner_architecture():
    return {
        'builderJob': 'self-hosted',
        'controlPlane': 'github-hosted',
        'lifetime': None,
        'label': 'roseos-mustang-builder',
        'reason': 'The builder job must read the guest instance id and the mounted data disk, then compile on that disk. '
                  'A GitHub-hosted builder cannot see either. Authenticated OS Login was not tested, project SSH is blocked, '
                  'no service account is attached, and the startup script was removed, so this route does not add an SSH executor. '
                  'Guard, controller, publisher and cleanup stay on ubuntu-24.04 so cleanup can STOP the VM after the builder disappears. '
                  'Persistent and ephemeral registration are both accepted once real registration evidence exists. '
                  'Ephemeral registration on every start needs a protected route; the removed startup script is not one.'}


def enrollment():
    lane = load(ROOT/mustang_lane.CONFIG)
    return {
        'runnerArchitecture': runner_architecture(),
        'user': RUNNER_USER,
        'refuseRoot': True,
        'workDirectory': mustang_lane.CONFIRMED_MOUNT + '/roseos/runner',
        'github': {
            'repositoryId': '1428996648',
            'repository': 'Immortal-Protocols/RoseOS-1',
            'workflow': 'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
            'label': lane['runnerLabel'],
            'groupName': None,
            'groupId': None,
            'separateFrom': list(EXCLUDED) + ['yogi'],
            'registrationTokenInGit': False},
        'iam': {
            'attachedVmServiceAccount': None,
            'controllerPermissions': ['compute.instances.get', 'compute.instances.start', 'compute.instances.stop'],
            'controllerResource': 'projects/protocols-465723/zones/us-west2-a/instances/' + lane['vm']['instance'],
            'variables': ['MUSTANG_CONTROLLER_SERVICE_ACCOUNT', 'MUSTANG_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER',
                          'MUSTANG_SIGNER_WORKLOAD_IDENTITY_PROVIDER', 'GCP_SIGNER_SERVICE_ACCOUNT'],
            'signer': SIGNER,
            'secretVersions': dict(SECRETS),
            'newKey': False,
            'oidc': {'repositoryId': '1428996648', 'ref': 'refs/heads/main',
                     'workflow': '.github/workflows/mustang-ota-dedicated.yml'}},
        'access': {
            'osLogin': 'on',
            'projectSsh': 'blocked',
            'ingress': 'IAP tcp/22 allow; deny all other ingress',
            'osLoginAuthenticated': False,
            'note': 'Installing a runner needs an authenticated IAP OS Login session. That session was not tested. '
                    'This repository does not open SSH, attach a service account, or mint a registration token.'}}


def blockers(root=ROOT):
    """Every fact that still blocks a build or a public release. None of these are guesses to fill in."""
    root = Path(root)
    runtime = load(root/'os/build-only/mustang-runtime.json')
    evidence = load(root/mustang_lane.READBACK)
    state = load(root/runtime['allocationState'])
    baseline = load(root/'os/mustang-delivery/41738359336/sign.json')['outputs']['targetFiles']
    items = []
    if runtime.get('status') != 'bound':
        items.append({'code': 'runtime-unbound',
                      'detail': 'Lane identity is bound. Runtime status is unbound, so the workflow guard refuses host start.'})
    runner = runtime.get('runner') or {}
    if not runner.get('registrationEvidence'):
        items.append({'code': 'runner-unregistered',
                      'detail': 'No runner is installed. Group, numeric group id, lifetime and registration evidence are unset. '
                                'The label roseos-mustang-builder is the required runner label and the firewall tag, not a registered runner.'})
    if runtime.get('sourceEvidence') is None or evidence['observed'].get('sourceInstalled') is not True:
        items.append({'code': 'source-unprovisioned',
                      'detail': 'roseos/ on the data disk is empty. Pinned source has not been synced and source evidence is unset.'})
    if runtime.get('jobs') is None:
        items.append({'code': 'jobs-unbound',
                      'detail': 'Make parallelism is unset. Do not copy the withdrawn c3-standard-44 proposal of 24 jobs onto this n2-highmem-32 VM.'})
    if any(runtime.get('tools', {}).get(name) is None for name in ('aapt', 'apksigner', 'node')):
        items.append({'code': 'tools-unbound',
                      'detail': 'aapt, apksigner and node paths are unset. gh and gcloud are also absent until the parent installs them.'})
    if evidence['httpChecks'].get('runnerEgressVerified') is not True or runtime.get('networkEvidence') is None:
        items.append({'code': 'network-runner-egress-unverified',
                      'detail': 'HTTP checks to GitHub, api.github.com and archive.ubuntu.com passed from the VM over ephemeral IPv4. '
                                'Runner egress is not verified, and no network evidence record is bound.'})
    if evidence['observed'].get('osLoginAuthenticated') is not True:
        items.append({'code': 'os-login-unauthenticated',
                      'detail': 'OS Login is on and project SSH is blocked. IAP returned an SSH banner only. Authenticated OS Login was not tested.'})
    if state.get('unknown') is not False or not isinstance(state.get('consumedBuilds'), list):
        items.append({'code': 'allocation-unresolved',
                      'detail': 'Run 41884368820 may have consumed a build number. The last confirmed public Mustang allocation is 2026110401. '
                                'No replacement number is selected.'})
    if runtime.get('appInputsRecord') is None:
        items.append({'code': 'app-inputs-unadmitted',
                      'detail': 'The private Frankel 2026110501 export is twelve files. Archive transfer and hash were not verified, so it is not admitted.'})
    if runtime.get('baselineTargetFiles') is None and runtime.get('baselineFactoryEvidence') is None:
        items.append({'code': 'baseline-not-on-disk',
                      'detail': 'Baseline build 2026110302 target-files receipt '
                                + baseline['sha256'] + ' is ' + str(baseline['sizeBytes'])
                                + ' bytes. That archive is not on this disk and is not admitted.'})
    if runtime.get('signingRouteEvidence') is None:
        items.append({'code': 'signing-route-unadmitted',
                      'detail': 'The existing signer ' + SIGNER + ' and Mustang v1 secret versions stay in place. '
                                'This workflow is not recorded as admitted to that signer.'})
    if runtime.get('publication', {}).get('enabled') is not True or runtime['publication'].get('authorityRecord') is None:
        items.append({'code': 'publication-disabled',
                      'detail': 'Public publication stays off. Channel metadata is not uploaded, and no authority record is invented.'})
    return items


def report(root=ROOT):
    lane = mustang_lane.validate(load(Path(root)/mustang_lane.CONFIG), root)
    found = blockers(root)
    return {
        'schema': 'roseos.mustang-route-readiness.v1',
        'identityConsistent': True,
        'buildAuthorized': False,
        'publicationAuthorized': False,
        'runtimeAuthorized': False,
        'admitted': {'baselineTargetFiles': False, 'appArchive': False, 'publication': False},
        'vm': lane['vm'],
        'diskResource': lane['diskResource'],
        'runnerArchitecture': runner_architecture(),
        'stopSafeguard': dict(mustang_lane.CONFIRMED_STOP),
        'cleanup': {'stopsOnlyOwnedVm': True, 'independentOfSigningAndInputs': True,
                    'safeguard': 'GCP maxRunDuration 14400 seconds with action STOP if cleanup does not stop the VM'},
        'minimumFreeBytes': compile_peak(root),
        'blockers': found}


def evaluate_guest(obs, lane, cfg, root=ROOT):
    """Identity, mount, free bytes and free inodes. A passing result admits no artifact."""
    if not isinstance(obs, dict) or obs.get('instanceId') != lane['vm']['instanceId']:
        raise ValueError('runner is not the admitted dedicated VM')
    if obs.get('instanceId') in {mustang_lane.FRANKEL_INSTANCE_ID, '2720796557371812468', '8812446529823929967'}:
        raise ValueError('runner is not the admitted dedicated VM')
    uuid = cfg.get('dataFilesystemUuid')
    if obs.get('filesystemUuid') != uuid:
        raise ValueError('dedicated data filesystem is not the admitted mounted disk')
    mount = cfg.get('dataMount') or obs.get('mount')
    if mount in ('/home/pixelbuild', '/srv/rose-yogi') or str(lane.get('sourceRoot', '')).startswith(mustang_lane.OTHER_STORAGE):
        raise ValueError('dedicated data mount differs')
    if cfg.get('dataMount') and (obs.get('mount') != cfg['dataMount']
                                 or not str(lane['sourceRoot']).startswith(cfg['dataMount'].rstrip('/') + '/')):
        raise ValueError('dedicated data mount differs')
    available, inodes = obs.get('availableBytes'), obs.get('availableInodes')
    if type(available) is not int or type(inodes) is not int or available <= 0 or inodes <= 0:
        raise ValueError('dedicated data filesystem has no available bytes/inodes')
    if uuid == mustang_lane.CONFIRMED_UUID:
        if available < compile_peak(root):
            raise ValueError('dedicated data filesystem free bytes are below the recorded compile peak')
        euid = obs.get('euid', os.geteuid())
        if type(euid) is not int or euid == 0:
            raise ValueError('dedicated runner must be a nonroot user')
    return {'identity': 'matched', 'availableBytes': available, 'availableInodes': inodes, 'admitted': False}


def provision_plan():
    mount = mustang_lane.CONFIRMED_MOUNT
    home = mount + '/roseos'
    user = RUNNER_USER
    instance = mustang_lane.CONFIRMED_INSTANCE_ID
    uuid = mustang_lane.CONFIRMED_UUID
    commands = [
        'set -euo pipefail',
        f'test "$(curl -fsS -H Metadata-Flavor:Google http://metadata.google.internal/computeMetadata/v1/instance/id)" = {instance}',
        f'test "$(findmnt --noheadings --output UUID --target {mount})" = {uuid}',
        f'test "$(findmnt --noheadings --output TARGET --target {mount})" = {mount}',
        'python3 scripts/mustang_route.py preflight --live',
        f'if ! id -u {user} >/dev/null 2>&1; then sudo useradd --system --create-home --home-dir {home}/runner --shell /usr/sbin/nologin {user}; fi',
        f'sudo install -d -o {user} -g {user} -m 0750 {home}/source {home}/rose-actions {home}/runner {home}/inputs',
        f'sudo -u {user} -H bash -lc \'cd {home}/source && repo init -u https://github.com/Immortal-Protocols/RoseOS-1.git -b "$ROSE_REVISION" -m os/pinned-manifest.xml && repo sync -c -j4 --fail-fast && repo manifest -r -o {home}/rose-actions/resolved-manifest.xml\'',
        f'# Runner registration, run as {user}, only after an org admin creates a group limited to repository 1428996648',
        f'# and workflow Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main.',
        f'# Pass the one-time token in the environment. Do not write it into this repository or the command line history.',
        f'# Persistent: sudo -u {user} ./config.sh --url https://github.com/Immortal-Protocols/RoseOS-1 --token "$RUNNER_TOKEN" '
        f'--name {mustang_lane.CONFIRMED_INSTANCE} --labels roseos-mustang-builder --runnergroup "$RUNNER_GROUP" --unattended --work {home}/runner/_work',
        f'# Ephemeral-on-start is not available: the startup script was removed and no protected registration route is installed.',
        f'# Do not use Frankel, Yogi, Blazer or Rango runners, groups, disks, locks or controller identities.']
    return {
        'executedByThisRepository': False,
        'selectsBuildNumber': False,
        'user': user,
        'packagesToConfirmOnUbuntu2404': ['git', 'repo', 'curl', 'ca-certificates', 'python3', 'unzip', 'zip', 'rsync',
                                          'openjdk-21-jdk', 'nodejs'],
        'packageBasis': 'Names for the parent to confirm against Ubuntu 24.04 and the pinned GrapheneOS host guide before any install. '
                        'They were not installed here. aapt and apksigner come from Android build-tools; record their absolute paths. '
                        'gh and gcloud are required before compile and are not installed here.',
        'commands': commands}


def live_observation():
    import subprocess
    import urllib.request
    lane = mustang_lane.validate(mustang_lane.configuration())
    runtime = load(RUNTIME)
    request = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/id',
                                     headers={'Metadata-Flavor': 'Google'})
    with urllib.request.urlopen(request, timeout=5) as response:
        instance = response.read(64).decode().strip()
    mount = runtime['dataMount']
    found = subprocess.run(['findmnt', '--noheadings', '--output', 'UUID,TARGET', '--target', mount],
                           capture_output=True, text=True, check=False)
    fields = found.stdout.split()
    if found.returncode or len(fields) < 2:
        raise ValueError('dedicated data filesystem is not the admitted mounted disk')
    space = os.statvfs(mount)
    frsize = space.f_frsize or space.f_bsize
    return evaluate_guest({'instanceId': instance, 'filesystemUuid': fields[0], 'mount': fields[1],
                           'availableBytes': space.f_bavail * frsize, 'availableInodes': space.f_favail,
                           'euid': os.geteuid()}, lane, runtime)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='report', choices=('report', 'enrollment', 'provision-plan', 'preflight'))
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'enrollment':
            value = enrollment()
        elif args.action == 'provision-plan':
            value = provision_plan()
        elif args.action == 'preflight':
            value = live_observation() if args.live else report()
        else:
            value = report()
        print(json.dumps(value, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Dedicated Mustang route stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__),
              file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
