// Self-test for apk-soong-precheck.py with synthetic zips (no SDK needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const script = join(dirname(fileURLToPath(import.meta.url)), 'apk-soong-precheck.py');
const dir = mkdtempSync(join(tmpdir(), 'qa-precheck-'));
// Build zips with Python: stored entries padded via the extra field like zipalign does.
const make = (name, spec) => {
  const out = join(dir, name);
  execFileSync('python3', ['-c', `
import zipfile,sys,json
spec=json.loads(sys.argv[2])
with zipfile.ZipFile(sys.argv[1],'w') as z:
  for n,stored,align in spec:
    i=zipfile.ZipInfo(n); i.compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    if stored and align:
      off=z.fp.tell()+30+len(n.encode())
      i.extra=b'\\0'*((-off)%align)
    z.writestr(i,b'x'*100)
`, out, JSON.stringify(spec)]);
  return out;
};
const run = (apk) => {
  const r = spawnSync('python3', ['-I', script, apk], { encoding: 'utf8' });
  return { code: r.status, report: JSON.parse(r.stdout)[0] };
};

test('aligned, no native libs, deflated dex: no rewrite trigger, dex reported', () => {
  const { code, report } = run(make('ok.apk', [['AndroidManifest.xml', false, 0], ['resources.arsc', true, 4], ['classes.dex', false, 0]]));
  assert.equal(code, 0);
  assert.deepEqual(report.compressedDex, ['classes.dex']);
  assert.equal(report.zipalignCheckP4, true);
});

test('native library is a rewrite trigger', () => {
  const { code, report } = run(make('so.apk', [['AndroidManifest.xml', false, 0], ['lib/arm64-v8a/libx.so', true, 4096]]));
  assert.equal(code, 1);
  assert.deepEqual(report.nativeLibs, ['lib/arm64-v8a/libx.so']);
});

test('misaligned stored entry is a rewrite trigger', () => {
  const { code, report } = run(make('bad.apk', [['a', false, 0], ['resources.arsc', true, 0], ['zz', true, 0]]));
  assert.equal(code, 1);
  assert.ok(report.misaligned.length >= 1);
});
