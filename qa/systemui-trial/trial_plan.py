#!/usr/bin/env python3
"""EXP006-ISOLATED-TRIAL-001 setup plan as data, validated OFFLINE (Infra verifier, QA-owned). It derives the T0
timeline, the root-owned deadline file, the VM-side teardown timer and script, the outbound byte bound and the cleanup
coverage, and checks them. Nothing here touches a host: no VM, firewall, token, key or runner.

  trial_plan.py plan --t0 EPOCH [--egress-budget-mib N]   print the plan JSON
  trial_plan.py validate PLAN.json                        print problems; exit 1 if any
  trial_plan.py scan-leak PATH... < sentinel              harmless-input check: is the sentinel (read from stdin, never
                                                          argv) anywhere in PATH? Fixed-word result; exit 1 if present
A plan that validates is a reviewed plan, not proof that a VM was set up that way.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

MINUTE = 60
TIMELINE = {'sshKeyRemoved': 55, 'sshKeyExpires': 60, 'lastJobStart': 75, 'stopAccepting': 110, 'gceStop': 120}
JOB_MINUTES, MIN_REMAINING_MINUTES = 30, 35   # trial_preflight.MIN_REMAINING: one 30-minute job plus cleanup
MAX_RUN_SECONDS = 7200
EGRESS_USD_PER_GIB, OUTBOUND_RESERVE_USD = 0.12, 1.0   # conservative internet-egress price; the approved reserve
DEADLINE_FILE = '/etc/roseos-systemui-test/deadline'
RUNNER_HOME = '/home/rose-test/actions-runner'
TEARDOWN = '/usr/local/sbin/roseos-trial-teardown'
RECEIPT = '/var/log/roseos-trial-teardown.json'
METADATA_IP = '169.254.169.254'
CLEANUP_OWNERS = {'vm-timer', 'job-cleanup', 'mac-operator', 'browser-operator', 'gce'}
REQUIRED_CLEANUP = {'runner-credentials', 'runner-registration', 'runner-work', 'avd-and-test-keys', 'job-temp',
                    'owned-processes', 'ssh-key', 'iap-ssh-allow-rule', 'ephemeral-ipv4', 'teardown-timer', 'vm-running'}


def utc(epoch):
    return time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(epoch))


def teardown_script():
    """Runs as root at stopAccepting. ROOT prefixes every path (empty on the VM) so the same text runs offline."""
    return f'''#!/bin/bash
# RoseOS SystemUI trial teardown (T+110m). Stops work and removes job-owned state; never unregisters the runner.
set -u
ROOT="${{ROSE_TRIAL_ROOT:-}}"
pkill -TERM -u rose-test -f 'Runner.Listener|Runner.Worker|run.sh' || true
sleep "${{ROSE_TRIAL_GRACE:-10}}"
pkill -KILL -u rose-test || true
rm -f "$ROOT{RUNNER_HOME}/.runner" "$ROOT{RUNNER_HOME}/.credentials" "$ROOT{RUNNER_HOME}/.credentials_rsaparams"
rm -rf "$ROOT{RUNNER_HOME}/_work" "$ROOT/home/rose-test/.android"
find "$ROOT/tmp" "$ROOT/var/tmp" -xdev -user rose-test -mindepth 1 -delete 2>/dev/null || true
left=0
for f in .runner .credentials .credentials_rsaparams _work; do [ -e "$ROOT{RUNNER_HOME}/$f" ] && left=1; done
[ -e "$ROOT/home/rose-test/.android" ] && left=1
pgrep -u rose-test >/dev/null 2>&1 && left=1
state=removed; [ "$left" = 1 ] && state=incomplete
printf '{{"teardown": "%s", "runnerUnregistered": "not-by-timer", "at": %s}}\\n' "$state" "$(date +%s)" > "$ROOT{RECEIPT}"
systemctl disable --now roseos-trial-teardown.timer >/dev/null 2>&1 || true
[ "$state" = removed ]
'''


def nft_ruleset(budget_bytes):
    return f'''table inet roseos_trial {{
  quota egress_budget {{ over {budget_bytes} bytes }}
  chain output {{
    type filter hook output priority filter; policy drop;
    oifname "lo" accept
    meta nfproto ipv6 drop
    ip daddr {METADATA_IP} accept
    tcp dport 443 quota name "egress_budget" drop
    tcp dport 443 accept
    ct state established,related accept
  }}
}}
'''


def plan(t0, egress_budget_mib=1024):
    at = {name: t0 + minutes * MINUTE for name, minutes in TIMELINE.items()}
    budget = egress_budget_mib * 1024 * 1024
    return {
        'schemaVersion': 1, 'kind': 'roseos-systemui-trial-setup-plan', 'status': 'plan-only (offline; not executed)',
        't0': t0, 'timeline': at, 'timelineUtc': {k: utc(v) for k, v in at.items()},
        'gce': {'maxRunDuration': MAX_RUN_SECONDS, 'instanceTerminationAction': 'STOP', 'automaticRestart': False},
        'deadlineFile': {'path': DEADLINE_FILE, 'content': f'{at["stopAccepting"]}\n', 'fileMode': '0444',
                         'fileOwner': 'root:root', 'dirMode': '0755', 'dirOwner': 'root:root', 'writtenAt': 'setup, once'},
        'sshKey': {'removeBy': at['sshKeyRemoved'], 'expireOn': utc(at['sshKeyExpires']).replace(' UTC', '+0000')},
        'teardown': {
            'timer': ('[Unit]\nDescription=RoseOS SystemUI trial teardown (T+110m)\n\n[Timer]\n'
                      f'OnCalendar={utc(at["stopAccepting"])}\nAccuracySec=1s\nPersistent=false\n'
                      'Unit=roseos-trial-teardown.service\n\n[Install]\nWantedBy=timers.target\n'),
            'service': ('[Unit]\nDescription=RoseOS SystemUI trial teardown\n\n[Service]\nType=oneshot\nUser=root\n'
                        f'ExecStart={TEARDOWN}\n'),
            'script': teardown_script(), 'installedBefore': 'the runner accepts work'},
        'outbound': {'budgetBytes': budget, 'nft': nft_ruleset(budget),
                     'scope': 'host output to TCP 443 beyond the GCE metadata server; ingress stays the GCE firewall'},
        'cleanup': [
            {'item': 'runner-credentials', 'owner': 'vm-timer', 'verify': 'teardown receipt removed; files absent'},
            {'item': 'runner-work', 'owner': 'vm-timer', 'verify': '_work absent'},
            {'item': 'avd-and-test-keys', 'owner': 'job-cleanup', 'verify': 'existing experiment cleanup; ~/.android absent after teardown'},
            {'item': 'job-temp', 'owner': 'vm-timer', 'verify': 'no rose-test file under /tmp or /var/tmp'},
            {'item': 'owned-processes', 'owner': 'vm-timer', 'verify': 'pgrep -u rose-test empty'},
            {'item': 'runner-registration', 'owner': 'browser-operator',
             'verify': 'runner roseos-systemui-test-01 absent from group roseos-systemui-test; never claimed by the timer'},
            {'item': 'ssh-key', 'owner': 'mac-operator', 'verify': 'instance ssh-keys entry and Mac-local key removed by T+55m; expireOn T+60m'},
            {'item': 'iap-ssh-allow-rule', 'owner': 'mac-operator', 'verify': 'allow rule absent; deny-ingress kept for review'},
            {'item': 'ephemeral-ipv4', 'owner': 'gce', 'verify': 'released by STOP'},
            {'item': 'teardown-timer', 'owner': 'vm-timer', 'verify': 'timer disabled by the teardown itself'},
            {'item': 'vm-running', 'owner': 'gce', 'verify': 'status TERMINATED by T+120m (maxRunDuration, STOP)'},
        ],
    }


def validate(p):
    problems = []
    at, t0 = p['timeline'], p['t0']
    order = ['sshKeyRemoved', 'sshKeyExpires', 'lastJobStart', 'stopAccepting', 'gceStop']
    if [at[k] for k in order] != sorted(at[k] for k in order) or len({at[k] for k in order}) != len(order):
        problems.append('timeline is not strictly ordered sshKeyRemoved < sshKeyExpires < lastJobStart < stopAccepting < gceStop')
    if at['sshKeyExpires'] - t0 > 60 * MINUTE:
        problems.append('the SSH key outlives T+60m')
    if at['stopAccepting'] - at['lastJobStart'] < MIN_REMAINING_MINUTES * MINUTE:
        problems.append('the last job start leaves less than 35 min before stopAccepting')
    if at['gceStop'] - at['stopAccepting'] < 10 * MINUTE:
        problems.append('less than 10 min between teardown and the GCE STOP')
    gce = p['gce']
    if (gce['maxRunDuration'], gce['instanceTerminationAction'], gce['automaticRestart']) != (MAX_RUN_SECONDS, 'STOP', False) \
            or at['gceStop'] - t0 != gce['maxRunDuration']:
        problems.append('GCE STOP is not maxRunDuration 7200 s with action STOP, no restart, at T+120m')
    d = p['deadlineFile']
    if d['path'] != DEADLINE_FILE or d['content'] != f'{at["stopAccepting"]}\n' or not re.fullmatch(r'[0-9]{10}\n', d['content']):
        problems.append('the deadline file is not one Unix time equal to stopAccepting')
    if (d['fileMode'], d['fileOwner'], d['dirMode'], d['dirOwner']) != ('0444', 'root:root', '0755', 'root:root'):
        problems.append('the deadline file or directory is not root-owned and read-only to others')
    tear = p['teardown']
    if f'OnCalendar={utc(at["stopAccepting"])}\n' not in tear['timer'] or 'Persistent=false' not in tear['timer'] \
            or 'Restart=' in tear['timer'] + tear['service']:
        problems.append('the teardown timer does not fire once at stopAccepting')
    if 'Type=oneshot' not in tear['service'] or 'User=root' not in tear['service'] or f'ExecStart={TEARDOWN}' not in tear['service']:
        problems.append('the teardown service is not a root oneshot of the teardown script')
    script = tear['script']
    for needed in ('.credentials', '.credentials_rsaparams', '.runner', '_work', '.android', 'pkill -KILL -u rose-test',
                   '"runnerUnregistered": "not-by-timer"'):
        if needed not in script:
            problems.append(f'the teardown script lacks {needed}')
    if re.search(r'config\.sh|--token|remove-token|registration-token', script):
        problems.append('the teardown script handles runner tokens; unregistration belongs to the browser operator')
    out = p['outbound']
    nft = out['nft']
    if 'policy drop;' not in nft or 'meta nfproto ipv6 drop' not in nft or f'over {out["budgetBytes"]} bytes' not in nft \
            or 'tcp dport 443 quota name "egress_budget" drop' not in nft:
        problems.append('the outbound ruleset is not default-drop, IPv6-drop and 443-quota bounded')
    if nft.index('quota name "egress_budget" drop') > nft.index('tcp dport 443 accept'):
        problems.append('the 443 quota is evaluated after the 443 accept')
    accepts = [line.strip() for line in nft.splitlines() if line.strip().endswith('accept')]
    allowed = {'oifname "lo" accept', f'ip daddr {METADATA_IP} accept', 'tcp dport 443 accept', 'ct state established,related accept'}
    if set(accepts) - allowed:
        problems.append(f'the outbound ruleset accepts more: {sorted(set(accepts) - allowed)}')
    if out['budgetBytes'] / 2**30 * EGRESS_USD_PER_GIB > OUTBOUND_RESERVE_USD:
        problems.append('the outbound byte budget can exceed the USD 1 reserve')
    items = {c['item']: c for c in p['cleanup']}
    if REQUIRED_CLEANUP - set(items):
        problems.append(f'cleanup misses {sorted(REQUIRED_CLEANUP - set(items))}')
    if any(c['owner'] not in CLEANUP_OWNERS or not c.get('verify') for c in p['cleanup']):
        problems.append('a cleanup item has no known owner or no verification')
    reg = items.get('runner-registration') or {}
    if reg.get('owner') != 'browser-operator' or 'absent' not in reg.get('verify', ''):
        problems.append('runner unregistration is not owned and verified by the browser operator')
    return problems


def scan_leak(paths, sentinel):
    """Fixed words only: how many files hold the sentinel. Never prints a path's contents or the sentinel."""
    needle, hits, scanned = sentinel.encode(), 0, 0
    for root in paths:
        root = Path(root)
        files = [root] if root.is_file() else [p for p in root.rglob('*') if p.is_file() and not p.is_symlink()]
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            scanned += 1
            hits += needle in data
    return {'result': 'present' if hits else 'absent', 'filesScanned': scanned, 'filesWithSentinel': hits}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    pl = sub.add_parser('plan')
    pl.add_argument('--t0', type=int, required=True)
    pl.add_argument('--egress-budget-mib', type=int, default=1024)
    va = sub.add_parser('validate')
    va.add_argument('plan')
    sc = sub.add_parser('scan-leak')
    sc.add_argument('paths', nargs='+')
    a = ap.parse_args(argv)
    if a.command == 'plan':
        print(json.dumps(plan(a.t0, a.egress_budget_mib), indent=1, sort_keys=True))
        return 0
    if a.command == 'validate':
        problems = validate(json.loads(Path(a.plan).read_text()))
        print(json.dumps({'result': 'refused' if problems else 'valid', 'problems': problems}, indent=1))
        return 1 if problems else 0
    sentinel = sys.stdin.readline().strip()
    if len(sentinel) < 12:
        print(json.dumps({'result': 'refused', 'problem': 'sentinel shorter than 12 characters'}))
        return 2
    result = scan_leak(a.paths, sentinel)
    print(json.dumps(result))
    return 1 if result['result'] == 'present' else 0


if __name__ == '__main__':
    sys.exit(main())
