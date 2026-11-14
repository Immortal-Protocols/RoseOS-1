// Rose HiFi — Project 2 adaptive workspace review suite (page −1, left of Home).
// Usage: node prototypes/rose-hifi/workspace-check.cjs [outDir]
// Real input: mouse at the 384×832 design size; CDP touch on iPhone 13 (390), iPhone SE (320) and a
// 390×600 short screen. Glacier at all four sizes, Oxblood at design and 390.
// Headless Chromium + SwiftShader: browser-emulated interaction/layout evidence, not a phone or native build.
// Generation and all data are simulated in the prototype; this suite checks the prototype's behaviour only.
'use strict';
const path = require('path'), fs = require('fs'), crypto = require('crypto');
const { execSync } = require('child_process');
const { chromium, devices } = require('playwright');
const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out', 'workspace'));
fs.mkdirSync(OUT, { recursive: true });
const results = [], shots = [];
const check = (n, c, x) => { results.push({ n, ok: !!c }); console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  ' + (x === undefined ? '' : JSON.stringify(x).slice(0, 400)))); };
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const git = (c) => { try { return execSync(c, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };

async function run(browser, name, file, ctxOpts, phone, full) {
  const ctx = await browser.newContext(ctxOpts);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('file://' + path.join(__dirname, file)); await page.waitForTimeout(1200);
  const P = (n) => name + ': ' + n;
  const wait = (ms) => page.waitForTimeout(ms || 350);
  const R = (fn, a) => page.evaluate(fn, a);
  const D = (a) => R((a) => window.__rose.dispatch(a), a);
  const W = () => R(() => { const w = window.RoseWorkspace.state(), s = window.__rose.getState(); return { page: s.homePage, view: s.view, app: s.app, key: w.layout && w.layout.key, variant: w.layout && w.layout.variant, cards: w.layout ? w.layout.cards.map((c) => c.id) : [], types: w.layout ? [...new Set(w.layout.cards.map((c) => c.type))] : [], phase: w.phase, pins: [...w.pins], hist: w.history.length, kb: window.RoseSystem.kbState().open, field: window.RoseSystem.fields.ws.text, tasks: Object.fromEntries(Object.entries(s.tasks).map(([k, v]) => [k, v.state])) }; });
  const tap = async (sel) => { if (phone) await page.locator(sel).first().tap(); else await page.locator(sel).first().click(); };
  const key = (k) => '#keys [data-k="' + k + '"]';
  const typeKeys = async (str) => { for (const ch of str) await tap(key(ch === ' ' ? 'space' : ch)); };
  const shot = async (n, note) => { await page.mouse.move(2, 2).catch(() => {}); await wait(450); const f = path.join(OUT, name + '-' + n + '.png'); const b = await (phone ? page.screenshot({ path: f }) : page.locator('#device').screenshot({ path: f })); shots.push({ file: path.basename(f), note, sha256: sha(b) }); };
  const box = (sel) => R((sel) => { const e = document.querySelector(sel); if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; }, sel);
  const hswipe = async (x0, x1, y) => {
    if (phone) { const cdp = await ctx.newCDPSession(page); await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y }] }); for (let i = 1; i <= 12; i++) { await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * i / 12, y }] }); await page.waitForTimeout(16); } await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach(); }
    else { await page.mouse.move(x0, y); await page.mouse.down(); for (let i = 1; i <= 12; i++) await page.mouse.move(x0 + (x1 - x0) * i / 12, y); await page.mouse.up(); }
    await wait(600);
  };
  const SB = await page.locator('#screen').boundingBox();
  const L = SB.x + SB.width * 0.18, Rr = SB.x + SB.width * 0.82, MY = SB.y + SB.height * 0.40;

  // ---------- Reach the workspace from Home; Home, dock and chin preserved ----------
  const home0 = await R(() => ({ pages: document.querySelectorAll('#home-track > .home-page').length, icons: document.querySelectorAll('#home-track > .home-page button').length }));
  const dock0 = await box('#home-tray'), chin0 = await box('#home-bar');
  check(P('starts on Home page 1 with no workspace layout'), (await W()).page === 0 && !(await W()).key);
  await hswipe(L, Rr, MY);
  let w = await W();
  check(P('swipe right from Home reaches the workspace page (left of Home)'), w.page === -1 && w.view === 'home', w);
  const vis = await R(() => { const p = document.getElementById('ws-page').getBoundingClientRect(), s = document.getElementById('screen').getBoundingClientRect(); return Math.abs(p.left - s.left) < 2 && !document.getElementById('ws-page').inert; });
  check(P('workspace is on screen and interactive; Home pages inert'), vis && (await R(() => [...document.querySelectorAll('#home-track > .home-page')].every((p) => p.inert))));
  const dock1 = await box('#home-tray'), chin1 = await box('#home-bar');
  check(P('dock and chin unchanged on the workspace page'), JSON.stringify(dock0) === JSON.stringify(dock1) && JSON.stringify(chin0) === JSON.stringify(chin1), { dock0, dock1, chin0, chin1 });
  check(P('Home pages and icons unchanged (no workspace content inside Home)'), JSON.stringify(home0) === JSON.stringify(await R(() => ({ pages: document.querySelectorAll('#home-track > .home-page').length, icons: document.querySelectorAll('#home-track > .home-page button').length }))));
  const lbl = await R(() => document.querySelector('#ws-page .ws-sim').textContent);
  check(P('page is labelled “Simulated generation · sample data”'), lbl === 'Simulated generation · sample data', lbl);
  const nc = await R(() => { const c = document.getElementById('ws-canvas').getBoundingClientRect(), d = document.getElementById('page-dots').getBoundingClientRect(), t = document.getElementById('home-tray').getBoundingClientRect(); return { c: c.bottom, d: d.top, t: t.top }; });
  check(P('canvas ends above the page dots and dock'), nc.c <= nc.d + 1 && nc.c <= nc.t, nc);
  await hswipe(L, Rr, MY); w = await W();
  check(P('further right swipe resists and stays on the workspace (no wrap)'), w.page === -1, w);
  await shot('01-empty', 'Workspace, empty state');

  // ---------- Arrange (best guess): loading keeps controls still ----------
  if (full) await R(() => { const c = document.getElementById('h-wslat'); c.value = '2500'; c.dispatchEvent(new Event('change')); });
  const CTRL = ['#f-ws', '#ws-voice', '#ws-arrange', '#ws-regen', '#ws-undo', '#ws-status', '#ws-recent', '#ws-canvas'];
  const ctrlBoxes = () => R((sels) => sels.map((s) => { const b = document.querySelector(s).getBoundingClientRect(); return [s, Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)].join(','); }), CTRL);
  const c0 = await ctrlBoxes();
  await tap('#ws-arrange'); await wait(200);
  w = await W();
  const ld = await R(() => ({ st: document.getElementById('ws-status').textContent, dis: ['ws-arrange', 'ws-regen', 'ws-undo'].map((i) => document.getElementById(i).disabled), skel: !!document.querySelector('#ws-canvas .ws-skel') }));
  check(P('Arrange → loading: “Composing on this phone… simulated”, skeleton, controls disabled, still on the page'), w.phase === 'loading' && w.page === -1 && /Composing on this phone/.test(ld.st) && /simulated/.test(ld.st) && ld.skel && ld.dis.every(Boolean), { w, ld });
  const c1 = await ctrlBoxes();
  check(P('no control moves during loading'), JSON.stringify(c0) === JSON.stringify(c1), { c0, c1 });
  if (full) await shot('02-loading', 'Arrange for me: loading (simulated, 2.5 s harness latency)');
  await wait(full ? 2600 : 900);
  w = await W();
  check(P('best-guess layout arrives (apps, actions, notification…)'), w.key === 'guess' && w.phase === 'idle' && w.cards.length >= 3, w);
  check(P('controls identical after the layout arrives'), JSON.stringify(c0) === JSON.stringify(await ctrlBoxes()));
  await shot('03-best-guess', 'Arrange for me: best guess');
  if (full) await R(() => { const c = document.getElementById('h-wslat'); c.value = '300'; c.dispatchEvent(new Event('change')); });
  else await R(() => { window.RoseWorkspace.state().latency = 300; });

  // ---------- Text request via the F10 keyboard ----------
  await tap('#f-ws'); await wait(450);
  w = await W(); check(P('tapping the request field opens the keyboard'), w.kb, w);
  await typeKeys('dinner tonight');
  w = await W(); check(P('typing via keys'), /^dinner tonight$/i.test(w.field), w.field);
  if (full) await shot('04-typing', 'Typing a request with the keyboard');
  await tap(key('action')); await wait(800);
  w = await W();
  check(P('action key submits; keyboard hides; cooking layout'), !w.kb && w.key === 'cooking' && w.page === -1, w);
  const cookTypes = w.types;
  check(P('cooking uses apps, direct + Rose actions, list and carousel'), ['apps', 'actions', 'carousel', 'list'].every((t) => cookTypes.includes(t)), cookTypes);
  const st = await R(() => document.getElementById('ws-status').textContent);
  check(P('status echoes the interpreted request'), /Cooking/.test(st) && /dinner tonight/i.test(st), st);
  await shot('05-cooking', 'Request “dinner tonight” → cooking');

  // ---------- Carousel drag scrolls sideways; page does not move ----------
  const cb = await page.locator('#ws-canvas .ws-carousel').first().boundingBox();
  if (cb) {
    await page.locator('#ws-canvas .ws-carousel').first().scrollIntoViewIfNeeded(); await wait(200);
    const cb2 = await page.locator('#ws-canvas .ws-carousel').first().boundingBox();
    const sl0 = await R(() => document.querySelector('#ws-canvas .ws-carousel').scrollLeft);
    if (phone) await hswipe(cb2.x + cb2.width * 0.85, cb2.x + cb2.width * 0.1, cb2.y + cb2.height / 2);
    else { await page.mouse.move(cb2.x + cb2.width / 2, cb2.y + cb2.height / 2); await page.mouse.wheel(220, 0); await wait(500); }
    const sl1 = await R(() => document.querySelector('#ws-canvas .ws-carousel').scrollLeft);
    w = await W();
    check(P('carousel scrolls sideways (' + (phone ? 'touch drag' : 'trackpad/wheel') + '); page stays on the workspace'), sl1 > sl0 + 20 && w.page === -1, { sl0, sl1, w });
    if (!phone) {
      // A mouse drag starting on the carousel is owned by it: the page does not flip.
      await page.mouse.move(cb2.x + cb2.width * 0.8, cb2.y + cb2.height / 2); await page.mouse.down(); await page.mouse.move(cb2.x + cb2.width * 0.1, cb2.y + cb2.height / 2, { steps: 10 }); await page.mouse.up(); await wait(500);
      check(P('a drag that starts on the carousel never flips the page'), (await W()).page === -1);
    }
  } else check(P('carousel present'), false);

  // ---------- Pin, Regenerate keeps pins, Undo ----------
  const pinId = (await W()).cards[1];
  await tap('#ws-canvas [data-pin="' + pinId + '"]'); await wait(250);
  w = await W(); check(P('Pin marks a card (aria-pressed, outline)'), w.pins.includes(pinId) && (await R((id) => document.querySelector('[data-pin="' + id + '"]').getAttribute('aria-pressed'), pinId)) === 'true', w);
  const beforeRegen = await W();
  await tap('#ws-regen'); await wait(700);
  w = await W();
  check(P('Regenerate → another arrangement of the same intent; pinned card kept and first'), w.key === 'cooking' && w.variant !== beforeRegen.variant && w.cards[0] === pinId && w.pins.includes(pinId) && JSON.stringify(w.cards) !== JSON.stringify(beforeRegen.cards), { beforeRegen, w });
  await shot('06-regenerated-pinned', 'Regenerate with a pinned card');
  await tap('#ws-undo'); await wait(300);
  w = await W();
  check(P('Undo restores the previous arrangement exactly'), JSON.stringify(w.cards) === JSON.stringify(beforeRegen.cards) && w.variant === beforeRegen.variant, { w, beforeRegen });

  // ---------- Materially different compositions ----------
  const comps = {};
  for (const [q, k] of [['work', 'work'], ['unwind', 'unwinding']]) {
    await R((q) => window.RoseWorkspace.submit(q), q); await wait(700);
    w = await W(); comps[k] = w;
    check(P('request “' + q + '” → ' + k + ' layout'), w.key === k, w);
  }
  const cooking = beforeRegen;
  const pinned = new Set(comps.work.pins);
  const overlap = (a, b) => a.cards.filter((c) => !pinned.has(c) && b.cards.includes(c)).length;
  check(P('cooking, work and unwinding are materially different (no shared cards besides the carried pin)'), overlap(cooking, comps.work) === 0 && overlap(cooking, comps.unwinding) === 0 && overlap(comps.work, comps.unwinding) === 0, { cooking: cooking.cards, work: comps.work.cards, unwinding: comps.unwinding.cards });
  await shot('07-unwinding', 'Request “unwind” → unwinding');
  await R(() => window.RoseWorkspace.submit('work')); await wait(700);
  await shot('08-work', 'Request “work” → work');
  const chips = await R(() => [...document.querySelectorAll('#ws-recent .ws-chip')].map((c) => c.textContent));
  check(P('recent layouts row lists retained layouts (most recent first)'), chips.length >= 3 && chips[0] === 'Work', chips);

  // ---------- Direct action vs Rose task ----------
  await R(() => window.RoseWorkspace.submit('dinner')); await wait(700);
  const timerBtn = '#ws-canvas [data-direct]';
  const dSel = await R(() => { const b = [...document.querySelectorAll('#ws-canvas [data-direct]')].find((x) => /timer/i.test(x.textContent)); return b ? '[data-direct="' + b.dataset.direct + '"]' : null; });
  if (dSel) { await page.locator('#ws-canvas ' + dSel).scrollIntoViewIfNeeded(); await tap('#ws-canvas ' + dSel); await wait(300); }
  const dState = dSel ? await R((s) => ({ p: document.querySelector('#ws-canvas ' + s).getAttribute('aria-pressed'), t: document.querySelector('#ws-canvas ' + s).textContent, sheet: !document.getElementById('ws-sheet').hidden }), dSel) : null;
  check(P('direct action (timer) acts immediately, no confirmation'), dState && dState.p === 'true' && /Timer ·/.test(dState.t) && !dState.sheet, dState);
  const aSel = await R(() => { const b = document.querySelector('#ws-canvas [data-agent]'); return b ? '[data-agent="' + b.dataset.agent + '"]' : null; });
  const aLook = aSel ? await R((s) => { const b = document.querySelector('#ws-canvas ' + s); return { pearl: !!b.querySelector('.ws-pearl'), sub: b.querySelector('small').textContent, bg: getComputedStyle(b).backgroundColor }; }, aSel) : null;
  check(P('Rose task is visibly different: pearl + “Rose task · asks first”, outline not fill'), aLook && aLook.pearl && aLook.sub === 'Rose task · asks first' && /rgba\(0, 0, 0, 0\)|transparent/.test(aLook.bg), aLook);
  await page.locator('#ws-canvas ' + aSel).scrollIntoViewIfNeeded(); await tap('#ws-canvas ' + aSel); await wait(350);
  const sh = await R(() => ({ open: !document.getElementById('ws-sheet').hidden, t: document.getElementById('ws-sheet-title').textContent }));
  check(P('Rose task asks first (confirm sheet; nothing started yet)'), sh.open && /^Ask Rose to/.test(sh.t), sh);
  await shot('09-agent-confirm', 'Rose task: asks first');
  await tap('#ws-sheet-cancel'); await wait(250);
  w = await W(); check(P('“Not now” starts nothing'), await R(() => document.getElementById('ws-sheet').hidden));
  const t0 = (await W()).tasks;
  await tap('#ws-canvas ' + aSel); await wait(300); await tap('#ws-sheet-go'); await wait(400);
  w = await W();
  const aAfter = await R((s) => document.querySelector('#ws-canvas ' + s + ' small').textContent, aSel);
  const started = Object.keys(w.tasks).some((k) => w.tasks[k] !== t0[k]) || Object.values(w.tasks).some((v) => v === 'running');
  check(P('“Ask Rose” starts Rose work; card reads “Rose is working”; stays on the workspace'), aAfter === 'Rose is working' && started && w.page === -1 && w.view === 'home', { aAfter, t0, tasks: w.tasks });

  // ---------- Failure keeps the last layout; Retry ----------
  const pre = await W();
  await R(() => { const c = document.getElementById('h-wsfail'); if (c) { c.checked = true; c.dispatchEvent(new Event('change')); } else window.RoseWorkspace.state().failNext = true; });
  const cf0 = await ctrlBoxes();
  await tap('#ws-regen'); await wait(700);
  w = await W();
  const fs1 = await R(() => document.getElementById('ws-status').textContent);
  check(P('failure: “Couldn’t arrange · kept your last layout · Retry”; layout unchanged'), w.phase === 'failed' && /Couldn’t arrange/.test(fs1) && JSON.stringify(w.cards) === JSON.stringify(pre.cards), { w, fs1 });
  check(P('no control moves on failure'), JSON.stringify(cf0) === JSON.stringify(await ctrlBoxes()));
  await shot('10-failed', 'Generation failed; last layout kept');
  await tap('#ws-retry'); await wait(700);
  w = await W(); check(P('Retry succeeds'), w.phase === 'idle' && w.key === 'cooking' && w.variant !== pre.variant, w);

  // ---------- Voice (simulated) and muted refusal ----------
  await tap('#ws-voice'); await wait(200);
  check(P('voice → listening (simulated)'), (await R(() => document.getElementById('ws-voice').dataset.listening)) === 'true');
  await wait(1700); w = await W();
  check(P('voice request “Something relaxing for tonight” → unwinding'), w.key === 'unwinding' && /relaxing/.test(w.field), w);
  await D({ type: 'micMute' }); await wait(200);
  const mutedNow = await R(() => window.__rose.getState().muted);
  if (mutedNow) {
    const k0 = (await W()).key;
    await tap('#ws-voice'); await wait(400);
    const v = await R(() => ({ l: document.getElementById('ws-voice').dataset.listening, m: document.getElementById('ws-voice').dataset.muted, toast: document.getElementById('sys-toast').textContent, aria: document.getElementById('ws-voice').getAttribute('aria-label') }));
    check(P('mic muted from the chin → voice refused (no listening), toast and label say so'), v.l === 'false' && v.m === 'true' && /muted/i.test(v.toast) && /muted/.test(v.aria) && (await W()).key === k0, v);
    if (full) await shot('11-voice-muted', 'Voice refused while the mic is muted');
    await D({ type: 'micMute' }); await wait(200);
  } else check(P('mute toggle available'), false, 'micMute had no effect');

  // ---------- Retained layout after leaving to an app and Home ----------
  const kept = await W();
  await D({ type: 'openApp', value: 'messages' }); await wait(600);
  check(P('workspace keyboard/state does not leak into an app'), (await W()).view === 'app' && !(await W()).kb);
  await D({ type: 'home' }); await wait(500);
  if ((await W()).page !== -1) await hswipe(L, Rr, MY);
  w = await W();
  check(P('layout retained after leaving to an app and returning'), w.page === -1 && JSON.stringify(w.cards) === JSON.stringify(kept.cards) && w.key === kept.key, { kept, w });

  // ---------- Conversational request from another app prepares the page in the background ----------
  await D({ type: 'openApp', value: 'notes' }); await wait(500);
  const inApp = await W();
  await R(() => window.RoseWorkspace.prepareFromConversation('work')); await wait(150);
  const mid = await W();
  const tst = await R(() => document.getElementById('sys-toast').textContent);
  await wait(700); const done = await W();
  check(P('conversational request inside an app prepares the workspace without interrupting it'), inApp.view === 'app' && mid.view === 'app' && mid.app === inApp.app && done.view === 'app' && done.key === 'work' && /swipe right from Home/.test(tst), { inApp, mid, done, tst });
  if (full) await shot('12-prepared-in-app', 'Prepared from a conversation while in an app (toast)');
  await D({ type: 'home' }); await wait(400);
  if ((await W()).page !== -1) await hswipe(L, Rr, MY);
  check(P('the prepared work layout is waiting on the workspace'), (await W()).key === 'work');

  // ---------- Back to Home with an ordinary swipe ----------
  const hb = await page.locator('#ws-page .ws-head').boundingBox();
  await hswipe(Rr, L, hb.y + hb.height / 2);
  w = await W(); check(P('swipe left (outside a carousel) returns to Home page 1'), w.page === 0, w);
  check(P('dock and chin unchanged back on Home'), JSON.stringify(dock0) === JSON.stringify(await box('#home-tray')) && JSON.stringify(chin0) === JSON.stringify(await box('#home-bar')));
  await hswipe(Rr, L, MY);
  check(P('Home paging to the right still works (page 2)'), (await W()).page === 1);
  await D({ type: 'homePage', value: 0, max: 3, min: -1 }); await wait(300);
  await shot('13-home', 'Home after visiting the workspace (unchanged)');
  // Undo history depth is bounded by what the person did; there is no auto-change while away.
  check(P('no console errors'), errors.length === 0, errors);
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  await run(browser, 'glacier-design', 'Rose-Glacier.html', { viewport: { width: 1280, height: 900 } }, false, true);
  await run(browser, 'glacier-iphone13-390', 'Rose-Glacier.html', { ...devices['iPhone 13'] }, true, true);
  await run(browser, 'glacier-iphoneSE-320', 'Rose-Glacier.html', { ...devices['iPhone SE'] }, true, false);
  await run(browser, 'glacier-short-390x600', 'Rose-Glacier.html', { ...devices['iPhone 13'], viewport: { width: 390, height: 600 } }, true, false);
  await run(browser, 'oxblood-design', 'Rose-HiFi.html', { viewport: { width: 1280, height: 900 } }, false, false);
  await run(browser, 'oxblood-iphone13-390', 'Rose-HiFi.html', { ...devices['iPhone 13'] }, true, false);
  // Classic has no workspace (Project 2 is scoped to the Oxblood/Glacier HiFi).
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } }); const p = await ctx.newPage();
    await p.goto('file://' + path.join(__dirname, 'Rose-HiFi.html') + '?theme=classic'); await p.waitForTimeout(800);
    const r = await p.evaluate(() => { window.__rose.dispatch({ type: 'homePage', value: -1, min: document.getElementById('device').dataset.theme === 'oxblood' ? -1 : 0, max: 3 }); return { theme: document.getElementById('device').dataset.theme, ws: !!document.getElementById('ws-page'), page: window.__rose.getState().homePage }; });
    check('classic: no workspace page; Home cannot go left of page 1', r.theme !== 'oxblood' ? (!r.ws && r.page === 0) : true, r);
    await ctx.close();
  }
  await browser.close();
  const passed = results.filter((r) => r.ok).length;
  const htmls = ['Rose-Glacier.html', 'Rose-HiFi.html'].map((f) => ({ file: f, sha256: sha(fs.readFileSync(path.join(__dirname, f))), blob: git('git hash-object ' + f), committed_blob: git('git rev-parse HEAD:prototypes/rose-hifi/' + f) }));
  fs.writeFileSync(path.join(OUT, 'workspace-check.json'), JSON.stringify({ when: new Date().toISOString(), commit: git('git rev-parse HEAD'), tree_clean: git('git status --porcelain -- .') === '', htmls, passed, total: results.length, results, shots }, null, 2));
  console.log(passed + '/' + results.length + ' checks passed');
  if (passed !== results.length) process.exitCode = 1;
})();
