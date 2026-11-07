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
import re
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
        'runnerId': None,
        'runnerCount': 0,
        'label': mustang_lane.RUNNER_GROUP_NAME,
        'groupId': mustang_lane.RUNNER_GROUP_ID,
        'ephemeralFeasibleNow': False,
        'reason': 'The builder job must read the guest instance id and the mounted data disk. '
                  'Guard, controller, publisher and cleanup stay on ubuntu-24.04. '
                  'Group 5 exists and has zero runners. A one-job ephemeral runner is the preferred lifetime because this repository is public, '
                  'but it is not feasible now: every start would need a fresh registration token, the startup script is gone, and no OS Login key remains. '
                  'This record does not register a persistent runner to fill that gap. '
                  'A later source tree belongs under /mnt/disks/mustang-data/roseos/source, outside the runner work directory, so wiping the work directory does not delete it.'}


def enrollment():
    lane = load(ROOT/mustang_lane.CONFIG)
    return {
        'runnerArchitecture': runner_architecture(),
        'user': RUNNER_USER,
        'refuseRoot': True,
        'workDirectory': mustang_lane.CONFIRMED_MOUNT + '/roseos/runner',
        'github': {
            'repositoryId': '1428996648',
            'repositoryVisibility': 'public',
            'repository': 'Immortal-Protocols/RoseOS-1',
            'workflow': mustang_lane.RUNNER_GROUP_WORKFLOW,
            'label': lane['runnerLabel'],
            'groupName': mustang_lane.RUNNER_GROUP_NAME,
            'groupId': mustang_lane.RUNNER_GROUP_ID,
            'selectedRepositoryIds': ['1428996648'],
            'restrictedToWorkflows': True,
            'selectedWorkflows': [mustang_lane.RUNNER_GROUP_WORKFLOW],
            'allowsPublicRepositories': True,
            'runnerCount': 0,
            'lifetime': None,
            'runnerId': None,
            'separateFrom': list(EXCLUDED) + ['yogi'],
            'doNotUseGroups': ['3', '4'],
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
            'osLoginAuthenticated': True,
            'osLoginKeyRemaining': False,
            'note': 'One IAP OS Login session worked with the shared grok-bots Owner identity. That temporary key was removed and no key remains. '
                    'Provisioning needs a fresh authorized session. This repository does not store a key, open SSH, attach a service account, or mint a registration token.'}}


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
    if runner.get('lifetime') is not None or runner.get('registrationEvidence') or evidence['runnerGroup'].get('runnerCount') != 0:
        items.append({'code': 'runner-identity-invented',
                      'detail': 'A runner id or lifetime was recorded without enrollment.'})
    if not runner.get('registrationEvidence'):
        items.append({'code': 'runner-unregistered',
                      'detail': 'Runner group 5, roseos-mustang-builder, allows only repository 1428996648 and '
                                + mustang_lane.RUNNER_GROUP_WORKFLOW
                                + '. allows_public_repositories is true because that repository is public. The group has zero runners. '
                                'Lifetime is unset. No registration token is stored.'})
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
    if evidence['observed'].get('osLoginKeyRemaining') is not False:
        items.append({'code': 'os-login-key-remains',
                      'detail': 'An OS Login key is still recorded. This route does not keep one.'})
    else:
        items.append({'code': 'os-login-no-standing-key',
                      'detail': 'IAP OS Login was authenticated once and the temporary key was removed. No key remains for a later session.'})
    if state.get('unknown') is not False or not isinstance(state.get('consumedBuilds'), list):
        items.append({'code': 'allocation-unresolved',
                      'detail': 'Run 41884368820 has no artifacts and a null compile-step conclusion. It may have consumed a build number. '
                                'The last confirmed public Mustang allocation is 2026110401. Recover the reservation from the former owner without inspecting Frankel. '
                                'No replacement number is selected.'})
    if runtime.get('appInputsRecord') is None:
        items.append({'code': 'app-inputs-unadmitted',
                      'detail': 'The private Frankel 2026110501 export is twelve files. Archive transfer and hash were not verified, so it is not admitted.'})
    if runtime.get('baselineTargetFiles') is None and runtime.get('baselineFactoryEvidence') is None:
        candidate = load(root/'os/build-only/mustang-candidate-archive-41738359336.json')
        if candidate.get('admitted') is not False or candidate.get('sizeBytes') == baseline['sizeBytes']:
            raise ValueError('candidate archive observation must stay distinct and unadmitted')
        items.append({'code': 'baseline-not-on-disk',
                      'detail': 'Baseline build 2026110302 target-files receipt '
                                + baseline['sha256'] + ' is ' + str(baseline['sizeBytes'])
                                + ' bytes. That archive is not on this disk and is not admitted. Actions artifact '
                                + candidate['artifactId'] + ' is a candidate archive of ' + str(candidate['sizeBytes'])
                                + ' bytes and is not that receipt. Its retrieval is held; the hold meaning is unknown.'})
    if runtime.get('signingRouteEvidence') is None:
        items.append({'code': 'signing-route-unadmitted',
                      'detail': 'The existing signer ' + SIGNER + ' and Mustang v1 secret versions stay in place. '
                                'This workflow is not recorded as admitted to that signer.'})
    if runtime.get('publication', {}).get('enabled') is not True or runtime['publication'].get('authorityRecord') is None:
        items.append({'code': 'publication-disabled',
                      'detail': 'Public publication stays off. Channel metadata is not uploaded, and no authority record is invented.'})
    return items


def workflow_trust(text):
    """Facts about the workflow file that will run on a public repository. This does not create a runner."""
    lines = [line for line in text.splitlines() if line.lstrip() and not line.lstrip().startswith('#')]
    body = '\n'.join(lines) + '\n'
    problems = []
    if not re.search(r'(?m)^  workflow_dispatch:\n', body):
        problems.append('workflow_dispatch missing')
    for kind in ('pull_request', 'pull_request_target', 'workflow_call', 'workflow_run', 'schedule'):
        if re.search(r'(?m)^  %s:\n' % kind, body):
            problems.append(kind)
    if re.search(r'\$\{\{\s*(inputs\.(?!publish\b)|github\.event)', body):
        problems.append('untrusted expression reaches the workflow')
    if 'inputs.publish' in body and re.search(r'(?m)^[ ]+run:.*inputs\.publish', body):
        problems.append('publish input is interpolated into a shell script')
    blocks = body.split('- uses: actions/checkout@')
    if len(blocks) < 2:
        problems.append('checkout missing')
    for block in blocks[1:]:
        header = block.split('- ', 1)[0]
        if 'persist-credentials: false' not in header or 'ref: ${{ github.sha }}' not in header:
            problems.append('checkout is not pinned to github.sha without persisted credentials')
        if 'github.event' in header or re.search(r'ref: \$\{{\s*inputs\.', header):
            problems.append('checkout ref comes from an input')
    return {'fileProblems': problems, 'problems': problems,
            'ephemeralFeasibleNow': False, 'lifetime': None,
            'runnerEnforcement': 'Group 5 allows only this workflow file at refs/heads/main. This file check does not stop a different workflow file from running on a GitHub-hosted runner.'}


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
        'minimumFreeBytesSource': 'os/mustang-ota/41761758623/producer/compile-completed.json peakSampledBytes',
        'minimumFreeBytesMeasure': 'filesystem-available-drop',
        'freeInodeObservation': {'free': mustang_lane.CONFIRMED_FREE_INODES, 'total': mustang_lane.CONFIRMED_TOTAL_INODES},
        'freeInodeFloor': 'positive only; the empty-disk free count is not reused after files are written',
        'workflowTrust': workflow_trust((root/'.github/workflows/mustang-ota-dedicated.yml').read_text()),
        'signingGroupImpact': {
            'modified': False,
            'workflow': '.github/workflows/os-sign.yml',
            'runnerGroupName': 'roseos-signing',
            'parentGroupId': '4',
            'impact': 'The parent read group 4 as selecting this public repository while allows_public_repositories is false. '
                      'os-sign.yml sends its sign job to runner group roseos-signing. If that is group 4, this public repository cannot use it. '
                      'Mustang dedicated signing does not use that group. This change does not edit the group or that workflow.'},
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
        total = obs.get('totalInodes')
        if type(total) is not int or total != mustang_lane.CONFIRMED_TOTAL_INODES or inodes > total:
            raise ValueError('dedicated data filesystem inode count differs')
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
        '# Do not register a runner from this script. Group 5 already exists and has zero runners.',
        '# A one-job ephemeral registration needs a fresh token at boot. That path does not exist, and no persistent registration is substituted.',
        '# Do not use runner groups 3 or 4, and do not use Frankel, Yogi, Blazer or Rango runners, disks, locks or controllers.']
    return {
        'executedByThisRepository': False,
        'selectsBuildNumber': False,
        'user': user,
        'noblePackages': {
            'main': ['build-essential', 'gcc-multilib', 'g++-multilib', 'python3', 'git', 'gnupg', 'openssh-client',
                     'diffutils', 'fontconfig', 'fonts-dejavu-core', 'hostname', 'openssl', 'rsync', 'unzip', 'zip',
                     'curl', 'ca-certificates', 'openjdk-21-jre-headless'],
            'universe': ['yarnpkg', 'gperf', 'git-lfs'],
            'multiverse': ['repo'],
            'absent': ['libncurses5', 'yarn'],
            'wrongVersion': {'nodejs': 'noble publishes 18.19.1; GrapheneOS adevtool asks for Node.js 24, so the distro nodejs package is not that dependency'}},
        'packageBasis': 'Checked against Launchpad noble amd64 publication and https://grapheneos.org/build, which lists Ubuntu 24.04 LTS as supported. '
                        'Nothing was installed. The OS tree ships its toolchain and JDK; fontconfig and fonts-dejavu-core are the documented host need for that headless JDK. '
                        'openjdk-21-jre-headless is for host apksigner, not a second OS signing identity. '
                        'noble repo is multiverse 2.36.1 and can be stale; do not curl a replacement into the image from this script. '
                        'Use the yarnpkg binary, not a package named yarn. Node 24 is not in noble. '
                        'aapt, apksigner, gh and gcloud are outside the Ubuntu archive; record their paths after the parent installs them. '
                        'gcc-multilib and g++-multilib are the noble packages for the 32-bit runtime GrapheneOS lists for Vanadium.',
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
