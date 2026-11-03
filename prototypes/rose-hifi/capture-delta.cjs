// Rose HiFi — full-screen context captures for a delta packet.
// Usage: node prototypes/rose-hifi/capture-delta.cjs <outDir> <option>:<scene> ...
// scene: home | home-voice-muted | rose (populated: connected, speaking, Saturday result).
// Same browser route/viewport as capture-trays.cjs. Headless Chromium + SwiftShader:
// not phone/GPU evidence.
'use strict';
const path = require('path'), fs = require('fs'), crypto = require('crypto');
const { execSync } = require('child_process');
const { chromium } = require('playwright');
const OUT = path.resolve(process.argv[2]);
const JOBS = process.argv.slice(3).map((j) => j.split(':'));
const FILE = path.join(__dirname, 'Rose-HiFi.html');
const VIEWPORT = { width: 1280, height: 900 }, DPR = 2;
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const sh = (c) => { try { return execSync(c, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };
fs.mkdirSync(OUT, { recursive: true });
(async () => {
  const browser = await chromium.launch(); const shots = [], errors = [];
  for (const [n, scene] of JOBS) {
    const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: DPR });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('file://' + FILE + '?tray=' + n); await page.waitForTimeout(1200);
    if (scene === 'home-voice-muted') { await page.locator('#tray-voice').click(); await page.waitForTimeout(500); }
    if (scene === 'rose') { await page.locator('#tray-rose').click(); await page.waitForFunction(() => { const s = window.__rose.getState(); return s.connection === 'connected' && s.result === 'saturday' && s.speaking; }, null, { timeout: 8000 }); await page.waitForTimeout(900); }
    await page.mouse.move(VIEWPORT.width - 20, 20); await page.waitForTimeout(200);
    const file = path.join(OUT, 'context-' + String(n).padStart(2, '0') + '-' + scene + '.png');
    const buf = await page.locator('#device').screenshot({ path: file });
    const st = await page.evaluate(() => { const s = window.__rose.getState(); return { tray: document.getElementById('screen').dataset.tray, view: s.view, connection: s.connection, muted: s.muted, voiceMuted: s.voiceMuted, speaking: s.speaking, result: s.result }; });
    shots.push({ option: +n, scene, file: path.basename(file), sha256: sha(buf), state: st }); await page.close();
  }
  const meta = { capturedUtc: new Date().toISOString(), commit: sh('git rev-parse HEAD'), dirty: !!sh('git status --porcelain -- .'), htmlSha256: sha(fs.readFileSync(FILE)),
    browser: 'Chromium ' + browser.version() + ' (Playwright, headless)', viewport: VIEWPORT, deviceScaleFactor: DPR,
    limitations: 'Headless Chromium with SwiftShader software WebGL. Not phone, GPU, touch or performance evidence.', shots, errors };
  fs.writeFileSync(path.join(OUT, 'capture-delta.json'), JSON.stringify(meta, null, 2));
  console.log(meta.commit, 'dirty', meta.dirty, shots.map((s) => s.file + ' ' + s.sha256.slice(0, 12)).join(' | '), 'errors', errors.length);
  await browser.close(); process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
