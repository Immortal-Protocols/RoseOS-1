// Rose HiFi — real-browser walkthrough with screenshots.
// Usage: node prototypes/rose-hifi/browser-check.cjs [outDir]
// Requires Playwright + Chromium (pre-installed in the authoring container). Opens
// the built file over file://, drives the UI with real pointer/keyboard input,
// records console/page errors, renderer status and frame intervals.
// SwiftShader/software rendering is NOT device GPU evidence.
'use strict';
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out'));
const FILE = 'file://' + path.join(__dirname, 'Rose-HiFi.html');
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail: detail === undefined ? '' : detail }); };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  const warnings = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); else if (m.type() === 'warning') warnings.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(FILE);
  await page.waitForTimeout(800);
  const st = () => page.evaluate(() => window.__rose.getState());
  const rs = () => page.evaluate(() => window.__rose.renderer.status());
  const shot = async (name) => { await page.locator('#device').screenshot({ path: path.join(OUT, name + '.png') }); };
  const settle = (ms) => page.waitForTimeout(ms || 700);

  let r = await rs();
  check('renderer initialises WebGL', r.mode === 'webgl', r.mode + ' · ' + r.renderer + (r.failed ? ' · ' + r.failed : ''));
  check('home view at load', (await st()).view === 'home');
  await shot('01-home');

  // Swipe Home pages with a real pointer drag; tray must stay put.
  const trayBefore = await page.locator('#home-tray').boundingBox();
  const pages = await page.locator('#home-pages').boundingBox();
  await page.mouse.move(pages.x + pages.width * 0.8, pages.y + 300);
  await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(pages.x + pages.width * (0.8 - i * 0.06), pages.y + 300);
  await page.screenshot({ path: path.join(OUT, '02-home-mid-swipe-fullpage.png') });
  const trayDuring = await page.locator('#home-tray').boundingBox();
  await page.mouse.up();
  await settle();
  check('swipe advances to the full app page', (await st()).homePage === 1);
  check('tray fixed during swipe', JSON.stringify(trayBefore) === JSON.stringify(trayDuring));
  await shot('03-home-app-page');
  await page.locator('#page-dots button').first().click();
  await settle();
  check('page dot returns to first page', (await st()).homePage === 0);

  // Home → Rose
  await page.locator('#tray-rose').click();
  await page.waitForTimeout(260);
  await shot('04-rose-opening-transition');
  await page.waitForTimeout(500);
  let s = await st();
  check('Rose opens and connects', s.view === 'rose' && s.connection === 'connecting', s.connection);
  await shot('05-rose-connecting');
  await page.waitForTimeout(1500);
  s = await st();
  check('connected cue only', s.connection === 'connected' && s.notices.every((n) => n.text === 'Connected'), JSON.stringify(s.notices.map((n) => n.text)));
  await page.waitForTimeout(1400);
  s = await st();
  check('Rose speaks with result', s.speaking && s.result === 'saturday');
  await shot('06-rose-speaking-result');

  // Mute while Rose speaks: speech continues, mic silver.
  await page.locator('#rose-mic').click();
  await page.waitForTimeout(300);
  s = await st();
  check('mute while speaking keeps speech', s.muted && s.speaking);
  r = await rs();
  check('renderer mic=0 when muted', r.signals.mic === 0 && r.signals.voice === 2, JSON.stringify(r.signals));
  await page.waitForTimeout(500);
  await shot('07-rose-muted-speaking');
  await page.waitForTimeout(4000);
  s = await st();
  check('speech ends independently while muted', !s.speaking && s.muted);
  await shot('08-rose-muted-quiet');
  await page.keyboard.press('Tab');
  await page.locator('#rose-mic').focus();
  await page.keyboard.press('Enter');
  await page.waitForTimeout(400);
  s = await st();
  check('keyboard unmute', !s.muted && s.notices.some((n) => n.text === 'Unmuted'));
  await shot('09-rose-unmuted-listening');

  // Start tasks, then end call: tasks must survive.
  await page.locator('#h-tasks').click();
  await page.locator('#rose-end').click();
  await page.waitForTimeout(250);
  await shot('10-end-call-transition');
  await page.waitForTimeout(700);
  s = await st();
  check('end call returns Home', s.view === 'home' && s.connection === 'disconnected');
  check('end call keeps tasks running', ['saturday', 'reply'].every((id) => s.tasks[id].state === 'running'), JSON.stringify(s.tasks));
  check('call ended cue', s.notices.some((n) => n.text === 'Call ended'));
  await shot('11-home-after-call');

  const allowed = ['Connected', 'Muted', 'Unmuted', 'Call ended'];
  const text = await page.locator('#screen').innerText();
  check('no Interrupt control / composer', !/interrupt/i.test(text) && (await page.locator('#screen textarea, #screen input[type=text]').count()) === 0);

  // Reduced motion + static fallback + context loss + hidden suspension.
  await page.locator('#tray-rose').click();
  await page.waitForTimeout(2200);
  await page.locator('#h-reduced').check();
  await page.waitForTimeout(400);
  r = await rs();
  check('reduced motion stops animation loop', r.reduced && !r.animating, JSON.stringify({ reduced: r.reduced, animating: r.animating }));
  await shot('12-rose-reduced-motion');
  await page.locator('#h-reduced').uncheck();
  await page.locator('#h-static').check();
  await page.waitForTimeout(400);
  r = await rs();
  check('static fallback visible', r.mode === 'static-forced' && await page.locator('#rose-fallback').isVisible());
  await shot('13-rose-static-fallback');
  await page.locator('#h-static').uncheck();
  await page.locator('#h-lose').click();
  await page.waitForTimeout(400);
  r = await rs();
  check('context loss → fallback', r.mode === 'static-context-lost', r.mode);
  await shot('14-context-lost');
  await page.locator('#h-lose').click();
  await page.waitForTimeout(800);
  r = await rs();
  check('context restored → WebGL', r.mode === 'webgl', r.mode);
  const drawsBefore = r.draws;
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForTimeout(600);
  const d1 = (await rs()).draws; await page.waitForTimeout(600); const d2 = (await rs()).draws;
  check('hidden page suspends drawing', d1 === d2, d1 + ' → ' + d2);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForTimeout(600);
  check('visible page resumes drawing', (await rs()).draws > d2);

  // Resize + unfold preserve call state.
  await page.locator('#h-unfolded').click();
  await page.waitForTimeout(800);
  s = await st();
  check('unfold preserves call/tasks/view', s.layout === 'unfolded' && s.connection === 'connected' && s.view === 'rose' && s.tasks.saturday.state !== 'idle');
  await shot('15-rose-unfolded');
  await page.setViewportSize({ width: 900, height: 720 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, '16-page-900x720.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, '17-page-390x844.png') });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  check('no horizontal page overflow at 390 px', !overflow);

  // ---------- Hour two: switcher, agents, windows, stop ----------
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator('#h-folded').click();
  await page.locator('#h-reset').click();
  await settle(500);
  await page.locator('.home-page.sparse .app[data-app=camera]').click();
  await settle(400);
  check('home app opens a simulated app view', (await st()).view === 'app' && (await st()).app === 'camera');
  await shot('20-app-camera');
  await page.locator('#home-bar').click();
  await settle(400);
  await page.locator('#tray-rose').click();
  await page.waitForTimeout(1600);
  await page.locator('#h-tasks').click();
  await page.locator('#rose-agents').click();
  await settle(500);
  s = await st();
  check('agents screen from Rose keeps call', s.view === 'agents' && s.connection === 'connected');
  await page.waitForTimeout(2800);
  await shot('21-agents-folded');
  await page.locator('#h-unfolded').click();
  await settle(900);
  const cardsVisible = await page.evaluate(() => { const s = document.getElementById('screen').getBoundingClientRect(); return [...document.querySelectorAll('#agrid .wcard')].filter((c) => { const r = c.getBoundingClientRect(); return r.top >= s.top && r.bottom <= s.bottom && r.left >= s.left && r.right <= s.right; }).length; });
  check('eight windows fully visible unfolded', cardsVisible === 8, cardsVisible);
  const trayTop = await page.evaluate(() => document.querySelector('#v-agents .tray').getBoundingClientRect().top);
  const maxCardBottom = await page.evaluate(() => Math.max(...[...document.querySelectorAll('#agrid .wcard')].map((c) => c.getBoundingClientRect().bottom)));
  check('stop controls not obscured by tray', maxCardBottom <= trayTop, maxCardBottom + ' <= ' + trayTop);
  await shot('22-agents-unfolded-8');

  // Stop: cancel, then confirm for one task; unrelated task continues.
  await page.locator('#agrid [data-window=w-mail] [data-stop]').click();
  await settle(400);
  s = await st();
  check('stop asks for confirmation', s.pendingStop === 'reply');
  await shot('23-stop-confirm-unfolded');
  await page.locator('#h-folded').click();
  await settle(700);
  check('fold preserves open confirmation', (await st()).pendingStop === 'reply' && await page.locator('.sheet').isVisible());
  await shot('24-stop-confirm-folded');
  await page.locator('#sheet-cancel').click();
  await settle(300);
  s = await st();
  check('cancel preserves task', !s.pendingStop && s.tasks.reply.state === 'running');
  await page.locator('#agrid [data-window=w-files] [data-stop]').focus();
  await page.keyboard.press('Enter');
  await settle(300);
  await page.locator('[data-confirm-stop]').click();
  await page.waitForTimeout(250);
  s = await st();
  check('confirm shows Stopping', s.tasks.reply.state === 'stopping' && s.tasks.saturday.state === 'running');
  await shot('25-stopping');
  await page.waitForTimeout(1800);
  s = await st();
  check('acknowledged Stopped; unrelated task continues', s.tasks.reply.state === 'stopped' && s.tasks.saturday.state === 'running', JSON.stringify(s.tasks));
  const stoppedLabels = await page.locator('#agrid .wcard[data-state=stopped]').count();
  check('both windows of the stopped task update', stoppedLabels === 2, stoppedLabels);
  await shot('26-stopped');

  // Window detail from agents; observation doesn't cancel work.
  await page.locator('#agrid [data-open-window=w-maps]').click();
  await settle(500);
  s = await st();
  check('window detail opens', s.view === 'window' && s.window === 'w-maps' && s.tasks.saturday.state === 'running');
  await shot('27-window-detail');
  await page.keyboard.press('Escape');
  await settle(400);
  check('Escape returns to agents', (await st()).view === 'agents');

  // Switcher: two independent carousels.
  await page.locator('#home-bar').click();
  await settle(400);
  await page.locator('#tray-overview').click();
  await settle(700);
  check('switcher opens', (await st()).view === 'switcher');
  await shot('28-switcher');
  const hBefore = await page.evaluate(() => document.getElementById('sw-human').scrollLeft);
  const aBefore = await page.evaluate(() => document.getElementById('sw-agent').scrollLeft);
  await page.locator('#sw-agent').focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await settle(800);
  const hAfter = await page.evaluate(() => document.getElementById('sw-human').scrollLeft);
  const aAfter = await page.evaluate(() => document.getElementById('sw-agent').scrollLeft);
  check('agent carousel moves independently', aAfter > aBefore && hAfter === hBefore, aBefore + '→' + aAfter + ' / human ' + hBefore + '→' + hAfter);
  const hb = await page.locator('#sw-human').boundingBox();
  await page.mouse.move(hb.x + hb.width * 0.7, hb.y + hb.height / 2); await page.mouse.down();
  for (let i = 1; i <= 8; i++) await page.mouse.move(hb.x + hb.width * (0.7 - i * 0.05), hb.y + hb.height / 2);
  await page.mouse.up();
  await settle(800);
  const aAfter2 = await page.evaluate(() => document.getElementById('sw-agent').scrollLeft);
  const hAfter2 = await page.evaluate(() => document.getElementById('sw-human').scrollLeft);
  check('human carousel drags independently', hAfter2 !== hAfter && aAfter2 === aAfter, hAfter + '→' + hAfter2 + ' / agent ' + aAfter + '→' + aAfter2);
  await shot('29-switcher-scrolled');
  const working = await page.evaluate(() => document.getElementById('v-switcher').classList.contains('working'));
  check('connector animates while work runs', working);
  await page.locator('#sw-agent [data-open-window]').first().click();
  await settle(500);
  check('agent selection opens window detail', (await st()).view === 'window');
  await page.locator('#home-bar').click();
  await settle(300);
  await page.locator('#tray-overview').click();
  await settle(600);
  await page.locator('#sw-human .hcard [data-open-app]').nth(0).click({ force: true });
  await settle(500);
  check('human selection foregrounds that app', (await st()).view === 'app');

  // Lock/unlock: work continues.
  const stepBefore = (await st()).tasks.saturday.step;
  await page.locator('#h-lock').click();
  await page.waitForTimeout(3000);
  await shot('30-locked');
  await page.locator('#unlock').click();
  s = await st();
  check('work continues through lock', s.tasks.saturday.step > stepBefore || s.tasks.saturday.state === 'done', stepBefore + '→' + s.tasks.saturday.step + ' ' + s.tasks.saturday.state);
  await page.locator('#home-bar').click();
  await page.waitForTimeout(6000);
  await shot('31-home-task-notice');
  s = await st();
  check('completion notice is a task notice', s.tasks.saturday.state === 'done');

  r = await rs();
  const f = r.frames.slice().sort((a, b) => a - b);
  const env = await page.evaluate(() => ({ ua: navigator.userAgent, dpr: devicePixelRatio, vw: innerWidth, vh: innerHeight }));
  check('no console errors', errors.length === 0, errors.join(' | '));
  const summary = {
    file: FILE, browser: browser.version(), env, renderer: r.renderer,
    frames: f.length ? { n: f.length, median: f[Math.floor(f.length / 2)], p95: f[Math.floor(f.length * 0.95)] } : null,
    results, errors, warnings,
  };
  fs.writeFileSync(path.join(OUT, 'browser-check.json'), JSON.stringify(summary, null, 2));
  for (const x of results) console.log((x.ok ? 'PASS ' : 'FAIL ') + x.name + (x.detail ? '  [' + x.detail + ']' : ''));
  console.log('console warnings', warnings.length, warnings.length ? '(' + [...new Set(warnings.map((w) => w.replace(/0x[0-9a-f]+/g, '…')))].join(' | ') + ')' : '');
  console.log('browser', browser.version(), '| renderer', r.renderer, '| frames', JSON.stringify(summary.frames));
  await browser.close();
  process.exit(results.every((x) => x.ok) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
