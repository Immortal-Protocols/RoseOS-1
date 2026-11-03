// Rose HiFi — reproducible review screenshots.
// Usage: node prototypes/rose-hifi/capture.cjs <outDir> [scene ...]
// Scenes: home, rose (populated Rose: connected, speaking, formatted result).
// Same browser route as browser-check.cjs (Playwright Chromium, file://).
// Writes <scene>.png plus capture.json with revision, viewport, DPR, renderer,
// simulated state and PNG SHA-256. Headless Chromium uses SwiftShader software
// WebGL: these images are not phone/GPU evidence.
'use strict';
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execSync } = require('child_process');
const { chromium } = require('playwright');

const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out', 'capture'));
const SCENES = process.argv.slice(3).length ? process.argv.slice(3) : ['home', 'rose'];
const FILE = path.join(__dirname, 'Rose-HiFi.html');
const VIEWPORT = { width: 1280, height: 900 };
const DPR = 2;
fs.mkdirSync(OUT, { recursive: true });

const sh = (cmd) => { try { return execSync(cmd, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };
const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: DPR });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('file://' + FILE);
  await page.waitForTimeout(1200);
  // Park the pointer away from the phone so the sheen sits at its rest position.
  await page.mouse.move(VIEWPORT.width - 20, 20);
  const shots = [];
  for (const scene of SCENES) {
    if (scene === 'home') {
      await page.locator('#h-reset').click();
      await page.waitForTimeout(900);
    } else if (scene === 'rose') {
      await page.locator('#h-reset').click();
      await page.waitForTimeout(600);
      await page.locator('#tray-rose').click();
      // Connect (≈1.1 s), then Rose answers with the formatted Saturday result.
      await page.waitForFunction(() => { const s = window.__rose.getState(); return s.connection === 'connected' && s.result === 'saturday' && s.speaking; }, null, { timeout: 8000 });
      await page.waitForTimeout(900);
    } else throw new Error('unknown scene ' + scene);
    await page.mouse.move(VIEWPORT.width - 20, 20);
    const file = path.join(OUT, scene + '.png');
    const buf = await page.locator('#device').screenshot({ path: file });
    const st = await page.evaluate(() => { const s = window.__rose.getState(); return { view: s.view, layout: s.layout, homePage: s.homePage, connection: s.connection, muted: s.muted, speaking: s.speaking, result: s.result, locked: s.locked, tasks: Object.fromEntries(Object.entries(s.tasks).map(([k, v]) => [k, v.state])) }; });
    const r = await page.evaluate(() => window.__rose.renderer.status());
    shots.push({ scene, file: path.basename(file), sha256: sha(buf), state: st, renderer: { mode: r.mode, signals: r.signals } });
  }
  const meta = {
    capturedUtc: new Date().toISOString(),
    commit: sh('git rev-parse HEAD'),
    dirty: sh('git status --porcelain -- .') ? true : false,
    htmlSha256: sha(fs.readFileSync(FILE)),
    browser: 'Chromium ' + browser.version() + ' (Playwright, headless)',
    renderer: await page.evaluate(() => window.__rose.renderer.status().renderer),
    viewport: VIEWPORT, deviceScaleFactor: DPR, element: '#device (phone incl. bezel)',
    limitations: 'Headless Chromium with SwiftShader software WebGL. Not phone, GPU, touch or performance evidence.',
    shots, errors,
  };
  fs.writeFileSync(path.join(OUT, 'capture.json'), JSON.stringify(meta, null, 2));
  console.log(JSON.stringify(meta, null, 2));
  await browser.close();
  process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
