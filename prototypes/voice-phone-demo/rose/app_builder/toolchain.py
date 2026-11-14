"""Build and sign one generated app with the repository's pinned toolchain.

The build follows workspace/pixel-calculator-agent/build.sh (aapt v1 -> javac -> d8 -> zipalign), with Java at the
17 source level compiled against Android's own core library (``system_image``) and the identity injected by the
builder: the package is checked, and versionCode, versionName and SDK levels are set at
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
import stat
import subprocess
import sys
import time
import zipfile
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


JAVA_LEVEL = '17'
SYSTEM_RECIPE = 1  # bump when system_image builds differently, so an older cached image is never reused


@dataclass
class Tools:
    sdk: Path
    java_home: Path | None = None
    timeout_s: float = 600.0
    key_creation: bool = False  # set only by the host owner; never by a request
    cache: Path | None = None  # derived toolchain files (the Java system image); the Builder sets its own

    @property
    def bt(self) -> Path:
        return self.sdk / 'build-tools/android-15'

    @property
    def android_jar(self) -> Path:
        return self.sdk / 'platforms/android-35/android.jar'

    @property
    def core_modules(self) -> Path:
        """Android's core library as the platform ships it for compilers (java.*, javax.* and friends at API 35)."""
        return self.sdk / 'platforms/android-35/core-for-system-modules.jar'

    def jdk(self) -> Path:
        """The JDK the java launcher belongs to."""
        return Path(self.java()).resolve().parent.parent

    def jdk_tool(self, name: str) -> Path:
        return self.jdk() / 'bin' / name

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
        if not self.core_modules.exists():
            return f'The pinned Android toolchain is missing {self.core_modules.name}.'
        for tool in (self.java(), self.keytool()):
            if not (Path(tool).is_absolute() and Path(tool).exists()):
                return f'The builder\'s JDK is missing {Path(tool).name}.'
        for path in (self.jdk_tool('jmod'), self.jdk_tool('jlink'), self.jdk() / 'lib/jrt-fs.jar', self.jdk() / 'release'):
            if not path.exists():
                return f'The builder\'s JDK is missing {path.name}.'
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path) -> str | None:
    """sha256 over every file's relative path and content, in order; None if the tree holds anything but plain
    files and directories (a link is never trusted)."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            mode = os.lstat(path).st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                return None
            h.update(path.relative_to(root).as_posix().encode() + b'\0' + _sha256(path).encode() + b'\n')
    return h.hexdigest()


def jdk_identity(tools: Tools) -> dict:
    """The JDK's version, feature release and jmod target platform, from its own ``release`` file."""
    values = {}
    for line in (tools.jdk() / 'release').read_text(encoding='utf-8', errors='replace').splitlines():
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip().strip('"')
    version = values.get('JAVA_VERSION', '')
    os_name = {'linux': 'linux', 'darwin': 'macos', 'mac os x': 'macos'}.get(values.get('OS_NAME', '').lower())
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}.get(
        values.get('OS_ARCH', '').lower())
    if not re.fullmatch(r'\d+(\.\d+)*', version) or os_name is None or arch is None:
        raise StepFailed('toolchain', 'The builder\'s JDK does not say which version and platform it is.')
    return {'version': values.get('JAVA_RUNTIME_VERSION') or version, 'feature': version.split('.')[0],
            'platform': f'{os_name}-{arch}'}


def system_image(tools: Tools, cache: Path, cancelled=lambda: False) -> Path:
    """A Java system image of Android's core library (platform 35's core-for-system-modules.jar), the way the Android
    Gradle plugin makes one: a generated module-info exporting every package, then jmod and jlink, all with the
    pinned JDK. javac compiles against it with --system, so Java 17 source sees exactly the java.* APIs Android has.

    Cached under ``cache`` by a key over the recipe, the core jar's sha256 and the JDK identity, so a changed JDK or
    platform gets a new image and a stale one is never used. On every use the image's tree digest is checked against
    the one recorded when it was made; a missing, partial or changed image is discarded and made again (the build is
    deterministic). This guards against corruption and staleness, not against someone who can write the builder's
    own directory, which also holds the signing keys."""
    identity = {'recipe': SYSTEM_RECIPE, 'core': _sha256(tools.core_modules), 'jdk': jdk_identity(tools)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(os.lstat(cache).st_mode):
        raise StepFailed('toolchain', 'The builder\'s toolchain cache is not a plain directory.')
    final = cache / f'system-{key}'
    if _valid_image(final, identity):
        return final / 'image'
    _discard(final)
    staging = cache / f'.system-{key}-{secrets.token_hex(4)}'
    (staging / 'core').mkdir(parents=True)
    (staging / 'home').mkdir()
    try:
        with zipfile.ZipFile(tools.core_modules) as z:
            packages = set()
            for name in z.namelist():
                parts = name.split('/')
                if name.startswith('/') or '..' in parts or '\\' in name:
                    raise StepFailed('toolchain', 'The pinned Android core library has an unexpected entry.')
                if name.endswith('.class') and len(parts) > 1:
                    packages.add('.'.join(parts[:-1]))
            z.extractall(staging / 'core')
        (staging / 'mi').mkdir()
        (staging / 'mi/module-info.java').write_text(
            'module java.base {\n' + ''.join(f'  exports {p};\n' for p in sorted(packages)) + '}\n', encoding='utf-8')
        home, hide = staging / 'home', (staging, tools.sdk)
        run([tools.java(), '-m', 'jdk.compiler/com.sun.tools.javac.Main', '--system=none',
             f'--patch-module=java.base={staging / "core"}', '-d', staging / 'mi', staging / 'mi/module-info.java'],
            staging, home, tools, cancelled, 'toolchain', hide)
        shutil.copyfile(staging / 'mi/module-info.class', staging / 'core/module-info.class')
        (staging / 'jmods').mkdir()
        run([tools.jdk_tool('jmod'), 'create', '--module-version', identity['jdk']['feature'], '--target-platform',
             identity['jdk']['platform'], '--class-path', staging / 'core', staging / 'jmods/java.base.jmod'],
            staging, home, tools, cancelled, 'toolchain', hide)
        run([tools.jdk_tool('jlink'), '--module-path', staging / 'jmods', '--add-modules', 'java.base', '--output',
             staging / 'image', '--disable-plugin', 'system-modules'], staging, home, tools, cancelled, 'toolchain', hide)
        shutil.copyfile(tools.jdk() / 'lib/jrt-fs.jar', staging / 'image/lib/jrt-fs.jar')  # javac reads it via --system
        for scratch in ('core', 'mi', 'jmods', 'home'):
            shutil.rmtree(staging / scratch)
        digest = tree_digest(staging / 'image')
        _private(staging / 'manifest.json', json.dumps({'identity': identity, 'tree': digest}, sort_keys=True))
        try:
            os.rename(staging, final)
        except OSError:
            if not _valid_image(final, identity):  # another builder finished first; its image must still hold
                raise StepFailed('toolchain', 'The builder could not store its Java system image.', retryable=True)
            shutil.rmtree(staging, ignore_errors=True)
    except zipfile.BadZipFile:
        shutil.rmtree(staging, ignore_errors=True)
        raise StepFailed('toolchain', f'The pinned {tools.core_modules.name} is not a valid archive.') from None
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final / 'image'


def _plain(path: Path, kind) -> bool:
    try:
        return kind(os.lstat(path).st_mode)
    except OSError:
        return False


def _valid_image(final: Path, identity: dict) -> bool:
    """Whether ``final`` holds the image for ``identity``, unchanged since it was made. No link is followed: the
    entry, its manifest and its image must be a plain directory, file and directory."""
    if not (_plain(final, stat.S_ISDIR) and _plain(final / 'manifest.json', stat.S_ISREG)
            and _plain(final / 'image', stat.S_ISDIR)):
        return False
    try:
        recorded = json.loads((final / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(recorded, dict) and recorded.get('identity') == identity
            and tree_digest(final / 'image') == recorded.get('tree'))


def _discard(path: Path):
    """Remove a cache entry that is not valid, whatever it is (a link or file is unlinked, never followed)."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    try:
        if stat.S_ISDIR(mode):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError:
        raise StepFailed('toolchain', 'The builder could not clear a stale Java system image.',
                         retryable=True) from None


def missing_packages(log: str) -> list[str]:
    return sorted(set(re.findall(r'error: package ([\w.]+) does not exist', log)))


# Packages of the JDK itself: an app cannot add them with a library; Android either has them or not.
JDK_ONLY = ('java.', 'jdk.', 'sun.', 'com.sun.', 'javax.swing', 'javax.imageio', 'javax.sound', 'javax.management',
            'javax.naming', 'javax.print', 'javax.rmi', 'javax.script', 'javax.tools', 'javax.accessibility',
            'javax.smartcardio', 'javax.lang.model')


def build(source: Path, work: Path, package: str, version_code: int, version_name: str, tools: Tools,
          cancelled=lambda: False, libraries=()) -> tuple[Path, str]:
    """An unsigned, aligned APK for this exact identity, and the build log. ``libraries`` are verified job-local
    JARs (rose.app_builder.dependencies.resolve): javac compiles against them and d8 adds them to the app."""
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
    jars = [str(lib.jar) for lib in libraries]
    image = system_image(tools, tools.cache or work / 'system-cache', cancelled)
    hidden = (*hidden, image)
    try:
        # Java 17 source against Android's own core library: --system is that image, android.jar and the declared
        # libraries are the classpath, and string concatenation is compiled inline (Android has no
        # StringConcatFactory). d8 then desugars records, lambdas and the like for minSdk.
        log.append(run([tools.java(), '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-encoding', 'UTF-8', '--system',
                        image, '-source', JAVA_LEVEL, '-target', JAVA_LEVEL, '-XDstringConcat=inline',
                        '-Xlint:-options', '-proc:none', '-classpath', ':'.join([str(tools.android_jar), *jars]),
                        '-d', work / 'classes', *sources], work, home, tools, cancelled, 'compile', hidden))
    except StepFailed as failed:
        missing = missing_packages(failed.log) if failed.category == 'compile' else []
        if missing and all(p.startswith(JDK_ONLY) for p in missing):
            raise StepFailed('compile', 'The app uses Java APIs Android does not provide ('
                             + ', '.join(missing[:3]) + ').', log=failed.log) from None
        if failed.category == 'compile' and jars and re.search(r'error: cannot access|bad class file', failed.log):
            raise StepFailed('dependency', 'A library the app declares could not be read by the compiler.',
                             log=failed.log) from None
        # An import of a library the toolchain lacks (androidx, kotlin, an undeclared one) is a dependency failure.
        if failed.category == 'compile' and re.search(r'error: package [\w.]+ does not exist', failed.log):
            raise StepFailed('dependency', 'The app needs a library this builder does not provide (it builds with '
                             'Android framework APIs and the libraries it declares in dependencies.json, if the '
                             'builder has them).', log=failed.log) from None
        raise
    classes = sorted(str(p) for p in (work / 'classes').rglob('*.class'))
    own = package.replace('.', '/') + '/'
    for c in classes:  # whatever the source text claimed, compiled classes live in the app's own package
        if not Path(c).relative_to(work / 'classes').as_posix().startswith(own):
            raise StepFailed('compile', f'Classes must be in package {package}.')
    if jars:  # the libraries alone first, so a library d8 cannot convert is reported as the library's failure
        (work / 'libdex').mkdir(exist_ok=True)
        try:
            run([tools.bt / 'd8', '--min-api', str(template.MIN_SDK), '--lib', tools.android_jar, '--output',
                 work / 'libdex', *jars], work, home, tools, cancelled, 'compile', hidden)
        except StepFailed as failed:
            if failed.category != 'compile':
                raise
            raise StepFailed('dependency', 'A library the app declares could not be converted for Android.',
                             log=failed.log) from None
    log.append(run([tools.bt / 'd8', '--min-api', str(template.MIN_SDK), '--lib', tools.android_jar, '--output',
                    work / 'dex', *classes, *jars], work, home, tools, cancelled, 'compile', hidden))
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
    if re.search(r'^\s*E: \S*:', tree, re.M):  # an element form the identity checks above do not know
        raise StepFailed('compile', 'Manifest elements may not use a namespace.')
    problem = declared_permission_problem(tree, package)
    if problem:
        raise StepFailed('compile', problem)
    report = api_report(work, package, tools, home, cancelled, hidden)  # advisory: never fails the build
    _private(work / 'api-report.json', json.dumps(report, sort_keys=True))
    from .apilevel import summary
    log.append(summary(report))
    return unsigned, '\n'.join(x for x in log if x)


def api_report(work: Path, package: str, tools: Tools, home: Path, cancelled, hidden=()) -> dict:
    """The advisory API level report of the built dex (rose.app_builder.apilevel). Whatever goes wrong, it says
    ``unavailable`` and the build goes on; only a cancel stops it."""
    from . import apilevel
    database, dexdump = tools.sdk / apilevel.DATABASE, tools.bt / 'dexdump'
    if not database.is_file() or not dexdump.exists():
        return apilevel.unavailable('the pinned API database or dexdump is missing', template.MIN_SDK)
    try:
        dumps = [run([dexdump, '-d', dex], work, home, tools, cancelled, 'internal', hidden)
                 for dex in sorted((work / 'dex').glob('classes*.dex'))]
        return apilevel.analyse(dumps, apilevel.database(database), package, template.MIN_SDK)
    except StepFailed as failed:
        if failed.category == 'cancelled':
            raise
        return apilevel.unavailable(f'dexdump did not finish ({failed.category})', template.MIN_SDK)
    except Exception as error:
        return apilevel.unavailable(f'the analysis stopped ({type(error).__name__})', template.MIN_SDK)


def declared_permission_problem(tree: str, package: str) -> str | None:
    """The source check's permission-ownership rule again, on ``aapt dump xmltree`` of the built manifest: every
    <permission> or <permission-tree> name is under the app's own package, given as a plain string."""
    element = None
    for line in tree.splitlines():
        opened = re.match(r'\s*E: (\S+)', line)
        if opened:
            element = opened.group(1)
            continue
        if element not in ('permission', 'permission-tree'):
            continue
        attribute = re.match(r'\s*A: [^(]*\((0x01010003)\)=(.*)$', line)
        if attribute:
            value = re.match(r'"([^"]*)"', attribute.group(2))
            name = value.group(1) if value else ''
            if not name.startswith(package + '.') or len(name) <= len(package) + 1:
                return f'An app may declare permissions only under its own name ({package}.*).'
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
