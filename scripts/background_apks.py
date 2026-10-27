"""Shared APK identity verification for the background-control preparation tools."""
import hashlib
from pathlib import Path
import re
import subprocess

AGENT = 'dev.pixel.backgroundcontrol'
VOICE = 'dev.pixel.voice'
EXCLUDED = {AGENT, VOICE, 'android', 'com.android.permissioncontroller', 'com.google.android.permissioncontroller'}
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+')
DIGEST = re.compile(r'[A-F0-9]{64}')

def identity(apk, aapt, apksigner, sdk):
    apk = Path(apk).resolve(strict=True)
    badging = subprocess.check_output([str(aapt), 'dump', 'badging', str(apk)], text=True)
    match = re.search(r"^package: name='([^']+)'", badging, re.M)
    if not match or not PACKAGE.fullmatch(match[1]):
        raise ValueError(f'No valid package identity in {apk.name}')
    output = subprocess.check_output([str(apksigner), 'verify', '--print-certs',
        '--min-sdk-version', str(sdk), '--max-sdk-version', str(sdk), str(apk)], text=True)
    certs = set()
    for line in output.splitlines():
        found = re.match(r'Signer (?:#\d+|\(minSdkVersion=(\d+), maxSdkVersion=(\d+)\)) certificate SHA-256 digest: ([0-9a-fA-F]{64})', line)
        if found and (found[1] is None or int(found[1]) <= sdk <= int(found[2])):
            certs.add(found[3].upper())
    if not certs:
        raise ValueError(f'No verified signer for target SDK {sdk}: {apk.name}')
    return {'packageName': match[1], 'certificates': sorted(certs),
        'apkSha256': hashlib.sha256(apk.read_bytes()).hexdigest()}

def validate_policy(policy, apk):
    if policy.get('schemaVersion') != 1 or not isinstance(policy.get('sdk'), int) or policy['sdk'] < 1:
        raise ValueError('Unsupported background policy schema or SDK')
    agent, voice, targets = policy['agent'], policy['voice'], policy['targets']
    if agent['packageName'] != AGENT or voice['packageName'] != VOICE:
        raise ValueError('Policy must identify Background Control and Rose Agent')
    if not isinstance(policy.get('prototypeControl', False), bool):
        raise ValueError('prototypeControl must be a boolean')
    if not targets and not policy.get('prototypeControl', False):
        raise ValueError('At least one exact target is required')
    names = set()
    for row in [agent, voice, *targets]:
        name = row['packageName']
        if not PACKAGE.fullmatch(name) or name in names:
            raise ValueError('Invalid or duplicate package identity')
        names.add(name)
        certs = row['certificates']
        if not certs or len(certs) != len(set(certs)) or any(not DIGEST.fullmatch(c) for c in certs):
            raise ValueError('Only exact uppercase SHA-256 certificate pins are accepted')
    if agent['certificates'] != voice['certificates']:
        raise ValueError('Agent and Rose Agent must have the same signing identity')
    if any(t['packageName'] in EXCLUDED for t in targets):
        raise ValueError('Agent, Rose Agent and permission controllers cannot be targets')
    if hashlib.sha256(Path(apk).read_bytes()).hexdigest() != agent['apkSha256']:
        raise ValueError('Agent APK differs from the APK verified in the policy receipt')
    return policy

def preinstall_plan(policy, apks):
    """Map APKs to product preinstalls; each must be byte-identical to Rose Agent or a policy target."""
    pinned = {row['apkSha256']: row for row in [policy['voice'], *policy['targets']] if row.get('apkSha256')}
    plan, names = [], set()
    for apk in apks:
        row = pinned.get(hashlib.sha256(Path(apk).read_bytes()).hexdigest())
        if row is None:
            raise ValueError(f'{Path(apk).name} is not Rose Agent or a target APK verified in the policy receipt')
        if row['packageName'] in names:
            raise ValueError(f'{row["packageName"]} is supplied more than once')
        names.add(row['packageName'])
        plan.append({'module': 'RosePreinstall_' + row['packageName'].replace('.', '_'),
            'packageName': row['packageName'], 'apk': Path(apk)})
    return sorted(plan, key=lambda p: p['module'])

def preinstall_blueprint(module):
    # Presigned keeps the existing app signer; not privileged, so no privapp allowlist is needed.
    return ('package { default_applicable_licenses: ["Android-Apache-2.0"] }\n'
        'android_app_import {\n'
        f'    name: "{module}",\n'
        '    apk: "app.apk",\n'
        '    presigned: true,\n'
        '    product_specific: true,\n'
        '    dex_preopt: { enabled: false },\n'
        '}\n')

def preinstall_make_block(modules):
    return ('\n# Rose Agent and allowed targets preinstalled from the exact APKs pinned by the background policy.\n'
        'PRODUCT_PACKAGES += ' + ' '.join(modules) + '\n')
