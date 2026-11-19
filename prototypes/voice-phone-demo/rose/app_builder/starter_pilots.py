"""Starter pilots: Rose starters (rose.app_builder.starters) built through the builder's whole job, with a scripted
writer that changes nothing (docs/APP-BUILDER.md §3.1 ``starter``, §2.4).

Each pilot is one template under one theme. It is created with that starter, built offline by Gradle inside
bubblewrap, passes the binary checks and is signed with its own throwaway key, exactly as a real app would be.
What it is not: an app Rose's writer wrote. Every record says so (``LABEL``), and nothing downstream may present
it otherwise.

    python -m rose.app_builder.starter_pilots --out DIR [--pilot TEMPLATE:THEME ...]

needs ROSE_GRADLE_STACK (rose.app_builder.gradle_prepare), ANDROID_SDK_ROOT (the legacy SDK the builder signs with),
JAVA_HOME and a non-root Linux user that can run bubblewrap. DIR must not exist. It receives:

    apks/<template>-<theme>.apk   the signed APKs
    pilots.json                   one record per pilot (``record``) under {schemaVersion, label, pilots}
    SHA256SUMS                    sha256sum -c format, for the APKs and pilots.json

Signing keys, the ledger and the sources stay in a private work directory that is deleted at the end; they are
never written under DIR. Exit status: 0 when every pilot is ready, 1 otherwise (pilots.json still says which).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import gradle, starters, toolchain

LABEL = 'compiled starter tree, scripted writer (not writer-generated)'
SCHEMA_VERSION = 1

# Wave 3's fixed requests, verbatim from Home (2026-11-19). Each is the same under every theme and carries no style
# words, so the theme is the only variable; each exercises the main interaction its matrix scenario tests. A writer
# run (stage 1) uses the same text, so scripted and written pilots answer the same request.
REQUESTS = {
    'feed': ('A neighbourhood notice board. Show a scrolling list of posts from local groups, each with its group, '
             'title, time and a short summary. Tapping a post opens it in full; going back returns to the same place '
             'in the list. Let me save a post and see which posts are saved.'),
    'sprite-game': ('A short arcade game: a small delivery robot moves through a walled courtyard to collect three '
                    'parcels, avoiding puddles, then reaches the drop-off door to finish. Touching a puddle ends the '
                    'round. Show the parcels collected and how the round is going, and let me restart.'),
    'player': ('A practice player for a 30-second groove. Play and pause it, drag along the timeline to any moment, '
               'and mark a start and an end so that passage repeats while I practise.'),
}


def unchanged(source, kind, request, cancelled):
    """The scripted writer: the starter's own tree is the app."""
    return 'unchanged'


def local_backup(keys, project_id, package):
    """A pilot's key is throwaway and never leaves the work directory; the builder only needs a receipt."""
    if not (Path(keys) / 'app.p12').is_file():
        raise toolchain.StepFailed('internal', 'There is no key to back up.')
    return f'starter-pilot-local:{project_id}'


def all_pilots(catalog: starters.Starters) -> list[tuple[str, str]]:
    return [(t, h) for t in catalog.templates for h in catalog.themes]


def parse_pilot(text: str, catalog: starters.Starters) -> tuple[str, str]:
    template, sep, theme = text.partition(':')
    if not sep:
        raise ValueError(f'{text!r} is not TEMPLATE:THEME')
    catalog.resolve({'template': template, 'theme': theme})
    return template, theme


def stack_tools(stack: Path) -> gradle.GradleTools:
    from .isolation import BubblewrapIsolation
    return gradle.GradleTools(stack / 'jdk', stack / 'gradle', stack / 'sdk', stack / 'cache', BubblewrapIsolation(),
                              timeout_s=1500)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def launchable(badging: str) -> str | None:
    found = re.search(r"^launchable-activity: name='([^']+)'", badging, re.M)
    return found.group(1) if found else None


def sdk_levels(badging: str) -> tuple[int | None, int | None]:
    def level(name):
        found = re.search(rf"^{name}:'(\d+)'", badging, re.M)
        return int(found.group(1)) if found else None
    return level('sdkVersion'), level('targetSdkVersion')


def build(out: Path, pilots: list[tuple[str, str]], tools: toolchain.Tools, gradle_tools: gradle.GradleTools,
          catalog: starters.Starters, *, work: Path | None = None, log=print) -> dict:
    """Builds each pilot in its own builder root under ``work`` and writes DIR's contents. Returns pilots.json's
    object. ``out`` must not exist yet."""
    from .service import Builder
    out = Path(out)
    out.mkdir(mode=0o755)
    (out / 'apks').mkdir(mode=0o755)
    owned = work is None
    work = Path(tempfile.mkdtemp(prefix='rose-pilots-')) if owned else Path(work)
    records = []
    try:
        for template, theme in pilots:
            name = f'{template}-{theme}'
            log(f'starter_pilots: building {name}')
            b = Builder(work / name, tools, unchanged, backup=local_backup, gradle=gradle_tools,
                        skeleton=starters.skeleton, resolve_starter=catalog.resolve)
            try:
                created = b.create('starter-pilots', {
                    'schemaVersion': 1, 'clientRequestId': name,
                    'description': REQUESTS.get(template, f'A {template} app.'),
                    'starter': {'template': template, 'theme': theme}})
                b.run_next()
                job = b.job(created['jobId'])
                record = {'template': template, 'theme': theme, 'label': LABEL, 'state': job['state']}
                if job['state'] != 'ready':
                    record['failure'] = job.get('failure') or {'message': job.get('message')}
                    records.append(record)
                    log(f'starter_pilots: {name} {job["state"]}')
                    continue
                project = next(p for p in b.projects() if p['projectId'] == created['projectId'])
                version = project['versions'][-1]
                built = b.root / 'artifacts' / f'{version["apk"]["sha256"]}.apk'
                apk = out / 'apks' / f'{name}.apk'
                shutil.copyfile(built, apk)
                badging = toolchain.run([tools.bt / 'aapt', 'dump', 'badging', apk], work, work, tools,
                                        lambda: False, 'internal')
                min_sdk, target_sdk = sdk_levels(badging)
                record.update({
                    'apk': f'apks/{name}.apk', 'apkSha256': sha256(apk), 'packageName': project['packageName'],
                    'versionCode': version['versionCode'], 'signerSha256': project['signerSha256'],
                    'launchableActivity': launchable(badging), 'minSdk': min_sdk, 'targetSdk': target_sdk})
                records.append(record)
                log(f'starter_pilots: {name} ready {record["apkSha256"][:12]}')
            finally:
                b.ledger.close()
    finally:
        if owned:
            gradle.remove_tree(work)
    manifest = {'schemaVersion': SCHEMA_VERSION, 'label': LABEL, 'pilots': records}
    (out / 'pilots.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    sums = [(r['apkSha256'], r['apk']) for r in records if 'apk' in r]
    sums.append((sha256(out / 'pilots.json'), 'pilots.json'))
    (out / 'SHA256SUMS').write_text(''.join(f'{digest}  {path}\n' for digest, path in sums))
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m rose.app_builder.starter_pilots', description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--pilot', action='append', default=[], metavar='TEMPLATE:THEME',
                        help='build only these (default: every template under every theme)')
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f'{args.out} exists')
    if not starters.available():
        parser.error('this checkout has no workspace/rose-starters')
    stack = os.environ.get('ROSE_GRADLE_STACK')
    sdk = os.environ.get('ANDROID_SDK_ROOT')
    if not stack or not sdk:
        parser.error('ROSE_GRADLE_STACK and ANDROID_SDK_ROOT must be set')
    catalog = starters.Starters.load()
    try:
        pilots = [parse_pilot(p, catalog) for p in args.pilot] or all_pilots(catalog)
    except ValueError as error:
        parser.error(str(error))
    java_home = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None
    tools = toolchain.Tools(Path(sdk), java_home, timeout_s=900, key_creation=True)
    manifest = build(args.out, pilots, tools, stack_tools(Path(stack)), catalog,
                     log=lambda m: print(m, file=sys.stderr, flush=True))
    ready = sum(1 for p in manifest['pilots'] if p['state'] == 'ready')
    print(json.dumps({'ready': ready, 'pilots': len(manifest['pilots']), 'label': LABEL}))
    return 0 if ready == len(manifest['pilots']) else 1


if __name__ == '__main__':
    sys.exit(main())
