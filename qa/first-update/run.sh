#!/usr/bin/env bash
# Independent QA offline evidence run for the First Update milestone.
# Offline only: no network, VM, phone, cloud, signing or credential access.
# Prerequisite: npm ci --prefix workspace/rose-agent/server (see scripts/bootstrap.sh).
# Writes a receipt under out/qa-first-update/ (ignored). Exit status is non-zero if any check fails.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
OUT="out/qa-first-update/$(git rev-parse --short=12 HEAD)-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
RESULTS="$OUT/results.tsv"; : > "$RESULTS"
run(){ local name="$1"; shift; "$@" > "$OUT/${name//[\/:]/_}.log" 2>&1; local rc=$?; printf '%s\t%s\n' "$name" "$rc" >> "$RESULTS"; printf '%-40s %s\n' "$name" "$([ $rc -eq 0 ] && echo pass || echo "FAIL ($rc)")"; }

# 1. Repository default offline suite.
run default-suite bash scripts/test-local.sh

# 2. Criterion-relevant offline tests that scripts/test-local.sh does not run.
for f in server/background-phone.test.mjs server/launcher-task.test.mjs server/task-status-transport.test.mjs \
  server/completion-progress.test.mjs server/visual-verification.test.mjs server/task-evidence.test.mjs \
  server/voice-routing.test.mjs voice-providers/task-progress.test.cjs tests/phone-app-context.test.cjs \
  server/tool-activity.test.mjs server/checker-completion-retry.test.mjs conversation/work.test.mjs \
  conversation/remote-work.test.mjs decisions/execution-trace.test.mjs server/live-handoff-trace.test.mjs \
  server/task-setup.test.mjs server/phone-vision-policy.test.mjs voice-providers/app-worker-policy.test.mjs; do
  run "rose-agent:$f" node --test "workspace/rose-agent/$f"
done

# 3. tests/agent-app-trust.test.cjs hardcodes a macOS JDK path; run its embedded Java check with the system JDK.
trust(){
  local d rc; d="$(mktemp -d)"
  sed -n '/`package dev.pixel.voice;/,/}} `/p' workspace/rose-agent/tests/agent-app-trust.test.cjs \
    | sed '1s/.*`package/package/; $s/}} `.*/}}/' > "$d/TrustTest.java"
  javac -d "$d" workspace/rose-agent/app/src/dev/pixel/voice/AgentAppTrust.java "$d/TrustTest.java" &&
  [ "$(java -cp "$d" dev.pixel.voice.TrustTest 2>/dev/null)" = passed ]; rc=$?
  rm -rf "$d"; return $rc
}
run agent-app-trust-system-jdk trust

# 4. Independent Stop acceptance gate (expected to fail until Stop is fixed end to end).
run stop-gate node --test qa/first-update/stop-gate.test.mjs
run server-stop-gate node --test qa/first-update/server-stop-gate.test.mjs

python3 - "$OUT" <<'PY'
import json, subprocess, sys, pathlib, platform
out = pathlib.Path(sys.argv[1])
v = lambda *c: subprocess.run(c, capture_output=True, text=True).stdout.strip() or subprocess.run(c, capture_output=True, text=True).stderr.strip().splitlines()[-1]
rows = [l.split('\t') for l in (out / 'results.tsv').read_text().splitlines()]
receipt = {
  'source_commit': v('git', 'rev-parse', 'HEAD'),
  'worktree_clean': not subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout.strip(),
  'tools': {'node': v('node', '--version'), 'python': platform.python_version(), 'javac': v('javac', '-version')},
  'results': {name: ('pass' if rc == '0' else 'fail') for name, rc in rows},
  'scope': 'offline unit/fixture tests only; no Android build, VM, Pixel Voice runtime, phone, release channel or signing',
}
(out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print('Receipt:', out / 'receipt.json')
PY
! grep -qv $'\t0$' "$RESULTS"
