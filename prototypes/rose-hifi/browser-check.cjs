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
// ROSE_TRAY=1…10 runs the same walkthrough with a bottom-tray option applied (default: none).
// ROSE_THEME=classic runs the previous look; the default is the Oxblood prototype direction.
const OX = !process.env.ROSE_TRAY && process.env.ROSE_THEME !== 'classic';
const FILE = 'file://' + path.join(__dirname, 'Rose-HiFi.html') + (process.env.ROSE_TRAY ? '?tray=' + process.env.ROSE_TRAY : OX ? '' : '?theme=classic');
const WORK = OX ? '#rose-work' : '#rose-agents';
// Oxblood (study 11): Rose has one connection control; universal mic mute lives on Home's edge,
// the swipe-up edge and the harness, so in-Rose mic steps use the harness control.
const MIC = OX ? '#h-mic' : '#rose-mic';
const END = OX ? '#rose-conn' : '#rose-end';
const endCallToHome = async (page) => { await page.locator(END).click(); if (OX) { await page.waitForTimeout(250); await page.locator('#home-bar').click(); } };
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail: detail === undefined ? '' : detail }); };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  const warnings = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); else if (m.type() === 'warning') warnings.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message + ' @ ' + String(e.stack).split('\n').slice(1, 4).join(' | ')));
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
  if (OX) { await page.locator('.home-page[aria-hidden=false] .app').first().focus(); await page.keyboard.press('ArrowLeft'); } else await page.locator('#page-dots button').first().click();
  await settle();
  check('page dot returns to first page', (await st()).homePage === 0);

  // Home → Rose
  await page.locator('#tray-rose').click();
  const firstConn = (await st()).connection;
  await page.waitForTimeout(260);
  await shot('04-rose-opening-transition');
  await page.waitForTimeout(500);
  let s = await st();
  check('Rose opens and starts connecting', s.view === 'rose' && firstConn === 'connecting', firstConn + ' → ' + s.connection);
  await shot('05-rose-connecting');
  await page.waitForTimeout(1500);
  s = await st();
  check('connected cue only', s.connection === 'connected' && s.notices.every((n) => n.text === 'Connected'), JSON.stringify(s.notices.map((n) => n.text)));
  await page.waitForTimeout(1400);
  s = await st();
  check('Rose speaks with result', s.speaking && s.result === 'saturday');
  await shot('06-rose-speaking-result');

  // Mute while Rose speaks: speech continues, mic silver.
  await page.locator(MIC).click();
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
  await page.locator(MIC).focus();
  await page.keyboard.press('Enter');
  await page.waitForTimeout(400);
  s = await st();
  check('keyboard unmute', !s.muted && s.notices.some((n) => n.text === 'Unmuted'));
  await shot('09-rose-unmuted-listening');

  // Start tasks, then end call: tasks must survive.
  await page.locator('#h-tasks').click();
  await endCallToHome(page);
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
  // Oxblood draws Rose as a CSS pearl (WebGL canvas not displayed); the renderer's visual checks run in Classic.
  if (!OX) check('static fallback visible', r.mode === 'static-forced' && await page.locator('#rose-fallback').isVisible());
  else check('Oxblood: renderer reports forced fallback mode', r.mode === 'static-forced');
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
  if (!OX) check('visible page resumes drawing', (await rs()).draws > d2);
  else check('Oxblood: pearl visible after page returns', await page.evaluate(() => getComputedStyle(document.querySelector('#v-rose .rose-head h2'), '::before').width === '16px'));

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
  await page.locator(WORK).click();
  await settle(500);
  s = await st();
  check('agents screen from Rose keeps call', s.view === 'agents' && s.connection === 'connected');
  await page.waitForTimeout(2800);
  await shot('21-agents-folded');
  await page.locator('#h-unfolded').click();
  await settle(900);
  const cardsVisible = await page.evaluate(() => { const s = document.getElementById('screen').getBoundingClientRect(); return [...document.querySelectorAll('#agrid .wcard')].filter((c) => { const r = c.getBoundingClientRect(); return r.top >= s.top && r.bottom <= s.bottom && r.left >= s.left && r.right <= s.right; }).length; });
  check('eight windows fully visible unfolded', cardsVisible === 8, cardsVisible);
  const trayTop = await page.evaluate(() => { const t = document.querySelector('#v-agents .tray').getBoundingClientRect(); return t.height ? t.top : document.getElementById('screen').getBoundingClientRect().bottom; });
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
  // Wait for smooth scroll + snap to come to rest before comparing positions.
  for (let i = 0, prev = -1; i < 20; i++) { const v = await page.evaluate(() => document.getElementById('sw-agent').scrollLeft + ',' + document.getElementById('sw-human').scrollLeft); if (v === prev) break; prev = v; await page.waitForTimeout(250); }
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
  const conn = await page.evaluate(() => ({ working: document.getElementById('v-switcher').classList.contains('working'), running: Object.values(window.__rose.getState().tasks).filter((t) => t.state === 'running').length, routes: document.querySelectorAll('#sw-routes .pulse').length }));
  check('connector animates exactly while work runs', conn.working === conn.running > 0 && (conn.running === 0 || conn.routes > 0), JSON.stringify(conn));
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

  // ---------- Regression checks for review findings ----------
  await page.locator('#h-folded').click();
  await page.locator('#h-reset').click(); await settle(400);
  await page.locator('#h-tasks').click();
  await page.locator('#tray-overview').click(); await page.waitForTimeout(80);
  await page.locator('#h-reset').click(); await settle(700);
  const active = await page.evaluate(() => [...document.querySelectorAll('.view[data-active=true]')].map((v) => v.id));
  const hit = await page.evaluate(() => { const r = document.getElementById('tray-rose').getBoundingClientRect(); return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).closest('#tray-rose') !== null; });
  check('reset mid-transition leaves only Home active and tappable', active.join() === 'v-home' && hit, active.join());
  await page.locator('#h-tasks').click();
  await page.locator('#tray-rose').click(); await page.waitForTimeout(1500);
  await page.locator(WORK).click(); await settle(500);
  await page.locator('#agrid [data-window=w-research] [data-stop]').click(); await settle(300);
  await page.locator('#h-lock').click(); await settle(300);
  const unlockHit = await page.evaluate(() => { const r = document.getElementById('unlock').getBoundingClientRect(); return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).id === 'unlock'; });
  check('lock screen covers an open Stop sheet; Unlock reachable', unlockHit && !(await page.locator('.sheet').isVisible()));
  await page.locator('#unlock').click(); await settle(300);
  check('confirmation preserved through lock', (await st()).pendingStop === 'compare' && await page.locator('.sheet').isVisible());
  await page.keyboard.press('Escape'); await settle(300);
  const fid = await page.evaluate(() => { const a = document.activeElement; return a && a.closest('[data-window]') ? a.closest('[data-window]').dataset.window : a.tagName; });
  check('focus returns to the Stop control’s card after the sheet closes', fid === 'w-research', fid);
  await page.locator('#h-attn').click(); await settle(300);
  await page.locator('#agrid [data-open-window=w-mail]').click(); await settle(500);
  const choices = await page.locator('[data-choose]').count();
  check('needs-choice window offers a choice', choices === 2, choices);
  await shot('32-attention-choice');
  await page.locator('[data-choose]').first().click(); await settle(300);
  check('making the choice resumes the task', (await st()).tasks.reply.state === 'running');
  const tooSmall = await page.evaluate(() => [...document.querySelectorAll('#screen button:not([disabled]), .harness button')].filter((b) => { const r = b.getBoundingClientRect(); const k = document.getElementById('screen').getBoundingClientRect().width / document.getElementById('screen').offsetWidth; const isPhone = b.closest('#screen'); const w = isPhone ? r.width / k : r.width, h = isPhone ? r.height / k : r.height; return r.width && (w < 43.5 || h < 43.5); }).map((b) => b.id || b.className || b.getAttribute('aria-label')));
  check('visible controls are at least 44 CSS px', tooSmall.length === 0, tooSmall.join(', '));
  const scrolled = await page.evaluate(() => document.getElementById('screen').scrollTop + document.getElementById('screen').scrollLeft);
  check('phone surface never scrolls under focus', scrolled === 0);

  // ---------- Rose's voice (output) mute on Home's tray ----------
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator('#h-folded').click();
  await page.locator('#h-reset').click(); await settle(500);
  if (process.env.ROSE_TRAY) {
    const vbox = await page.evaluate(() => { const b = document.getElementById('tray-voice'); const k = document.getElementById('screen').getBoundingClientRect().width / document.getElementById('screen').offsetWidth; const r = b.getBoundingClientRect(); return { w: r.width / k, h: r.height / k, pressed: b.getAttribute('aria-pressed'), name: b.getAttribute('aria-label') }; });
    check('voice control on Home tray, ≥44 px, unpressed, named', vbox.w >= 43.5 && vbox.h >= 43.5 && vbox.pressed === 'false' && /Rose's voice/.test(vbox.name), JSON.stringify(vbox));
    await page.locator('#h-tasks').click();
    await page.locator('#tray-rose').click();
    await page.waitForFunction(() => window.__rose.getState().connection === 'connected');
    await page.locator('#home-bar').click(); await settle(500);
    const pre = await st();
    const noticesBefore = pre.notices.map((n) => n.id).join();
    await page.locator('#tray-voice').click(); await settle(300);
    let v = await st();
    check('tap mutes Rose’s voice only (mic, call, speech, tasks untouched)', v.voiceMuted && !v.muted && v.connection === 'connected' && v.speaking === pre.speaking && v.view === 'home' && JSON.stringify(Object.values(v.tasks).map((t) => t.state)) === JSON.stringify(Object.values(pre.tasks).map((t) => t.state)) && !v.pendingStop, JSON.stringify({ voiceMuted: v.voiceMuted, muted: v.muted, connection: v.connection }));
    check('voice mute adds no conversation notice', v.notices.filter((n) => !noticesBefore.split(',').includes(String(n.id))).every((n) => n.kind === 'task'));
    const shown = await page.evaluate(() => ({ pressed: document.getElementById('tray-voice').getAttribute('aria-pressed'), flag: document.getElementById('tray-voice').dataset.voiceMuted, announce: document.getElementById('announcer').textContent }));
    check('muted state is exposed (aria-pressed, visual flag, announcement)', shown.pressed === 'true' && shown.flag === 'true' && /muted/i.test(shown.announce), JSON.stringify(shown));
    await shot('40-home-voice-muted');
    await page.locator('#tray-rose').click(); await settle(700);
    await page.evaluate(() => window.__rose.dispatch({ type: 'speak', ms: 4000 })); await settle(300);
    check('Rose caption reflects muted voice while speaking', /voice muted/.test(await page.locator('#rose-caption').textContent()));
    await page.locator(MIC).click(); await settle(300);
    v = await st();
    check('microphone mute is separate from voice mute', v.muted && v.voiceMuted);
    await page.locator('#home-bar').click(); await settle(500);
    await page.locator('#tray-voice').focus();
    await page.keyboard.press('Enter'); await settle(300);
    v = await st();
    check('keyboard unmutes Rose’s voice; mic stays muted; call continues', !v.voiceMuted && v.muted && v.connection === 'connected' && await page.locator('#tray-voice').getAttribute('aria-pressed') === 'false');
    await page.locator('#h-lock').click(); await settle(200);
    await page.evaluate(() => window.__rose.dispatch({ type: 'voiceMute' }));
    check('locked phone ignores the voice control', !(await st()).voiceMuted);
    await page.locator('#unlock').click(); await settle(200);
  } else if (OX) {
    const ids = await page.evaluate(() => [...document.querySelectorAll('#home-tray .well')].filter((w) => w.getBoundingClientRect().width).map((w) => w.id));
    check('Oxblood Home edge: Rose · universal mic · switcher (study 09)', JSON.stringify(ids) === JSON.stringify(['tray-rose', 'tray-mic', 'tray-overview']), JSON.stringify(ids));
  } else {
    const hidden = await page.evaluate(() => getComputedStyle(document.getElementById('tray-voice')).display === 'none' && [...document.querySelectorAll('#home-tray .well')].filter((w) => w.getBoundingClientRect().width).length === 2);
    check('default tray unchanged: two controls, voice control hidden', hidden);
  }

  // ---------- Oxblood: navigation separated from conversation state ----------
  if (OX) {
    await page.locator('#h-reset').click(); await settle(500);
    check('Oxblood is the prototype default', await page.evaluate(() => document.getElementById('device').dataset.theme) === 'oxblood' && !(await page.evaluate(() => document.getElementById('screen').dataset.tray)));
    await page.locator('#tray-rose').click();
    await page.waitForFunction(() => window.__rose.getState().connection === 'connected');
    await settle(400);
    const ctl = await page.evaluate(() => {
      const k = document.getElementById('screen').getBoundingClientRect().width / document.getElementById('screen').offsetWidth;
      const vis = [...document.querySelectorAll('#v-rose .tray .well')].filter((w) => w.getBoundingClientRect().width).map((w) => w.id);
      const b = document.getElementById('rose-work'); const r = b.getBoundingClientRect();
      const head = document.querySelector('#v-rose .rose-head').getBoundingClientRect(), sr = document.getElementById('screen').getBoundingClientRect();
      return { vis, w: r.width / k, h: r.height / k, name: b.getAttribute('aria-label'), upperRight: r.right > sr.left + sr.width * 0.75 && r.top < sr.top + sr.height * 0.15, working: b.dataset.working };
    });
    const conn = await page.evaluate(() => { const b = document.getElementById('rose-conn'); const k = document.getElementById('screen').getBoundingClientRect().width / document.getElementById('screen').offsetWidth; const r = b.getBoundingClientRect(); return { label: b.textContent.trim(), name: b.getAttribute('aria-label'), w: r.width / k, h: r.height / k }; });
    check('Rose: tray retracted, one Disconnect control (study 11)', ctl.vis.length === 0 && conn.label === 'Disconnect' && /disconnect/i.test(conn.name) && conn.w >= 44 && conn.h >= 44, JSON.stringify({ vis: ctl.vis, conn }));
    check('Background-work entry is upper right, named, ≥44 px', ctl.upperRight && ctl.w >= 44 && ctl.h >= 44 && /background work/i.test(ctl.name), JSON.stringify(ctl));
    check('work entry is not highlighted without running work', ctl.working === 'false');
    await page.locator('#h-tasks').click(); await settle(300);
    check('work entry highlights while work runs', await page.locator('#rose-work').getAttribute('data-working') === 'true');
    await page.locator(MIC).click(); await settle(200);
    let ox = await st();
    check('mic mute keeps call and work', ox.muted && ox.connection === 'connected' && Object.values(ox.tasks).every((t) => t.state === 'running'));
    const workHit = await page.evaluate(() => { const w = document.getElementById('rose-work').getBoundingClientRect(); return document.elementFromPoint(w.left + w.width / 2, w.top + w.height / 2).closest('#rose-work') !== null; });
    check('Muted notice never covers the Background Work entry', workHit);
    await page.locator('#rose-work').click(); await settle(500);
    ox = await st();
    check('work entry opens Rose’s apps without ending the call', ox.view === 'agents' && ox.connection === 'connected');
    await shot('50-oxblood-agents');
    await page.keyboard.press('Escape'); await settle(400);
    check('back returns to Rose', (await st()).view === 'rose');
    await page.locator('#rose-conn').click(); await settle(500);
    ox = await st();
    check('Disconnect stays on Rose and offers Connect; work continues', ox.view === 'rose' && ox.connection === 'disconnected' && (await page.locator('#rose-conn').textContent()).trim() === 'Connect' && Object.values(ox.tasks).every((t) => t.state === 'running'));
    await page.locator('#rose-conn').click(); await page.waitForFunction(() => window.__rose.getState().connection === 'connected');
    check('Connect reconnects in place', (await st()).view === 'rose');
    await page.locator('#rose-conn').click(); await settle(300);
    await page.locator('#home-bar').click(); await settle(700);
    ox = await st();
    check('end call returns Home; work continues; universal mic mute persists', ox.view === 'home' && ox.connection === 'disconnected' && ox.muted && Object.values(ox.tasks).every((t) => t.state === 'running'));
    const micBox = await page.evaluate(() => { const k = document.getElementById('screen').getBoundingClientRect().width / document.getElementById('screen').offsetWidth; const b = document.getElementById('tray-mic'); const r = b.getBoundingClientRect(); return { w: r.width / k, h: r.height / k, pressed: b.getAttribute('aria-pressed'), name: b.getAttribute('aria-label') }; });
    check('Home mic: ≥44 px, named, shows muted', micBox.w >= 44 && micBox.h >= 44 && micBox.pressed === 'true' && /microphone/i.test(micBox.name), JSON.stringify(micBox));
    await page.locator('#tray-mic').click(); await settle(200);
    ox = await st();
    check('Home mic unmutes without a call; work untouched', !ox.muted && ox.connection === 'disconnected' && Object.values(ox.tasks).every((t) => t.state === 'running'));
    await page.locator('#tray-mic').click(); await settle(200);
    await page.locator('#tray-rose').click(); await page.waitForFunction(() => window.__rose.getState().connection === 'connected'); await settle(300);
    ox = await st();
    check('a call starts in the universal mute state', ox.muted && ox.connection === 'connected');
    await page.locator(MIC).click(); await settle(200);
    await endCallToHome(page); await settle(700);
    // Swipe up inside an ordinary app: scrunch + Home's control edge; tap the app to return.
    await page.locator('#tray-overview').click(); await settle(500);
    await page.locator('#home-bar').click(); await settle(400);
    await page.locator('.app[data-app=camera]').click(); await settle(500);
    const sb = await page.locator('#screen').boundingBox();
    await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height - 12); await page.mouse.down();
    for (let i = 1; i <= 8; i++) await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height - 12 - i * 12);
    await page.mouse.up(); await settle(500);
    const pk = await page.evaluate(() => ({ peek: document.getElementById('screen').classList.contains('peek'), tray: !document.getElementById('peek-tray').hidden, view: window.__rose.getState().view }));
    check('swipe up in an app scrunches it and shows the system edge', pk.peek && pk.tray && pk.view === 'app', JSON.stringify(pk));
    await shot('52-oxblood-app-swipe-up');
    // The scrunched app keeps working: a tap acts in the app and the edge stays up.
    await page.locator('#v-app .back-btn').click(); await settle(500);
    check('scrunched app stays interactive (its Back works)', (await st()).view === 'home');
    await page.locator('.home-page[aria-hidden=false] .app').first().click(); await settle(500);
    await page.locator('#h-peek').click(); await settle(300);
    const sb2 = await page.locator('#screen').boundingBox();
    await page.mouse.move(sb2.x + sb2.width / 2, sb2.y + sb2.height * 0.5); await page.mouse.down();
    for (let i = 1; i <= 8; i++) await page.mouse.move(sb2.x + sb2.width / 2, sb2.y + sb2.height * 0.5 + i * 12);
    await page.mouse.up(); await settle(400);
    check('swipe down lowers the edge; app still open', !(await page.evaluate(() => document.getElementById('screen').classList.contains('peek'))) && (await st()).view === 'app');
    await page.locator('#h-peek').click(); await settle(300);
    await page.mouse.move(sb2.x + sb2.width / 2, sb2.y + sb2.height - 30); await page.mouse.down();
    for (let i = 1; i <= 12; i++) await page.mouse.move(sb2.x + sb2.width / 2, sb2.y + sb2.height - 30 - i * 25);
    await page.mouse.up(); await settle(500);
    check('long swipe up goes Home', (await st()).view === 'home');
    await page.locator('.home-page[aria-hidden=false] .app').first().click(); await settle(500);
    await page.locator('#h-peek').click(); await settle(300);
    await page.locator('#peek-overview').click(); await settle(600);
    check('switcher from the raised edge', (await st()).view === 'switcher' && !(await page.evaluate(() => document.getElementById('screen').classList.contains('peek'))));
    await page.locator('#home-bar').click(); await settle(400);
    check('home bar works right after a swipe gesture', (await st()).view === 'home');
    await page.locator('#tray-overview').click(); await settle(700);
    check('switcher has no persistent tray', await page.evaluate(() => !document.querySelector('#v-switcher .tray')));
    await shot('51-oxblood-switcher');
    await page.locator('#home-bar').click(); await settle(400);
  }

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
})().catch((e) => { for (const x of results) console.log((x.ok ? 'PASS ' : 'FAIL ') + x.name); console.error(e); process.exit(2); });
