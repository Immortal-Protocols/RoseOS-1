#!/usr/bin/env python3
"""EXP006: a bounded, non-secret summary of one experiment run for the job page.

  report.py --evidence EDIR --work WORK [--summary FILE]

Reads only EDIR/emulator-setup.json and EDIR/results.json, which this workflow wrote, and checks that the job-local
key and AVD paths under WORK are gone. It writes a short Markdown table (FILE, normally $GITHUB_STEP_SUMMARY) and one
workflow annotation per stage: result and reason, each reason cut to 300 characters with control characters removed.
It never prints logs, device output, file contents or paths beyond those names. A missing or unreadable record is
reported as `missing`, never as a pass. The step outcomes of setup, run and cleanup come from the environment
(SETUP_OUTCOME, RUN_OUTCOME, CLEANUP_OUTCOME, SYSTEMUI). Always exits 0: the summary reports, it does not decide.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

LIMIT = 300
PRIVATE = ('test-only.keystore', 'avd', 'emulator-home')

def clean(text, limit=LIMIT):
    text = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(text if text is not None else '')).strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'

def annotation(level, title, message):
    """A GitHub workflow command with its data and properties escaped."""
    escape = lambda s: s.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    title = escape(clean(title, 80)).replace(':', '%3A').replace(',', '%2C')
    return f'::{level} title={title}::{escape(clean(message))}'

def read(path):
    try:
        return json.loads(Path(path).read_text()), None
    except FileNotFoundError:
        return None, 'missing'
    except (OSError, ValueError) as error:
        return None, f'unreadable ({type(error).__name__})'

def build(evidence, work, env):
    """(markdown lines, annotation lines)."""
    rows, notes = [], []
    setup, problem = read(Path(evidence)/'emulator-setup.json')
    if setup is None:
        rows.append(('emulator setup', 'missing', problem))
    else:
        rows.append(('emulator setup', clean(setup.get('result'), 40),
            clean(setup.get('version') or setup.get('reason'))))
    results, problem = read(Path(evidence)/'results.json')
    stages = (results or {}).get('stages') if isinstance(results, dict) else None
    if not isinstance(stages, dict):
        rows.append(('experiment', 'missing', problem or 'results.json has no stages'))
    else:
        for name, stage in list(stages.items())[:20]:
            stage = stage if isinstance(stage, dict) else {}
            rows.append((clean(name, 40), clean(stage.get('result'), 20), clean(stage.get('reason'))))
    leftovers = [name for name in PRIVATE if os.path.lexists(Path(work)/name)]
    rows.append(('cleanup', clean(env.get('CLEANUP_OUTCOME') or 'unknown', 20),
        f'still present: {leftovers}' if leftovers else 'test key, AVD and emulator home absent'))
    for name, result, reason in rows:
        level = 'notice' if result in ('pass', 'installed', 'pre-existing', 'success') and not (
            name == 'cleanup' and leftovers) else 'warning'
        notes.append(annotation(level, f'{name}: {result}', reason or result))
    head = [f'## SystemUI persistent-app experiment', '',
        f'SystemUI stages requested: {clean(env.get("SYSTEMUI") or "false", 10)}. Step outcomes: setup '
        f'{clean(env.get("SETUP_OUTCOME") or "unknown", 20)}, run {clean(env.get("RUN_OUTCOME") or "unknown", 20)}.',
        '', '| Stage | Result | Reason |', '| --- | --- | --- |']
    table = [f'| {n} | {r} | {(why or "").replace("|", "/")} |' for n, r, why in rows]
    tail = ['', 'Synthetic emulator evidence only: not GrapheneOS SystemUI, OTA, rollback or Pixel acceptance.']
    return head + table + tail, notes

def main(argv=None, env=os.environ):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--evidence', required=True)
    p.add_argument('--work', required=True)
    p.add_argument('--summary')
    a = p.parse_args(argv)
    lines, notes = build(a.evidence, a.work, env)
    for note in notes:
        print(note)
    if a.summary:
        with open(a.summary, 'a') as summary:
            summary.write('\n'.join(lines) + '\n')
    return 0

if __name__ == '__main__':
    sys.exit(main())
