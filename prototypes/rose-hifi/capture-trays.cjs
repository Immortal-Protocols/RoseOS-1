// Rose HiFi — bottom-tray option captures (HIFI-TRAY-OPTIONS).
// Usage: node prototypes/rose-hifi/capture-trays.cjs <outDir> [options...]
// For the current default (option 0) and options 1–10, renders identical scenes:
//   home-NN.png       folded Home, page 1, call disconnected, no tasks (phone incl. bezel)
//   home-voice-muted-NN.png  bottom 230 CSS px of the same Home with Rose's voice (output) muted
//                     via the real tray control (options 1–10; the default has no voice control)
//   rose-tray-NN.png  populated Rose (connected, speaking, Saturday result), bottom 230 CSS px
//                     of the phone at the same scale for every option
// Records commit, HTML hash, viewport/DPR, renderer, per-shot state and PNG SHA-256 in
// capture-trays.json. Headless Chromium + SwiftShader: not phone/GPU evidence.
'use strict';
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execSync } = require('child_process');
const { chromium } = require('playwright');

const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out', 'trays'));
const OPTIONS = process.argv.slice(3).length ? process.argv.slice(3).map(Number) : [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const FILE = path.join(__dirname, 'Rose-HiFi.html');
const VIEWPORT = { width: 1280, height: 900 };
const DPR = 2;
const CROP_CSS = 230;
fs.mkdirSync(OUT, { recursive: true });
const sh = (c) => { try { return execSync(c, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const pad = (n) => String(n).padStart(2, '0');

(async () => {
  const browser = await chromium.launch();
  const shots = [];
  const errors = [];
  let rendererName = null;
  for (const n of OPTIONS) {
    const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: DPR });
    page.on('pageerror', (e) => errors.push('option ' + n + ': ' + e.message));
    page.on('console', (m) => { if (m.type() === 'error') errors.push('option ' + n + ': ' + m.text()); });
    await page.goto('file://' + FILE + (n ? '?tray=' + n : ''));
    await page.waitForTimeout(1200);
    await page.mouse.move(VIEWPORT.width - 20, 20);
    const tray = await page.evaluate(() => document.getElementById('screen').dataset.tray || '');
    if (tray !== (n ? String(n) : '')) throw new Error('option ' + n + ' not applied (data-tray="' + tray + '")');
    rendererName = rendererName || await page.evaluate(() => window.__rose.renderer.status().renderer);
    const state = () => page.evaluate(() => { const s = window.__rose.getState(); return { view: s.view, layout: s.layout, homePage: s.homePage, connection: s.connection, muted: s.muted, voiceMuted: s.voiceMuted, speaking: s.speaking, result: s.result }; });
    // Touch targets of the visible tray controls, in CSS px.
    const targets = () => page.evaluate(() => {
      const k = document.getElementById('screen').getBoundingClientRect().width / document.getElementById('screen').offsetWidth;
      const view = document.querySelector('.view[data-active=true]');
      return [...view.querySelectorAll('.tray .well')].map((w) => { const r = w.getBoundingClientRect(); return [w.id, Math.round(r.width / k), Math.round(r.height / k)]; });
    });

    const homeFile = path.join(OUT, 'home-' + pad(n) + '.png');
    const homeBuf = await page.locator('#device').screenshot({ path: homeFile });
    shots.push({ option: n, scene: 'home', file: path.basename(homeFile), sha256: sha(homeBuf), state: await state(), targets: await targets() });

    if (n) {
      // Rose's voice (output) mute through the real control, then back on for the Rose capture.
      await page.locator('#tray-voice').click();
      await page.waitForTimeout(500);
      await page.mouse.move(VIEWPORT.width - 20, 20);
      const hb = await page.locator('#device').boundingBox();
      const mutedFile = path.join(OUT, 'home-voice-muted-' + pad(n) + '.png');
      const mutedBuf = await page.screenshot({ path: mutedFile, clip: { x: hb.x, y: hb.y + hb.height - CROP_CSS, width: hb.width, height: CROP_CSS } });
      const ms = await state();
      if (!ms.voiceMuted || ms.muted) throw new Error('option ' + n + ': voice mute did not apply cleanly');
      shots.push({ option: n, scene: 'home-voice-muted', file: path.basename(mutedFile), sha256: sha(mutedBuf), state: ms, targets: await targets() });
      await page.locator('#tray-voice').click();
      await page.waitForTimeout(300);
    }
    await page.locator('#tray-rose').click();
    await page.waitForFunction(() => { const s = window.__rose.getState(); return s.connection === 'connected' && s.result === 'saturday' && s.speaking; }, null, { timeout: 8000 });
    await page.waitForTimeout(900);
    await page.mouse.move(VIEWPORT.width - 20, 20);
    const box = await page.locator('#device').boundingBox();
    const roseFile = path.join(OUT, 'rose-tray-' + pad(n) + '.png');
    const roseBuf = await page.screenshot({ path: roseFile, clip: { x: box.x, y: box.y + box.height - CROP_CSS, width: box.width, height: CROP_CSS } });
    shots.push({ option: n, scene: 'rose-tray', file: path.basename(roseFile), sha256: sha(roseBuf), state: await state(), targets: await targets() });
    await page.close();
  }
  const meta = {
    capturedUtc: new Date().toISOString(),
    commit: sh('git rev-parse HEAD'),
    dirty: !!sh('git status --porcelain -- .'),
    htmlSha256: sha(fs.readFileSync(FILE)),
    browser: 'Chromium ' + browser.version() + ' (Playwright, headless)',
    renderer: rendererName,
    viewport: VIEWPORT, deviceScaleFactor: DPR,
    scenes: { home: '#device (phone incl. bezel), folded Home page 1, call disconnected, mic on, Rose voice on, no tasks', 'home-voice-muted': 'bottom ' + CROP_CSS + ' CSS px of the same Home after tapping the voice control (Rose voice muted, mic on)', 'rose-tray': 'bottom ' + CROP_CSS + ' CSS px of #device, populated Rose: connected, speaking, mic on, Saturday result' },
    limitations: 'Headless Chromium with SwiftShader software WebGL. Not phone, GPU, touch or performance evidence.',
    shots, errors,
  };
  fs.writeFileSync(path.join(OUT, 'capture-trays.json'), JSON.stringify(meta, null, 2));
  for (const s of shots) console.log(pad(s.option), s.scene.padEnd(9), s.sha256.slice(0, 12), JSON.stringify(s.targets));
  console.log('errors', errors.length, errors.slice(0, 3));
  await browser.close();
  process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
