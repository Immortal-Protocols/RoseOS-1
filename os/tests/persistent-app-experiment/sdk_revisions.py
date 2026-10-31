#!/usr/bin/env python3
"""EXP006-ISOLATED-TRIAL-001: the trial SDK's installed packages, exactly as pinned, or a refusal.

  sdkmanager --list_installed | sdk_revisions.py [--manifest qa/systemui-trial/tool-manifest.json]

Reads the "Installed packages:" table that the pinned sdkmanager prints, and compares it with the QA tool manifest's
sdk.packages. Every pinned package must appear exactly once with exactly its pinned revision and install location.
`cmdline-tools;22.0` may be listed as `cmdline-tools;latest` (it is unpacked as cmdline-tools/latest, the path the
experiment calls) but only with revision 22.0. Any other installed package, a missing or duplicated one, a different
revision (drift: `sdkmanager --install` takes the newest stable revision at install time) or a row that does not parse
refuses: the manifest is updated deliberately, never passed silently. The table layout (Path | Version |
Description | Location, header and dashed rows) is sdkmanager's; tested here on SYNTHETIC rows in that layout, not on
captured output, so a real table that does not parse refuses rather than passes. A package unpacked straight from an archive
has no package.xml and is not listed, so it reads as missing. Prints fixed-word JSON; exit 0 exact, 1 refused.
It reads only its input and the manifest: it installs, downloads and accepts nothing.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT/'qa/systemui-trial/tool-manifest.json'
ROW = re.compile(r'[a-z][a-z0-9-]*(;[A-Za-z0-9._-]+)*')
ALIASES = {'cmdline-tools;22.0': 'cmdline-tools;latest'}

def pinned(manifest):
    """{path: (revision, location)} from the manifest's sdk.packages."""
    out = {}
    for package in manifest['sdk']['packages']:
        out[package['path']] = (package['revision'], package.get('installDir') or package['path'].replace(';', '/'))
    return out

def installed(text):
    """({path: (version, location)}, problems) from `sdkmanager --list_installed` output."""
    rows, problems, in_table = {}, [], False
    for line in text.splitlines():
        if line.strip() == 'Installed packages:':
            in_table = True
            continue
        if not in_table or not line.strip():
            continue
        cells = [c.strip() for c in line.split('|')]
        if len(cells) != 4:
            if rows or problems:
                break  # the table ended (e.g. an "Available Packages:" section follows)
            problems.append('a row of the installed table does not have four columns')
            continue
        path, version, _, location = cells
        if path == 'Path' or set(path) <= {'-'}:
            continue
        if not ROW.fullmatch(path) or not re.fullmatch(r'[0-9][0-9A-Za-z._-]*', version) or not location:
            problems.append('a row of the installed table does not parse')
            continue
        if path in rows:
            problems.append(f'{path} is listed twice')
            continue
        rows[path] = (version, location.rstrip('/'))  # sdkmanager may print a trailing '/'
    if not in_table:
        problems.append('no "Installed packages:" table')
    return rows, problems

def check(text, manifest):
    rows, problems = installed(text)
    expected = pinned(manifest)
    seen = set()
    for path, (revision, location) in sorted(expected.items()):
        listed = [p for p in (path, ALIASES.get(path)) if p and p in rows]
        if len(listed) != 1:
            problems.append(f'{path} is not installed exactly once' if not listed else f'{path} is listed under two paths')
            continue
        seen.add(listed[0])
        version, at = rows[listed[0]]
        if version != revision:
            problems.append(f'{path} is revision {version}, pinned {revision}')
        if at != location:
            problems.append(f'{path} is installed at {at}, pinned {location}')
    for extra in sorted(set(rows) - seen):
        problems.append(f'{extra} is installed but not pinned')
    return problems

def main(argv=None, stdin=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--manifest', type=Path, default=MANIFEST)
    a = p.parse_args(argv)
    manifest = json.loads(a.manifest.read_text())
    problems = check((stdin or sys.stdin).read(), manifest)
    print(json.dumps({'result': 'refused' if problems else 'exact', 'problems': problems,
        'pinned': len(manifest['sdk']['packages'])}, indent=2, sort_keys=True))
    return 1 if problems else 0

if __name__ == '__main__':
    sys.exit(main())
