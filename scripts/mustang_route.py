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
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import mustang_epoch
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


ACCEPTED_APP_EXPORT = {
    'path': 'os/app-inputs/frankel-2026110901.json',
    'run': '42561803703',
    'artifact': 13649846651,
    'digest': 'sha256:e14607d7385952a19c9b78977d0c3dd6f84f1e8d76cc31bb7ae35bf09c4bc3d0',
    'producerRun': '42559170713',
    'sourceSha': '9882edf0a68b640c24e6c0d0de119e663c6ac573',
    'recordMerge': '303f69823b0ca0f92a61fa3b95f9a2e8364da812',
    'archiveBytes': 1865637,
    'sdk': 37,
    'signerSha256': '650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb',
    'targetSdk': 35,
    'apps': {'dev.pixel.backgroundcontrol': 41, 'dev.pixel.calculator.agent': 6, 'dev.pixel.voice': 369},
}


def git_local(root, args):
    """Read local objects only. protocol.allow=never stops a partial clone from fetching."""
    return subprocess.run(
        ['git', '-C', str(root), '-c', 'protocol.allow=never', *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def require_local_commit(root, rev):
    if not re.fullmatch(r'[0-9a-f]{40}', str(rev)):
        raise ValueError('app source revision differs')
    if git_local(root, ['cat-file', '-e', rev + '^{commit}']).returncode != 0:
        raise ValueError('app source history is unavailable')


def git_name_diff(root, before, after, paths):
    """Name-only diff of two local commits. A missing object is never an empty diff."""
    require_local_commit(root, before)
    require_local_commit(root, after)
    for path in paths:
        if not isinstance(path, str) or path.startswith('/') or '..' in Path(path).parts:
            raise ValueError('app source path differs')
    diff = git_local(root, ['diff', '--name-only', before, after, '--', *paths])
    if diff.returncode != 0:
        raise ValueError('app source history is unavailable')
    return diff.stdout


def app_source_drift(record, head, root=ROOT, diff=None):
    """Names of allocated apps whose sources differ between the export and head."""
    differ = diff or (lambda before, after, paths: git_name_diff(root, before, after, paths))
    packages = load(Path(root)/'os/latest-beta/allocation.json')['packages']
    return [package['packageName'] for package in packages
            if differ(record['producer']['sourceSha'], head, package['sources'])]


def bound_app_freshness(record, root=ROOT):
    """Stale packages, or None when the producer commit is not in this checkout."""
    head = git_local(root, ['rev-parse', 'HEAD'])
    if head.returncode != 0 or not re.fullmatch(r'[0-9a-f]{40}', head.stdout.strip()):
        return None
    try:
        return app_source_drift(record, head.stdout.strip(), root)
    except ValueError as error:
        if str(error) != 'app source history is unavailable':
            raise
        return None


ACCEPTED_SOURCE_SYNC = {
    'path': 'os/build-only/mustang-source-sync-42409634329.json',
    'schema': 'roseos.mustang-source-sync-evidence.v1',
    'receiptSchema': 'roseos.mustang-source-sync-run.v1',
    'receiptSha256': '5e4012c279d5a7be5bed6810a9e490dffd087bc0ebd9efaab5c3230a5013b52c',
    'recipeSha256': '0af7e942f7baf3ec0594d01b6b75bfdd40f5e6514f099a6ec8564816b2e3b28c',
    'workflowRunId': '42409634329',
    'runAttempt': '1',
    'headSha': 'c89d530cda33354ec593c1fb1338aa50e5dc7484',
    'headBranch': 'main',
    'event': 'workflow_dispatch',
    'workflow': '.github/workflows/mustang-ota-dedicated.yml',
    'sourceJobId': 128515080622,
    'roseRevision': '2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8',
    'manifestSha256': '32d30e70ee59d373a17fe8090bdded898afa80651f27c331caca4578b3008888',
    'projectCount': 1057,
    'instanceId': '5585425566393384049',
    'diskUuid': '4183bff8-6be4-46bb-a641-2193d19813ee',
    'sourceRoot': '/mnt/disks/mustang-data/roseos/source',
}


def accepted_source_sync(record, path, root=ROOT):
    """The source-only stdout receipt still matches the current recipe. It admits no build."""
    expected = ACCEPTED_SOURCE_SYNC
    if path != expected['path'] or not isinstance(record, dict):
        return False
    if record.get('schema') != expected['schema'] or record.get('pristine') is not False \
            or record.get('buildAuthorized') is not False or record.get('toolsAttested') is not False \
            or record.get('signingAttested') is not False or record.get('runtimeProvisioned') is not False \
            or record.get('freshnessForever') is not False or record.get('receiptSha256') != expected['receiptSha256']:
        return False
    receipt = record.get('receipt')
    if not isinstance(receipt, dict):
        return False
    receipt_sha = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    if receipt_sha != expected['receiptSha256']:
        return False
    observed = receipt.get('observed') or {}
    provenance = record.get('provenance') or {}
    jobs = provenance.get('jobs') or {}
    source_job = jobs.get('source') or {}
    if receipt.get('schema') != expected['receiptSchema'] or receipt.get('admitted') != 'source-sync-only' \
            or receipt.get('executed') is not True or receipt.get('compileAuthorized') is not False \
            or receipt.get('signAuthorized') is not False or receipt.get('runtimeBound') is not False \
            or receipt.get('authenticatedStop') is not False or receipt.get('recipeSha256') != expected['recipeSha256'] \
            or receipt.get('runnerCrashStopSeconds') != mustang_lane.CONFIRMED_STOP['seconds'] \
            or (receipt.get('historical') or {}).get('oidcExercised') is not False:
        return False
    if observed.get('instance') != expected['instanceId'] or observed.get('disk') != expected['diskUuid'] \
            or observed.get('pin-head') != expected['roseRevision'] or observed.get('manifest-head') != expected['roseRevision'] \
            or observed.get('pin-hash') != expected['manifestSha256'] or observed.get('manifest-hash') != expected['manifestSha256'] \
            or observed.get('resolvedSha256') != expected['manifestSha256'] or observed.get('projectCount') != expected['projectCount']:
        return False
    if provenance.get('workflowRunId') != expected['workflowRunId'] or provenance.get('runAttempt') != expected['runAttempt'] \
            or provenance.get('headSha') != expected['headSha'] or provenance.get('headBranch') != expected['headBranch'] \
            or provenance.get('event') != expected['event'] or provenance.get('workflow') != expected['workflow'] \
            or source_job.get('id') != expected['sourceJobId'] or source_job.get('conclusion') != 'success' \
            or (jobs.get('builder') or {}).get('conclusion') != 'skipped' \
            or (jobs.get('signer') or {}).get('conclusion') != 'skipped' \
            or (jobs.get('publisher') or {}).get('conclusion') != 'skipped':
        return False
    import mustang_source_sync as sync
    recipe = sync.load(root)
    if sync.recipe_sha256(root) != expected['recipeSha256'] or recipe.get('roseRevision') != expected['roseRevision'] \
            or recipe.get('manifestSha256') != expected['manifestSha256'] or recipe.get('sourceRoot') != expected['sourceRoot'] \
            or recipe.get('executed') is not False or recipe.get('compileAuthorized') is not False \
            or (recipe.get('checkoutHost') or {}).get('instanceId') != expected['instanceId'] \
            or (recipe.get('checkoutHost') or {}).get('diskUuid') != expected['diskUuid']:
        return False
    if expected['instanceId'] != mustang_lane.CONFIRMED_INSTANCE_ID or expected['diskUuid'] != mustang_lane.CONFIRMED_UUID:
        return False
    projects = list(ET.parse(Path(root)/'os/pinned-manifest.xml').getroot().iter('project'))
    if len(projects) != expected['projectCount']:
        return False
    shown = git_local(root, ['show', expected['headSha'] + ':os/build-only/mustang-source-sync-recipe-20261107.json'])
    if shown.returncode != 0 or hashlib.sha256(shown.stdout.encode()).hexdigest() != expected['recipeSha256']:
        return False
    return True


def accepted_app_export(record, path):
    artifact = (record.get('export') or {}).get('artifact') or {}
    apps = {row.get('package'): row.get('versionCode') for row in record.get('apps') or []}
    producer = record.get('producer') or {}
    return path == ACCEPTED_APP_EXPORT['path'] and record.get('schema') == 'roseos.app-inputs-record.v1' \
        and (record.get('export') or {}).get('run') == ACCEPTED_APP_EXPORT['run'] \
        and (record.get('export') or {}).get('attempt') == '1' \
        and artifact.get('id') == ACCEPTED_APP_EXPORT['artifact'] \
        and artifact.get('digest') == ACCEPTED_APP_EXPORT['digest'] \
        and record.get('sdk') == ACCEPTED_APP_EXPORT['sdk'] \
        and record.get('signerSha256') == ACCEPTED_APP_EXPORT['signerSha256'] \
        and apps == ACCEPTED_APP_EXPORT['apps'] \
        and producer.get('sourceSha') == ACCEPTED_APP_EXPORT['sourceSha'] \
        and (producer.get('run') or {}).get('id') == ACCEPTED_APP_EXPORT['producerRun']


def current_runtime_blockers(root, runtime):
    """Current full-route contracts; historical empty-host snapshots stay historical."""
    items=[]
    def check(code, detail, function):
        try:function()
        except (ValueError,OSError,KeyError,TypeError):items.append(dict(code=code,detail=detail))
    lane=load(root/'os/build-only/mustang-lane.json')
    if runtime.get('status')!='bound':items.append(dict(code='runtime-unbound',detail='Final runtime admission remains unbound.'))
    import mustang_bootstrap_collector, mustang_clock, mustang_ephemeral, mustang_ota, mustang_hosted_sign
    check('source-contract-unbound','Fresh native source/copy verification contract is invalid.',
        lambda:mustang_bootstrap_collector.source_contract(lane,runtime,load(root/runtime['sourceEvidence']),root=root))
    check('runner-route-unbound','Dedicated group5 one-job JIT route differs.',lambda:mustang_ephemeral.require_group5(runtime))
    check('allocation-unresolved','UTC-hour legacy-exclusion or local initialization policy differs.',lambda:mustang_clock.policy(lane,runtime,root))
    if runtime.get('jobs')!=24:items.append(dict(code='jobs-unbound',detail='Selected jobs differ from completed native qualification.'))
    if any(not isinstance(runtime.get('tools',{}).get(k),str) for k in ('aapt','apksigner','node')):
        items.append(dict(code='tools-unbound',detail='Native tool paths are not selected.'))
    network=load(root/runtime['networkEvidence']) if runtime.get('networkEvidence') else {}
    if network.get('builder')!=lane['vm'] or network.get('githubEgressVerified') is not True:
        items.append(dict(code='network-runner-egress-unverified',detail='Same-builder runner egress evidence is not bound.'))
    app_ref=runtime.get('appInputsRecord');record=load(root/app_ref) if app_ref else {}
    if not accepted_app_export(record,app_ref):items.append(dict(code='app-inputs-unadmitted',detail='Immutable fresh common export is not selected.'))
    else:
        drift=bound_app_freshness(record,root)
        if drift is None:items.append(dict(code='app-inputs-history-unavailable',detail='Producer source history unavailable.'))
        elif drift:items.append(dict(code='app-inputs-stale',detail='Common app sources changed after the verified producer.'))
    check('baseline-contract-unbound','Candidate continuity contract is invalid; no installed-device compatibility is inferred.',
        lambda:mustang_ota.candidate_continuity(load(root/runtime['candidateContinuity'])))
    signing=load(root/runtime['signingRouteEvidence']) if runtime.get('signingRouteEvidence') else {}
    if signing.get('builder')!=lane['vm'] or signing.get('approved') is not True:
        items.append(dict(code='signing-route-unadmitted',detail='Existing dedicated signing identity route is not bound.'))
    check('signer-capacity-unadmitted','Signer profile and budgets are selected; delivery admission awaits repository/exact-workflow scheduling confirmation.',
        lambda:mustang_hosted_sign.require_delivery_capacity(root))
    if runtime.get('publication',{}).get('enabled') is not True or not runtime['publication'].get('authorityRecord'):
        items.append(dict(code='publication-disabled',detail='Public publication remains default-off until its current dedicated authority is reconciled.'))
    return items


def blockers(root=ROOT):
    """Every fact that still blocks a build or a public release. None of these are guesses to fill in."""
    root = Path(root)
    runtime = load(root/'os/build-only/mustang-runtime.json')
    if runtime.get('sourceEvidence')=='os/build-only/mustang-full-run-source.json':
        return current_runtime_blockers(root,runtime)
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
    sync_path = runtime.get('sourceEvidence')
    sync_record = None
    sync_ok = False
    if sync_path is not None:
        try:
            sync_record = load(root/sync_path)
        except (OSError, json.JSONDecodeError, ValueError):
            sync_record = None
        sync_ok = accepted_source_sync(sync_record, sync_path, root)
    if sync_path == ACCEPTED_SOURCE_SYNC['path'] and not sync_ok:
        items.append({'code': 'source-sync-unverified',
                      'detail': 'The bound source evidence is not the source-only receipt for run 42409634329.'})
    copy_time = ('The old /home/pixelbuild/grapheneos-2026100600 snapshot has resolvedManifest null. '
                 'Selection 34cdc3a98e8cfc614a88c5a40d002c51e87404aaec8a4f2962cb46e824c4f6e4 is copy-time only and is not a source pin.')
    if sync_ok:
        items.append({'code': 'source-unprovisioned',
                      'detail': 'Source-only run 42409634329 attempt 1 on main c89d530cda33354ec593c1fb1338aa50e5dc7484 is bound. '
                                'Rose 2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8, manifest pin and resolved sha256 '
                                '32d30e70ee59d373a17fe8090bdded898afa80651f27c331caca4578b3008888, 1057 projects, tree '
                                '/mnt/disks/mustang-data/roseos/source, VM 5585425566393384049, disk '
                                '4183bff8-6be4-46bb-a641-2193d19813ee. The 2026-11-06 infra readback still has '
                                'sourceInstalled false and roseosDirectory empty; this receipt does not refresh that readback. '
                                'It does not attest tools, a pristine bootstrap, signing capacity, or build admission. '
                                + copy_time})
    elif sync_path is None or evidence['observed'].get('sourceInstalled') is not True:
        items.append({'code': 'source-unprovisioned',
                      'detail': 'roseos/ on the data disk is empty. Pinned source has not been synced and source evidence is unset. '
                                + copy_time})
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
    if state.get('unknown') is not False or not isinstance(state.get('consumedBuilds'), list) \
            or state.get('allocatedBuild') is not None or state.get('authoritativeLedger') is not False:
        items.append({'code': 'allocation-unresolved',
                      'detail': 'Run 41884368820 has no artifacts, a null compile-step conclusion, and allocatedBuild null. '
                                'The former owner has no authoritative ledger. Do not guess whether it consumed a number and do not inspect Frankel. '
                                'The last confirmed public Mustang allocation is 2026110401. '
                                'No replacement number is selected, and 9999123199 is only the old date allocator\'s maximum output, not the next build.'})
    app_record = runtime.get('appInputsRecord')
    if app_record is None:
        items.append({'code': 'app-inputs-unadmitted',
                      'detail': 'No immutable signed-app export is bound.'})
    else:
        bound = load(root/app_record)
        if not accepted_app_export(bound, app_record):
            items.append({'code': 'app-inputs-unadmitted',
                          'detail': 'The bound app export is not the accepted frankel-2026110704 artifact.'})
        else:
            drift = bound_app_freshness(bound, root)
            if drift is None:
                items.append({'code': 'app-inputs-history-unavailable',
                              'detail': 'The accepted app export producer commit is not a local commit in this checkout. '
                                        'Missing history is not an unchanged app source, and this check does not fetch it.'})
            elif drift:
                items.append({'code': 'app-inputs-stale',
                              'detail': 'The bound app export omits current app source changes.'})
    if runtime.get('baselineTargetFiles') is None and runtime.get('baselineFactoryEvidence') is None:
        candidate = load(root/'os/build-only/mustang-candidate-archive-41738359336.json')
        inventory = candidate.get('inventory') or {}
        if candidate.get('admitted') is not False or candidate.get('installed') is True \
                or inventory.get('signedTargetFilesZip') is not False \
                or inventory.get('innerInstallerIsBaselineTargetFiles') is not False \
                or candidate.get('sizeBytes') == baseline['sizeBytes']:
            raise ValueError('candidate archive observation must stay distinct and unadmitted')
        observed = load(root/'os/mustang-ota/41761758623/baseline.json')
        current = current_continuity(root, observed['updaterApkSha256'], observed['trustedOtaCertificateSha256'][0])
        items.append({'code': 'baseline-not-on-disk',
                      'detail': 'Baseline build 2026110302 target-files receipt '
                                + baseline['sha256'] + ' is ' + str(baseline['sizeBytes'])
                                + ' bytes. That archive is not on this disk and is not admitted. '
                                'Its former Frankel path was not inspected and its existence is unproved. '
                                'Actions artifact ' + candidate['artifactId']
                                + ' is a preserved candidate of ' + str(candidate['sizeBytes'])
                                + ' bytes with no signed-target_files.zip. The inner mustang-install-2026110302.zip is not that receipt. '
                                'The historical installer inventory was recorded before lpunpack. '
                                'Current continuity ' + current['record'] + ' matches Updater.apk '
                                + current['updaterSha256'] + ' and v1 certificate ' + current['certificateDerSha256']
                                + '. The APK signer was not remeasured. Runtime stays unbound. '
                                'This is not a current phone observation and does not admit the old target-files.'})
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
    expressions = re.findall(r'\$\{\{(.*?)\}\}', body, re.DOTALL)
    if any(re.search(r'(?<![\w.])(?:inputs\.(?!(?:publish|dry_run|source_only|hostdeps_only|lock_admin_only|factory_only|vendor_only|otatools_only)\b)|github\.event)', expression)
           for expression in expressions):
        problems.append('untrusted expression reaches the workflow')
    if re.search(r'(?m)^[ ]+run:.*inputs\.(?:publish|dry_run|source_only|hostdeps_only|lock_admin_only|factory_only|vendor_only|otatools_only)', body):
        problems.append('workflow input is interpolated into a shell script')
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
        'numberStrategy': number_strategy(root),
        'sourceRequirement': load(root/'os/build-only/mustang-source-requirement-20261107.json'),
        'sourcePin': source_pin_facts(root),
        'sourceSync': {'bound': True, 'buildAuthorized': False, 'pristine': False,
                       'path': ACCEPTED_SOURCE_SYNC['path'], 'workflowRunId': ACCEPTED_SOURCE_SYNC['workflowRunId'],
                       'projectCount': ACCEPTED_SOURCE_SYNC['projectCount']}
        if accepted_source_sync(load(root/ACCEPTED_SOURCE_SYNC['path']), ACCEPTED_SOURCE_SYNC['path'], root)
        else {'bound': False, 'buildAuthorized': False},
        'otaContinuity': ota_continuity(root),
        'controllerWif': controller_wif_prerequisites(),
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


COPY_TIME_SELECTION = '34cdc3a98e8cfc614a88c5a40d002c51e87404aaec8a4f2962cb46e824c4f6e4'
FRANKEL_SOURCE_BASE = '/home/pixelbuild/grapheneos-2026100600'


def reject_copy_time_source(source):
    """The old Frankel snapshot is not a pin for the dedicated builder."""
    if not isinstance(source, dict):
        raise ValueError('dedicated source pin is missing')
    rendered = json.dumps(source, sort_keys=True)
    if COPY_TIME_SELECTION in rendered or FRANKEL_SOURCE_BASE in rendered \
            or str(source.get('sourceRoot', '')).startswith('/home/pixelbuild') \
            or str(source.get('base', '')).startswith('/home/pixelbuild') \
            or (source.get('resolvedManifest') is None and source.get('selectionSha256')):
        raise ValueError('copy-time Frankel snapshot is not a dedicated source pin')


def number_strategy(root=ROOT):
    """Record the lost legacy gap. Do not select, backfill, or reserve a number."""
    state = load(Path(root)/'os/build-only/mustang-allocation-state-20261107.json')
    policy = load(Path(root)/'os/build-only/mustang-numbering.json')
    baseline = load(Path(root)/'os/ota/mustang.json')['baseline']
    if state.get('failedRun') != '41884368820' or state.get('allocatedBuild') is not None \
            or state.get('unknown') is not True or state.get('consumedBuilds') is not None \
            or state.get('authoritativeLedger') is not False             or state.get('guessSpentAllocation') is not False \
            or state.get('recovery') != 'retired-legacy-epoch':
        raise ValueError('failed-run allocation was guessed')
    if policy.get('status') != 'unbound' or policy.get('legacyAllocationKnown') is not False \
            or policy.get('epoch') is not None or policy.get('authority') is not None \
            or policy.get('legacyCeiling') != '9999123199':
        raise ValueError('legacy allocation stays unknown')
    runtime=load(Path(root)/'os/build-only/mustang-runtime.json')
    if runtime.get('clockNumbering') is not None:
        import mustang_clock
        configured=mustang_clock.policy(load(Path(root)/mustang_lane.CONFIG),runtime,root)
        return dict(selectsBuildNumber=False,selectedMode='UTC-hour00-local',format=configured['format'],
            reserved=False,authority=None,configuredAuthority='dedicated persistent disk/canonical lock',
            issued=False,allocatedBuild=None,authoritativeLedger=False,guessSpentAllocation=False,backfill=False,
            inspectFrankel=False,failedRun='41884368820',legacyAllocationKnown=False,
            legacySuffixExcluded='01..99',initializationRunNumber=configured['initializationRunNumber'],
            legacyCeiling='9999123199',legacyCeilingMeaning='Captured normal legacy generator bound; not the next BUILD_NUMBER. The selected clock route excludes its01..99 suffixes.',
            basis='Source configuration only: no on-disk allocation observed. Missing initialized state cannot be reset automatically.')
    chosen = mustang_epoch.describe()
    return {
        'selectsBuildNumber': False,
        'selectedMode': chosen['selectedMode'],
        'epoch': chosen['epoch'],
        'proposalStart': chosen['proposalStart'],
        'reserved': False,
        'authority': None,
        'issued': False,
        'allocatedBuild': None,
        'authoritativeLedger': False,
        'guessSpentAllocation': False,
        'backfill': False,
        'inspectFrankel': False,
        'failedRun': '41884368820',
        'lastConfirmedPublicBuild': state.get('lastConfirmedPublicBuild'),
        'legacyCeiling': '9999123199',
        'legacyCeilingMeaning': 'Maximum output of release-allocate.next_build: UTC YYYYMMDD plus sequence 01..99. '
                                'Python cannot format a year above 9999. This is not a build date, not ro.build.date.utc, '
                                'and not the next BUILD_NUMBER.',
        'dateAllocatorConstraint': 'The retired range is not a valid UTC date, so it is not passed to next_build.',
        'incremental': {'field': 'ro.build.version.incremental', 'width': 10,
                        'installedBaseline': baseline['build'],
                        'mustExceedInstalled': True},
        'timestamp': {'field': 'ro.build.date.utc', 'separateFromBuildNumber': True,
                      'installedBaseline': baseline['timestamp'],
                      'publishedFloor': str(mustang_epoch.PUBLISHED_TIMESTAMP),
                      'mustExceedInstalled': True},
        'range': {'start': chosen['rangeStart'], 'end': chosen['rangeEnd']}}


def source_pin_facts(root=ROOT):
    """The sync commit and upstream refs. This does not sync a tree or admit the pin."""
    root = Path(root)
    record = load(root/'os/build-only/mustang-source-requirement-20261107.json')
    manifest = root/'os/pinned-manifest.xml'
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    upstream = record.get('upstream') or {}
    if digest != upstream.get('defaultXmlSha256') or record.get('roseRevision') != upstream.get('roseRevision'):
        raise ValueError('pinned manifest no longer matches the recorded GrapheneOS tag')
    projects = list(ET.parse(manifest).getroot().iter('project'))
    pinned = [item for item in projects if re.fullmatch(r'[0-9a-f]{40}', item.get('revision') or '')]
    adevtool = next(item.get('revision') for item in projects if item.get('path') == 'vendor/adevtool')
    if adevtool != upstream.get('adevtool'):
        raise ValueError('pinned adevtool revision differs from the upstream record')
    device = load(root/'os/devices/pixel10.json')
    vendor = device['devices']['mustang']['vendorInputs']['factoryImage']
    factory = load(root/'os/build-only/mustang-factory-box-20261107.json')
    index_line = factory['index']['entry'][2]
    if factory.get('match') is not True or factory.get('admitted') is not False or factory.get('extracted') is not False \
            or factory.get('transferred') is not False or factory.get('sourceTreePinned') is not False \
            or factory.get('sha256') != factory.get('expectedSha256') \
            or factory['sha256'] not in index_line or factory['index']['revision'] != upstream.get('adevtool') \
            or factory['index']['sha256'] != 'a108c6dd08afe9ad5e3fe09f36ae513b2fac34c7d18a2a66d5a90ebb742933f4' \
            or factory['installerInventory'].get('apkExtracted') is not False \
            or factory['installerInventory'].get('phoneAcceptance') is not False:
        raise ValueError('factory box observation was promoted or does not match the pinned index')
    return {
        'pinned': False,
        'admitted': False,
        'syncAuthorized': False,
        'deviceSupport': 'adevtool-generated',
        'blockedOnMissingUpstreamDevice': False,
        'roseRevision': record['roseRevision'],
        'manifestPath': 'os/pinned-manifest.xml',
        'manifestSha256': digest,
        'projectCount': len(projects),
        'projectsWith40HexRevision': len(pinned),
        'projectsWithout40HexRevision': [item.get('name') for item in projects if item not in pinned],
        'mustangProjectInManifest': any('mustang' in ((item.get('name') or '') + (item.get('path') or '')) for item in projects),
        'upstream': upstream,
        'vendorFactoryFile': vendor.get('file'),
        'vendorFactoryStatus': vendor.get('status'),
        'vendorFactoryDownloaded': False,
        'vendorFactoryBodyHashed': False,
        'factoryBodyHashMatchesIndex': True,
        'factoryOnDedicatedDisk': False,
        'factoryExtracted': False,
        'factoryBox': {'sha256': factory['sha256'], 'sizeBytes': factory['sizeBytes'], 'location': factory['location']},
    }


def installer_members_match(updater_sha256, certificate_sha256, root=ROOT):
    """Compare extracted installer bytes with the committed observations. This admits nothing."""
    expected = ota_continuity(root)['updater']
    return {'matches': updater_sha256 == expected['updaterApkSha256']
            and certificate_sha256 == expected['otaCertificateSha256'],
            'labelsInstallerAsTargetFiles': False, 'phoneObserved': False}


def ota_continuity(root=ROOT):
    """Full payload and installed-trust are different requirements. Neither is satisfied here."""
    root = Path(root)
    baseline = load(root/'os/ota/mustang.json')['baseline']
    observed = load(root/'os/mustang-ota/41761758623/baseline.json')
    staged = load(root/'os/mustang-ota/41761758623/mustang-ota-receipt.json')
    installer = load(root/'os/mustang-delivery/41738359336/package.json')['output']
    identity = load(root/'os/signing-bootstrap/mustang-bootstrap-receipt.json')['publicIdentity']
    updater = observed['updaterApkSha256']
    certificate = observed['trustedOtaCertificateSha256']
    if observed.get('phoneObserved') is not False or staged.get('phoneUpdatePassed') is not False:
        raise ValueError('phone acceptance was recorded without an observation')
    if updater != staged['packagedUpdater']['updaterApkSha256'] or certificate != [identity['otaCertificateSha256']] \
            or certificate != staged['packagedUpdater']['trustedOtaCertificateSha256']:
        raise ValueError('committed updater or v1 certificate observations disagree')
    return {
        'fullOtaUsesPreviousTargetFiles': False,
        'producer': 'generate-release.sh calls ota_from_target_files on the new target-files only; OTA_ARGS is --disable_ublk and there is no --incremental_from',
        'incrementalPackageNeedsPreviousTargetFiles': True,
        'metadataRejectsPreBuildIncremental': True,
        'oldSignedTargetFilesNeededForFullPayload': False,
        'oldSignedTargetFilesNeededForInstalledTrust': False,
        'installedTrustObservationRequired': True,
        'installedTrustPath': 'candidate-installer-continuity',
        'stagingRequiresInstalledTrustObservation': True,
        'newSignedTargetFilesIsCandidateOnly': True,
        'phoneAcceptanceIncluded': False,
        'phoneUpdatePassed': False,
        'installedBuild': baseline['build'],
        'installedTimestamp': baseline['timestamp'],
        'updater': {'updaterApkSha256': updater, 'otaCertificateSha256': identity['otaCertificateSha256'],
                    'observedFrom': 'os/mustang-ota/41761758623/baseline.json',
                    'agreesWithPublishedOta': 'os/mustang-ota/41761758623/mustang-ota-receipt.json'},
        'installerAlternative': {
            'historical': True,
            'asOf': 'inventory recorded before lpunpack',
            'file': installer['file'], 'sha256': installer['sha256'], 'sizeBytes': installer['sizeBytes'],
            'isTargetFiles': False, 'phoneAcceptance': False,
            'members': ['system image /system/priv-app/Updater/Updater.apk',
                        'system image /system/etc/security/otacerts.zip'],
            'extracted': False,
            'inventory': {'safeEntries': 34, 'uncompressed': 'about 4.63GB', 'androidSparseSuper': '1..16'},
            'apkExtracted': False,
            'certificateExtracted': False,
        },
        'currentContinuity': current_continuity(root, updater, identity['otaCertificateSha256']),
    }


def current_continuity(root, updater, certificate):
    """The matching extraction. It does not admit the runtime or remeasure the APK signer."""
    root = Path(root)
    record = load(root/'os/build-only/mustang-candidate-continuity-20261107.json')
    obs = record.get('observations') or {}
    certs = obs.get('otaCerts') or []
    der = certs[0].get('derSha256') if certs and isinstance(certs[0], dict) else None
    runtime = load(root/'os/build-only/mustang-runtime.json')
    if record.get('candidateOnly') is not True or record.get('currentPhone') is not False \
            or obs.get('updaterSha256') != updater or obs.get('updaterBytes') != 5752573 \
            or der != certificate or certs[0].get('derBytes') != 1297:
        raise ValueError('current continuity receipt does not match the installed updater and v1 certificate')
    return {
        'record': 'os/build-only/mustang-candidate-continuity-20261107.json',
        'matchesInstalledUpdater': True,
        'matchesV1Certificate': True,
        'updaterBytes': 5752573,
        'updaterSha256': updater,
        'certificateDerSha256': certificate,
        'certificateDerBytes': 1297,
        'apkSignerRemeasured': False,
        'currentPhone': False,
        'phoneObserved': False,
        'admitsRuntime': False,
        'runtimeStatus': runtime.get('status'),
        'tools': {'lpunpackSha256': record['tools']['lpunpackSha256'], 'rawBytes': record['tools']['rawBytes']},
    }


def controller_wif_prerequisites():
    """The live controller binding. OIDC has not been exercised, and this does not grant anything."""
    return {
        'expandsPermissions': False,
        'neededBeforeBootstrap': False,
        'signerRequiredForDryRun': False,
        'oidcTested': False,
        'appHandoff': 'underway',
        'vmStoppedAtPacific': '2026-11-07 06:46',
        'jitPresent': False,
        'buildStarted': False,
        'githubApp': {
            'permission': 'Organization permissions: Self-hosted runners, write',
            'endpoint': 'POST /orgs/{org}/actions/runners/generate-jitconfig',
            'notIncluded': ['repository contents', 'administration', 'admin:org user token'],
            'installation': 'An organization owner installs the app on Immortal-Protocols. Not on the VM.',
            'variable': 'MUSTANG_RUNNER_APP_ID',
            'secret': 'MUSTANG_RUNNER_APP_PRIVATE_KEY',
            'status': 'handoff underway',
        },
        'controller': {
            'serviceAccount': 'mustang-controller@protocols-465723.iam.gserviceaccount.com',
            'keyless': True,
            'accountVariable': 'MUSTANG_CONTROLLER_SERVICE_ACCOUNT',
            'provider': 'projects/1072799993443/locations/global/workloadIdentityPools/mustang-actions/providers/mustang-main',
            'providerVariable': 'MUSTANG_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER',
            'workflowVariablesSet': True,
            'instance': 'roseos-mustang-builder-20261107-a',
            'project': 'protocols-465723',
            'zone': 'us-west2-a',
            'permissions': ['compute.instances.get', 'compute.instances.start', 'compute.instances.stop',
                            'compute.instances.setMetadata', 'compute.instances.getGuestAttributes'],
            'scope': 'this instance only',
            'statusPoll': 'compute.instances.get',
            'providerChecks': {
                'repositoryId': '1428996648',
                'ownerId': '223367958',
                'ref': 'refs/heads/main',
                'event': 'workflow_dispatch',
                'workflowRef': 'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
                'jobWorkflowRef': 'not used; a direct workflow has no job_workflow_ref',
            },
            'notGranted': ['a service account on the VM', 'Owner', 'signer secret access',
                           'setMetadata on any other instance', 'compute.zoneOperations', 'a project-wide compute grant'],
        },
    }


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
        '# ROSE_REVISION is 2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8. Do not repo sync from this plan.',
        '# Do not register a runner from this script. Group 5 already exists and has zero runners.',
        '# scripts/mustang-guest-bootstrap.sh enables the one-job unit and does not mint a token or select a build number.',
        '# No persistent registration is substituted.',
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
