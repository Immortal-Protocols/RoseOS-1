// Rose HiFi — GLACIER-1E-1110 review suite: functional checks with real input, composite contrast
// measured on rendered pixels, and the review capture set.
// Usage: node prototypes/rose-hifi/glacier-check.cjs [outDir]
// Runs Rose-Glacier.html (and Oxblood via the harness) at the 384×832 design size, iPhone 13
// (390 wide, 654pt short screen), iPhone SE (320 wide) and Pixel 7, with CDP touch on phones.
// Headless Chromium + SwiftShader: interaction/layout/pixel evidence, not a physical phone or Safari.
'use strict';
const path = require('path'), fs = require('fs'), crypto = require('crypto');
const { execSync } = require('child_process');
const { chromium, devices } = require('playwright');
const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out', 'glacier'));
const FILE = 'file://' + path.join(__dirname, 'Rose-Glacier.html');
fs.mkdirSync(OUT, { recursive: true });
const results = [], shots = [], contrast = [];
const check = (n, c, x) => { results.push({ n, ok: !!c }); console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  ' + (x === undefined ? '' : JSON.stringify(x).slice(0, 300)))); };
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const git = (c) => { try { return execSync(c, { cwd: __dirname }).toString().trim(); } catch (e) { return null; } };

// ---- composite contrast: foreground = computed text colour; background = rendered pixels around it ----
function lum([r, g, b]) { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); }
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

async function measure(page, label, sel, min) {
  // Freeze transitions/animations on the element so colours are read and hidden at their settled values.
  await page.evaluate((sel) => { const el = document.querySelector(sel); if (!el) return; [el, ...el.querySelectorAll('*')].forEach((n) => { n.style.setProperty('transition', 'none', 'important'); }); void el.offsetWidth; }, sel);
  const info = await page.evaluate((sel) => {
    const el = document.querySelector(sel); if (!el) return null;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    const m = cs.color.match(/[\d.]+/g).map(Number);
    return { x: r.left, y: r.top, w: r.width, h: r.height, fg: m.slice(0, 3), a: m[3] === undefined ? 1 : m[3], vis: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' };
  }, sel);
  if (!info || !info.vis) { check('contrast ' + label + ' (element visible)', false, sel); return; }
  // Hide the element's own text so the rendered background beneath it can be sampled.
  // Hide the element's text, its descendants' text and its glyphs with an injected stylesheet rule, so
  // re-rendered nodes stay hidden too (inline styles are lost when a field or strip re-renders).
  await page.evaluate((sel) => {
    const st = document.createElement('style'); st.id = '__cm_hide';
    st.textContent = sel + ',' + sel + ' *{color:transparent!important;transition:none!important;caret-color:transparent!important}' +
      sel + ' svg,' + sel + ' img{visibility:hidden!important}' + (/(^|\s)(svg|img)$/.test(sel) ? sel + '{visibility:hidden!important}' : '') +
      sel + ' .caret{visibility:hidden!important}';
    document.head.appendChild(st);
  }, sel);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const buf = await page.screenshot({ clip: { x: info.x, y: info.y, width: Math.max(1, info.w), height: Math.max(1, info.h) } });
  await page.evaluate((sel) => { const st = document.getElementById('__cm_hide'); if (st) st.remove(); const el = document.querySelector(sel); if (el) [el, ...el.querySelectorAll('*')].forEach((n) => n.style.removeProperty('transition')); }, sel);
  const { PNG } = await pngTools();
  const png = PNG(buf);
  // Ratio of the text colour against every sampled background pixel; report the 5th percentile so a
  // small decoration inside the box (e.g. the pearl) cannot dominate, while a bright bevel or field
  // covering a real share of the area still fails. The absolute minimum is recorded too.
  const rs = [];
  for (let i = 0; i < png.data.length; i += 4 * 3) {
    const bg = [png.data[i], png.data[i + 1], png.data[i + 2]];
    const fg = info.a < 1 ? info.fg.map((c, k) => c * info.a + bg[k] * (1 - info.a)) : info.fg;
    // Pixels in the foreground colour itself (e.g. a muted ring drawn in the same warm colour) are not background.
    if (Math.abs(bg[0] - fg[0]) + Math.abs(bg[1] - fg[1]) + Math.abs(bg[2] - fg[2]) < 40) continue;
    rs.push(ratio(fg, bg));
  }
  rs.sort((a, b) => a - b);
  const p5 = rs[Math.floor(rs.length * 0.05)], min0 = rs[0];
  const ok = p5 >= min;
  contrast.push({ label, sel, p5: +p5.toFixed(2), min: +min0.toFixed(2), required: min, ok });
  if (!ok) fs.writeFileSync(path.join(OUT, 'contrast-fail-' + label.replace(/\W+/g, '-') + '.png'), buf);
  check('contrast ' + label + ' ≥ ' + min + ':1 on the rendered background (p5 ' + p5.toFixed(2) + ', min ' + min0.toFixed(2) + ')', ok);
}
// Minimal PNG decode via the browser-independent zlib (RGBA, 8-bit, no interlace — Chromium screenshots).
async function pngTools() {
  const zlib = require('zlib');
  return { PNG: (buf) => {
    let p = 8, w, h, ctype, idat = [];
    while (p < buf.length) { const len = buf.readUInt32BE(p), type = buf.toString('ascii', p + 4, p + 8), d = buf.slice(p + 8, p + 8 + len);
      if (type === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ctype = d[9]; if (d[8] !== 8 || d[12] !== 0 || (ctype !== 2 && ctype !== 6)) throw new Error('unsupported PNG'); }
      else if (type === 'IDAT') idat.push(d); p += 12 + len; }
    const raw = zlib.inflateSync(Buffer.concat(idat)), bpp = ctype === 6 ? 4 : 3, stride = w * bpp, out = Buffer.alloc(w * h * 4);
    let prev = Buffer.alloc(stride);
    for (let y = 0; y < h; y++) {
      const f = raw[y * (stride + 1)], line = raw.slice(y * (stride + 1) + 1, (y + 1) * (stride + 1)), cur = Buffer.alloc(stride);
      for (let x = 0; x < stride; x++) {
        const a = x >= bpp ? cur[x - bpp] : 0, b = prev[x], c = x >= bpp ? prev[x - bpp] : 0;
        const pa = Math.abs(b - c), pb = Math.abs(a - c), pc = Math.abs(a + b - 2 * c);
        cur[x] = (line[x] + [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][f]) & 255;
      }
      for (let x = 0; x < w; x++) { out[(y * w + x) * 4] = cur[x * bpp]; out[(y * w + x) * 4 + 1] = cur[x * bpp + 1]; out[(y * w + x) * 4 + 2] = cur[x * bpp + 2]; out[(y * w + x) * 4 + 3] = bpp === 4 ? cur[x * bpp + 3] : 255; }
      prev = cur;
    }
    return { width: w, height: h, data: out };
  } };
}

async function run(browser, name, ctxOpts, phone) {
  const ctx = await browser.newContext(ctxOpts);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(FILE); await page.waitForTimeout(1200);
  const P = (n) => name + ': ' + n;
  const wait = (ms) => page.waitForTimeout(ms || 350);
  const R = (fn, a) => page.evaluate(fn, a);
  const st = () => R(() => { const s = window.__rose.getState(), y = window.RoseSystem.state(); return { view: s.view, app: s.app, window: s.window, muted: s.muted, peek: window.__rose.isPeek(), kb: y.kb.open, kbT: y.kb.target, msg: y.fields.msg.text, ma: y.fields.msg.a, mb: y.fields.msg.b, reply: y.fields.reply.text, rstate: y.shade.reply.state, shade: y.shade.open, items: y.shade.items.length, ret: y.ret.phase, origin: !!y.ret.origin, tasks: Object.fromEntries(Object.entries(s.tasks).map(([k, v]) => [k, v.state + ':' + v.step])) }; });
  const tap = async (sel) => { if (phone) await page.locator(sel).first().tap(); else await page.locator(sel).first().click(); };
  const key = (k) => '#keys [data-k="' + k.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"]';
  const typeKeys = async (str) => { for (const ch of str) await tap(key(ch === ' ' ? 'space' : ch)); };
  const shot = async (n, note) => { await page.mouse.move(2, 2).catch(() => {}); await wait(450); const f = path.join(OUT, name + '-' + n + '.png'); const b = await (phone ? page.screenshot({ path: f }) : page.locator('#device').screenshot({ path: f })); shots.push({ file: path.basename(f), note, sha256: sha(b) }); };
  const D = (a) => R((a) => window.__rose.dispatch(a), a);
  const geo = (sel) => R((sel) => { const s = document.getElementById('screen'), r = s.getBoundingClientRect(), k = r.height / s.offsetHeight, e = document.querySelector(sel); if (!e) return null; const b = e.getBoundingClientRect(); return { top: (b.top - r.top) / k, bottom: (r.bottom - b.bottom) / k, left: (b.left - r.left) / k, right: (r.right - b.right) / k, w: b.width / k, h: b.height / k, H: s.offsetHeight }; }, sel);
  const swipe = async (x, y0, y1) => {
    if (phone) { const cdp = await ctx.newCDPSession(page); await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: y0 }] }); for (let i = 1; i <= 10; i++) { await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y0 + (y1 - y0) * i / 10 }] }); await page.waitForTimeout(16); } await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
    else { await page.mouse.move(x, y0); await page.mouse.down(); for (let i = 1; i <= 10; i++) await page.mouse.move(x, y0 + (y1 - y0) * i / 10); await page.mouse.up(); }
    await wait(500);
  };
  const SB = await page.locator('#screen').boundingBox(), K = SB.height / (await R(() => document.getElementById('screen').offsetHeight));

  // ---------- Home + field ----------
  const fieldImg = await R(() => getComputedStyle(document.querySelector('#v-home .wall') || document.body).backgroundImage.slice(0, 30));
  check(P('Glacier palette active with the Illustrator field image'), (await R(() => document.getElementById('device').dataset.palette)) === 'glacier' && /data:image\/png/.test(fieldImg), fieldImg);
  await shot('01-home', 'Home');
  // ---------- Messages + keyboard ----------
  await D({ type: 'openApp', value: 'messages' }); await wait(700);
  await tap('#f-msg'); await wait(450);
  let s = await st(); check(P('tapping the composer opens the keyboard'), s.kb && s.kbT === 'msg', s);
  let g = await geo('#v-app'), gk = await geo('#kb');
  check(P('app window ends on the keyboard (adjust-resize above the 24pt rail)'), Math.abs(g.bottom - (gk.h + 24)) < 2 && Math.abs(gk.bottom - 24) < 1.5, { g, gk });
  await typeKeys('see you at th'); s = await st();
  check(P('typing via keys, auto-capital'), s.msg === 'See you at th', s.msg);
  check(P('suggestion strip shows typed | best | next'), (await page.locator('#strip .sg.best').textContent()) === 'the');
  await shot('02-keyboard-suggestions', 'Keyboard with suggestions');
  await measure(page, 'key label', key('q'), 4.5);
  await measure(page, 'best suggestion', '#strip .sg.best span', 4.5);
  await measure(page, 'composer text', '#f-msg span[data-base="0"]', 4.5);
  await tap(key('shift')); await wait(150);
  check(P('shift once'), (await R(() => window.RoseSystem.state().fields.msg.shift)) === 'once');
  await R(() => { window.RoseSystem.state().fields.msg.shift = 'off'; }); await tap(key('shift')); await tap(key('shift')); await wait(150);
  check(P('double-tap shift → caps lock'), (await R(() => window.RoseSystem.state().fields.msg.shift)) === 'lock');
  await shot('03-keyboard-capslock', 'Caps lock');
  await tap(key('shift')); await tap(key('?123')); await wait(150);
  check(P('?123 → symbols page'), (await page.locator(key('1')).count()) === 1);
  await shot('04-keyboard-symbols', 'Symbols');
  await tap(key('ABC'));
  await R(() => { const y = window.RoseSystem.state(); window.RoseKeys.selectWord(y.fields.msg, 1); window.RoseSystem.showKb('msg'); });
  await wait(200);
  check(P('selection shows the edit strip (Cut/Copy/Paste/Select all/Undo/Redo)'), (await page.locator('#strip .tool').allTextContents()).join('|') === 'Cut|Copy|Paste|Select all|Undo|Redo');
  await shot('05-keyboard-edit', 'Edit strip with a selection');
  await R(() => { const y = window.RoseSystem.state(); window.RoseKeys.setSel(y.fields.msg, y.fields.msg.text.length); window.RoseSystem.showKb('msg'); });
  // ---------- Chin raised over the keyboard; mic states ----------
  await swipe(SB.x + SB.width / 2, SB.y + SB.height - 8 * K, SB.y + SB.height - 110 * K);
  s = await st(); g = await geo('#kb');
  check(P('swipe up from the 24pt rail raises the chin; keyboard stays, 34pt higher (58pt)'), s.peek && s.kb && Math.abs(g.bottom - 58) < 2, { s, g });
  const reach = await R(() => ['peek-rose', 'peek-mic', 'peek-overview'].every((id) => { const b = document.getElementById(id).getBoundingClientRect(); const e = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return e && e.closest('#' + id); }));
  check(P('chin controls unobstructed with the keyboard open'), reach);
  await shot('06-chin-raised-keyboard', 'Chin raised under the keyboard (mic on)');
  await tap('#peek-mic'); await wait(300);
  s = await st(); check(P('chin mic mutes; keyboard and draft untouched'), s.muted && s.kb && s.msg === 'See you at th', s);
  await shot('07-chin-muted', 'Mic muted from the chin');
  await measure(page, 'muted mic glyph (≥3:1 UI)', '#peek-mic svg', 3);
  await tap('#peek-mic'); await wait(200);
  // ---------- Return: success restores exact context ----------
  await R(() => { const t = document.querySelector('#v-app .c-msg'); for (let i = 0; i < 14; i++) { const p = document.createElement('p'); p.className = i % 2 ? 'out' : 'in'; p.textContent = 'Earlier message ' + (i + 1); t.prepend(p); } });
  await wait(200);
  await R(() => { const t = document.querySelector('#v-app .c-msg'); t.scrollTop = 40; t.dispatchEvent(new Event('scroll')); });
  await R(() => { const y = window.RoseSystem.state(); window.RoseKeys.setSel(y.fields.msg, 4, 7); window.RoseSystem.showKb('msg'); });
  const before = await R(() => ({ top: document.querySelector('#v-app .c-msg').scrollTop }));
  await tap('#peek-rose'); await wait(1700);
  s = await st(); check(P('Rose opened from Messages keeps the origin; keyboard tucked away'), s.view === 'rose' && s.origin && !s.kb, s);
  const setSel = (id, v) => page.evaluate(([id, v]) => { const c = document.getElementById(id); c.value = v; c.dispatchEvent(new Event('change')); }, [id, v]);
  g = await geo('#rose-return'); const gc = await geo('#rose-conn'), go = await geo('#rose-output');
  check(P('return control: leading edge of Rose content, above the chin and Disconnect'), g && g.left < 24 && g.bottom > 58 && g.bottom >= gc.bottom + gc.h - 4, { g, gc });
  const rr = await R(() => { const b = document.getElementById('rose-return'), cs = getComputedStyle(b); return { label: b.querySelector('.source-label').textContent, h: b.offsetHeight, font: cs.fontSize, icon: b.querySelectorAll('img').length + b.querySelectorAll('svg').length, aria: b.getAttribute('aria-label') }; });
  check(P('return control matches V3: chevron + source name, 12px label, ≥48pt hit area'), rr.label === 'Messages' && rr.h >= 48 && rr.font === '12px' && rr.icon === 1 && rr.aria === 'Return to Messages', rr);
  await shot('08-rose-return', 'Rose with the V3 return control');
  await measure(page, 'return label', '#rose-return .source-label', 4.5);
  await measure(page, 'Rose caption', '#rose-caption', 4.5);
  await measure(page, 'Disconnect label', '#rose-conn .conn-label', 4.5);
  // Pending: direct request, held by the harness's manual acknowledgement (simulation, explicit).
  await setSel('h-ack', 'manual');
  await tap('#rose-return'); await wait(150);
  s = await st();
  check(P('tap requests return directly → Returning… with a separate Cancel'), s.ret === 'pending' && (await page.locator('#rose-return .source-label').textContent()) === 'Returning…' && await page.locator('#rose-return-cancel').isVisible() && (await page.getAttribute('#rose-return', 'aria-busy')) === 'true', s);
  await shot('09-return-pending', 'Return pending (acknowledgement held by the harness)');
  const id1 = await R(() => window.RoseSystem.state().ret.pending.id);
  await R(() => document.getElementById('rose-return').click()); await wait(150);
  check(P('repeat taps while pending are ignored'), (await R(() => window.RoseSystem.state().ret.pending.id)) === id1);
  await wait(900);
  check(P('no departure without acknowledgement (Rose stays usable)'), (await st()).view === 'rose');
  await tap('#rose-return-cancel'); await wait(250);
  s = await st(); check(P('Cancel invalidates the request and stays in Rose'), s.view === 'rose' && s.ret === 'idle' && (await R(() => window.RoseSystem.state().ret.status)) === 'cancelled', s);
  // Failure: acknowledged failure → exact sheet, no keyboard.
  await tap('#rose-return'); await wait(150); await page.evaluate(() => document.getElementById('h-ack-fail').click()); await wait(300);
  s = await st();
  check(P('acknowledged failure → “Could not return.” sheet; stays in Rose; keyboard hidden'), s.view === 'rose' && !s.kb && (await page.locator('#ret-sheet-title').textContent()) === 'Could not return.' && await page.locator('#ret-sheet').isVisible(), s);
  await shot('10a-return-failed', 'Return failed: exact sheet');
  await measure(page, 'sheet text', '#ret-sheet-desc', 4.5);
  await tap('#ret-stay'); await wait(250);
  check(P('Stay in Rose closes the sheet'), !(await page.locator('#ret-sheet').isVisible()) && (await st()).view === 'rose');
  // Unavailable (closed) and replaced tasks.
  await setSel('h-origin', 'closed'); await wait(100);
  await tap('#rose-return'); await wait(300);
  s = await st();
  const sheetTxt = await page.locator('#ret-sheet').textContent();
  check(P('closed origin → “Your source app is unavailable.” with Stay / Open switcher / Go Home; no relaunch, no keyboard'), s.view === 'rose' && !s.kb && /Your source app is unavailable\./.test(sheetTxt) && /You are still in Rose\. Choose where to go; Rose keeps working\./.test(sheetTxt) && /Stay in Rose/.test(sheetTxt) && /Open switcher/.test(sheetTxt) && /Go Home/.test(sheetTxt), { s, sheetTxt });
  await shot('10-return-unavailable', 'Origin unavailable: exact sheet');
  await tap('#ret-stay'); await wait(200);
  await setSel('h-origin', 'replaced'); await wait(100);
  await tap('#rose-return'); await wait(300);
  check(P('replaced task (new generation) is not the exact live task → unavailable'), (await R(() => window.RoseSystem.state().ret.phase)) === 'unavailable' && !(await st()).kb);
  await tap('#ret-stay'); await wait(200);
  // Success: back to the original live task (the harness re-points the origin to the current generation).
  await R(() => { const r = window.RoseSystem.state().ret; r.cond = 'alive'; r.origin.gen = r.gen; });
  await setSel('h-origin', 'alive'); await setSel('h-ack', 'success-420');
  await D({ type: 'startTask', id: 'saturday' }); const t0 = (await st()).tasks.saturday;
  await tap('#rose-return'); await wait(1300);
  s = await st();
  const after = await R(() => ({ top: document.querySelector('#v-app .c-msg').scrollTop }));
  check(P('acknowledged success departs to the exact app with draft, selection and keyboard'), s.view === 'app' && s.app === 'messages' && s.msg === 'See you at th' && s.ma === 4 && s.mb === 7 && s.kb && s.kbT === 'msg', s);
  check(P('reading position restored'), Math.abs(after.top - before.top) <= 2, { before, after });
  await wait(2800);
  const t1 = (await st()).tasks.saturday;
  check(P('Rose work keeps running through the return'), t0.startsWith('running') && (t1 !== t0), { t0, t1 });
  await shot('11-returned', 'Returned: draft, selection, scroll, keyboard restored');
  await tap('#strip [data-tool=hide]'); await wait(400);
  // ---------- Notifications ----------
  await swipe(SB.x + SB.width / 2, SB.y + 10 * K, SB.y + 200 * K);
  s = await st(); check(P('pull down from the top opens the shade'), s.shade, s);
  await shot('12-shade-grouped', 'Shade: grouped');
  await measure(page, 'shade title', '#sh-title', 4.5);
  await measure(page, 'notification head', '.sh-card[data-id="rose-review"] .sh-open b', 4.5);
  await measure(page, 'notification body', '.sh-card[data-id="rose-review"] .sh-open span', 4.5);
  await measure(page, 'primary action', '.sh-card[data-id="rose-review"] .sh-btn.primary', 4.5);
  await measure(page, 'input status', '.sh-input span', 4.5);
  await tap('[data-expand]'); await wait(250);
  check(P('Messages group expands'), (await page.locator('.sh-msg').count()) === 2);
  await shot('13-shade-expanded', 'Shade: expanded');
  await tap('[data-reply="m-jamie"]'); await wait(450);
  await typeKeys('three works'); s = await st();
  check(P('inline reply types through the keyboard; shade sits above it'), s.kb && s.kbT === 'reply' && s.reply === 'Three works', s);
  g = await geo('#shade'); gk = await geo('#kb');
  check(P('shade content ends above the keyboard inset'), g.bottom >= gk.h + 24 - 1.5, { g, gk });
  await shot('14-shade-reply', 'Inline reply with keyboard');
  await tap('#strip [data-tool=hide]'); await wait(300);
  s = await st(); check(P('hiding the keyboard never sends or dismisses; draft kept'), !s.kb && s.shade && s.reply === 'Three works' && s.rstate === 'draft', s);
  await page.evaluate(() => { const c = document.getElementById('h-fail'); c.checked = true; c.dispatchEvent(new Event('change')); });
  await tap('#reply-send'); await wait(80);
  s = await st(); check(P('Send shows Sending… and blocks repeats'), s.rstate === 'sending' && await page.locator('#reply-send').isDisabled(), s);
  await wait(1100);
  s = await st(); check(P('failure: “Couldn’t send · Retry”, draft kept'), s.rstate === 'failed' && s.reply === 'Three works' && /Couldn.t send/.test(await page.locator('#sh-rstat').textContent()), s);
  await shot('15-shade-error', 'Reply failed, draft kept, Retry');
  await measure(page, 'reply error text (warm)', '#sh-rstat', 4.5);
  await page.evaluate(() => { const c = document.getElementById('h-fail'); c.checked = false; c.dispatchEvent(new Event('change')); });
  await tap('#reply-retry'); await wait(1300);
  s = await st(); check(P('Retry sends after acknowledgement; draft cleared only then'), s.rstate === 'sent' && s.reply === '', s);
  const n0 = (await st()).items;
  await tap('[data-dismiss="cal"]'); await wait(200);
  check(P('dismiss with Undo'), (await st()).items === n0 - 1 && await page.locator('#sh-undo-btn').isVisible());
  await tap('#sh-undo-btn'); await wait(200);
  check(P('Undo restores the notification'), (await st()).items === n0);
  await tap('[data-reply="m-jamie"]').catch(() => {});
  await R(() => { window.RoseSystem.state().shade.items.forEach(() => {}); });
  await page.evaluate(() => document.getElementById('h-empty').click()); await wait(250);
  check(P('empty state'), /No notifications/.test(await page.locator('#sh-list').textContent()));
  await shot('16-shade-empty', 'Shade: empty');
  await measure(page, 'empty state text', '.sh-empty span', 4.5);
  await page.evaluate(() => document.getElementById('h-empty').click()); await wait(200);
  await page.keyboard.press('Escape'); await wait(300);
  check(P('Esc closes the shade'), !(await st()).shade);
  // ---------- Rose screen + Home contrast ----------
  await D({ type: 'home' }); await wait(700);
  await measure(page, 'Home date', '#v-home .home-date', 4.5);
  await measure(page, 'tray mic glyph (≥3:1 UI)', '#tray-mic svg', 3);
  // ---------- Reduced motion ----------
  await page.evaluate(() => { const c = document.getElementById('h-reduced'); if (c && !c.checked) c.click(); });
  await D({ type: 'openApp', value: 'messages' }); await wait(400); await tap('#f-msg'); await wait(100);
  check(P('reduced motion: keyboard appears without travel'), await R(() => getComputedStyle(document.getElementById('kb')).transitionDuration.split(',').every((d) => parseFloat(d) <= 0.002)));
  check(P('no console errors'), errors.length === 0, errors);
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  await run(browser, 'design', { viewport: { width: 1280, height: 900 } }, false);
  await run(browser, 'iphone13-390', { ...devices['iPhone 13'] }, true);
  await run(browser, 'iphoneSE-320', { ...devices['iPhone SE'] }, true);
  await run(browser, 'short-390x600', { ...devices['iPhone 13'], viewport: { width: 390, height: 600 } }, true);
  // ---------- Regressions (Designer review of be74288d) ----------
  for (const variant of ['closed', 'replaced', 'home', 'control']) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } }); const p = await ctx.newPage();
    const errs = []; p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(FILE); await p.waitForTimeout(800);
    const R = (f, a) => p.evaluate(f, a);
    await R(() => window.__rose.dispatch({ type: 'openApp', value: 'messages' })); await p.waitForTimeout(500);
    await p.locator('#f-msg').click(); await p.waitForTimeout(300);
    await R(() => window.__rose.dispatch({ type: 'openRose' })); await p.waitForTimeout(1500);
    await R(() => { const c = document.getElementById('h-ack'); c.value = 'manual'; c.dispatchEvent(new Event('change')); });
    await p.locator('#rose-return').click(); await p.waitForTimeout(100);
    await R((v) => { document.getElementById('h-ack-ok').click();
      if (v === 'closed' || v === 'replaced') { const c = document.getElementById('h-origin'); c.value = v; c.dispatchEvent(new Event('change')); }
      if (v === 'home') window.__rose.dispatch({ type: 'home' }); }, variant);
    await p.waitForTimeout(600);
    const r = await R(() => ({ view: window.__rose.getState().view, kb: window.RoseSystem.kbOpen(), sheet: !document.getElementById('ret-sheet').hidden }));
    const ok = variant === 'control' ? r.view === 'app' && r.kb : variant === 'home' ? r.view === 'home' && !r.kb && !r.sheet : r.view === 'rose' && !r.kb && r.sheet;
    check('departure race (' + variant + ' during the 200 ms departure): ' + (variant === 'control' ? 'acknowledged return departs' : variant === 'home' ? 'Home wins; no stale return' : 'stays in Rose with the unavailable sheet; no keyboard'), ok && !errs.length, { r, errs });
    await ctx.close();
  }
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } }); const p = await ctx.newPage();
    await p.goto(FILE); await p.waitForTimeout(800);
    const R = (f, a) => p.evaluate(f, a);
    await R(() => window.__rose.dispatch({ type: 'openApp', value: 'messages' })); await p.waitForTimeout(500);
    await R(() => { const t = document.querySelector('#v-app .c-msg'); for (let i = 0; i < 20; i++) { const q = document.createElement('p'); q.className = i % 2 ? 'out' : 'in'; q.textContent = 'Earlier message ' + i; t.prepend(q); } t.scrollTop = t.scrollHeight; });
    await p.waitForTimeout(300);
    const tb = await p.locator('#v-app .c-msg').boundingBox();
    await p.locator('#f-msg').click(); await p.waitForTimeout(60);
    await p.mouse.move(tb.x + tb.width / 2, tb.y + 60); await p.mouse.wheel(0, -400);
    await p.waitForTimeout(700);
    const r = await R(() => { const t = document.querySelector('#v-app .c-msg'); return { gap: t.scrollHeight - t.clientHeight - t.scrollTop, follow: window.RoseSystem.state().follow.msg }; });
    check('a scroll up during the keyboard animation is kept (not re-pinned to the bottom)', r.gap > 100 && r.follow === false, r);
    await ctx.close();
  }
  // Oxblood stays available from the same file (harness).
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } }); const p = await ctx.newPage();
  await p.goto(FILE + '?theme=oxblood'); await p.waitForTimeout(800);
  check('Oxblood remains available from the review harness (?theme=oxblood)', (await p.evaluate(() => document.getElementById('device').dataset.palette)) === undefined && (await p.evaluate(() => document.getElementById('device').dataset.theme)) === 'oxblood');
  await ctx.close(); await browser.close();
  const passed = results.filter((r) => r.ok).length;
  fs.writeFileSync(path.join(OUT, 'glacier-check.json'), JSON.stringify({ when: new Date().toISOString(), commit: git('git rev-parse HEAD'), tree_clean: git('git status --porcelain -- .') === '',
    html_sha256: sha(fs.readFileSync(path.join(__dirname, 'Rose-Glacier.html'))), html_blob: git('git hash-object Rose-Glacier.html'), committed_html_blob: git('git rev-parse HEAD:prototypes/rose-hifi/Rose-Glacier.html'),
    passed, total: results.length, results, contrast, shots }, null, 2));
  console.log(passed + '/' + results.length + ' checks passed');
  if (passed !== results.length) process.exitCode = 1;
})();
