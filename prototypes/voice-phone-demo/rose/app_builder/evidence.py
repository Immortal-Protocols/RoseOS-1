"""Home's behaviour evidence (workspace/rose-starters ``:evidence:compareThemeSnapshots``) run inside the builder's
build isolation, offline, against the stack's read-only cache (docs/APP-BUILDER.md §5 5a).

The task compiles one template module and runs the same Robolectric script under every pilot theme on API 34 and
37, comparing normalized semantics snapshots. For a template Rose's writer wrote, that module is untrusted code: it
runs here only, exactly as a project's build scripts do in rose.app_builder.gradle. The sandbox sees a private copy
of the starters project and of the written module, the pinned JDK, Gradle, SDK, cache and Robolectric jars
(read-only), and no network, credentials, keys or other projects. With no written module it checks the pilot
templates (Home's own code).

``run`` returns the parsed report.json and copies report.json and the per-theme snapshots out of the sandbox.
Identical snapshots across themes are behaviour evidence; a difference is recorded, never hidden.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path

from . import gradle, gradle_stack, starters, toolchain
from .toolchain import StepFailed

TASK = ':evidence:compareThemeSnapshots'
INIT = 'rose-evidence.init.gradle.kts'
# Gradle's test workers are JVMs of their own: they inherit neither the daemon's java.io.tmpdir nor its TMPDIR, so
# they would use /tmp, which the isolation does not have. The builder points them at the sandbox's own temp folder.
INIT_SCRIPT = '''gradle.lifecycle.beforeProject {
    tasks.withType(Test::class.java).configureEach {
        systemProperty("java.io.tmpdir", "@TMP@")
        environment("TMPDIR", "@TMP@")
    }
}
'''
OUTPUT = Path('evidence/build/evidence')  # relative to the starters project
SKIP = frozenset({'build', '.gradle', '.kotlin', 'local.properties'})  # never copied in: state, not source
XML_MAX = 4 << 20  # a JUnit result file larger than this is not read
OBJECT = re.compile(r'[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*\.[A-Z][A-Za-z0-9_]*')


def _copy_source(source: Path, target: Path) -> None:
    """A fresh copy of a source tree without build state: plain directories and files only (gradle._copy_tree)."""
    target.mkdir(mode=0o700)
    for entry in sorted(os.scandir(source), key=lambda e: e.name):
        if entry.name in SKIP:
            continue
        if entry.is_dir(follow_symlinks=False):
            _copy_source(Path(entry.path), target / entry.name)
        elif entry.is_file(follow_symlinks=False):
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as src, os.fdopen(os.open(target / entry.name, os.O_WRONLY | os.O_CREAT
                                                               | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as dst:
                shutil.copyfileobj(src, dst)
        else:
            raise StepFailed('compile', 'Source contains a link or special file; it is not run.')


def _plain_dir(top: Path, path: Path) -> Path | None:
    """``path`` when it and every folder between ``top`` and it is a real directory (never a link), else None."""
    current = top
    for part in path.relative_to(top).parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(mode):
            return None
    return path


def _plain_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _failures(results: Path, limit: int = 4000) -> str:
    """The failing tests' names and first message lines from JUnit XML, bounded."""
    import xml.etree.ElementTree as ET
    lines = []
    for path in sorted(results.glob('*.xml')) if results.is_dir() else ():
        if not _plain_file(path) or path.stat().st_size > XML_MAX:
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter('testcase'):
            for bad in list(case.findall('failure')) + list(case.findall('error')):
                first = (bad.get('message') or bad.text or '').strip().splitlines()[:3]
                lines.append(f"{case.get('classname')}.{case.get('name')}: " + ' | '.join(first))
    return ('\nFailing checks:\n' + '\n'.join(lines))[:limit] if lines else ''


def run(gradle_tools: gradle.GradleTools, robolectric: Path, tools: toolchain.Tools, work: Path, *,
        written: Path | None = None, template_object: str | None = None, root: Path = starters.ROOT,
        cancelled=lambda: False) -> dict:
    """The evidence for ``written`` (a written template module, e.g. <tree>/templates/feed, whose Kotlin object is
    ``template_object``) or, with neither, for the pilot templates. Returns {passed, report, files} where files are
    the copied outputs (relative to work/evidence). Raises StepFailed when no report was produced."""
    if (written is None) != (template_object is None):
        raise ValueError('a written template needs its object, and only then')
    if template_object is not None and not OBJECT.fullmatch(template_object):
        raise StepFailed('compile', 'The template object is not a Kotlin class name.')
    missing = gradle_tools.available()
    if missing:
        raise StepFailed('toolchain', missing, retryable=True)
    robolectric = Path(os.path.realpath(robolectric))
    for jar in gradle_stack.ROBOLECTRIC_JARS:
        if not (robolectric / jar.name).is_file():
            raise StepFailed('toolchain', f'The stack has no Robolectric Android jar {jar.name}; reprovision it.')
    work = Path(os.path.realpath(work))
    jdk, dist, sdk, cache = (Path(os.path.realpath(p)) for p in
                             (gradle_tools.jdk, gradle_tools.gradle, gradle_tools.sdk, gradle_tools.cache))
    box = work / 'sandbox'
    gradle.remove_tree(box)
    box.mkdir(mode=0o700, parents=True)
    project, home, gradle_home, tmp = box / 'project', box / 'home', box / 'gradle-home', box / 'tmp'
    out = work / 'evidence'
    gradle.remove_tree(out)
    try:
        _copy_source(Path(root), project)
        args = [f'-ProbolectricDependencyDir={robolectric}']
        if written is not None:
            _copy_source(Path(written), box / 'written')
            args += [f'-PwrittenTemplate={box / "written"}', f'-PtemplateObject={template_object}']
        for folder in (home, gradle_home, tmp):
            folder.mkdir(mode=0o700)
        gradle._write(project / 'local.properties', f'sdk.dir={sdk}\n', replace=True)
        gradle._write(gradle_home / 'gradle.properties', gradle._properties(jdk, home, tmp))
        gradle._write(box / INIT, INIT_SCRIPT.replace('@TMP@', str(tmp)))
        opts = f'-Djava.io.tmpdir={tmp} -Duser.home={home} -XX:-UsePerfData'
        env = {'PATH': f'{jdk}/bin:/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(tmp), 'LANG': 'C.UTF-8',
               'JAVA_HOME': str(jdk), 'GRADLE_USER_HOME': str(gradle_home), 'GRADLE_RO_DEP_CACHE': str(cache),
               'ANDROID_HOME': str(sdk), 'ANDROID_USER_HOME': str(home / '.android'), 'GRADLE_OPTS': opts}
        argv = [str(dist / 'bin/gradle'), '--offline', '--no-daemon', '--console=plain', '--init-script',
                str(box / INIT), '-p', str(project), TASK, *args]
        redact = (project, work, jdk, dist, sdk, cache, robolectric)
        failure = None
        try:
            log = gradle._run(gradle_tools, tools, argv, env, box, (jdk, dist, sdk, cache, robolectric), redact,
                              cancelled)
        except StepFailed as failed:  # a difference fails the task after it wrote report.json
            failure, log = failed, failed.log
        # Everything below the project was written inside the isolation, by code that may be the writer's: read it
        # only through paths with no link anywhere below the box (nothing runs there any more: the sandbox's own PID
        # namespace ended with it), and copy it with the builder's snapshot (no links, bounded).
        from .service import _snapshot
        out.mkdir(mode=0o700)
        results = _plain_dir(box, project / 'evidence/build/test-results/testDebugUnitTest')
        if results is not None:  # the JUnit results, kept for the record: what each check asserted, and any failure
            _snapshot(results, out / 'test-results')
        produced = _plain_dir(box, project / OUTPUT)
        if produced is None or not _plain_file(produced / 'report.json'):
            if failure is not None:
                raise StepFailed(failure.category, failure.summary, log=failure.log + _failures(out / 'test-results'))
            raise StepFailed('toolchain', 'The behaviour evidence produced no report.')
        _snapshot(produced, out / 'snapshots')
    finally:
        gradle.remove_tree(box)
    try:
        report = json.loads((out / 'snapshots' / 'report.json').read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError):  # the report names the template, and the template is the writer's
        raise StepFailed('compile', 'The behaviour evidence report is not valid JSON.') from None
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
    return {'passed': failure is None, 'report': report, 'files': files,
            'log': (log or '')[-gradle.MAX_LOG:], 'label': 'behaviour snapshots across themes (Robolectric, offline)'}
