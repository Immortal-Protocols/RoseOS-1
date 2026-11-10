#!/usr/bin/env python3
"""Mustang OTA observation, checked staging and fresh separate-device publication.

No key generation, IAM, phone operations or automatic activation. Hosted publication
uses the existing pixel-ota App token; only public assets leave the builder.
"""
import argparse
import datetime
import fnmatch
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import github_release_publisher as publisher
import host_secrets
import release_receipts
import mustang_edge
import mustang_lane
import mustang_paths

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/ota/mustang.json'
REPOSITORY = 'Immortal-Protocols/pixel-ota'
LABEL = 'build-and-package candidate; omitted verification remains undone; physical-device acceptance unproven'
SOURCES = ('os/pinned-manifest.xml', 'os/patches/*', 'os/overlays/*', 'os/background-control/*',
           'os/build-only/mustang*', 'os/flavors/mustang.json', 'scripts/mustang-pipeline.py',
           'scripts/mustang-build-only.py', 'scripts/mustang-candidate-bundle.py', 'scripts/mustang_integration.py',
           'scripts/mustang-reconcile-seed.py', 'scripts/apply-os-changes.py', 'scripts/host-reconcile.py',
           'workspace/rose-agent/*')
VALIDATED_SOURCES = SOURCES + ('scripts/mustang*', 'os/ota/mustang.json', 'scripts/agent-beta-auto.py',
           'os/ota/mustang-baseline-factory.json',
           'scripts/host-sign.py', 'scripts/host_secrets.py', 'scripts/host-decrypt-keys.py',
           'scripts/host-factory-package.py', 'scripts/github_release_publisher.py', 'scripts/release-pipeline.py',
           '.github/workflows/os-release.yml', '.github/workflows/mustang-ota*.yml',
           'scripts/tests/test_mustang_ota.py', 'scripts/tests/test_mustang_edge_gate.py',
           'scripts/tests/test_edge_artifact.py', 'scripts/tests/dex_fixture.py',
           'scripts/tests/fixtures/rose-edge-dex/*',
           'scripts/edge_artifact.py', 'os/release-pipeline/frankel.json', 'os/tests/MustangReleasesTest.java')


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def config():
    cfg = read(CONFIG)
    if cfg.get('schema') != 'roseos.mustang-ota-config.v1' or cfg.get('device') != 'mustang' \
            or cfg.get('repository') != REPOSITORY:
        raise ValueError('Mustang OTA configuration identity differs')
    baseline = cfg['baseline']
    if baseline['installedBuild'] != baseline['build'] or baseline['installedChannel'] != 'stable' \
            or not baseline.get('installationEvidence'):
        raise ValueError('installed compatible Mustang baseline and Beta selection must be recorded')
    return cfg


def unconditional_access(ref, root=ROOT):
    """Whether the committed readback `ref` (under os/ota) records, from IAM metadata, roles/secretmanager.secretAccessor
    with no condition and no expiry for the existing signer on exactly the two Mustang signing secrets that
    os/signing-bootstrap/mustang-access-binding.json names."""
    raw = Path(root)/str(ref)
    try:
        if raw.is_symlink() or raw.resolve().parent != (Path(root)/'os/ota').resolve():
            return False
        path = raw.resolve()
        record, access = read(path), read(Path(root)/'os/signing-bootstrap/mustang-access-binding.json')
        secrets, rows = {row['secret'] for row in access['bindings']}, record.get('bindings')
        return (record.get('schema') == 'roseos.mustang-ota-access-readback.v1' and bool(record.get('observedAt'))
                and record.get('project') == access['project'] and 'expiresAt' in record and record['expiresAt'] is None
                and isinstance(rows, list) and len(rows) == len(secrets) == 2
                and {row.get('secret') for row in rows} == secrets
                and all(row.get('role') == 'roles/secretmanager.secretAccessor' and row.get('member') == access['signer']
                        and 'condition' in row and row['condition'] is None
                        and 'expiresAt' in row and row['expiresAt'] is None for row in rows))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def guard(cfg, automatic=False, access=False, now=None, publication=True):
    if publication and cfg.get('publicationEnabled') is not True:
        raise ValueError('Mustang public OTA activation remains pending; no publication authorized by source alone')
    if automatic and cfg.get('automationEnabled') is not True:
        raise ValueError('Mustang automatic publication activation remains pending')
    if access:
        binding = cfg['signingAccess']
        if binding.get('status') != 'approved' or not binding.get('approvalRef') or 'expiresAt' not in binding:
            raise ValueError('existing Mustang version-1 signing read access requires an approved current binding; no extension performed')
        if binding['expiresAt'] is None:
            if not unconditional_access(binding['approvalRef']):
                raise ValueError('non-expiring Mustang signing read access requires a recorded unconditional readback of '
                                 'both signer bindings; no extension performed')
            return
        expiry = datetime.datetime.fromisoformat(binding['expiresAt'].replace('Z', '+00:00'))
        now = now or datetime.datetime.now(datetime.timezone.utc)
        if expiry.tzinfo is None or (expiry-now).total_seconds() < 1800:
            raise ValueError('approved signing read-access window expired or has less than 30 minutes remaining')


def identities():
    return read(ROOT/'os/signing-bootstrap/mustang-bootstrap-receipt.json')['publicIdentity']


def fingerprint(sha, root=ROOT, patterns=SOURCES):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('full source SHA required')
    rows = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-rz', sha]).split(b'\0')
    selected = [row for row in rows if row and any(fnmatch.fnmatchcase(row.split(b'\t', 1)[1].decode(), p) for p in patterns)]
    if not selected:
        raise ValueError('no relevant source content found')
    return hashlib.sha256(b'\0'.join(sorted(selected))).hexdigest()


def validation_fingerprint(sha):
    return fingerprint(sha, patterns=VALIDATED_SOURCES)


def checked_file(row):
    path = Path(row['path'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size != row['sizeBytes'] or digest(path) != row['sha256']:
        raise ValueError('retained output bytes differ')
    return path


def member(archive, name, limit):
    rows = [x for x in archive.infolist() if x.filename == name]
    if len(rows) != 1 or rows[0].file_size > limit:
        raise ValueError('required archive metadata missing, ambiguous or oversized: ' + name)
    return archive.read(rows[0])


def properties(raw):
    rows = [line.split('=', 1) for line in raw.decode().splitlines() if '=' in line and not line.startswith('#')]
    if len({key for key, _ in rows}) != len(rows):
        raise ValueError('duplicate metadata properties')
    return dict(rows)


def vendor_identity(raw):
    """Read the three literal identity values with init's in-file last-definition
    behavior (pinned system/core 322ff512 property_service.cpp LoadProperties).
    Compare the resulting values below; do not impose OTA-manifest uniqueness
    on a build.prop file. OTA metadata keeps its strict duplicate parser.
    """
    required = {'ro.product.vendor.device', 'ro.vendor.build.version.incremental', 'ro.vendor.build.date.utc'}
    identity = {}
    for line in raw.decode().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = (part.strip() for part in line.split('=', 1))
        if key in required:
            identity[key] = value
    if set(identity) != required:
        raise ValueError('required vendor build identity missing')
    return identity


def observe_target(path, build, epoch, tools, pins, execute=subprocess.run):
    """Actual packaged Updater resources, signer and system OTA trust; no phone claim."""
    with zipfile.ZipFile(path) as archive:
        return observe_components(member(archive,'VENDOR/build.prop',4<<20),
            member(archive,'SYSTEM/priv-app/Updater/Updater.apk',128<<20),
            member(archive,'SYSTEM/etc/security/otacerts.zip',4<<20),build,epoch,tools,pins,execute)


def observe_components(vendor_props, updater, trust_store, build, epoch, tools, pins,
                       execute=subprocess.run, workdir=None, origin='target-files'):
    """The same baseline checks on explicitly proven image/target-files components."""
    pipeline = module('mustang_ota_resources', 'release-pipeline.py')
    route = read(ROOT/'os/build-only/mustang-updater-route.json')
    with tempfile.TemporaryDirectory(prefix='mustang-ota-observe-',dir=workdir) as directory:
        props = vendor_identity(vendor_props)
        if (props.get('ro.product.vendor.device'), props.get('ro.vendor.build.version.incremental'),
                props.get('ro.vendor.build.date.utc')) != ('mustang', build, epoch):
            raise ValueError(f'actual {origin} device/build/time differs')
        apk = Path(directory)/'Updater.apk'
        apk.write_bytes(updater)
        with zipfile.ZipFile(apk) as package:
            dexes = [row.filename for row in package.infolist() if re.fullmatch(r'classes[0-9]*\.dex', row.filename)]
            descriptors = [b'Lapp/seamlessupdate/client/'+name+b';' for name in (b'RoseChannels', b'RoseReleases')]
            compiled = [member(package, name, 128 << 20) for name in dexes]
            if not all(any(descriptor in dex for dex in compiled) for descriptor in descriptors):
                raise ValueError('packaged Updater discovery classes not observed; diagnose baseline compatibility before proceeding')
        result = execute([tools['aapt'], 'dump', '--values', 'resources', str(apk)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError('packaged Updater resources could not be read')
        expected = {('string', 'url'): route['updaterUrl'], ('string', 'release_index'): route['releaseIndex'],
                    ('string', 'channel_default'): 'prod', ('array', 'channel_published'): ['stable'],
                    ('array', 'channel_values'): ['prod', 'stable', 'alpha']}
        failures = pipeline.updater_failures(result.stdout, expected)
        if failures:
            raise ValueError('; '.join(failures))
        result = execute([tools['aapt'], 'dump', 'badging', str(apk)], capture_output=True, text=True, timeout=120)
        if result.returncode or not re.search(r"package: name='app\.seamlessupdate\.client'", result.stdout):
            raise ValueError('packaged Updater package identity differs')
        result = execute([tools['apksigner'], 'verify', '--print-certs', str(apk)], capture_output=True, text=True, timeout=120)
        certs = re.findall(r'Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]+)', result.stdout)
        if result.returncode or [x.lower() for x in certs] != [pins['otaCertificateSha256']]:
            raise ValueError('packaged Updater signer differs from the established Mustang identity')
        trust = []
        with zipfile.ZipFile(io.BytesIO(trust_store)) as certificates:
            for row in certificates.infolist():
                pem = member(certificates, row.filename, 1 << 20)
                cert = Path(directory)/'trusted.pem'
                cert.write_bytes(pem)
                trust.append(host_secrets.certificate_sha256(cert))
        if pins['otaCertificateSha256'] not in trust:
            raise ValueError('installed-system OTA trust does not contain the established Mustang identity')
        return {'device': 'mustang', 'build': build, 'timestamp': epoch, 'updaterApkSha256': digest(apk),
                'updaterResources': {k[1]: v for k, v in expected.items()}, 'updaterSignerSha256': certs[0].lower(),
                'discoveryClassesPresent': True,
                'trustedOtaCertificateSha256': sorted(trust), 'packagedUpdaterVerified': True}


def candidate_continuity(record):
    """Installed updater and v1 cert from the candidate installer. Not the current phone or target-files."""
    if not isinstance(record, dict) or record.get('schema') != 'roseos.mustang-candidate-continuity.v1' \
            or record.get('candidateOnly') is not True or record.get('currentPhone') is not False \
            or record.get('phoneObserved') is not False or record.get('admitsRuntime') is not False \
            or record.get('admitsSourcePin') is not False or record.get('targetFiles') is not None \
            or record.get('kernelMount') is not False:
        raise ValueError('candidate continuity is not a current phone, target-files, or admission')
    obs = record.get('observations') or {}
    certs = obs.get('otaCerts')
    if not isinstance(certs, list) or len(certs) != 1 or not isinstance(certs[0], dict):
        raise ValueError('candidate OTA certificate observation is incomplete')
    prior = read(ROOT/'os/mustang-ota/41761758623/baseline.json')
    staged = read(ROOT/'os/mustang-ota/41761758623/mustang-ota-receipt.json')
    installer = read(ROOT/'os/mustang-delivery/41738359336/package.json')['output']
    identity = identities()['otaCertificateSha256']
    apk, der = obs.get('updaterSha256'), certs[0].get('derSha256')
    if obs.get('updaterBytes') != 5752573 or apk != prior.get('updaterApkSha256') \
            or apk != staged.get('packagedUpdater', {}).get('updaterApkSha256') \
            or certs[0].get('derBytes') != 1297 or der != identity \
            or prior.get('trustedOtaCertificateSha256') != [identity] \
            or staged.get('packagedUpdater', {}).get('trustedOtaCertificateSha256') != [identity] \
            or record.get('installerSha256') != installer.get('sha256') \
            or record.get('installerSize') != installer.get('sizeBytes') \
            or (record.get('tools') or {}).get('rawBytes') != 4191461376 \
            or (record.get('tools') or {}).get('lpunpackSha256') != '95a07401e3f54e0a16f948938b464cab6d0f31b40cb08e196b4cc45f7e8811e5':
        raise ValueError('candidate updater or v1 certificate does not match the installed baseline')
    if obs.get('apkMatchesExpected') is not True or obs.get('certMatchesExpected') is not True:
        raise ValueError('candidate updater or v1 certificate does not match the installed baseline')
    if 'updaterSignerSha256' in obs:
        raise ValueError('candidate inspection did not measure the APK signer')
    prebuild = read(ROOT/'os/mustang-ota/41761758623/producer/baseline-prebuild.json')
    historical_signer = prior.get('updaterSignerSha256')
    if type(historical_signer) is not str or not re.fullmatch(r'[0-9a-f]{64}', historical_signer) \
            or historical_signer != staged.get('packagedUpdater', {}).get('updaterSignerSha256') \
            or historical_signer != prebuild.get('updaterSignerSha256'):
        raise ValueError('historical updater signer observations disagree')
    return {
        'schema': 'roseos.mustang-ota-baseline.v1',
        'evidenceKind': 'candidate-installer-continuity',
        'candidateOnly': True,
        'currentPhone': False,
        'phoneObserved': False,
        'admitsRuntime': False,
        'apkSignerRemeasured': False,
        'targetFiles': None,
        'build': prior['build'],
        'timestamp': prior['timestamp'],
        'device': 'mustang',
        'packagedUpdaterVerified': True,
        'updaterApkSha256': apk,
        'updaterBytes': obs['updaterBytes'],
        'trustedOtaCertificateSha256': [identity],
        'historicalUpdaterSignerSha256': historical_signer,
        'discoveryClassesPresent': prior.get('discoveryClassesPresent') is True,
        'installedEvidence': prior.get('installedEvidence'),
        'continuityRecord': 'os/build-only/mustang-candidate-continuity-20261107.json',
    }


def require_staged_baseline(baseline):
    """Reject a relabeled baseline before any artifact work. Equality is against the vetted object."""
    if not isinstance(baseline, dict) or baseline.get('evidenceKind') != 'candidate-installer-continuity':
        raise ValueError('staged baseline differs from the vetted candidate continuity')
    path = baseline.get('continuityRecord')
    if path != 'os/build-only/mustang-candidate-continuity-20261107.json':
        raise ValueError('staged baseline differs from the vetted candidate continuity')
    expected = candidate_continuity(read(ROOT/path))
    if baseline != expected:
        raise ValueError('staged baseline differs from the vetted candidate continuity')
    return expected


def observe_baseline(out, lane=None, tools=None, target_files=None, factory=None, runtime=None):
    cfg = config()
    baseline = cfg['baseline']
    signed = read(ROOT/baseline['signReceipt'])
    if signed['device'] != 'mustang' or signed['build'] != baseline['build'] or signed['candidateSource'] != baseline['source']:
        raise ValueError('committed baseline receipt differs')
    if factory is not None:
        if lane is None or runtime is None or target_files is not None:
            raise ValueError('factory baseline needs exclusive dedicated evidence')
        hp=module('mustang_baseline_lock','host-factory-package.py')
        with hp.shared_lock(str(mustang_paths.layout(lane)['lockPath'])):
            return module('mustang_factory_baseline','mustang-baseline-image.py').observe(factory,lane,runtime,out,root=ROOT)
    if lane is None:
        tools = read(ROOT/'os/build-only/mustang-host.json')
        lock = read(ROOT/'os/release-pipeline/frankel.json')['host']['lockPath']
        target_files = signed['outputs']['targetFiles']
    else:
        paths = mustang_paths.layout(lane)
        lock = str(paths['lockPath'])
        if tools is None or target_files is None or any(target_files.get(key)!=signed['outputs']['targetFiles'][key]
                                                      for key in ('sha256','sizeBytes')) \
                or paths['home'] not in Path(target_files['path']).parents \
                or any(p.is_symlink() for p in (Path(target_files['path']),*Path(target_files['path']).parents)):
            raise ValueError('dedicated baseline must retain the committed archive identity on its own disk')
    hp = module('mustang_ota_host', 'host-factory-package.py')
    with hp.shared_lock(lock):
        observed = observe_target(checked_file(target_files), baseline['build'], baseline['timestamp'], tools, identities())
    observed.update(schema='roseos.mustang-ota-baseline.v1', targetFiles=target_files,
                    installedEvidence=baseline['installationEvidence'], phoneObserved=False)
    release_receipts.write_once(out, observed)
    return observed


def require_factory_baseline(record):
    runtime=read(ROOT/'os/build-only/mustang-runtime.json')
    ref=runtime.get('baselineFactoryEvidence')
    if not ref or runtime.get('baselineTargetFiles') is not None:
        raise ValueError('factory-image baseline evidence is not explicitly selected')
    value=module('mustang_baseline_reference','mustang-dedicated.py').reference(ref,ROOT)
    lane=read(ROOT/'os/build-only/mustang-lane.json')
    return module('mustang_baseline_receipt','mustang-baseline-image.py').require_receipt(record,value,lane,runtime,ROOT)


def ota_metadata(path, build, epoch, baseline):
    with zipfile.ZipFile(path) as archive:
        values = properties(member(archive, 'META-INF/com/android/metadata', 1 << 20))
    if values.get('pre-device') != 'mustang' or values.get('post-build-incremental') != build \
            or values.get('post-timestamp') != epoch or f'/{build}:' not in values.get('post-build', '') \
            or values.get('ota-type') != 'AB' or values.get('ota-downgrade') == 'yes' \
            or values.get('ota-wipe') == 'yes' or 'pre-build' in values or 'pre-build-incremental' in values:
        raise ValueError('OTA target/version/full-update metadata differs or requests downgrade/wipe')
    if int(build) <= int(baseline['build']) or int(epoch) <= int(baseline['timestamp']):
        raise ValueError('OTA must be newer than the installed baseline')
    return values


def stage(sign_run, attempt, baseline_file, out, lane=None, tools=None, publication=True):
    cfg = config()
    guard(cfg,publication=publication)
    adapter = module('mustang_ota_sign', 'mustang-sign-package.py')
    context = {'lane':lane} if lane is not None else {}
    signed = read(adapter.run_root('sign', sign_run, attempt,**context)/'mustang-sign-receipt.json')
    if signed.get('status') != 'signed-command-complete' or signed.get('device') != 'mustang' \
            or signed.get('runId') != sign_run or signed.get('runAttempt') != attempt:
        raise ValueError('fresh same-run Mustang signing receipt required')
    mustang_edge.require_signing(signed)
    if lane is not None and (signed.get('builder')!=lane['vm'] or signed.get('workflow')!=lane['workflow']):
        raise ValueError('OTA signing receipt differs from the dedicated Mustang VM/workflow')
    if lane is not None:
        release=adapter.run_root('sign',sign_run,attempt,lane=lane)/'release'
        source=adapter.run_root('prep',signed['compileRunId'],signed['compileRunAttempt'],lane=lane)/'source'
        if Path(signed['sourceRoot'])!=source or Path(signed['outputs']['targetFiles']['path'])!=release/'signed-target_files.zip' \
                or Path(signed['outputs']['ota']['path'])!=release/'public'/f'mustang-ota_update-{signed["build"]}.zip':
            raise ValueError('OTA inputs leave the dedicated same-run source/release paths')
    if signed['secrets'] != {'keys': {'name': adapter.SECRETS['keys'], 'version': '1'},
                             'passphrase': {'name': adapter.SECRETS['passphrase'], 'version': '1'}}:
        raise ValueError('established Mustang signing versions must be preserved')
    baseline = read(baseline_file)
    if baseline.get('evidenceKind')=='candidate-installer-continuity':
        baseline = require_staged_baseline(baseline)
    factory_mode=lane is not None and read(ROOT/'os/build-only/mustang-runtime.json').get('baselineFactoryEvidence') is not None
    if factory_mode or baseline.get('evidenceKind')=='factory-images':
        require_factory_baseline(baseline)
        if baseline.get('baselineSource')!=cfg['baseline']['source']:
            raise ValueError('factory baseline original candidate differs')
    if baseline.get('packagedUpdaterVerified') is not True or baseline.get('build') != cfg['baseline']['installedBuild'] \
            or baseline.get('trustedOtaCertificateSha256') is None \
            or identities()['otaCertificateSha256'] not in baseline['trustedOtaCertificateSha256']:
        raise ValueError('actual baseline updater and OTA trust observation required')
    out = Path(out)
    out.mkdir(mode=0o700)
    pins, build = identities(), signed['build']
    lock = str(mustang_paths.layout(lane)['lockPath']) if lane is not None else read(ROOT/'os/release-pipeline/frankel.json')['host']['lockPath']
    if lane is not None and tools is None: raise ValueError('dedicated OTA tools must be explicit')
    tools = tools if lane is not None else read(ROOT/'os/build-only/mustang-host.json')
    hp = module('mustang_ota_factory', 'host-factory-package.py')
    with hp.shared_lock(lock):
        tf, ota = (checked_file(signed['outputs'][name]) for name in ('targetFiles', 'ota'))
        collection_path = adapter.run_root('collect', sign_run, attempt,**context)/'compile-collection.json'
        collection = read(collection_path)
        if digest(collection_path) != signed['compileCollectionReceiptSha256'] \
                or collection.get('roseEdge') != signed['roseEdge']['unsigned']:
            raise ValueError('fresh unsigned raised-edge collection proof differs')
        epoch = collection['outputs']['actualBuildIdentity']['timestamp']
        if epoch != signed['timestamp']:
            raise ValueError('raised-edge timestamp differs from collected build')
        observed = observe_target(tf, build, epoch, tools, pins)
        metadata = ota_metadata(ota, build, epoch, cfg['baseline'])
        public = ota.parent
        cert, avb = public/'releasekey.x509.pem', public/'avb_pkmd.bin'
        if host_secrets.certificate_sha256(cert) != pins['otaCertificateSha256'] or digest(avb) != pins['avbPublicKeySha256']:
            raise ValueError('produced public signing identity differs from the existing Mustang bootstrap')
        checker = Path(signed['sourceRoot'])/'build/make/tools/releasetools/check_ota_package_signature.py'
        with tempfile.TemporaryDirectory(prefix='mustang-ota-tools-', dir=out.parent) as temp:
            hp.unzip_otatools(checked_file(signed['compileArtifacts']['otatools']), Path(temp))
            binary = Path(temp)/'bin'
            if not (binary/'delta_generator').is_file() or not checker.is_file():
                raise ValueError('OTA signature verifier dependency unavailable; verification not performed')
            result = subprocess.run([sys.executable, str(checker), str(cert), str(ota)], capture_output=True, text=True,
                                    timeout=1800, env=dict(os.environ, PATH=str(binary)+':'+os.environ.get('PATH', '')))
            if result.returncode:
                raise ValueError('OTA whole-package or payload signature verification failed')
            verification_tools = {'checkOtaSignatureSha256': digest(checker),
                                  'deltaGeneratorSha256': digest(binary/'delta_generator')}
        shutil.copyfile(ota, out/ota.name)
        (out/'mustang-stable').write_text(f'{build} {epoch} mustang stable\n')
        receipt = {'schema': 'roseos.mustang-ota-stage.v1', 'device': 'mustang', 'build': build, 'timestamp': epoch,
                   'candidateSource': signed['candidateSource'], 'recipeSource': signed['recipeSource'],
                   'runId': sign_run, 'label': LABEL, 'contentFingerprint': fingerprint(signed['candidateSource']),
                   'runAttempt':signed['runAttempt'], 'compileRunId':signed['compileRunId'],
                   'compileRunAttempt':signed['compileRunAttempt'],
                   'roseEdge':signed['roseEdge'], 'edgeTargetFiles':signed['edgeTargetFiles'],
                   'baselineObservationSha256': digest(baseline_file), 'baselineBuild': baseline['build'],
                   'packagedUpdater': observed, 'otaMetadata': metadata, 'publicIdentity': pins,
                   'checks': {'targetVersion': True, 'baselineTrust': True, 'otaSignatures': True,
                              'packagedUpdater': True, 'roseEdge': True},
                   'verificationTools': verification_tools,
                   'phoneUpdatePassed': False, 'currentPhone': False, 'automationDemonstrated': False, 'sprintOmissions': signed['omittedChecks'],
                   'ota': {'file': ota.name, 'sizeBytes': ota.stat().st_size, 'sha256': digest(ota)}}
        if lane is not None: receipt.update(builder=dict(lane['vm']),workflow=lane['workflow'])
        if baseline.get('evidenceKind')=='candidate-installer-continuity':
            receipt['baselineEvidenceKind']='candidate-installer-continuity'
        if factory_mode: receipt['baselineFactoryEvidence']=baseline
        release_receipts.write_once(out/'mustang-ota-receipt.json', receipt)
        (out/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in sorted(out.iterdir())))
    return receipt


def stage_hosted(bundle,unsigned,signed,ota,public,extracted,java_home,out,*,root=ROOT,execute=subprocess.run):
    """Verify and stage a newly signed full OTA on the isolated hosted signer.

    Uses the same Updater, OTA metadata, v1 identity and raised-edge verifiers
    as native staging. No old target-files/installer or native-disk path is read.
    """
    cfg=config();guard(cfg,publication=False);root=Path(root)
    runtime=read(root/'os/build-only/mustang-runtime.json')
    if runtime.get('baselineTargetFiles') is not None or runtime.get('baselineFactoryEvidence') is not None \
            or runtime.get('candidateContinuity')!='os/build-only/mustang-candidate-continuity-20261107.json':
        raise ValueError('full hosted staging requires the explicitly selected candidate continuity contract')
    if runtime.get('clockNumbering') is not None:
        import mustang_clock, mustang_lane
        mustang_clock.require_bundle(bundle,mustang_lane.configuration(),runtime,root)
    baseline=candidate_continuity(read(root/runtime['candidateContinuity']))
    lane=read(root/'os/build-only/mustang-lane.json');pins=identities()
    import mustang_retained
    compile_run,compile_attempt,compile_source=mustang_retained.compile_context(bundle)
    build,epoch=bundle['build'],bundle['timestamp'];run=str(bundle['runId']);attempt=str(bundle['runAttempt'])
    if attempt!='1' or bundle.get('workflow')!=lane['workflow'] or not re.fullmatch(r'[1-9][0-9]{9}',build):
        raise ValueError('full hosted staging run/build differs')
    unsigned,signed,ota,public=map(Path,(unsigned,signed,ota,public));binary=Path(extracted)/'bin'
    bound=mustang_edge.binding(compile_source,bundle['sourceSha'],build,epoch,run,attempt,compile_run,compile_attempt)
    unsigned_archive=mustang_edge.observed_archive(unsigned);signed_archive=mustang_edge.observed_archive(signed)
    unsigned_proof=bundle.get('unsignedRaisedEdge')
    mustang_edge.require(unsigned_proof,'unsigned',bound,unsigned_archive)
    signed_proof=mustang_edge.inspect(signed,'signed',bound,signed_archive)
    mustang_edge.require(signed_proof,'signed',bound,signed_archive)
    clean=dict(PATH=str(Path(java_home)/'bin')+':'+str(binary)+':/usr/bin:/bin',JAVA_HOME=str(java_home),LANG='C',LC_ALL='C')
    def checked(argv,**kwargs):return execute(argv,**dict(kwargs,env=clean))
    tools=dict(aapt=str(binary/'aapt2'),apksigner=str(binary/'apksigner'))
    observed=observe_target(signed,build,epoch,tools,pins,execute=checked)
    metadata=ota_metadata(ota,build,epoch,cfg['baseline'])
    cert=public/'releasekey.x509.pem';avb=public/'avb_pkmd.bin'
    if host_secrets.certificate_sha256(cert)!=pins['otaCertificateSha256'] or digest(avb)!=pins['avbPublicKeySha256']:
        raise ValueError('hosted public signing identity differs from Mustang v1')
    checker=binary/'check_ota_package_signature'
    if not checker.is_file() or not (binary/'delta_generator').is_file():
        raise ValueError('qualified OTA signature verifier is unavailable')
    result=checked([str(checker),str(cert),str(ota)],capture_output=True,text=True,timeout=1800)
    if result.returncode:raise ValueError('hosted OTA package/payload signature verification failed')
    out=Path(out);out.mkdir(mode=0o700)
    # Preserve the signed output and avoid a third physical OTA copy.
    os.link(ota,out/ota.name,follow_symlinks=False)
    (out/'mustang-stable').write_text(f'{build} {epoch} mustang stable\n')
    baseline_raw=(json.dumps(baseline,indent=2,sort_keys=True)+'\n').encode()
    receipt=dict(schema='roseos.mustang-ota-stage.v1',device='mustang',build=build,timestamp=epoch,
        candidateSource=compile_source,recipeSource=bundle['sourceSha'],runId=run,runAttempt=attempt,
        compileRunId=compile_run,compileRunAttempt=compile_attempt,label=LABEL,contentFingerprint=fingerprint(compile_source),
        roseEdge=dict(unsigned=unsigned_proof,signed=signed_proof),
        edgeTargetFiles=dict(unsigned=unsigned_archive,signed=signed_archive),
        baselineObservation=baseline,baselineObservationSha256=hashlib.sha256(baseline_raw).hexdigest(),
        baselineEvidenceKind='candidate-installer-continuity',baselineBuild=baseline['build'],
        packagedUpdater=observed,otaMetadata=metadata,publicIdentity=pins,
        checks=dict(targetVersion=True,baselineTrust=True,otaSignatures=True,packagedUpdater=True,roseEdge=True),
        verificationTools=dict(checkOtaSignatureSha256=digest(checker),deltaGeneratorSha256=digest(binary/'delta_generator')),
        phoneUpdatePassed=False,currentPhone=False,automationDemonstrated=False,sprintOmissions=[],
        builder=lane['vm'],workflow=lane['workflow'],ota=dict(file=ota.name,sizeBytes=ota.stat().st_size,sha256=digest(ota)))
    if 'clockAllocation' in bundle:
        receipt['clockAllocation']=bundle['clockAllocation']
    if bundle.get('retainedProducer') is not None:
        receipt['retainedProducer']=bundle['retainedProducer']
    release_receipts.write_once(out/'mustang-ota-receipt.json',receipt)
    (out/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in sorted(out.iterdir())))
    verify_staged(out,cfg,publication=False)
    return receipt


def verify_staged(directory, cfg, publication=True):
    guard(cfg,publication=publication)
    directory = Path(directory)
    r = read(directory/'mustang-ota-receipt.json')
    if r.get('schema') != 'roseos.mustang-ota-stage.v1' or r.get('device') != 'mustang' \
            or r.get('checks') != dict(targetVersion=True, baselineTrust=True, otaSignatures=True,
                                      packagedUpdater=True, roseEdge=True) \
            or any(v is not True for v in r['checks'].values()) \
            or r.get('publicIdentity') != identities() or r.get('baselineBuild') != cfg['baseline']['installedBuild']:
        raise ValueError('complete Mustang OTA-specific verification required')
    runtime=read(ROOT/'os/build-only/mustang-runtime.json')
    if r.get('clockAllocation') is not None or (runtime.get('status')=='bound' and runtime.get('clockNumbering') is not None):
        import mustang_clock, mustang_lane
        mustang_clock.require_bundle(dict(clockAllocation=r.get('clockAllocation'),build=r['build'],timestamp=r['timestamp'],
            runId=r['runId'],runAttempt=r['runAttempt'],sourceSha=r['recipeSource'],
            **({'retainedProducer':r['retainedProducer']} if r.get('retainedProducer') is not None else {})),
            mustang_lane.configuration(),runtime,ROOT)
    factory=r.get('baselineFactoryEvidence')
    if r.get('baselineObservation') is not None:
        baseline=require_staged_baseline(r['baselineObservation'])
        raw=(json.dumps(baseline,indent=2,sort_keys=True)+'\n').encode()
        if hashlib.sha256(raw).hexdigest()!=r.get('baselineObservationSha256'):
            raise ValueError('embedded candidate baseline receipt hash differs')
    selected=read(ROOT/'os/build-only/mustang-runtime.json').get('baselineFactoryEvidence') is not None
    if selected or factory is not None:
        if not isinstance(factory,dict): raise ValueError('selected factory baseline proof missing')
        require_factory_baseline(factory)
        if factory.get('baselineSource')!=cfg['baseline']['source']:
            raise ValueError('factory baseline original candidate differs')
        if any(factory.get(k)!=r.get(k) for k in ('runId','runAttempt','recipeSource')):
            raise ValueError('factory baseline and staged OTA run/source differ')
        encoded=(json.dumps(factory,indent=2,sort_keys=True)+'\n').encode()
        if hashlib.sha256(encoded).hexdigest()!=r.get('baselineObservationSha256'):
            raise ValueError('embedded factory baseline receipt hash differs')
    mustang_edge.require_pair(r)
    if r.get('label') != LABEL or not all(re.fullmatch(r'[0-9a-f]{40}', str(r.get(k,'')))
                                         for k in ('candidateSource','recipeSource')) \
            or not re.fullmatch(r'[1-9][0-9]*', str(r.get('runId',''))):
        raise ValueError('candidate label, source or producer run identity differs')
    build, epoch = r['build'], r['timestamp']
    if not re.fullmatch(r'[0-9]{10}', build) or not re.fullmatch(r'[0-9]+', epoch) \
            or r['ota']['file'] != f'mustang-ota_update-{build}.zip':
        raise ValueError('Mustang OTA asset identity differs')
    if int(build) <= int(cfg['baseline']['build']) or int(epoch) <= int(cfg['baseline']['timestamp']):
        raise ValueError('publication cannot advertise a non-newer build')
    if set(p.name for p in directory.iterdir()) != {r['ota']['file'], 'mustang-stable', 'mustang-ota-receipt.json', 'SHA256SUMS'}:
        raise ValueError('unexpected public asset set')
    if (directory/'mustang-stable').read_text() != f'{build} {epoch} mustang stable\n':
        raise ValueError('channel metadata does not describe the exact OTA')
    sums = (directory/'SHA256SUMS').read_text().splitlines()
    expected = {r['ota']['file'], 'mustang-stable', 'mustang-ota-receipt.json'}
    names = []
    for line in sums:
        parts = line.split('  ')
        if len(parts) != 2 or parts[1] not in expected or not re.fullmatch(r'[0-9a-f]{64}', parts[0]):
            raise ValueError('downloaded checksum manifest differs')
        names.append(parts[1])
        if digest(directory/parts[1]) != parts[0]:
            raise ValueError('downloaded staged asset checksum differs')
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError('downloaded checksum manifest is incomplete or ambiguous')
    checked_file(dict(path=str(directory/r['ota']['file']), **{k:r['ota'][k] for k in ('sha256', 'sizeBytes')}))
    return r


def release_rows(client):
    rows = []
    for page in range(1, 4):
        part = client._request('GET', f'{client.api}/repos/{client.repository}/releases?per_page=100&page={page}', 'list releases')[2]
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise ValueError('release listing exceeds updater discovery bound; publication stopped')


def publish(client, directory, cfg, out):
    """No existing release/asset is modified; metadata added only after OTA readback."""
    directory, out = Path(directory), Path(out)
    r = verify_staged(directory, cfg)
    out.mkdir(mode=0o700)
    tag = 'mustang-'+r['build']
    names = [r['ota']['file'], 'mustang-ota-receipt.json', 'SHA256SUMS', 'mustang-stable']
    if not tag.startswith('mustang-') or any(tag.startswith(prefix) for prefix in ('frankel-', 'blazer-', 'rango-')) \
            or any(name.startswith(('frankel', 'blazer', 'rango')) for name in names):
        raise ValueError('publication is not a Mustang release')
    before = client.latest_tag()
    releases = release_rows(client)
    if len(releases) >= 299:
        raise ValueError('adding a release would exceed updater discovery completeness bound')
    for row in releases:
        match = re.fullmatch(r'mustang-([0-9]{10})', row.get('tag_name', ''))
        if match and int(match[1]) >= int(r['build']):
            raise ValueError('Mustang release/draft already consumes this or a newer version; no mutation')
    state = {'schema': 'roseos.mustang-ota-publication.v1', 'device': 'mustang', 'build': r['build'],
             'tag': tag, 'source': r['candidateSource'], 'contentFingerprint': r['contentFingerprint'],
             'recipeSource': r['recipeSource'], 'producerRun': r['runId'],
             'publicationRun': os.environ.get('GITHUB_RUN_ID'), 'stageReceiptSha256': digest(directory/'mustang-ota-receipt.json'),
             'status': 'intent', 'public': False, 'latestBefore': before, 'phoneUpdatePassed': False}
    def record():
        with (out/'journal.jsonl').open('a') as f:
            f.write(json.dumps(state)+'\n')
    record()
    try:
        release = client.create_draft(tag, tag, LABEL+'\n\nSource: '+r['candidateSource']+
                                      '\nContent fingerprint: '+r['contentFingerprint']+
                                      '\nOTA-specific checks only; see mustang-ota-receipt.json.')
        state.update(releaseId=release['id'], status='draft-created')
        record()
        # The channel asset is deliberately last and the release is still a draft.
        # Frankel, Blazer and Rango releases are never upload targets.
        for name in names:
            client.upload(release['id'], directory/name)
            current = client.get_release(release['id'])
            matches = [x for x in current.get('assets', []) if x['name'] == name and x.get('state') == 'uploaded']
            if len(matches) != 1 or matches[0]['size'] != (directory/name).stat().st_size:
                raise ValueError('uploaded asset is missing, ambiguous or incomplete')
            fetched = out/name
            client.download(matches[0]['id'], fetched, matches[0]['size'])
            if digest(fetched) != digest(directory/name):
                raise ValueError('uploaded asset readback differs; draft remains unadvertised')
            state.update(status='uploaded-and-readback', lastAsset=name)
            record()
        if {a['name'] for a in current['assets']} != set(names) or client.latest_tag() != before:
            raise ValueError('draft asset set or Latest changed; no finalization')
        state.update(status='finalize-requested', public=None)
        record()
        finalized = client.finalize_public(release['id'])
        if finalized.get('draft') is not False or finalized.get('prerelease') is not False:
            raise ValueError('public finalization result unclear; inspect before any retry')
        state.update(public=True, status='public-download-pending')
        record()
        for name in names:
            url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
            fetched = out/('public-'+name)
            client.public_download(url, fetched, (directory/name).stat().st_size)
            if digest(fetched) != digest(directory/name):
                raise ValueError('public asset readback differs; inspect existing publication, do not retry')
        if client.latest_tag() != before:
            raise ValueError('repository Latest changed during Mustang publication')
        state.update(status='ota-published', releaseUrl=finalized.get('html_url', f'https://github.com/{REPOSITORY}/releases/tag/{tag}'),
                     otaUrl=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{r["ota"]["file"]}',
                     metadataVersion=r['build'], channel='stable',
                     ota=r['ota'], metadata=(directory/'mustang-stable').read_text().strip(),
                     publicDownloadVerified=True, latestAfter=before)
        return state
    except BaseException:
        state['status'] = 'failed-or-uncertain'
        raise
    finally:
        record()
        release_receipts.write_once(out/'publication.json', state)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('guard', 'observe', 'stage', 'verify-staged', 'publish'))
    p.add_argument('--out'); p.add_argument('--baseline'); p.add_argument('--sign-run'); p.add_argument('--attempt', default='1')
    p.add_argument('--dir')
    a = p.parse_args()
    try:
        if a.action != 'verify-staged':
            mustang_lane.reject_legacy()
        if a.action == 'guard':
            guard(config(), access=True)
        elif a.action == 'observe':
            observe_baseline(a.out)
        elif a.action == 'stage':
            stage(a.sign_run, a.attempt, a.baseline, a.out)
        elif a.action in ('verify-staged', 'publish'):
            r = verify_staged(a.dir, config())
            if r['recipeSource'] != os.environ['GITHUB_SHA'] or r['runId'] != os.environ['GITHUB_RUN_ID']:
                raise ValueError('staged OTA recipe/run differs from this same-run publisher')
            if a.action == 'publish':
                client = publisher.GitHubReleases(REPOSITORY, os.environ.get('PIXEL_OTA_TOKEN', ''))
                print(json.dumps(publish(client, a.dir, config(), a.out)))
    except (ValueError, OSError, KeyError, publisher.PublishError, subprocess.SubprocessError) as error:
        print(f'Mustang OTA stopped: {type(error).__name__}: {error if isinstance(error, (ValueError, publisher.PublishError)) else "operation failed; inspect the retained receipt"}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
