#!/usr/bin/env python3
"""Mustang Pixel 10 Pro XL adapter for the existing build-stage mechanisms.

Loads a private instance of the shared Blazer engine, as that engine already does
for the output collector. Only this process gets Mustang paths and Nathan's
explicit reduced checklist. No Blazer/Rango source tree, receipt or artifact is
an input. No signing, secret, IAM, publication or phone operation exists here.
"""
import importlib.util
import functools
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import mustang_integration
import mustang_lane
import mustang_existing_lock

OTATOOLS_PATTERN = 'soong/.intermediates/build/make/tools/otatools_package/otatools-package/*/gen/otatools.zip'


def otatools_outputs(source, out_dir):
    """The pinned java_genrule_host's output, inside this allocation's OUT_DIR only."""
    out = Path(source)/out_dir
    return sorted(p for p in out.glob(OTATOOLS_PATTERN) if p.is_file() and not p.is_symlink()
                  and not any(q.is_symlink() for q in p.parents if q == out or out in q.parents))


def collect_outputs(source, out_dir):
    """Remaining output checks only: existence, size/hash and actual device/build metadata.

    No archive CRC sweep, required-partition/firmware coverage or packaged APK,
    controller-policy, signing or installer content verification is performed.
    """
    out = Path(source)/out_dir
    product = out/'target/product/mustang'
    packaging = product/'obj/PACKAGING/target_files_intermediates'
    targets = sorted(packaging.glob('mustang-target_files*.zip'))
    tools = otatools_outputs(source, out_dir)
    paths = {'vendorBootImage':product/'vendor_boot.img',
             'vendorKernelBootImage':product/'vendor_kernel_boot.img'}
    if len(tools) == 1:
        paths['otatools'] = tools[0]
    if len(targets) == 1:
        paths['targetFiles'] = targets[0]
    artifacts = {}
    for name, path in paths.items():
        if path.is_file() and not path.is_symlink():
            artifacts[name] = {'state':'collected', 'path':str(path), 'sizeBytes':path.stat().st_size,
                              'sha256':mustang_integration.sha256_file(path)}
        else:
            artifacts[name] = {'state':'missing', 'path':str(path)}
    if len(targets) != 1:
        artifacts['targetFiles'] = {'state':'missing-or-ambiguous', 'matches':len(targets)}
    if len(tools) != 1:
        artifacts['otatools'] = {'state':'missing-or-ambiguous', 'matches':len(tools), 'pattern':OTATOOLS_PATTERN}
    complete = all(row['state']=='collected' for row in artifacts.values())
    identity = {}
    if artifacts['targetFiles']['state'] == 'collected':
        try:
            with zipfile.ZipFile(targets[0]) as archive:
                info = archive.getinfo('VENDOR/build.prop')
                if info.file_size > (4 << 20):
                    raise ValueError('VENDOR/build.prop exceeds metadata bound')
                props = dict(line.split('=',1) for line in archive.read(info).decode().splitlines()
                             if '=' in line and not line.startswith('#'))
            identity = {'device':props.get('ro.product.vendor.device'),
                        'build':props.get('ro.vendor.build.version.incremental'),
                        'timestamp':props.get('ro.vendor.build.date.utc')}
            build = out_dir.removeprefix('out-mustang-build-')
            allocation = json.loads((Path(source).parent/'allocation.json').read_text())
            import datetime
            epoch = str(int(datetime.datetime.strptime(allocation['reservedAt'],'%Y-%m-%dT%H:%M:%SZ')
                            .replace(tzinfo=datetime.timezone.utc).timestamp()))
            complete = complete and identity == {'device':'mustang','build':build,'timestamp':epoch}
        except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile) as error:
            complete = False
            identity['error'] = type(error).__name__
    return {'outDir':str(out), 'verdict':'complete' if complete else 'incomplete',
            'artifacts':artifacts, 'actualBuildIdentity':identity, 'signed':False,
            'omitted':'CRC sweep, partition/firmware coverage, packaged app/policy and signing checks'}


def engine():
    spec = importlib.util.spec_from_file_location('mustang_build_engine', ROOT/'scripts/blazer-build-only.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DEVICE = 'mustang'
    module.RECIPE = ROOT/'os/build-only/mustang.json'
    module.BUILD_INDEX = 'os/build-only/mustang-inputs/build-index.json'
    module.COMPILE_OUT_DIR = 'out-mustang-build-{build}'
    module.RESERVATIONS = 'mustang-allocations'
    module.ALLOCATION_HELPER = 'scripts/mustang-allocate.py'
    module.blazer_integration = mustang_integration
    module.collect_compile_outputs = collect_outputs
    canonical=mustang_lane.validate(mustang_lane.configuration())['lockPath']
    module.open_shared_lock=functools.partial(mustang_existing_lock.open_stream,expected=canonical)
    module.lock_free=functools.partial(mustang_existing_lock.probe,expected=canonical)
    return module


if __name__ == '__main__':
    mustang_lane.reject_legacy()
    if len(sys.argv) > 1 and sys.argv[1] not in ('plan', 'preflight', 'run'):
        sys.exit('Mustang supports plan/preflight/run only; historical inspection targets are not Mustang bindings')
    sys.exit(engine().main())
