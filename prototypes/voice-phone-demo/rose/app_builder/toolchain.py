"""Build and sign one generated app with the repository's pinned toolchain.

The build mirrors workspace/pixel-calculator-agent/build.sh (aapt v1 -> javac -source 8 -> d8 -> zipalign) with the
identity injected by the builder: the package is checked, and versionCode, versionName and SDK levels are set at
packaging. Every tool runs with a scrubbed environment (no inherited tokens, HOME in the job's own directory), a
timeout and cooperative cancellation. Generated code is compiled, never executed.

Signing is scripts/sign-apk.py with the project's own key, always with the project's certificate pin once one
exists, so a revision can never change identity. A project key is created only when the host owner has explicitly
enabled key creation; otherwise signing stops with a clear category and no key is made.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import template

REPO = Path(__file__).resolve().parents[4]
SIGN_APK = REPO / 'scripts/sign-apk.py'


class StepFailed(Exception):
    """A build step that failed, with a failure category and an owner-safe summary."""

    def __init__(self, category: str, summary: str, retryable: bool = False, log: str = ''):
        super().__init__(summary)
        self.category, self.summary, self.retryable, self.log = category, summary, retryable, log


@dataclass
class Tools:
    sdk: Path
    java_home: Path | None = None
    timeout_s: float = 600.0
    key_creation: bool = False  # set only by the host owner; never by a request

    @property
    def bt(self) -> Path:
        return self.sdk / 'build-tools/android-15'

    @property
    def android_jar(self) -> Path:
        return self.sdk / 'platforms/android-35/android.jar'

    def java(self) -> str:
        return str(self.java_home / 'bin/java') if self.java_home else (shutil.which('java') or 'java')

    def path(self) -> str:
        """PATH for tools: java_home's bin first, so d8 and apksigner (java wrappers) use the configured JDK."""
        java = Path(self.java()).parent if Path(self.java()).is_absolute() else None
        jdk = f'{self.java_home}/bin:' if self.java_home else (f'{java}:' if java else '')
        return jdk + '/usr/bin:/bin:/usr/sbin:/sbin'

    def keytool(self) -> str:
        return str(self.java_home / 'bin/keytool') if self.java_home else (shutil.which('keytool') or 'keytool')

    def available(self) -> str | None:
        """None when the toolchain is present, else what is missing (category toolchain)."""
        for path in (self.bt / 'aapt', self.bt / 'd8', self.bt / 'zipalign', self.bt / 'apksigner', self.android_jar):
            if not path.exists():
                return f'The pinned Android toolchain is missing {path.name}.'
        for tool in (self.java(), self.keytool()):
            if not (Path(tool).is_absolute() and Path(tool).exists()):
                return f'The builder\'s JDK is missing {Path(tool).name}.'
        return None


def _redact(text: str, *paths: Path) -> str:
    for path in paths:
        text = text.replace(str(path), '<' + path.name + '>')
    return text  # whole: the identity checks parse it; the ledger keeps only a tail for display


def run(args: list, cwd: Path, home: Path, tools: Tools, cancelled, category: str, redact=()) -> str:
    """One tool with a scrubbed environment, a timeout and cancellation; raises StepFailed."""
    env = {'PATH': tools.path(), 'HOME': str(home), 'LANG': 'C.UTF-8', 'TMPDIR': str(home)}
    if tools.java_home:
        env['JAVA_HOME'] = str(tools.java_home)
    proc = subprocess.Popen([str(a) for a in args], cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors='replace')
    deadline = time.monotonic() + tools.timeout_s
    while True:
        try:
            out, _ = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancelled():
                proc.kill()
                proc.communicate()
                raise StepFailed('cancelled', 'Cancelled.')
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise StepFailed('timeout', f'{Path(str(args[0])).name} took longer than {int(tools.timeout_s)} s.',
                                 retryable=True)
    out = _redact(out or '', *redact)
    if proc.returncode:
        raise StepFailed(category, f'{Path(str(args[0])).name} failed.', log=out)
    return out


def build(source: Path, work: Path, package: str, version_code: int, version_name: str, tools: Tools,
          cancelled=lambda: False) -> tuple[Path, str]:
    """An unsigned, aligned APK for this exact identity, and the build log."""
    missing = tools.available()
    if missing:
        raise StepFailed('toolchain', missing, retryable=True)
    try:
        template.check(source, package)
    except template.TemplateRefused as refused:
        raise StepFailed('dependency' if refused.dependency else 'compile', str(refused)) from None
    for d in ('classes', 'generated', 'dex', 'home'):
        (work / d).mkdir(parents=True, exist_ok=True)
    home, log = work / 'home', []
    hidden = (source, work, tools.sdk)
    aapt = [tools.bt / 'aapt', 'package', '-f', '-M', source / 'AndroidManifest.xml', '-S', source / 'res',
            '-I', tools.android_jar, '-F', work / 'resources.apk', '-J', work / 'generated',
            '--min-sdk-version', str(template.MIN_SDK), '--target-sdk-version', str(template.TARGET_SDK),
            '--version-code', str(version_code), '--version-name', version_name]
    if (source / 'assets').is_dir():
        aapt[5:5] = ['-A', source / 'assets']
    log.append(run(aapt, work, home, tools, cancelled, 'compile', hidden))
    sources = sorted(str(p) for p in [*source.glob('src/**/*.java'), *(work / 'generated').rglob('*.java')])
    try:
        log.append(run([tools.java(), '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-encoding', 'UTF-8', '-source',
                        '8', '-target', '8', '-Xlint:-options', '-proc:none', '-bootclasspath',
                        f'{tools.android_jar}:{tools.bt / "core-lambda-stubs.jar"}', '-d', work / 'classes',
                        *sources], work, home, tools, cancelled, 'compile', hidden))
    except StepFailed as failed:
        # An import of a library the framework-only toolchain lacks (androidx, kotlin, ...) is a dependency failure.
        if failed.category == 'compile' and re.search(r'error: package [\w.]+ does not exist', failed.log):
            raise StepFailed('dependency', 'The app needs a library this builder does not provide (it builds with '
                             'Android framework APIs only).', log=failed.log) from None
        raise
    classes = sorted(str(p) for p in (work / 'classes').rglob('*.class'))
    own = package.replace('.', '/') + '/'
    for c in classes:  # whatever the source text claimed, compiled classes live in the app's own package
        if not Path(c).relative_to(work / 'classes').as_posix().startswith(own):
            raise StepFailed('compile', f'Classes must be in package {package}.')
    log.append(run([tools.bt / 'd8', '--min-api', str(template.MIN_SDK), '--lib', tools.android_jar, '--output',
                    work / 'dex', *classes], work, home, tools, cancelled, 'compile', hidden))
    unaligned = work / 'unaligned.apk'
    shutil.copyfile(work / 'resources.apk', unaligned)
    import zipfile
    with zipfile.ZipFile(unaligned, 'a', zipfile.ZIP_DEFLATED) as z:
        for dex in sorted((work / 'dex').glob('classes*.dex')):
            z.write(dex, dex.name)
    unsigned = work / 'unsigned.apk'
    run([tools.bt / 'zipalign', '-f', '4', unaligned, unsigned], work, home, tools, cancelled, 'compile', hidden)
    badging = run([tools.bt / 'aapt', 'dump', 'badging', unsigned], work, home, tools, cancelled, 'compile', hidden)
    found = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'(.*)$", badging, re.M)
    if not found or found.groups()[:3] != (package, str(version_code), version_name) or 'split=' in found.group(4):
        raise StepFailed('internal', 'The built APK does not carry the identity the builder set.')
    # Checked again on the built binary, by attribute id, whatever namespace prefix the source used.
    tree = run([tools.bt / 'aapt', 'dump', 'xmltree', unsigned, 'AndroidManifest.xml'], work, home, tools, cancelled,
               'compile', hidden)
    # sharedUserId, debuggable, sharedUserLabel, versionCodeMajor; and identity elements by name.
    if ('application-debuggable' in badging or re.search(r'\((0x0101000b|0x0101000f|0x01010261|0x01010576|0x0101054c)\)', tree)
            or re.search(r'E: (key-sets|upgrade-key-set|original-package|adopt-permissions)\b', tree)):
        raise StepFailed('compile', 'The app tries to set its own identity, debuggability or update keys, '
                         'which is not allowed.')
    problem = binary_permission_problem(tree, package)
    if problem:
        raise StepFailed('compile', problem)
    return unsigned, '\n'.join(x for x in log if x)


PERMISSION_IDS = {'0x01010006', '0x01010007', '0x01010008'}  # android:permission, readPermission, writePermission


def binary_permission_problem(tree: str, package: str) -> str | None:
    """The capability policy (template.permission_problem) applied again to ``aapt dump xmltree`` of the built
    manifest, by attribute id: requested permissions and the permissions components require of their callers."""
    element = None
    for line in tree.splitlines():
        opened = re.match(r'\s*E: (\S+)', line)
        if opened:
            element = opened.group(1)
            continue
        attribute = re.match(r'\s*A: [^(]*\((0x[0-9a-f]{8})\)=(.*)$', line)
        if not attribute:
            continue
        attr_id, raw = attribute.groups()
        if element == 'permission' and attr_id == '0x01010003':  # declarations only in the app's own namespace
            value = re.match(r'"([^"]*)"', raw)
            if not value or not value.group(1).startswith(package + '.'):
                return f'An app may declare only its own permissions ({package}.*).'
            continue
        requested = element in ('uses-permission', 'uses-permission-sdk-23', 'uses-permission-sdk-m') \
            and attr_id == '0x01010003'
        if not requested and attr_id not in PERMISSION_IDS:
            continue
        value = re.match(r'"([^"]*)"', raw)  # anything but a plain string (a reference, a number) is refused
        problem = template.permission_problem(value.group(1) if value else None, package, component=not requested)
        if problem:
            return problem
    return None


def _private(path: Path, data: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def fsync_path(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_key(keys: Path, package: str, tools: Tools, cancelled=lambda: False, signer_bound: bool = False) -> Path:
    """The project's signing config; a new key only when the host owner enabled key creation.

    config.json is written last, so without it any key material is an interrupted creation. It is cleared and made
    again only while the project has no signer; once one is bound, a missing key is never silently replaced."""
    config = keys / 'config.json'
    if config.exists():
        return config
    if signer_bound:
        raise StepFailed('signing', 'This app\'s signing key is missing, so it cannot be updated in place; '
                         'nothing was signed.')
    if not tools.key_creation:
        raise StepFailed('signing', 'This project has no signing key, and creating keys is not enabled on this '
                         'server, so nothing was signed.')
    if keys.exists():
        shutil.rmtree(keys)
    keys.mkdir(mode=0o700, parents=True)
    os.chmod(keys, 0o700)
    password = keys / 'store.pass'
    _private(password, secrets.token_urlsafe(32) + '\n')
    run([tools.keytool(), '-genkeypair', '-alias', 'app', '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10950',
         '-storetype', 'PKCS12', '-keystore', keys / 'app.p12', '-storepass:file', password,
         '-dname', f'CN={package}'], keys, keys, tools, cancelled, 'signing', (keys,))
    os.chmod(keys / 'app.p12', 0o600)
    fsync_path(keys / 'app.p12')
    # Names relative to the key directory, so a moved or restored config directory still signs.
    _private(config, json.dumps({'keyAlias': 'app', 'storePasswordFile': password.name,
                                 'keyPasswordFile': password.name}))
    fsync_path(keys)  # the key is on disk before any build signed with it can be delivered
    fsync_path(keys.parent)
    return config


def sign(unsigned: Path, output: Path, keys: Path, expected_signer: str | None, tools: Tools) -> dict:
    """Sign with the project's key; the certificate must equal expected_signer when the project already has one."""
    config = keys / 'config.json'
    names = json.loads(config.read_text())
    store = keys / Path(names['storePasswordFile']).name
    key = keys / Path(names['keyPasswordFile']).name
    args = [sys.executable, SIGN_APK, unsigned, '--keystore', keys / 'app.p12', '--config', config,
            '--store-password-file', store, '--key-password-file', key, '--output', output]
    if expected_signer:
        args += ['--expected-cert-sha256', expected_signer]
    env = {'PATH': tools.path(), 'ANDROID_SDK_ROOT': str(tools.sdk), 'LANG': 'C.UTF-8'}
    if tools.java_home:
        env['JAVA_HOME'] = str(tools.java_home)
    try:
        result = subprocess.run([str(a) for a in args], capture_output=True, text=True, env=env,
                                timeout=tools.timeout_s)
    except subprocess.TimeoutExpired:
        raise StepFailed('timeout', f'Signing took longer than {int(tools.timeout_s)} s.', retryable=True) from None
    if result.returncode:
        category = (result.stderr.strip().rsplit(': ', 1) + [''])[1] or 'signing_failed'
        summary = ('The build was signed by a different key than this app, so it was not delivered.'
                   if category == 'certificate_pin_mismatch' else f'Signing stopped ({category}).')
        raise StepFailed('signing', summary)
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    certs = receipt.get('certificateSha256') or []
    if len(certs) != 1:
        raise StepFailed('signing', 'The signed APK does not have exactly one signer.')
    data = output.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'signerSha256': certs[0]}
