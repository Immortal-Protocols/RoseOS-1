#!/usr/bin/env python3
"""INFRA-ALPHA-117: verification-only continuation of the Cuttlefish alpha compiled by run 40765631053.

That run's make succeeded and its image-content gate then failed on the Updater-absence package scan, which needed
resource-resolving aapt badging (proven by the ALPHA-116 diagnostic, run 40771878744). The scan now reads the package
from the compiled manifest (scripts/alpha_image.py); every other check is unchanged. This re-runs the FULL gate on the
retained output, without rebuilding.

  run   the os-release.yml emulator-alpha-verify-retained job, on the builder: --candidate-sha/--apps must be the
        bound base and --build-timestamp the original BUILD_DATETIME (1793220134). Under the shared builder lock it
        checks the pinned preparation/successor tree, that the retained build.props name build
        rose-cf-alpha-40765631053 and that timestamp, and that PRODUCT_OUT/super.img is the exact image the diagnostic
        measured (size and sha256 below); any other preimage is refused before extraction. It then runs
        emulator-alpha.py's image_content (the full contract: Rose app bytes, package, version and signer; framework
        policy; tray property; Seedvault closure; no Updater), re-hashes super.img afterwards, and hashes the retained
        top-level images and host package as NEW observations. --receipt-dir receives
        emulator-alpha-verification.json and SHA256SUMS (verdict PASS or FAIL).

Never runs lunch, make, signing, an installer or a publisher; writes only the receipt directory (and the gate's own
temporary extraction, which it removes). The original compile is not relabelled: no build receipt existed for it, and
this receipt does not claim one; hashes measured here are new observations. Not a boot, UI or phone result.
Exit status: 0 verified, 1 check failed, 2 invalid input, 4 setup missing.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_RUN = '40765631053'
ORIGINAL_BUILD_DATETIME = '1793220134'
DIAGNOSTIC_RUN = '40771878744'
# super.img as the ALPHA-116 diagnostic measured it (a new observation of the retained output, not a run-40765631053 hash).
SUPER_SHA256 = 'e543c1301c3f999db5d051bd85ff9cc3a006e47a8d2a8cfd1f484c339d909ed3'
SUPER_SIZE = 2410014072
RECEIPT = 'emulator-alpha-verification.json'
IDENTITY_KEYS = ('ro.build.fingerprint', 'ro.build.type', 'ro.build.version.incremental', 'ro.build.date.utc', 'ro.product.device')

def emulator_alpha():
    spec = importlib.util.spec_from_file_location('emulator_alpha', ROOT/'scripts/emulator-alpha.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def recipe():
    result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def preimage(super_image):
    """The retained super.img must be exactly the measured one; checked before and after the gate."""
    if os.path.islink(super_image) or not super_image.is_file():
        return 'super.img is not a regular file in PRODUCT_OUT'
    size = super_image.stat().st_size
    if size != SUPER_SIZE:
        return f'super.img is {size} bytes, not the measured {SUPER_SIZE}'
    digest = sha256_file(super_image)
    if digest != SUPER_SHA256:
        return f'super.img sha256 {digest} is not the measured {SUPER_SHA256}'
    return None

def observed_props(props):
    """Every identity field as observed in each partition build.prop; absent stays absent (None), never assumed."""
    return {name: {k: values.get(k) for k in IDENTITY_KEYS} for name, values in sorted(props.items())}

def run(config, ea, sha, apps, run_id, timestamp, receipt_dir, now=time.time):
    if not re.fullmatch(r'[0-9a-f]{40}', sha or '') or not re.fullmatch(r'[1-9][0-9]*', str(run_id or '')):
        raise ea.InvalidInput('a full candidate SHA and the numeric Actions run id are required')
    if timestamp != ORIGINAL_BUILD_DATETIME:
        raise ea.InvalidInput(f'--build-timestamp must be run {ORIGINAL_RUN} BUILD_DATETIME {ORIGINAL_BUILD_DATETIME}')
    receipt_dir = Path(receipt_dir)
    if receipt_dir.exists():
        raise ea.CheckFailed(f'{receipt_dir} already exists; use a new receipt directory')
    ea.guard(config)
    ea.check_dispatch(config, sha, apps)
    original = ea.build_id(config, ORIGINAL_RUN)
    lunch = config['hostEvidence']['lunch']
    receipt = {'receiptType': 'emulator-alpha-retained-verification', 'verdict': 'FAIL', 'error': None,
        'verificationRun': str(run_id), 'verifierRecipe': recipe(),
        'originalCompile': {'runId': ORIGINAL_RUN, 'emulatorBuildId': original, 'buildDatetime': ORIGINAL_BUILD_DATETIME,
            'buildReceipt': None, 'note': 'run ' + ORIGINAL_RUN + ' failed its image gate after make; it wrote no build receipt'},
        'diagnosticProvenance': {'runId': DIAGNOSTIC_RUN, 'superImage': {'size': SUPER_SIZE, 'sha256': SUPER_SHA256},
            'note': 'measured by the ALPHA-116 diagnostic on the retained output, not recorded by run ' + ORIGINAL_RUN},
        'candidate': sha, 'sourceBinding': {k: config['sourceBinding'][k] for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps')},
        'preparation': {'preparationId': config['prepared']['preparationId'], 'receiptSha256': config['prepared']['receiptSha256'],
            'successor': {k: (config['prepared'].get('successor') or {}).get(k) for k in ('successorId', 'receiptSha256')}},
        'lunch': lunch, 'outDir': config['build']['outDir'],
        'executedMake': False, 'signed': False, 'keys': config['target']['keys'], 'published': False, 'testOnly': True,
        'bootAttempted': False, 'bootVerified': False, 'imageVerified': False,
        'notProven': ['signature or verified boot (AOSP test keys, userdebug)', 'guest boot (sys.boot_completed)',
            'Rose UI, viewer, input or Stop behaviour', 'Pixel hardware, firmware, radio or flashing']}
    lock = ea.locked(config)
    try:
        _, tree, failures = ea.pinned_failures(config)
        if failures:
            raise ea.CheckFailed('; '.join(failures))
        product_out, host_out = tree/lunch['PRODUCT_OUT'], tree/lunch['HOST_OUT']
        props = ea.read_props(product_out)
        receipt['buildProps'] = observed_props(props)
        receipt['identityChecked'] = sorted({k for values in receipt['buildProps'].values() for k, v in values.items() if v is not None})
        failures = ea.identity_failures(props, config, original, ORIGINAL_BUILD_DATETIME, lunch['PRODUCT_DEVICE'])
        if failures:
            raise ea.CheckFailed('the retained output is not run ' + ORIGINAL_RUN + "'s build: " + '; '.join(failures))
        problem = preimage(product_out/'super.img')
        if problem:
            raise ea.CheckFailed('preimage refused: ' + problem)
        receipt['preimage'] = {'size': SUPER_SIZE, 'sha256': SUPER_SHA256, 'matched': True}
        content = ea.image_content(config, config['prepared']['preparationId'], product_out)
        problem = preimage(product_out/'super.img')
        if problem:
            raise ea.CheckFailed('super.img changed during verification: ' + problem)
        images = {p.name: {'size': p.stat().st_size, 'sha256': sha256_file(p)} for p in sorted(product_out.glob('*.img'))}
        host = host_out/config['hostPackage']['output']
        hosts = {host.name: {'size': host.stat().st_size, 'sha256': sha256_file(host)}} if host.is_file() else {host.name: None}
        receipt.update(verdict='PASS', imageVerified=True, imageContent=content, images=images, hostOutputs=hosts,
            newObservations='images and hostOutputs were measured by this verification run; run ' + ORIGINAL_RUN
                + ' recorded no image or host-package hash before its gate failed')
    except ea.CheckFailed as error:
        receipt['error'] = str(error)[:4096]
        raise
    except ea.SetupMissing as error:
        receipt['error'] = 'setup missing: ' + ', '.join(error.missing)
        raise
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        receipt['error'] = error.__class__.__name__
        raise
    finally:
        lock.close()
        receipt['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))
        receipt_dir.mkdir(parents=True)
        release_receipts.write_once(receipt_dir/RECEIPT, receipt)
        (receipt_dir/'SHA256SUMS').write_text(f'{sha256_file(receipt_dir/RECEIPT)}  {RECEIPT}\n')
    return receipt

def main(argv=None, ea=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['run'])
    p.add_argument('--config')
    p.add_argument('--candidate-sha')
    p.add_argument('--apps', default='[]')
    p.add_argument('--run-id')
    p.add_argument('--build-timestamp')
    p.add_argument('--receipt-dir', required=True)
    a = p.parse_args(argv)
    ea = ea or emulator_alpha()
    try:
        config = ea.load(a.config or ea.CONFIG)
        result = run(config, ea, a.candidate_sha, json.loads(a.apps), a.run_id, a.build_timestamp, a.receipt_dir)
    except ea.SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except ea.InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (ea.CheckFailed, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)[:4096]}), file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps({k: result[k] for k in ('receiptType', 'verdict', 'imageVerified', 'verificationRun')}, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
