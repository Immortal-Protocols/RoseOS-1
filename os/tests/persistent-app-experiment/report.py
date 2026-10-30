#!/usr/bin/env python3
"""EXP006: a bounded, non-secret summary of one experiment run for the job page.

  report.py --evidence EDIR --work WORK [--summary FILE]

Reads only EDIR/emulator-setup.json and EDIR/results.json, which this workflow wrote, and checks that the job-local
key and AVD paths under WORK are gone. It writes a short Markdown table (FILE, normally $GITHUB_STEP_SUMMARY) and one
workflow annotation per stage: result and reason, each reason cut to 300 characters with control characters removed.
It never prints logs, device output, file contents or paths beyond those names. A missing or unreadable record is
reported as `missing`, never as a pass. The step outcomes of setup, run and cleanup come from the environment
(SETUP_OUTCOME, RUN_OUTCOME, CLEANUP_OUTCOME, SYSTEMUI). Always exits 0: the summary reports, it does not decide.

EXP006-F3: an explicit functional verdict leads the summary, from the required stages alone: pass only when emulator
setup is usable and every fixture stage (and, with SYSTEMUI true, every SystemUI stage, at least one) passed; fail
when any of them failed; otherwise unavailable, naming the first stage that was missing, unavailable or not run. A
green job or run step never makes it pass. Cleanup is reported separately and does not change it.

EXP006-KVM-001: with KVM_ACL true it also reports EDIR/kvm-access.json, the grant (granted, not-needed or refused) and
its restoration (restored, not-needed or restore-failed); a missing restore record is `not observed`, never restored.
Without KVM_ACL the KVM access row says disabled.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

LIMIT = 300
PRIVATE = ('test-only.keystore', 'avd', 'emulator-home')
FIXTURE = ('environment', 'image', 'device', 'seed', 'ordinary', 'staged', 'restore')
USABLE = ('installed', 'pre-existing')

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

def verdict(setup, stages, systemui):
    """(pass|fail|unavailable, reason) over the required stages; never derived from a job or step outcome."""
    if not isinstance(setup, dict) or setup.get('result') not in USABLE:
        return 'unavailable', 'emulator setup is ' + (clean(setup.get('result'), 40) if isinstance(setup, dict) else 'missing')
    if not isinstance(stages, dict):
        return 'unavailable', 'no stage results'
    required = list(FIXTURE)
    if systemui:
        names = [n for n in stages if str(n).startswith('systemui')]
        if not names:
            return 'unavailable', 'SystemUI stages were requested but none were recorded'
        required += names
    results = {name: (stages.get(name) or {}).get('result') if isinstance(stages.get(name), dict) else None
        for name in required}
    failed = [n for n, r in results.items() if r == 'fail']
    if failed:
        return 'fail', 'failed: ' + ', '.join(failed)
    short = [n for n, r in results.items() if r != 'pass']
    if short:
        return 'unavailable', f'{short[0]} is {results[short[0]] or "missing"}'
    return 'pass', 'every required stage passed'

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
    functional, why = verdict(setup, stages, (env.get('SYSTEMUI') or 'false') == 'true')
    if (env.get('KVM_ACL') or 'false') == 'true':
        kvm, problem = read(Path(evidence)/'kvm-access.json')
        kvm = kvm if isinstance(kvm, dict) else {}
        for key, label in (('grant', 'kvm access'), ('restore', 'kvm restore')):
            entry = kvm.get(key) if isinstance(kvm.get(key), dict) else None
            if entry is None:
                rows.append((label, 'missing' if key == 'grant' else 'not observed', problem or f'no {key} record'))
            else:
                rows.append((label, clean(entry.get('result'), 20), clean(entry.get('reason') or entry.get('result'))))
    else:
        rows.append(('kvm access', 'disabled', 'kvm_acl input false: no ACL change'))
    leftovers = [name for name in PRIVATE if os.path.lexists(Path(work)/name)]
    rows.append(('cleanup', clean(env.get('CLEANUP_OUTCOME') or 'unknown', 20),
        f'still present: {leftovers}' if leftovers else 'test key, AVD and emulator home absent'))
    notes.append(annotation('notice' if functional == 'pass' else 'warning', f'functional verdict: {functional}', why))
    for name, result, reason in rows:
        level = 'notice' if result in ('pass', 'installed', 'pre-existing', 'success', 'granted', 'not-needed', 'restored',
            'disabled') and not (
            name == 'cleanup' and leftovers) else 'warning'
        notes.append(annotation(level, f'{name}: {result}', reason or result))
    head = [f'## SystemUI persistent-app experiment', '', f'**Functional verdict: {functional}** ({clean(why)}). '
        'Job and step success do not imply it.', '',
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
