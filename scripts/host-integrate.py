#!/usr/bin/env python3
"""os-release host.integrateCommand: sign the selected apps, then integrate the exact candidate.

Invoked by release-pipeline.py on the builder with cwd at the Android source root. Order:
1. check out the exact candidate commit as a worktree of this trusted checkout;
2. build the unsigned apps from it with the lead-supplied recipe (real public connection assets);
3. check each APK's package and versionCode against the dispatch inputs (APPS);
4. sign them with scripts/sign-apk.py and the existing app identity: the four pinned
   PIXEL_APP_SIGNING_* versions are read with the job's short-lived signer identity into an owner-only
   RAM directory and removed on success or failure; the signer pin must match;
5. write the SDK 37 background policy from the signed bytes (prepare-background-policy.py);
6. reconcile the candidate into the retained tree with the lead-supplied recipe;
7. record `repo manifest -r` as resolved-manifest.xml.
Steps whose source-specific recipe has not been supplied fail closed (exit 4) and name the config key.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ('keystore', 'config', 'storePassword', 'keyPassword')

def expand(part, values):
    for key, value in values.items():
        part = part.replace('{' + key + '}', value)
    return part

def run(argv, **kwargs):
    result = subprocess.run(argv, **kwargs)
    if result.returncode:
        raise AdapterFailed(f'{Path(argv[0]).name} exited {result.returncode}')
    return result

def badging(aapt, apk):
    out = subprocess.run([aapt, 'dump', 'badging', str(apk)], capture_output=True, text=True).stdout
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)'", out, re.M)
    if not match:
        raise AdapterFailed(f'no package identity in {apk.name}')
    return match[1], int(match[2])

def missing_inputs(config):
    host = config['host']
    integrate = host.get('integrate') or {}
    missing = [f'host.integrate.{k}' for k in ('project', 'secretVersion', 'appSdkRoot', 'appBuildCommand',
        'unsignedApks', 'reconcileCommand') if not integrate.get(k)]
    missing += [f'host.integrate.appSecrets.{k}' for k in SECRETS if not (integrate.get('appSecrets') or {}).get(k)]
    return missing + [f'host.tools.{k}' for k in ('aapt', 'apksigner') if not host['tools'].get(k)]

def main(config_path=ROOT/'os/release-pipeline/frankel.json', env=os.environ, gcloud='gcloud'):
    config = json.loads(Path(config_path).read_text())
    host = config['host']
    integrate = host.get('integrate') or {}
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    sha, release_dir = env['ROSE_CANDIDATE_SHA'], Path(env['ROSE_RELEASE_DIR'])
    expected = {package: version for package, version in json.loads(env.get('APPS', '[]'))}
    roles = integrate['unsignedApks']  # {"voice" | "agent" | "target:<package>": unsigned APK path template}
    if not {'voice', 'agent'} <= set(roles) or not all(r in ('voice', 'agent') or r.startswith('target:') for r in roles):
        raise AdapterFailed('host.integrate.unsignedApks needs voice, agent and target:<package> roles')
    candidate = release_dir/'candidate'
    run(['git', '-C', str(ROOT), 'worktree', 'add', '--detach', str(candidate), sha], stdout=subprocess.DEVNULL)
    try:
        if subprocess.run(['git', '-C', str(candidate), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip() != sha:
            raise AdapterFailed('candidate worktree is not at the selected commit')
        values = {'candidate': str(candidate), 'release_dir': str(release_dir), 'build': env['BUILD_NUMBER'],
            'python': sys.executable, 'checkout': str(ROOT)}
        run([expand(p, values) for p in integrate['appBuildCommand']], cwd=str(candidate), env=dict(env))
        aapt, apksigner = (str(host_path(config, host['tools'][k])) for k in ('aapt', 'apksigner'))
        signed_dir = release_dir/'signed-apps'
        signed_dir.mkdir()
        signed = {}
        with host_secrets.private_directory('rose-app-signing-') as private:
            files = {k: host_secrets.fetch_secret(integrate['project'], integrate['appSecrets'][k], integrate['secretVersion'],
                private/k, gcloud) for k in SECRETS}
            for role, template in sorted(roles.items()):
                apk = Path(expand(template, values))
                package, version = badging(aapt, apk)
                if expected.get(package) != version:
                    raise AdapterFailed(f'{package} versionCode {version} differs from the dispatch input')
                output = signed_dir/f'{package}.apk'
                run([sys.executable, str(ROOT/'scripts/sign-apk.py'), str(apk), '--keystore', str(files['keystore']),
                    '--config', str(files['config']), '--store-password-file', str(files['storePassword']),
                    '--key-password-file', str(files['keyPassword']), '--output', str(output),
                    '--expected-cert-sha256', config['identities']['appSignerSha256']],
                    env=dict(env, ANDROID_SDK_ROOT=integrate['appSdkRoot']), stdout=subprocess.DEVNULL)
                if role.startswith('target:') and role.split(':', 1)[1] != package:
                    raise AdapterFailed(f'{role} built {package}')
                signed[role] = output
            if {badging(aapt, p)[0] for p in signed.values()} != set(expected):
                raise AdapterFailed('signed apps differ from the dispatch input APPS')
        targets = [str(p) for r, p in sorted(signed.items()) if r.startswith('target:')]
        run([sys.executable, str(ROOT/'scripts/prepare-background-policy.py'), '--agent-apk', str(signed['agent']),
            '--voice-apk', str(signed['voice']), *[x for t in targets for x in ('--target-apk', t)],
            # The signed controller as the sole prototype agent, only when the release config names it; the release
            # verifier then requires exactly that controller pinned to the app signer in the compiled framework.
            *(['--prototype-control'] if (config.get('framework') or {}).get('prototypeAgents') else []),
            '--sdk', str(integrate.get('sdk', 37)), '--aapt', aapt, '--apksigner', apksigner,
            '--output', str(release_dir/'background-policy.json')], stdout=subprocess.DEVNULL)
        values.update(policy=str(release_dir/'background-policy.json'), signed_apps=str(signed_dir))
        run([expand(p, values) for p in integrate['reconcileCommand']], cwd=os.getcwd(), env=dict(env))
        with open(release_dir/'resolved-manifest.xml', 'w') as out:
            run([integrate.get('repo', 'repo'), 'manifest', '-r'], cwd=os.getcwd(), stdout=out)
    finally:
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove', '--force', str(candidate)], capture_output=True)
    return 0

def host_path(config, value):
    return Path(value) if Path(value).is_absolute() else Path(config['host']['sourceRoot'])/value

if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--check']:
            missing = missing_inputs(json.loads((ROOT/'os/release-pipeline/frankel.json').read_text()))
            if missing:
                raise AdapterSetupMissing(missing)
            sys.exit(0)
        sys.exit(main())
    except AdapterSetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        sys.exit(4)
    except (AdapterFailed, OSError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error) if isinstance(error, AdapterFailed) else error.__class__.__name__}),
            file=sys.stderr)
        sys.exit(1)
