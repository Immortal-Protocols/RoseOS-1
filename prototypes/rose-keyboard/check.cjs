// Rose Keyboard (F10 study) — functional checks with real input, plus review renders.
// Usage: node prototypes/rose-keyboard/check.cjs [outDir]
// Desktop: real mouse/keyboard events at 1280×900. Phones: Chromium device emulation with CDP touch
// events (iPhone 13, Pixel 7, iPhone SE). Headless Chromium + SwiftShader: interaction and layout
// evidence only — not a physical phone, Safari, a real IME or performance proof.
'use strict';
const path = require('path'), fs = require('fs'), crypto = require('crypto');
const { chromium, devices } = require('playwright');
const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out', 'check'));
const FILE = 'file://' + path.join(__dirname, 'Rose-Keyboard.html');
fs.mkdirSync(OUT, { recursive: true });
const results = [], shots = [];
const check = (n, c, x) => { results.push({ n, ok: !!c }); console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  ' + (x === undefined ? '' : JSON.stringify(x)))); };
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');

(async () => {
  const browser = await chromium.launch();
  // ================= Desktop =================
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(FILE); await page.waitForTimeout(600);
  const wait = (ms) => page.waitForTimeout(ms || 320);
  const st = () => page.evaluate(() => { const k = window.__kb, f = k.fields[k.ui.app]; return { kb: k.ui.kb, chin: k.ui.chin, app: k.ui.app, muted: k.ui.muted, text: f.text, a: f.a, b: f.b, shift: f.shift, page: f.page, clip: f.clip }; });
  // Geometry in design points relative to the screen's bottom-left.
  const geo = () => page.evaluate(() => {
    const s = document.getElementById('screen'), r = s.getBoundingClientRect(), k = r.height / s.offsetHeight;
    const g = (el) => { const b = el.getBoundingClientRect(); return { top: (b.top - r.top) / k, bottom: (r.bottom - b.bottom) / k, h: b.height / k, w: b.width / k }; };
    return { H: r.height / k, app: g(document.getElementById('app')), kb: g(document.getElementById('kb')), composer: g(document.querySelector('.composer')), thread: g(document.getElementById('thread')), chin: g(document.getElementById('chin')) };
  });
  const key = (k) => page.locator('#keys [data-k="' + k.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"]');
  const tapKeys = async (str) => { for (const ch of str) await key(ch === ' ' ? 'space' : ch).click(); };
  const shot = async (name, note) => { await wait(350); const f = path.join(OUT, name + '.png'); const b = await page.locator('#device').screenshot({ path: f }); shots.push({ file: name + '.png', note, sha256: sha(b) }); };
  const centerOf = async (sel) => { const b = await page.locator(sel).boundingBox(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
  const drag = async (x0, y0, x1, y1, steps, holdMs) => {
    await page.mouse.move(x0, y0); await page.mouse.down(); if (holdMs) await page.waitForTimeout(holdMs);
    for (let i = 1; i <= (steps || 10); i++) await page.mouse.move(x0 + (x1 - x0) * i / (steps || 10), y0 + (y1 - y0) * i / (steps || 10));
    await page.mouse.up(); await wait(400);
  };
  const sb = async () => page.locator('#screen').boundingBox();

  let s = await st(), g = await geo();
  check('starts with keyboard hidden and the app full height', !s.kb && Math.abs(g.app.h - g.H) < 1, g);
  await shot('01-messages-keyboard-hidden', 'Messages, keyboard hidden, chin lowered (handle only)');

  // ---- Show ----
  await page.locator('#f-msg').click(); await wait();
  s = await st(); g = await geo();
  check('tapping the field shows the keyboard', s.kb);
  check('app window resizes to sit above the keyboard + rail (adjust-resize)', Math.abs(g.app.bottom - (g.kb.h + 24)) < 1.5, g);
  check('composer sits directly above the keyboard, not under it', g.composer.bottom >= g.kb.h + 24 - 1, g);
  check('keyboard bottom rests on the 24pt rail (handle stays reachable)', Math.abs(g.kb.bottom - 24) < 1, g.kb);
  check('first letter auto-capitalised', s.shift === 'once');

  // ---- Typing, shift, caps lock ----
  await tapKeys('hello'); s = await st();
  check('typing with taps: "Hello" (auto-cap releases after one letter)', s.text === 'Hello' && s.shift === 'off', s);
  await shot('02-typing', 'Typing; caret and suggestions live');
  await key('shift').click(); await wait(120);
  check('shift tap → one capital', (await st()).shift === 'once');
  await shot('03-shift-once', 'Shift once: letters shown in capitals');
  // Double-tap from off locks caps.
  await page.evaluate(() => { const k = window.__kb; k.fields.messages.shift = 'off'; k.render(); });
  await key('shift').click(); await key('shift').click(); await wait(100);
  check('double-tap shift → caps lock', (await st()).shift === 'lock', await st());
  await shot('04-caps-lock', 'Caps lock: shift filled with underline');
  await tapKeys(' ok'); s = await st();
  check('caps lock types capitals and stays on', s.text.endsWith('OK') && s.shift === 'lock', s);
  await key('shift').click(); check('tap shift releases caps lock', (await st()).shift === 'off');

  // ---- Symbols ----
  await key('?123').click(); await wait(150);
  check('?123 opens the numbers/symbols page', (await st()).page === 'sym1' && await key('1').count() === 1);
  await shot('05-symbols', 'Numbers and symbols');
  await tapKeys('10'); await key('=\\<').click(); await wait(150);
  check('=\\< opens the second symbols page', (await st()).page === 'sym2' && await key('€').count() === 1);
  await shot('06-symbols-2', 'More symbols');
  await key('€').click(); await key('ABC').click(); await wait(150);
  s = await st(); check('symbols type and ABC returns to letters', s.text.endsWith('10€') && s.page === 'letters', s);

  // ---- Suggestions & autocorrect ----
  await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial(); k.render(); });
  await tapKeys('see you at th'); await wait(150);
  let strip = await page.locator('#strip .sg').allTextContents();
  check('three suggestion slots: typed | best | next', strip.length === 3 && strip[0] === 'th' && strip[1] === 'the', strip);
  await shot('07-suggestions', 'Suggestions while typing; best in the middle');
  await page.locator('#strip .sg.best').click(); s = await st();
  check('tapping the best suggestion completes the word and adds a space', s.text === 'See you at the ', s);
  strip = await page.locator('#strip .sg').allTextContents();
  check('next-word predictions appear between words', strip.includes('garden'), strip);
  await tapKeys('teh');
  check('correction offered with ruby underline', (await page.locator('#strip .sg.correct').count()) === 1);
  await shot('08-correction', 'Correction offered (“teh” → “the”)');
  await key('space').click(); s = await st();
  check('space applies the correction', s.text === 'See you at the the ', s);
  await key('bksp').click(); s = await st();
  check('one backspace right after a correction restores the typed word', s.text === 'See you at the teh', s);

  // ---- Editing: backspace hold, swipe-delete, space cursor, selection ----
  let c = await centerOf('#keys [data-k=bksp]');
  await page.mouse.move(c.x, c.y); await page.mouse.down(); await page.waitForTimeout(1700); await page.mouse.up(); await wait();
  s = await st(); check('holding ⌫ repeats and accelerates to whole words', s.text.length < 'See you at the teh'.length - 6, s.text);
  await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial({ text: 'Garden at ten works' }); k.render(); });
  c = await centerOf('#keys [data-k=bksp]');
  await drag(c.x, c.y, c.x - 60, c.y, 6);
  s = await st(); check('swipe left from ⌫ deletes a word', s.text === 'Garden at ten ', s.text);
  c = await centerOf('#keys [data-k=space]');
  const k1 = (await sb()).height / 832;
  await page.mouse.move(c.x, c.y); await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(c.x - i * 4.5 * k1, c.y);
  await shot('09-space-cursor', 'Dragging the space bar moves the cursor (keys dim)');
  await page.mouse.up(); await wait();
  s = await st(); check('dragging the space bar moves the caret left', s.a === s.b && s.a < 14 && s.a > 0, s);
  await page.evaluate(() => { const k = window.__kb; k.K.setSel(k.fields.messages, 14); k.render(); });
  c = await centerOf('#keys [data-k=space]');
  await drag(c.x, c.y, c.x - 40 * k1, c.y, 8, 520);
  s = await st(); check('hold space, then drag: extends a selection', s.a !== s.b, s);
  await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial({ text: 'Garden at ten works' }); k.render(); });
  // Long-press a word in the field.
  const fb = await page.locator('#f-msg span[data-base="0"]').boundingBox();
  await page.mouse.move(fb.x + 12, fb.y + fb.height / 2); await page.mouse.down(); await page.waitForTimeout(650); await page.mouse.up(); await wait();
  s = await st(); check('long-press selects the word under the finger', s.text.slice(Math.min(s.a, s.b), Math.max(s.a, s.b)) === 'Garden', s);
  const tools = await page.locator('#strip .tool').allTextContents();
  check('selection turns the strip into Cut / Copy / Paste / Select all / Undo / Redo', tools.join('|') === 'Cut|Copy|Paste|Select all|Undo|Redo', tools);
  const fitStrip = await page.evaluate(() => { const t = document.querySelector('#strip .tools').getBoundingClientRect(), h = document.querySelector('#strip .hide').getBoundingClientRect(); return [...document.querySelectorAll('#strip .tool')].every((b) => { const r = b.getBoundingClientRect(); return r.right <= Math.min(t.right, h.left) + 0.5; }); });
  check('edit strip fits: every tool clear of ⌄, none clipped', fitStrip);
  await shot('10-edit-strip', 'Selection: edit tools replace suggestions');
  await page.locator('#strip [data-tool=copy]').click(); await wait(150);
  await page.evaluate(() => { const k = window.__kb; k.K.setSel(k.fields.messages, k.fields.messages.text.length); k.render(); });
  await key('space').click();
  await page.evaluate(() => { const k = window.__kb; k.ui.editHold = true; k.render(); });
  await page.locator('#strip [data-tool=paste]').click(); s = await st();
  check('copy then paste', s.text === 'Garden at ten works Garden', s.text);
  await page.locator('#strip [data-tool=undo]').click(); s = await st();
  check('undo from the edit strip', s.text === 'Garden at ten works ', s.text);
  await page.keyboard.press('Control+a'); s = await st();
  check('hardware shortcut: Ctrl+A selects all', s.a === 0 && s.b === s.text.length);
  await page.keyboard.type('Hi Jamie'); s = await st();
  check('hardware typing replaces the selection', s.text === 'Hi Jamie', s.text);

  // ---- Send ----
  const before = await page.locator('#thread p').count();
  await key('action').click(); await wait();
  s = await st(); check('Send action key sends and clears the field; keyboard stays', s.text === '' && s.kb && (await page.locator('#thread p').count()) === before + 1, s);

  // ---- Hide paths (draft kept) ----
  const draft = async () => { await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial({ text: 'Draft' }); k.render(); }); };
  await draft();
  const sbb = await page.locator('#strip').boundingBox();
  await drag(sbb.x + sbb.width * 0.3, sbb.y + sbb.height / 2, sbb.x + sbb.width * 0.3, sbb.y + sbb.height / 2 + 70, 8);
  s = await st(); g = await geo();
  check('swipe down on the strip hides the keyboard; draft kept; app back to full height', !s.kb && s.text === 'Draft' && Math.abs(g.app.h - g.H) < 1.5, { s, g });
  await page.locator('#f-msg').click(); await wait();
  s = await st(); check('tapping past the end of a draft puts the caret at its end', s.a === s.text.length && s.a === s.b, s);
  let nMsg = await page.locator('#thread p').count();
  await page.locator('#strip [data-tool=hide]').click(); await wait();
  s = await st(); check('⌄ hides the keyboard without sending; draft kept', !s.kb && s.text === 'Draft' && (await page.locator('#thread p').count()) === nMsg, s);

  // Hardware keys are scoped to the editor: focused controls keep native Enter/Space activation.
  await page.locator('#f-msg').click(); await wait();
  await page.locator('#strip [data-tool=hide]').focus(); await page.keyboard.press('Enter'); await wait();
  s = await st(); check('Enter on focused ⌄ hides the keyboard; nothing sent or typed', !s.kb && s.text === 'Draft' && (await page.locator('#thread p').count()) === nMsg, s);
  await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial({ text: 'See you at th' }); k.render(); });
  await page.locator('#f-msg').click(); await wait();
  await page.evaluate(() => { const k = window.__kb; k.K.setSel(k.fields.messages, 13); k.render(); });
  await page.locator('#strip .sg.best').focus(); await page.keyboard.press(' '); await wait();
  s = await st(); check('Space on a focused suggestion picks it; no extra space typed', s.text === 'See you at the ', s.text);
  await page.locator('#strip .sg').nth(1).focus(); await page.keyboard.press('Enter'); await wait();
  s = await st(); check('Enter on a focused suggestion never sends', (await page.locator('#thread p').count()) === nMsg && s.text.startsWith('See you at the'), s.text);
  await page.evaluate(() => { const k = window.__kb; k.ui.chin = true; k.render(); }); await wait();
  await page.locator('#chin-mic').focus(); await page.keyboard.press('Enter'); await wait();
  s = await st(); check('Enter on the focused chin mic toggles mute; nothing sent or typed', s.muted && s.text.startsWith('See you at the') && (await page.locator('#thread p').count()) === nMsg, s);
  await page.locator('#chin-mic').focus(); await page.keyboard.press(' '); await wait();
  s = await st(); check('Space on the focused chin mic unmutes; no space typed', !s.muted && s.text.startsWith('See you at the') && !s.text.endsWith('  '), s);
  await page.evaluate(() => { const k = window.__kb; k.ui.chin = false; k.render(); }); await page.keyboard.press('Escape'); await wait();

  // Reading position: reviewing history is never yanked to the bottom; following resumes at the bottom or on send.
  const thr = () => page.evaluate(() => { const t = document.getElementById('thread'); const p = t.querySelectorAll('p')[3].getBoundingClientRect(), r = t.getBoundingClientRect(); return { top: t.scrollTop, gap: t.scrollHeight - t.clientHeight - t.scrollTop, anchor: p.top - r.top }; });
  await page.evaluate(() => { const t = document.getElementById('thread'); t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); }); await wait();
  const r0 = await thr();
  await page.locator('#f-msg').click(); await wait(500);
  let r1 = await thr();
  check('opening the keyboard while reading history keeps the messages being read in place', Math.abs(r1.anchor - r0.anchor) < 2 && r1.gap > 40, { r0, r1 });
  await tapKeys('ok'); await wait();
  r1 = await thr(); check('typing while reading history does not jump to the bottom', Math.abs(r1.anchor - r0.anchor) < 2, { r0, r1 });
  await page.evaluate(() => { const k = window.__kb; k.ui.chin = true; k.render(); }); await wait(500);
  r1 = await thr(); check('raising the chin while reading history keeps the reading position', Math.abs(r1.anchor - r0.anchor) < 2, { r0, r1 });
  await page.evaluate(() => { const k = window.__kb; k.ui.chin = false; k.render(); }); await wait(500);
  await key('action').click(); await wait(500);
  r1 = await thr(); check('sending returns to and follows the newest message', r1.gap <= 4, r1);
  await page.locator('#strip [data-tool=hide]').click(); await wait(500);
  await page.locator('#f-msg').click(); await wait(500);
  r1 = await thr(); check('at the bottom, opening the keyboard keeps the newest message visible', r1.gap <= 4, r1);
  await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial({ text: 'Draft' }); k.render(); });
  await page.locator('#strip [data-tool=hide]').click(); await wait();
  // Outside-tap dismissal: only a tap on blank conversation space.
  await page.locator('#f-msg').click(); await wait();
  await page.locator('#thread p.in').last().click(); await wait();
  check('tapping a message does not hide the keyboard', (await st()).kb);
  await page.locator('#msgs .a-head b').click(); await wait();
  check('tapping the header does not hide the keyboard', (await st()).kb);
  let tb = await page.locator('#thread').boundingBox();
  await drag(tb.x + tb.width - 20, tb.y + 60, tb.x + tb.width - 20, tb.y + 200, 8);
  check('scrolling/dragging the conversation does not hide the keyboard', (await st()).kb);
  const lastIn = await page.locator('#thread p.in').last().boundingBox();
  await page.mouse.click(tb.x + tb.width - 16, lastIn.y + lastIn.height / 2); await wait();
  check('a tap on blank conversation space hides the keyboard', !(await st()).kb);
  await page.locator('#f-msg').click(); await wait(); await page.keyboard.press('Escape'); await wait();
  check('Esc hides the keyboard', !(await st()).kb);

  // ---- Rose chin with the keyboard ----
  await page.evaluate(() => { const k = window.__kb; k.fields.messages = k.K.initial(); k.render(); });
  await page.locator('#f-msg').click(); await wait();
  await tapKeys('on my way');
  const B = await sb(), cx = B.x + B.width / 2, k = B.height / 832;
  await drag(cx, B.y + B.height - 8 * k, cx, B.y + B.height - 100 * k, 10);
  s = await st(); g = await geo();
  check('swipe up from the bottom rail raises the chin; keyboard stays open', s.chin && s.kb, s);
  check('keyboard rests on the chin’s concave top: 58pt from the bottom, 34pt above the 24pt rail', Math.abs(g.kb.bottom - 58) < 1.5, g.kb);
  check('app shrinks by the same amount; composer still above the keyboard', Math.abs(g.app.bottom - (g.kb.h + 58)) < 1.5 && g.composer.bottom >= g.kb.h + 57, g);
  const reach = await page.evaluate(() => ['chin-rose', 'chin-mic', 'chin-switch'].map((id) => { const b = document.getElementById(id).getBoundingClientRect(); const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return !!el && !!el.closest('#' + id); }));
  check('Rose, mic and switcher are unobstructed with the keyboard open', reach.every(Boolean), reach);
  await shot('11-chin-raised-keyboard', 'Chin raised under an open keyboard: mic reachable');
  await page.locator('#chin-mic').click(); await wait();
  s = await st(); check('chin mic mutes; keyboard and draft untouched', s.muted && s.kb && s.text === 'On my way', s);
  const ind = await page.evaluate(() => { const e = document.getElementById('mic-ind'); return { vis: getComputedStyle(e).display !== 'none', svg: !!e.querySelector('svg'), text: e.textContent, label: e.getAttribute('aria-label') }; });
  check('status bar shows an explicit muted-mic indicator (chin glyph + “Mic off”, labelled)', ind.vis && ind.svg && ind.text === 'Mic off' && ind.label === 'Microphone muted', ind);
  await key('?123').click(); await key('!').click(); await key('ABC').click();
  s = await st(); check('typing continues with the chin up', s.text === 'On my way!', s.text);
  await shot('12-muted-chin-keyboard', 'Mic muted from the chin while typing');
  const cb = await page.locator('#chin').boundingBox();
  await drag(cx, cb.y + cb.height - 10, cx, cb.y + cb.height + 40, 6);
  s = await st(); g = await geo();
  check('swipe down on the chin lowers it; keyboard returns to the rail', !s.chin && s.kb && Math.abs(g.kb.bottom - 24) < 1.5, { s, kb: g.kb });
  check('muted state stays visible in the status bar after the chin lowers', await page.locator('#mic-ind').isVisible());
  await page.evaluate(() => { const k = window.__kb; k.ui.muted = false; k.render(); });
  check('the indicator disappears when the mic is back on', !(await page.locator('#mic-ind').isVisible()));
  await page.evaluate(() => { const k = window.__kb; k.ui.muted = true; k.render(); });
  await page.locator('#strip [data-tool=hide]').click(); await wait();
  await drag(cx, B.y + B.height - 8 * k, cx, B.y + B.height - 100 * k, 10);
  g = await geo();
  check('chin with the keyboard hidden: app ends on the chin’s concave top', (await st()).chin && Math.abs(g.app.bottom - 58) < 1.5, g.app);
  await shot('13-chin-raised-no-keyboard', 'Chin raised, keyboard hidden (matches HiFi behaviour)');
  await page.locator('#chin-mic').click(); await page.keyboard.press('Escape'); await wait();
  await page.locator('#f-msg').click(); await wait();
  await drag(cx, B.y + B.height - 8 * k, cx, B.y + B.height - 380 * k, 12);
  s = await st(); check('long swipe up leaves the app: keyboard hides, draft kept', !s.kb && !s.chin && s.text === 'On my way!', s);

  // ---- Notes (multi-line) ----
  await page.locator('#h-notes').click(); await wait();
  await page.locator('#f-note').click(); await wait();
  await page.evaluate(() => { const k = window.__kb; const f = k.fields.notes; k.K.setSel(f, f.text.length); k.render(); });
  await tapKeys('call jamie'); await key('action').click(); await tapKeys('pack lunch');
  s = await st(); check('Notes: Return inserts a line, auto-capitalises, autocorrects names', s.text.endsWith('Call Jamie\nPack lunch'), s.text);
  g = await geo();
  const noteBar = await page.evaluate(() => { const s = document.getElementById('screen'), r = s.getBoundingClientRect(), k = r.height / s.offsetHeight; return (r.bottom - document.querySelector('.note-bar').getBoundingClientRect().bottom) / k; });
  check('Notes: the app’s bottom bar rides on top of the keyboard', noteBar >= g.kb.h + 24 - 1, { noteBar, kb: g.kb.h });
  const caretVis = await page.evaluate(() => { const c = document.querySelector('#f-note .caret'), f = document.getElementById('f-note'); if (!c) return false; const a = c.getBoundingClientRect(), b = f.getBoundingClientRect(); return a.top >= b.top - 1 && a.bottom <= b.bottom + 1; });
  check('Notes: caret kept visible inside the shortened body', caretVis);
  await shot('14-notes-multiline', 'Notes: multi-line editing with Return');

  // ---- Key sizes / a11y ----
  const sizes = await page.evaluate(() => { const s = document.getElementById('screen'), k = s.getBoundingClientRect().height / s.offsetHeight; return [...document.querySelectorAll('#keys .key')].map((e) => { const b = e.getBoundingClientRect(); return [b.width / k, b.height / k]; }); });
  const minW = Math.min(...sizes.map((x) => x[0])), minH = Math.min(...sizes.map((x) => x[1]));
  check('letter keys ≥ 32×46pt (industry-standard keyboard pitch); every key labelled', minW >= 31.5 && minH >= 45.5 && await page.evaluate(() => [...document.querySelectorAll('#keys .key')].every((k) => k.getAttribute('aria-label'))), { minW, minH });
  await page.locator('#h-reduced').check(); await page.locator('#strip [data-tool=hide]').click(); await page.locator('#f-note').click(); await wait(100);
  check('reduced motion: keyboard appears without travel', await page.evaluate(() => getComputedStyle(document.getElementById('kb')).transitionDuration.split(',').every((d) => parseFloat(d) <= 0.002)));
  check('desktop: no console errors', errors.length === 0, errors);
  await page.close();

  // ================= Phones: real touch =================
  for (const [name, dev] of [['iphone13', devices['iPhone 13']], ['pixel7', devices['Pixel 7']], ['iphoneSE', devices['iPhone SE']]]) {
    const ctx = await browser.newContext({ ...dev });
    const p = await ctx.newPage(); const errs = [];
    p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(FILE); await p.waitForTimeout(700);
    const cdp = await ctx.newCDPSession(p);
    const touch = async (pts) => { await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [pts[0]] }); for (const q of pts.slice(1)) { await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [q] }); await p.waitForTimeout(16); } await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await p.waitForTimeout(450); };
    const line = (x, y0, y1, n) => Array.from({ length: n + 1 }, (_, i) => ({ x, y: y0 + (y1 - y0) * i / n }));
    const S = () => p.evaluate(() => { const k = window.__kb, f = k.fields[k.ui.app]; return { kb: k.ui.kb, chin: k.ui.chin, text: f.text, sw: document.documentElement.scrollWidth, vw: innerWidth, sh: document.documentElement.scrollHeight, vh: innerHeight }; });
    const r = await p.locator('#screen').boundingBox();
    let q = await S();
    check(name + ': fills the screen, no page scroll', Math.abs(r.width - q.vw) < 1.5 && q.sw <= q.vw && q.sh <= q.vh + 1, { r, q });
    await p.locator('#f-msg').tap(); await p.waitForTimeout(450);
    for (const ch of 'hi') await p.locator('#keys [data-k="' + ch + '"]').tap();
    await p.locator('#keys [data-k=space]').tap();
    q = await S(); check(name + ': touch opens the keyboard and types', q.kb && q.text === 'Hi ', q);
    await p.screenshot({ path: path.join(OUT, 'phone-' + name + '-keyboard.png') });
    await touch(line(r.x + r.width / 2, r.y + r.height - 6, r.y + r.height - 96, 10));
    q = await S(); check(name + ': touch swipe up raises the chin over the open keyboard', q.chin && q.kb, q);
    await p.screenshot({ path: path.join(OUT, 'phone-' + name + '-chin.png') });
    const st2 = await p.locator('#strip').boundingBox();
    await touch(line(st2.x + st2.width * 0.3, st2.y + st2.height / 2, st2.y + st2.height / 2 + 60, 8));
    q = await S(); check(name + ': touch swipe down on the strip hides the keyboard, draft kept', !q.kb && q.text === 'Hi ', q);
    check(name + ': no errors', errs.length === 0, errs);
    await ctx.close();
  }
  await browser.close();
  const passed = results.filter((r) => r.ok).length;
  const git = (c) => { try { return require('child_process').execSync(c, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };
  fs.writeFileSync(path.join(OUT, 'check.json'), JSON.stringify({ when: new Date().toISOString(), file: FILE,
    commit: git('git rev-parse HEAD'), tree_clean: git('git status --porcelain -- .') === '',
    html_blob: git('git hash-object Rose-Keyboard.html'), committed_html_blob: git('git rev-parse HEAD:prototypes/rose-keyboard/Rose-Keyboard.html'), html_sha256: sha(fs.readFileSync(path.join(__dirname, 'Rose-Keyboard.html'))), passed, total: results.length, results, shots }, null, 2));
  console.log(passed + '/' + results.length + ' checks passed');
  if (passed !== results.length) process.exitCode = 1;
})();
