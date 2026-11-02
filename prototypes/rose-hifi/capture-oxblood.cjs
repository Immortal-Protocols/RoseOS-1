// Rose HiFi — Oxblood review captures.
// Usage: node prototypes/rose-hifi/capture-oxblood.cjs <outDir>
// Full-phone contexts (folded unless noted) and same-scale closeups, all driven through the real
// controls. Records commit, HTML hash, viewport/DPR, state and PNG SHA-256 in capture-oxblood.json.
// Headless Chromium + SwiftShader: not phone, GPU, touch or performance evidence.
'use strict';
const path = require('path'), fs = require('fs'), crypto = require('crypto');
const { execSync } = require('child_process');
const { chromium } = require('playwright');
const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out', 'oxblood'));
const FILE = 'file://' + path.join(__dirname, 'Rose-HiFi.html');
const VIEWPORT = { width: 1280, height: 900 }, DPR = 2;
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const sh = (c) => { try { return execSync(c, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: DPR });
  const errors = []; const shots = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(FILE); await page.waitForTimeout(1200);
  const rest = async (ms) => { await page.mouse.move(VIEWPORT.width - 20, 20); await page.waitForTimeout(ms || 600); };
  const state = () => page.evaluate(() => { const s = window.__rose.getState(); return { theme: document.getElementById('device').dataset.theme, view: s.view, layout: s.layout, connection: s.connection, muted: s.muted, voiceMuted: s.voiceMuted, speaking: s.speaking, result: s.result, pendingStop: s.pendingStop, tasks: Object.fromEntries(Object.entries(s.tasks).map(([k, v]) => [k, v.state])) }; });
  const full = async (name, note) => { await rest(); const f = path.join(OUT, name + '.png'); const b = await page.locator('#device').screenshot({ path: f }); shots.push({ file: name + '.png', kind: 'context', note, sha256: sha(b), state: await state() }); };
  // Closeup: an element's box padded and clipped to the phone, same DPR for every closeup.
  const close = async (name, sel, pad, note) => {
    await rest(300);
    const d = await page.locator('#device').boundingBox(), e = await page.locator(sel).boundingBox();
    const x = Math.max(d.x, e.x - pad), y = Math.max(d.y, e.y - pad), r = Math.min(d.x + d.width, e.x + e.width + pad), btm = Math.min(d.y + d.height, e.y + e.height + pad);
    const f = path.join(OUT, name + '.png'); const b = await page.screenshot({ path: f, clip: { x, y, width: r - x, height: btm - y } });
    shots.push({ file: name + '.png', kind: 'closeup', note, sha256: sha(b), state: await state() });
  };
  if (await page.evaluate(() => document.getElementById('device').dataset.theme) !== 'oxblood') throw new Error('Oxblood is not active');

  await full('01-home', 'Home page 1 · call disconnected · mic on · Rose voice on · no tasks');
  await close('c1-home-tray', '#home-tray', 26, 'Home tray: Rose · Rose voice (output) · overview');
  await page.locator('#tray-voice').click(); await page.waitForTimeout(400);
  await close('c2-home-tray-voice-muted', '#home-tray', 26, 'Home tray after tapping Rose voice: output muted, mic untouched');
  await full('02-home-voice-muted', 'Home · Rose voice (output) muted');
  await page.locator('#tray-voice').click(); await page.waitForTimeout(300);

  await page.locator('#tray-rose').click();
  await page.waitForFunction(() => { const s = window.__rose.getState(); return s.connection === 'connected' && s.result === 'saturday' && s.speaking; }, null, { timeout: 8000 });
  await page.waitForTimeout(900);
  await full('03-rose', 'Populated Rose · connected · speaking · mic on · no background work');
  await close('c3-rose-tray', '#v-rose .tray', 26, 'Rose tray: microphone and end call only');
  await close('c4-rose-work-idle', '#rose-work', 22, 'Background-work entry, no running work');
  await page.locator('#h-tasks').click(); await page.waitForTimeout(600);
  await page.locator('#rose-mic').click(); await page.waitForTimeout(400);
  await close('c5-rose-work-active', '#rose-work', 22, 'Background-work entry while 3 tasks run');
  await close('c6-rose-tray-mic-muted', '#v-rose .tray', 26, 'Rose tray with microphone muted (call continues)');
  await full('04-rose-mic-muted-working', 'Rose · mic muted · call connected · 3 tasks running');
  await page.locator('#rose-mic').click(); await page.waitForTimeout(300);

  await page.locator('#rose-work').click(); await page.waitForTimeout(2800);
  await full('05-agents', "Rose's apps (Background Work Manager) · folded · call connected · tasks running");
  await close('c7-agents-tray', '#v-agents .tray', 26, "Rose's apps tray: back · end call · manager");
  await page.locator('#agrid [data-window=w-mail] [data-stop]').click(); await page.waitForTimeout(500);
  await full('06-stop-confirm', 'Stop confirmation for one task (Draft a reply to Jamie)');
  await page.locator('#sheet-cancel').click(); await page.waitForTimeout(400);
  await page.locator('#h-unfolded').click(); await page.waitForTimeout(1000);
  await full('07-agents-unfolded', "Rose's apps · unfolded · eight windows");
  await page.locator('#h-folded').click(); await page.waitForTimeout(800);

  await page.locator('#home-bar').click(); await page.waitForTimeout(600);
  await page.locator('#tray-overview').click(); await page.waitForTimeout(1400);
  await full('08-switcher', 'System switcher · human carousel · agent carousel · no tray');
  await page.locator('#home-bar').click(); await page.waitForTimeout(600);
  await page.locator('#h-unfolded').click(); await page.waitForTimeout(1000);
  await full('09-home-unfolded', 'Home · unfolded · tasks running');

  const meta = { capturedUtc: new Date().toISOString(), commit: sh('git rev-parse HEAD'), dirty: !!sh('git status --porcelain -- .'), htmlSha256: sha(fs.readFileSync(path.join(__dirname, 'Rose-HiFi.html'))),
    browser: 'Chromium ' + browser.version() + ' (Playwright, headless)', renderer: await page.evaluate(() => window.__rose.renderer.status().renderer),
    viewport: VIEWPORT, deviceScaleFactor: DPR,
    demoData: 'All app content, tasks and results are prototype fixtures; procedural previews are not live application data.',
    limitations: 'Headless Chromium with SwiftShader software WebGL. Not phone, GPU, touch or performance evidence.', shots, errors };
  fs.writeFileSync(path.join(OUT, 'capture-oxblood.json'), JSON.stringify(meta, null, 2));
  console.log(meta.commit, 'dirty', meta.dirty, shots.length, 'shots', 'errors', errors.length);
  await browser.close(); process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
