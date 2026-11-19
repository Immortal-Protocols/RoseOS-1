"""Two-stage writer handoff (docs/APP-BUILDER.md §5 5a): Rose's writer writes a starter app's source on one host,
and the builder builds that source on another. No credential, login, ledger or key moves between them; only plain
source and a manifest do.

Stage 1, ``write``: on the host whose Codex is already signed in (the Mac), run as that same OS user, from a checkout
that is not the live server's, with the live server left running:

    python -m rose.app_builder.writer_handoff write --pilot TEMPLATE:THEME --scratch NEW_DIR --out NEW_DIR \\
        [--protected DIR ...]

1. refuses a scratch or output folder that exists, or that lies in a temp folder (the writer's profile denies
   those), ROSE_CONFIG_DIR or a ``--protected`` folder;
2. writes the starter's tree (rose.app_builder.starters.skeleton) under a package of its own, and records it with
   the builder's own git (repository kept beside the source; hooks, fsmonitor and attributes off);
3. runs the writer's live denial check (workspace/rose-agent/server/app-build-sandbox-check.mjs) on that source
   folder and refuses unless it says ``{"enforced": true}``;
4. runs one ``create`` turn of the builder's own writer (rose.app_builder.generator.CodexGenerator) with the
   starter and the fixed Wave 3 request (rose.app_builder.starter_pilots.REQUESTS);
5. copies the result out through the builder's own snapshot (no links, no special files, size limits) into
   ``OUT/tree``, with ``OUT/handoff.json`` (the identity, the request, the trees, every file's digest, the writer
   and the check's output) and ``OUT/SHA256SUMS``; then removes the scratch folder.

It never builds, signs or makes a key, and opens no ledger. Its only credential is the one the writer already uses.

Transfer: the operator moves ``OUT`` to the build host by any channel and reports ``sha256(handoff.json)`` out of
band. Nothing in OUT proves that a writer turn happened; that report is the attestation.

Stage 2, ``build``: on the build host (Linux, bubblewrap, non-root):

    python -m rose.app_builder.writer_handoff build --handoff DIR [--handoff DIR ...] --out NEW_DIR \\
        [--handoff-sha256 HEX ...]

verifies each handoff, builds it through the whole builder job (Gradle in bubblewrap, the binary checks, a
throwaway signing key) with a writer that applies exactly the handed-off tree, and writes the starter_pilots output
layout, which rose.app_builder.device_check reads. A record carries ``WRITER_LABEL`` only when its handoff came from
the real writer with the check enforced, its digest matches one given with ``--handoff-sha256``, and both trees
match what this checkout's builder recorded. Otherwise it carries ``TEST_LABEL`` (an imported tree, not Rose's
writer). A build failure is the handoff's result, with the build output kept as the next stage-1 repair request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import gradle, ledger as ledger_module, starters, template, toolchain
from .starter_pilots import REQUESTS, launchable, parse_pilot, sdk_levels, sha256, stack_tools

SCHEMA_VERSION = 1
WRITER_KIND = 'codex-app-server'
WRITER_LABEL = ('writer-generated source (Rose\'s Codex writer, stage 1, attested by the operator\'s digest), '
                'built on Linux')
TEST_LABEL = 'imported tree, test writer (not Rose\'s writer)'
TRANSPLANT_LABEL = 'transplanted template code under another theme (cross-theme evidence, not a writer run)'
PACKAGE = re.compile(re.escape(ledger_module.PACKAGE_PREFIX) + r'[0-9a-f]{16}')
CHECKOUT = Path(__file__).resolve().parents[4]  # the repository root this module runs from
SANDBOX_CHECK = CHECKOUT / 'workspace/rose-agent/server/app-build-sandbox-check.mjs'


class Refused(Exception):
    """A handoff that cannot be written or used as given; nothing was built."""


def _temp_roots() -> list[Path]:
    roots = {Path(tempfile.gettempdir()), Path('/tmp'), Path('/var/tmp'), Path('/private/tmp'),
             Path('/private/var/tmp'), Path('/private/var/folders')}
    if os.environ.get('TMPDIR'):
        roots.add(Path(os.environ['TMPDIR']))
    return [r.resolve() for r in roots if r.exists()] + [r for r in roots if not r.exists()]


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _check_place(path: Path, what: str, protected: list[Path], allow_temp=False):
    path = Path(os.path.abspath(path))
    if path.exists() or path.is_symlink():
        raise Refused(f'{what} {path} exists')
    real = path.parent.resolve() / path.name
    for root in ([] if allow_temp else _temp_roots()):
        if _under(real, root) or _under(path, root):
            raise Refused(f'{what} {path} is in a temp folder, which the writer\'s profile denies')
    for root in protected:
        if _under(real, root) or _under(root, real):
            raise Refused(f'{what} {path} overlaps the protected folder {root}')
    return real


def _tree_files(tree: Path) -> dict[str, str]:
    files = {}
    for folder, dirs, names in os.walk(tree):
        for name in dirs + names:
            if template.git_name(name):
                raise Refused(f'the tree has git metadata ({name})')
        for name in names:
            path = Path(folder) / name
            files[path.relative_to(tree).as_posix()] = sha256(path)
    return dict(sorted(files.items()))


def _codex_identity() -> dict:
    binary = os.environ.get('PIXEL_CODEX_BIN') or shutil.which('codex') or 'codex'
    try:
        version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=30,
                                 stdin=subprocess.DEVNULL).stdout.strip()[:200]
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {'codexBin': binary, 'codexVersion': version}


def run_sandbox_check(source: Path, protected: list[Path]) -> list[str]:
    """The writer's live denial check on ``source``, with this process's environment (the writer's own). Returns
    its output lines; raises Refused unless it exited 0 and ended with {"enforced": true}."""
    args = ['node', str(SANDBOX_CHECK), '--source', str(source)]
    for root in protected:
        args += ['--protected', str(root)]
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused(f'the writer\'s sandbox check did not run: {error}') from None
    lines = [l for l in done.stdout.splitlines() if l.strip()][:200]
    try:
        last = json.loads(lines[-1]) if lines else {}
    except ValueError:
        last = {}
    if done.returncode != 0 or last != {'enforced': True}:
        raise Refused('the writer\'s sandbox check is not enforced here (exit '
                      f'{done.returncode}): {(lines[-1] if lines else done.stderr.strip())[:300]}')
    return lines


def _checkout_commit() -> dict:
    def git(*args):
        return subprocess.run(['git', '-C', str(CHECKOUT), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL).stdout.strip()
    return {'commit': git('rev-parse', 'HEAD') or None, 'dirty': bool(git('status', '--porcelain'))}


def write(pilot: tuple[str, str], scratch: Path, out: Path, *, protected=(), writer=None, sandbox_check=None,
          catalog: starters.Starters | None = None, log=print, _allow_temp=False) -> dict:
    """Stage 1. ``writer`` defaults to the builder's CodexGenerator and ``sandbox_check`` to the live check; tests
    inject both, and such a handoff is labelled a test writer's. Returns handoff.json's object."""
    from .service import _git, _snapshot
    catalog = catalog or starters.Starters.load()
    template_id, theme = pilot
    catalog.resolve({'template': template_id, 'theme': theme})
    protected = [Path(p).resolve() for p in protected]
    if os.environ.get('ROSE_CONFIG_DIR'):
        protected.append(Path(os.environ['ROSE_CONFIG_DIR']).resolve())
    if _allow_temp and writer is None:
        raise Refused('only a test writer may write in a temp folder')
    scratch = _check_place(scratch, 'scratch', protected, _allow_temp)
    out = _check_place(out, 'out', protected, _allow_temp)
    if _under(out, scratch) or _under(scratch, out):
        raise Refused('scratch and out must be separate folders')
    real_writer = writer is None
    if real_writer:
        from .generator import CodexGenerator
        writer = CodexGenerator(gradle=True)
    check = sandbox_check or run_sandbox_check
    starter = {'template': template_id, 'theme': theme}
    request = REQUESTS[template_id]
    package = ledger_module.PACKAGE_PREFIX + secrets.token_hex(8)
    name = request.splitlines()[0][:40].strip() or 'New app'  # Builder.create's own derivation
    source = scratch / 'p' / 'source'
    try:
        source.mkdir(mode=0o700, parents=True)
        starters.skeleton(source, package, name, starter)
        _git(source, 'init', '-q')
        _git(source, 'add', '-A', '-f')
        _git(source, 'commit', '-q', '-m', 'skeleton')
        skeleton_tree = _git(source, 'rev-parse', 'HEAD^{tree}')
        log(f'writer_handoff: checking the writer\'s sandbox on {source}')
        check_lines = check(source, protected)
        log(f'writer_handoff: writing {template_id}-{theme}')
        note = writer(source, 'create', request, lambda: False, starter=starter)
        out.mkdir(mode=0o755)
        tree = out / 'tree'
        try:
            _snapshot(source, tree)  # the builder's own copy: plain files only, within its limits
        except toolchain.StepFailed as failed:
            raise Refused(f'the written source cannot be handed off: {failed.summary}') from None
        files = _tree_files(tree)
        _git(source, 'add', '-A', '-f', work_tree=tree)
        writer_tree = _git(source, 'write-tree')
    finally:
        gradle.remove_tree(scratch)
    handoff = {
        'schemaVersion': SCHEMA_VERSION, 'template': template_id, 'theme': theme, 'request': request,
        'packageName': package, 'name': name, 'checkout': _checkout_commit(),
        'skeletonTree': skeleton_tree, 'writerTree': writer_tree, 'files': files,
        'writer': ({'kind': WRITER_KIND, 'model': writer.model, **_codex_identity()} if real_writer
                   else {'kind': 'test'}) | {'note': str(note or '')[:1200]},
        'sandboxCheck': list(check_lines or []),
        'label': 'writer-generated source, not built' if real_writer else TEST_LABEL,
    }
    (out / 'handoff.json').write_text(json.dumps(handoff, indent=2, sort_keys=True) + '\n')
    sums = [(digest, f'tree/{path}') for path, digest in files.items()]
    sums.append((sha256(out / 'handoff.json'), 'handoff.json'))
    (out / 'SHA256SUMS').write_text(''.join(f'{d}  {p}\n' for d, p in sums))
    log(f'writer_handoff: handoff.json sha256 {sha256(out / "handoff.json")}')
    return handoff


class Handoff:
    """A verified handoff: its manifest and a private copy of its tree that matches every digest."""

    def __init__(self, manifest: dict, tree: Path, digest: str, transplant: dict | None = None):
        self.manifest, self.tree, self.digest, self.transplant = manifest, tree, digest, transplant

    @property
    def pilot(self) -> tuple[str, str]:
        return self.manifest['template'], self.manifest['theme']


def load(directory: Path, private: Path, catalog: starters.Starters) -> Handoff:
    """Checks a stage-1 output folder and copies its tree into ``private`` (which must not exist)."""
    from .service import _snapshot
    directory = Path(directory)
    try:
        sums = {}
        for line in (directory / 'SHA256SUMS').read_text().splitlines():
            digest, _, path = line.partition('  ')
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or not path or path in sums:
                raise Refused(f'SHA256SUMS has a malformed line: {line[:80]!r}')
            sums[path] = digest
        raw = (directory / 'handoff.json').read_bytes()
    except OSError as error:
        raise Refused(f'{directory} is not a handoff: {error}') from None
    digest = hashlib.sha256(raw).hexdigest()
    if sums.pop('handoff.json', None) != digest:
        raise Refused('handoff.json does not match SHA256SUMS')
    manifest = json.loads(raw)
    if manifest.get('schemaVersion') != SCHEMA_VERSION:
        raise Refused('handoff.json is not of this version')
    try:
        catalog.resolve({'template': manifest.get('template'), 'theme': manifest.get('theme')})
    except ValueError as error:
        raise Refused(str(error)) from None
    if manifest.get('request') != REQUESTS[manifest['template']]:
        raise Refused('the handoff answers another request than the fixed one for its template')
    if not PACKAGE.fullmatch(str(manifest.get('packageName', ''))):
        raise Refused('the handoff has no builder-shaped package')
    files = manifest.get('files')
    if not isinstance(files, dict) or {f'tree/{p}' for p in files} != set(sums) \
            or any(sums[f'tree/{p}'] != d for p, d in files.items()):
        raise Refused('the handoff\'s files and SHA256SUMS disagree')
    try:
        _snapshot(directory / 'tree', private)  # plain files only, no links, within the builder's limits
    except toolchain.StepFailed as failed:
        raise Refused(f'the handoff tree is refused: {failed.summary}') from None
    if _tree_files(private) != files:
        raise Refused('the handoff tree does not match its digests')
    return Handoff(manifest, private, digest)


def transplanted(h: Handoff, theme: str, private: Path, catalog: starters.Starters) -> Handoff:
    """The same handoff's template code and app resources under another ``theme`` (rose.app_builder.starters
    .transplant), as a handoff of its own for the builder: the cross-theme evidence that runs identical template
    code under every theme. Labelled a transplant, never a writer run."""
    m = h.manifest
    catalog.resolve({'template': m['template'], 'theme': theme})
    if theme == m['theme']:
        raise Refused('a transplant goes to another theme')
    starters.transplant(h.tree, {'template': m['template'], 'theme': m['theme']}, m['packageName'], m['name'],
                        theme, private)
    manifest = dict(m, theme=theme, files=_tree_files(private))
    return Handoff(manifest, private, h.digest, transplant={'theme': m['theme'], 'handoffSha256': h.digest})


def applier(handoff: Handoff, repairs: list):
    """The builder's writer for a handoff: a create turn becomes exactly the handed-off tree; a repair turn's build
    output is kept (for the next stage-1 turn) and the job fails."""
    starter = {'template': handoff.manifest['template'], 'theme': handoff.manifest['theme']}

    def write(source, kind, request, cancelled, **turn):
        if turn.get('starter') != starter:
            raise toolchain.StepFailed('internal', 'The handoff is for another starter than this project\'s.')
        if kind == 'repair':
            repairs.append(request)
            raise toolchain.StepFailed('compile', 'The handed-off source did not build; its build output is the next '
                                       'writer turn\'s repair request.')
        if kind != 'create' or request != handoff.manifest['request']:
            raise toolchain.StepFailed('internal', 'A handoff answers only its own create request.')
        for entry in list(source.iterdir()):
            gradle.remove_tree(entry)
        shutil.copytree(handoff.tree, source, dirs_exist_ok=True)
        return f'applied handoff {handoff.digest[:12]}'
    return write


def _bind_identity(b, project_id: str, package: str, name: str):
    """In a handoff's own throwaway builder only: the queued project takes the handoff's package and name, so the
    tree's package is the one every check and the build use. Refused once anything was built for it."""
    with b.ledger.lock:
        db = b.ledger.db
        db.execute('BEGIN IMMEDIATE')
        try:
            built = db.execute('SELECT COUNT(*) FROM versions WHERE project_id=?', (project_id,)).fetchone()[0]
            states = [r[0] for r in db.execute('SELECT state FROM jobs WHERE project_id=?', (project_id,))]
            if built or states != ['queued'] or not PACKAGE.fullmatch(package):
                raise Refused('the project is not a fresh, queued one')
            db.execute('UPDATE projects SET package_name=?, name=? WHERE project_id=? AND signer_sha256 IS NULL',
                       (package, name, project_id))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK')
            raise


def build(handoffs: list[Path], out: Path, tools: toolchain.Tools, gradle_tools: gradle.GradleTools,
          catalog: starters.Starters, *, attested=(), transplant_to=(), work: Path | None = None, log=print) -> dict:
    """Stage 2. ``attested`` holds the handoff.json digests the operator reported out of band; ``transplant_to``
    themes also build each handoff's template code under those themes (``transplanted``)."""
    from .service import Builder, _git
    out = Path(out)
    owned = work is None
    work = Path(tempfile.mkdtemp(prefix='rose-handoff-')) if owned else Path(work)
    attested = set(attested)
    records = []
    try:
        loaded = [load(d, work / f'handoff-{i}', catalog) for i, d in enumerate(handoffs)]
        for i, h in enumerate(list(loaded)):
            for theme in transplant_to:
                if theme != h.manifest['theme']:
                    loaded.append(transplanted(h, theme, work / f'transplant-{i}-{theme}', catalog))
        if len({h.pilot for h in loaded}) != len(loaded):
            raise Refused('two handoffs are for the same pilot')
        out.mkdir(mode=0o755)
        (out / 'apks').mkdir(mode=0o755)
        for h in loaded:
            template_id, theme = h.pilot
            name = f'{template_id}-{theme}'
            repairs = []
            b = Builder(work / name, tools, applier(h, repairs), backup=_local_backup, gradle=gradle_tools,
                        skeleton=starters.skeleton, resolve_starter=catalog.resolve, repairs=1)
            try:
                created = b.create('writer-handoff', {'schemaVersion': 1, 'clientRequestId': name,
                                                      'description': h.manifest['request'],
                                                      'starter': {'template': template_id, 'theme': theme}})
                _bind_identity(b, created['projectId'], h.manifest['packageName'], h.manifest['name'])
                log(f'writer_handoff: building {name} from {h.digest[:12]}')
                b.run_next()
                job = b.job(created['jobId'])
                writer = h.manifest.get('writer', {})
                real = writer.get('kind') == WRITER_KIND and h.digest in attested and _enforced(h.manifest)
                starter = {'template': template_id, 'theme': theme}
                package = h.manifest['packageName']
                record = {'template': template_id, 'theme': theme, 'state': job['state'], 'handoffSha256': h.digest,
                          'attested': h.digest in attested, 'writer': writer, 'request': h.manifest['request'],
                          # Home's starter checks on the verified copy, against the handoff's bound package
                          'outsideTemplate': starters.outside_template(h.tree, starter, package),
                          'templateEdits': starters.template_edits(h.tree, starter, package, h.manifest['name'])}
                if h.transplant:
                    record['transplantOf'] = h.transplant
                real = real and not h.transplant and not record['outsideTemplate']
                source = b.root / 'projects' / created['projectId'] / 'source'
                root = _git(source, 'rev-list', '--max-parents=0', 'HEAD')
                record['skeletonMatch'] = (not h.transplant
                                           and _git(source, 'rev-parse', root + '^{tree}') == h.manifest['skeletonTree'])
                if job['state'] != 'ready':
                    record['label'] = TRANSPLANT_LABEL if h.transplant else WRITER_LABEL if real else TEST_LABEL
                    record['failure'] = job.get('failure') or {'message': job.get('message')}
                    if repairs:
                        record['repairRequest'] = repairs[-1]
                    records.append(record)
                    log(f'writer_handoff: {name} {job["state"]}')
                    continue
                project = next(p for p in b.projects() if p['projectId'] == created['projectId'])
                version = b.ledger.versions(created['projectId'])[-1]
                record['sourceMatch'] = (not h.transplant and
                                         _git(source, 'rev-parse', version['sourceRevision'] + '^{tree}')
                                         == h.manifest['writerTree'])
                record['label'] = (TRANSPLANT_LABEL if h.transplant else
                                   WRITER_LABEL if real and record['skeletonMatch'] and record['sourceMatch']
                                   else TEST_LABEL)
                apk = out / 'apks' / f'{name}.apk'
                shutil.copyfile(b.root / 'artifacts' / f'{project["versions"][-1]["apk"]["sha256"]}.apk', apk)
                badging = toolchain.run([tools.bt / 'aapt', 'dump', 'badging', apk], work, work, tools,
                                        lambda: False, 'internal')
                min_sdk, target_sdk = sdk_levels(badging)
                record.update({'apk': f'apks/{name}.apk', 'apkSha256': sha256(apk),
                               'packageName': project['packageName'], 'versionCode': version['versionCode'],
                               'signerSha256': project['signerSha256'], 'launchableActivity': launchable(badging),
                               'minSdk': min_sdk, 'targetSdk': target_sdk})
                records.append(record)
                log(f'writer_handoff: {name} ready ({record["label"]})')
            finally:
                b.ledger.close()
    finally:
        if owned:
            gradle.remove_tree(work)
    labels = {r['label'] for r in records}
    manifest = {'schemaVersion': 1, 'label': labels.pop() if len(labels) == 1 else 'mixed', 'pilots': records}
    (out / 'pilots.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    (out / 'cross-theme.json').write_text(json.dumps(cross_theme(records), indent=2, sort_keys=True) + '\n')
    sums = [(r['apkSha256'], r['apk']) for r in records if 'apk' in r]
    sums.append((sha256(out / 'pilots.json'), 'pilots.json'))
    sums.append((sha256(out / 'cross-theme.json'), 'cross-theme.json'))
    (out / 'SHA256SUMS').write_text(''.join(f'{d}  {p}\n' for d, p in sums))
    return manifest


def cross_theme(records: list[dict]) -> dict:
    """Per template and per kind of run, Home's comparison of the template edits across themes
    (rose.app_builder.starters.compare_trio): writer runs and transplants are compared separately, never mixed.
    Equal edits mean the same template code ran; they are not by themselves evidence of equal behaviour."""
    report = {}
    for template in sorted({r['template'] for r in records}):
        for kind, pick in (('writerRuns', lambda r: 'transplantOf' not in r),
                           ('transplants', lambda r: 'transplantOf' in r)):
            runs = {r['theme']: r['templateEdits'] for r in records if r['template'] == template and pick(r)}
            if len(runs) > 1:
                report.setdefault(template, {})[kind] = starters.compare_trio(runs)
    return report


def _enforced(manifest: dict) -> bool:
    lines = manifest.get('sandboxCheck') or []
    try:
        return json.loads(lines[-1]) == {'enforced': True}
    except (ValueError, TypeError, IndexError):
        return False


def _local_backup(keys, project_id, package):
    if not (Path(keys) / 'app.p12').is_file():
        raise toolchain.StepFailed('internal', 'There is no key to back up.')
    return f'writer-handoff-local:{project_id}'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m rose.app_builder.writer_handoff',
                                     description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='stage', required=True)
    w = sub.add_parser('write', help='stage 1: one writer turn, source only')
    w.add_argument('--pilot', required=True, metavar='TEMPLATE:THEME')
    w.add_argument('--scratch', required=True, type=Path)
    w.add_argument('--out', required=True, type=Path)
    w.add_argument('--protected', action='append', default=[], type=Path)
    bl = sub.add_parser('build', help='stage 2: build handoffs through the builder')
    bl.add_argument('--handoff', action='append', required=True, type=Path)
    bl.add_argument('--out', required=True, type=Path)
    bl.add_argument('--handoff-sha256', action='append', default=[], metavar='HEX')
    bl.add_argument('--transplant-to', action='append', default=[], metavar='THEME',
                    help='also build each handoff\'s template code under this theme (cross-theme evidence)')
    args = parser.parse_args(argv)
    if not starters.available():
        parser.error('this checkout has no workspace/rose-starters')
    catalog = starters.Starters.load()
    say = lambda m: print(m, file=sys.stderr, flush=True)
    try:
        if args.stage == 'write':
            handoff = write(parse_pilot(args.pilot, catalog), args.scratch, args.out, protected=args.protected,
                            catalog=catalog, log=say)
            print(json.dumps({'handoffSha256': sha256(args.out / 'handoff.json'), 'label': handoff['label']}))
            return 0
        if args.out.exists():
            parser.error(f'{args.out} exists')
        for digest in args.handoff_sha256:
            if not re.fullmatch(r'[0-9a-f]{64}', digest):
                parser.error(f'{digest!r} is not a sha256')
        stack, sdk = os.environ.get('ROSE_GRADLE_STACK'), os.environ.get('ANDROID_SDK_ROOT')
        if not stack or not sdk:
            parser.error('ROSE_GRADLE_STACK and ANDROID_SDK_ROOT must be set')
        java_home = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None
        tools = toolchain.Tools(Path(sdk), java_home, timeout_s=900, key_creation=True)
        manifest = build(args.handoff, args.out, tools, stack_tools(Path(stack)), catalog,
                         attested=args.handoff_sha256, transplant_to=args.transplant_to, log=say)
    except (Refused, ValueError) as error:
        print(f'writer_handoff: refused: {error}', file=sys.stderr)
        return 2
    ready = sum(1 for p in manifest['pilots'] if p['state'] == 'ready')
    print(json.dumps({'ready': ready, 'pilots': len(manifest['pilots']),
                      'labels': sorted({p['label'] for p in manifest['pilots']})}))
    return 0 if ready == len(manifest['pilots']) else 1


if __name__ == '__main__':
    sys.exit(main())
