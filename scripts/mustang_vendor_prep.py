#!/usr/bin/env python3
"""Bounded vendor preparation using measured Mustang-only inputs. Plan by default.

This never runs Make, allocates a build, signs, logs in or stops the VM. The
existing controller/JIT/cleanup owns the lifecycle. All partial outputs remain.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import sys
import time
import urllib.error

import mustang_controller as controller
import mustang_ephemeral as ephemeral
import mustang_hostdeps as deps
import mustang_lane
import mustang_ota
import mustang_preparation_inputs as inputs
import mustang_yarn
import mustang_vendor_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]
BINDING = 'os/build-only/mustang-vendor-preparation.json'
RECEIPT = 'vendor-preparation-receipt.json'
TIMEOUT = 9000
WORK_BYTES = 128 << 30
RESERVE = 300 << 30
ENTRIES = 1_000_000
FLAGS = {'vendor_only', 'factory_only', 'lock_admin_only', 'hostdeps_only', 'source_only', 'dry_run', 'publish'}
GEN10_CONFIG_SHA256 = '92dd7f9cfdb3bdef2922892048f323407b6896f5e93f7ca8f73816488254ceca'


def binding(root=ROOT):
    record = json.loads((Path(root)/BINDING).read_text())
    if record.get('schema') != 'roseos.mustang-vendor-preparation.v1' \
            or not isinstance(record.get('hostDependenciesEvidence'), str) \
            or not isinstance(record.get('factoryInputEvidence'), str):
        raise ValueError('successful host-dependency and factory input evidence must be bound before START')
    host = inputs.evidence(record['hostDependenciesEvidence'], 'roseos.mustang-hostdeps-evidence.v1', root)
    factory = inputs.evidence(record['factoryInputEvidence'], 'roseos.mustang-factory-input-evidence.v1', root)
    if host['receipt'].get('success') is not True or type(host['receipt'].get('makeExitCode')) is not int \
            or host['receipt'].get('makeExitCode') != 0 \
            or factory['receipt'].get('success') is not True:
        raise ValueError('failed preparations cannot admit vendor generation')
    selected=record.get('bootstrapRecoveryEvidence')
    if selected is not None:
        if selected!=recovery.EVIDENCE:raise ValueError('bootstrap recovery evidence path differs')
        old=recovery.evidence(root)['receipt']
        if old['hostDependencies']['receiptSha256']!=host['receiptSha256'] \
                or old['factoryInput']['receiptSha256']!=factory['receiptSha256']:
            raise ValueError('bootstrap recovery requires the same successful prepared inputs')
    return host, factory


def guard(env, event, root=ROOT):
    lane = mustang_lane.validate(mustang_lane.configuration())
    run = controller.invocation(lane, env)
    selected = event.get('inputs') if isinstance(event, dict) else None
    disabled = lambda value: value is None or value is False or (type(value) is str and value == 'false')
    if env.get('GITHUB_REPOSITORY_ID') != '1428996648' or not isinstance(selected, dict) \
            or set(selected)-FLAGS or not (selected.get('vendor_only') is True or selected.get('vendor_only') == 'true') \
            or any(not disabled(selected.get(k)) for k in FLAGS-{'vendor_only'}):
        raise ValueError('vendor preparation requires its exclusive fresh manual scope')
    ephemeral.require_group5(json.loads((Path(root)/'os/build-only/mustang-runtime.json').read_text()))
    deps.recipe(root)
    mustang_yarn.pin(root)
    binding(root)
    return lane, run


def guest(env):
    lane, run = guard(env, deps.read_event(env))
    if env.get('GITHUB_JOB') != 'vendor' or pwd.getpwuid(os.geteuid()).pw_name != 'mustang-runner':
        raise ValueError('vendor preparation requires its unprivileged dedicated guest job')
    cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
    dedicated = mustang_ota.module('mustang_vendor_identity', 'mustang-dedicated.py')
    paths = dedicated.host_identity(lane, cfg['dataFilesystemUuid'], mount=cfg['dataMount'])
    source = Path(lane['sourceRoot'])
    for path in (source, paths['storageRoot']):
        if deps.plain_dir(path).st_dev != deps.plain_dir(paths['home']).st_dev:
            raise ValueError('vendor preparation leaves the dedicated data filesystem')
    clean = deps.child_env(paths['home']/'runner', paths['storageRoot'])
    git = ['/usr/bin/git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(ROOT)]
    deadline = time.monotonic()+60
    if deps.checked(git+['rev-parse', 'HEAD'], ROOT, clean, deadline) != run['source'] \
            or deps.checked(git+['status', '--porcelain'], ROOT, clean, deadline):
        raise ValueError('vendor checkout differs from captured clean source')
    return lane, dict(run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid']), paths


def factory_configuration(source, deadline, root=ROOT):
    adev = source/'vendor/adevtool'
    device = adev/'config/device/mustang.yml'
    if deps.readers.read_regular(str(source),str(device),65536) != (Path(root)/'os/build-only/mustang-inputs/mustang.yml').read_bytes():
        raise ValueError('adevtool Mustang device configuration differs from the pin')
    common = adev/'config/device/common/gen10pixel.yml'
    if hashlib.sha256(deps.readers.read_regular(str(source),str(common),65536)).hexdigest() != GEN10_CONFIG_SHA256:
        raise ValueError('adevtool stock build selection differs from the pinned CP3A configuration')
    index = adev/'config/build-index/build-index-main.yml'
    lines = deps.readers.read_regular(str(source),str(index),4 << 20).decode().splitlines()
    expected = json.loads((Path(root)/'os/build-only/mustang-inputs/build-index.json').read_text())['lines']
    block = [expected[str(i)] for i in range(1,5)]
    found = [i for i,line in enumerate(lines) if line == block[0]]
    if len(found) != 1 or lines[found[0]:found[0]+4] != block:
        raise ValueError('adevtool stock factory index differs from the pinned Mustang archive')
    return {name:deps.fingerprint(source,path,deadline) for name,path in
            (('device',device),('buildSelection',common),('buildIndex',index))}


def catalog(source, directory, deadline):
    """Hash the complete generated tree, including link text and file modes."""
    deps.plain_dir(directory)
    digest = hashlib.sha256();count = 0;total = 0
    def onerror(_error): raise ValueError('vendor inventory is incomplete')
    for base, dirs, files in os.walk(directory, followlinks=False, onerror=onerror):
        dirs.sort();files.sort()
        for name in sorted(dirs+files):
            count += 1
            if count > ENTRIES or time.monotonic() >= deadline:
                raise ValueError('vendor inventory bound exceeded')
            path = Path(base)/name;info = path.lstat();relative = str(path.relative_to(directory))
            if stat.S_ISREG(info.st_mode):
                row = dict(path=relative, **deps.fingerprint(source, path, deadline));total += info.st_size
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(directory):
                    raise ValueError('vendor inventory link escapes')
                row = {'path':relative,'link':target}
            elif stat.S_ISDIR(info.st_mode):
                row = {'path':relative,'directory':True,'mode':stat.S_IMODE(info.st_mode)}
            else: raise ValueError('vendor inventory special entry refused')
            if total > WORK_BYTES: raise ValueError('vendor inventory byte bound exceeded')
            digest.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n')
    return {'sha256':digest.hexdigest(),'entries':count,'fileBytes':total,'complete':True,
            'method':'sorted-tree-path-mode-link-and-file-sha256-v1'}


def monitor(source, attempt, watched, deadline, observations):
    used = sum(deps.tree_bytes(p, deadline, max_entries=ENTRIES) for p in [attempt,*watched])
    space = os.statvfs(source);free = space.f_bavail*space.f_frsize
    observations['samples'] = observations.get('samples',0)+1
    observations['peakWatchedBytes'] = max(observations.get('peakWatchedBytes',0),used)
    observations['minimumAvailableBytes'] = min(observations.get('minimumAvailableBytes',free),free)
    if used > WORK_BYTES or free < RESERVE or space.f_favail <= 0:
        raise ValueError('vendor preparation storage bound exceeded')
    if any(p.stat().st_size > deps.LOG_BYTES for p in attempt.glob('*.log')):
        raise ValueError('vendor preparation log bound exceeded')


def prepare(source, attempt, host_record, factory_record, identity, *, root=ROOT):
    started = time.monotonic();deadline = started+TIMEOUT
    spec = deps.readers.recipe(root);adev = source/'vendor/adevtool'
    product = source/'vendor/google_devices/mustang'
    unpack = source/spec['preparation']['vendor']['collisionPaths'][1]
    watched = [product,unpack,adev/'node_modules',adev/'lib']
    recovering=json.loads((Path(root)/BINDING).read_text()).get('bootstrapRecoveryEvidence') is not None
    result = {'schema':'roseos.mustang-vendor-preparation-run.v1','identity':identity,'success':False,
              'sourceRoot':str(source),'timeoutSeconds':TIMEOUT,'workBytes':WORK_BYTES,'reserveBytes':RESERVE,
              'commands':[],'spaceObservations':{},'makeStarted':False,'allocated':False,'signingStarted':False,
              'authenticatedStop':False,'partialsPreserved':True,'otaAdmitted':False,
              'boundEnforcement':'sampled-process-group-stop','detachedDescendantsContained':False,
              'maxEntriesPerWatchedTree':ENTRIES,'phase':'collision-check'}
    (attempt/'tmp').mkdir(mode=0o700);(attempt/'home').mkdir(mode=0o700)
    env = deps.child_env(attempt/'home',attempt/'tmp')
    env.update(ADEVTOOL_SKIP_DEP_BUILD='1',YARN_CACHE_FOLDER=str(attempt/'yarn-cache'),
               npm_config_cache=str(attempt/'npm-cache'),CI='true')
    check = lambda: monitor(source,attempt,watched,deadline,result['spaceObservations'])
    try:
        if any(os.path.lexists(p) for p in watched[:2]) or (not recovering and any(os.path.lexists(p) for p in watched[2:])):
            raise ValueError('vendor preparation output exists; preserve and reconcile it')
        check()
        space = os.statvfs(source)
        if space.f_bavail*space.f_frsize < RESERVE+WORK_BYTES:
            raise ValueError('vendor preparation initial reserve headroom is insufficient')
        result['phase'] = 'pinned-source-check'
        result['source'] = deps.source_check(source,env,deadline,root)
        result['phase'] = 'stock-configuration-check'
        result['factoryConfiguration'] = factory_configuration(source,deadline,root)
        storage = attempt.parent
        result['phase'] = 'host-dependency-rehash'
        result['hostDependencies'] = inputs.host_dependencies(source,storage,host_record,identity,deadline=deadline,root=root)
        result['phase'] = 'factory-input-rehash'
        result['factoryInput'] = inputs.factory_archive(storage,factory_record,identity,root=root)
        if recovering:
            result['phase']='qualify-completed-install'
            result['bootstrapRecovery']=recovery.qualify(source,storage,identity,root=root,catalog=catalog,deadline=deadline,
                host_sha=result['hostDependencies']['receiptSha256'],factory_sha=result['factoryInput']['receiptSha256'])
        env['ADEVTOOL_IMG_DOWNLOAD_DIR'] = str(Path(result['factoryInput']['path']).parent)
        node = Path(ephemeral.NODE['install'])
        result['phase'] = 'node-runtime-check'
        result['node'] = deps.fingerprint(node.parent,node,deadline)
        result['phase'] = 'pinned-public-yarn-bootstrap'
        result['yarnDistribution'] = mustang_yarn.install(attempt,deadline,root=root)
        yarn = Path(result['yarnDistribution']['path'])
        result['yarn'] = deps.fingerprint(attempt,yarn,deadline)
        version = deps.checked([str(node),'--version'],source,env,deadline)
        if version != ephemeral.NODE['version']:
            raise ValueError('vendor Node version differs from the installed pin')
        yarn_version = deps.checked([str(node),str(yarn),'--version'],source,env,deadline)
        if yarn_version != result['yarnDistribution']['version']:
            raise ValueError('vendor Yarn version differs from the pinned public CLI')
        result.update(nodeVersion=version,yarnVersion=yarn_version)
        if os.path.lexists(adev/'lib'):
            raise ValueError('compiled JavaScript must not shadow the pinned source loader')
        result['executionMode']='pinned-typescript-source-loader'
        setup=[str(node),str(yarn),*(['check','--integrity','--cwd','vendor/adevtool'] if recovering else
               ['install','--cwd','vendor/adevtool','--frozen-lockfile','--non-interactive'])]
        commands = [setup,
                    ['vendor/adevtool/bin/run','--help'],
                    ['vendor/adevtool/bin/run','generate-all','-d','mustang']]
        for number, command in enumerate(commands):
            result['phase'] = (('yarn-integrity-check' if recovering else 'yarn-install'),'source-loader-startup','vendor-generate')[number]
            outcome = {'argv':command,'processStarted':False,'exitCode':None}
            result['commands'].append(outcome)
            deps.supervise(command,source,env,attempt/f'{number}.log',deadline,check,
                           outcome=outcome,failure_message='vendor preparation child command failed')
            if number == 1 and deps.checked(['/usr/bin/git','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',
                    '-C',str(adev),'status','--porcelain','--untracked-files=no'],source,env,deadline):
                raise ValueError('adevtool tracked source changed during dependency bootstrap or source startup')
        if deps.checked(['/usr/bin/git','-C',str(adev),'rev-parse','HEAD'],source,env,deadline) != spec['pins']['vendor/adevtool']:
            raise ValueError('adevtool pin changed during vendor preparation')
        if deps.checked(['/usr/bin/git','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',
                '-C',str(adev),'status','--porcelain','--untracked-files=no'],source,env,deadline):
            raise ValueError('adevtool tracked source changed during vendor generation')
        result['adevtoolTrackedWorktreeUnchanged'] = True
        product_mk = deps.readers.read_regular(str(source),str(product/'mustang.mk'),1 << 20)
        if deps.readers.VERSION_INCLUDE not in product_mk:
            raise ValueError('generated Mustang product lacks the pinned version check include')
        result['phase'] = 'output-catalogs'
        result['vendorTree'] = catalog(source,product,deadline)
        result['nodeModules'] = catalog(source,adev/'node_modules',deadline)
        if recovering and result['nodeModules']!=result['bootstrapRecovery']['nodeModulesBefore']:
            raise ValueError('qualified installed dependencies changed during generation')
        result['adevtoolSource']=catalog(source,adev/'src',deadline)
        result['sourceLoader']={name:deps.fingerprint(source,path,deadline) for name,path in
            (('binRun',adev/'bin/run'),('packageJson',adev/'package.json'),('yarnLock',adev/'yarn.lock'))}
        result['productMakefile'] = deps.fingerprint(source,product/'mustang.mk',deadline)
        if deps.fingerprint(node.parent,node,deadline) != result['node'] \
                or deps.fingerprint(attempt,yarn,deadline) != result['yarn']:
            raise ValueError('vendor runtime tool bytes changed during preparation')
        check();result['success'] = True;result['phase'] = 'complete'
    except BaseException as error:
        result['failureType'] = type(error).__name__
        result['failureCode'] = str(error) if type(error) is ValueError else 'vendor-preparation-operation-failed'
        if isinstance(error,urllib.error.HTTPError):
            result['failureCode'] = 'pinned-public-tool-http-refused'
            result['httpStatus'] = error.code
        raise
    finally:
        result['elapsedSeconds'] = round(time.monotonic()-started,3)
        deps.receipt(attempt/RECEIPT,result)
    return result


def execute(env):
    lane, identity, paths = guest(env)
    with deps.lock(Path(lane['lockPath'])):
        attempt = paths['storageRoot']/('mustang-vendor-'+identity['run']['runId']+'-'+identity['run']['runAttempt'])
        attempt.mkdir(mode=0o750);deps.plain_dir(attempt)
        host, factory = binding()
        return prepare(Path(lane['sourceRoot']),attempt,host,factory,identity)


def report(env, destination):
    _lane,identity,paths = guest(env)
    path = paths['storageRoot']/('mustang-vendor-'+identity['run']['runId']+'-'+identity['run']['runAttempt'])/RECEIPT
    receipt = json.loads(deps.readers.read_regular(str(paths['storageRoot']),str(path),1 << 20))
    if receipt.get('schema') != 'roseos.mustang-vendor-preparation-run.v1' or receipt.get('identity') != identity:
        raise ValueError('vendor report belongs to another run or builder')
    deps.receipt(destination,receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','run','report'),nargs='?',default='plan')
    parser.add_argument('--execute',action='store_true');parser.add_argument('--out',type=Path)
    args = parser.parse_args()
    if args.action == 'plan' or (args.action == 'run' and not args.execute):
        print(json.dumps({'execute':False,'scope':'Mustang-vendor-only','binding':BINDING,
                          'timeoutSeconds':TIMEOUT,'workBytes':WORK_BYTES,'reserveBytes':RESERVE}));return 4
    if args.action == 'report':
        if args.out is None: raise ValueError('vendor receipt output is required')
        report(os.environ,args.out)
    else: execute(os.environ)
    return 0


if __name__ == '__main__':
    def cancelled(_signum,_frame): raise ValueError('vendor preparation cancelled')
    signal.signal(signal.SIGTERM,cancelled)
    try: code = main()
    except Exception as error:
        print(json.dumps({'success':False,'failureType':type(error).__name__}));code = 4
    sys.exit(code)
