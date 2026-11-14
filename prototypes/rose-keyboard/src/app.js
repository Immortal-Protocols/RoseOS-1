// Rose Keyboard (F10 study) — interface: fields, keyboard, suggestion/edit strip, Rose chin,
// show/hide and gestures. Text state lives in RoseKeys (keyboard.js); this file renders it.
(function () {
  'use strict';
  const K = window.RoseKeys;
  const $ = (id) => document.getElementById(id);
  const screen = $('screen'), kbEl = $('kb'), keysEl = $('keys'), strip = $('strip'), pop = $('pop');
  const ui = { app: 'messages', kb: false, chin: false, muted: false, editHold: false, log: [] };
  const NOTE = 'Garden at ten\nRiverside walk\nBring a book\n';
  const fields = { messages: K.initial(), notes: K.initial({ multiline: true, text: NOTE }) };
  const cur = () => fields[ui.app];
  const fieldEl = () => (ui.app === 'messages' ? $('f-msg') : $('f-note'));
  const now = () => performance.now();
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  let reducedOverride = false;
  const reduced = () => reducedOverride || media.matches;
  const scaleK = () => { const r = screen.getBoundingClientRect(); return r.height / screen.offsetHeight || 1; };
  const esc = (t) => t.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const note = (e) => { ui.log.push(e); if (ui.log.length > 40) ui.log.shift(); };

  // ---------- Icons ----------
  const svg = (body, fill) => '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="' + (fill || 'none') + '" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>';
  const IC = {
    shift: (st) => svg('<path d="M12 4 L20 12 H15.5 V18 H8.5 V12 H4 Z"/>' + (st === 'lock' ? '<path d="M8.5 21 H15.5"/>' : ''), st === 'off' ? 'none' : 'currentColor'),
    bksp: svg('<path d="M8 5 H20 V19 H8 L2.5 12 Z"/><path d="M11 9 L16 14 M16 9 L11 14"/>'),
    send: svg('<path d="M12 19 V5 M6 11 L12 5 L18 11"/>'),
    ret: svg('<path d="M19 6 V12 H6 M10 8 L6 12 L10 16"/>'),
    mic: (m) => svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>' + (m ? '<path d="M4 4 L20 20" stroke="#ff6b7d" stroke-width="2"/>' : '')),
  };

  // ---------- Keyboard build ----------
  let keysKey = '';
  function keyHtml(k, s) {
    const label = { shift: 'Shift', bksp: 'Delete', '?123': 'Symbols', ABC: 'Letters', '=\\<': 'More symbols', space: 'Space', action: ui.app === 'messages' ? 'Send' : 'Return' }[k];
    if (k === 'shift') return '<button class="key fn" data-k="shift" data-state="' + s.shift + '" aria-label="Shift, ' + (s.shift === 'off' ? 'off' : s.shift === 'lock' ? 'caps lock' : 'on') + '">' + IC.shift(s.shift) + '</button>';
    if (k === 'bksp') return '<button class="key fn" data-k="bksp" aria-label="' + label + '">' + IC.bksp + '</button>';
    if (k === 'space') return '<button class="key space" data-k="space" aria-label="Space">space</button>';
    if (k === 'action') return '<button class="key action" data-k="action" aria-label="' + label + '">' + (ui.app === 'messages' ? IC.send : IC.ret) + '</button>';
    if (label) return '<button class="key fn" data-k="' + esc(k) + '" aria-label="' + label + '">' + esc(k) + '</button>';
    const shown = s.page === 'letters' && s.shift !== 'off' ? k.toUpperCase() : k;
    return '<button class="key" data-k="' + esc(k) + '" data-ch="' + esc(shown) + '" aria-label="' + esc(shown) + '">' + esc(shown) + '</button>';
  }
  function buildKeys() {
    const s = cur(), id = s.page + s.shift + ui.app;
    if (id === keysKey) return;
    keysKey = id;
    keysEl.innerHTML = K.LAYOUTS[s.page].map((row, ri) => '<div class="krow' + (ri === 1 && s.page === 'letters' ? ' mid' : '') + '">' + row.map((k) => keyHtml(k, s)).join('') + '</div>').join('');
  }

  // ---------- Strip: suggestions, or edit tools while text is selected ----------
  function renderStrip() {
    const s = cur();
    const editing = s.a !== s.b || ui.editHold;
    let h;
    if (editing) {
      const sel = s.a !== s.b;
      const t = (id, label, on) => '<button class="tool" data-tool="' + id + '"' + (on ? '' : ' disabled') + '>' + label + '</button>';
      // Grouped and content-width: clipboard | selection | history. 44pt hit targets, 32pt visuals.
      h = '<div class="tools">' + t('cut', 'Cut', sel) + t('copy', 'Copy', sel) + t('paste', 'Paste', !!s.clip) + '<span class="sep" aria-hidden="true"></span>' +
        t('all', 'Select all', s.text.length > 0) + '<span class="sep" aria-hidden="true"></span>' + t('undo', 'Undo', s.undo.length > 0) + t('redo', 'Redo', s.redo.length > 0) + '</div>';
      strip.setAttribute('aria-label', 'Editing tools');
    } else {
      const sg = K.suggestions(s);
      // A lone typed word (no alternatives) sits in the middle slot, where the eye expects the best word.
      const slots = sg.length === 1 ? [undefined, sg[0], undefined] : [0, 1, 2].map((i) => sg[i]);
      h = slots.map((x, i) => x ? '<button class="sg ' + x.kind + (i === 1 ? ' best' : '') + '" data-pick="' + esc(x.text) + '" aria-label="' + (x.kind === 'typed' ? 'Keep ' : x.kind === 'correct' ? 'Correct to ' : 'Insert ') + esc(x.text) + '"><span>' + esc(x.text) + '</span></button>' : '<span class="sg" aria-hidden="true"></span>').join('');
      strip.setAttribute('aria-label', 'Suggestions');
    }
    strip.classList.toggle('editing', editing);
    strip.innerHTML = h + '<button class="hide" data-tool="hide" aria-label="Hide keyboard"></button>';
  }

  // ---------- Fields ----------
  function renderField(el, s, focused) {
    let h;
    if (!s.text) h = (focused ? '<span class="caret"></span>' : '') + '<span class="ph" data-base="0">' + esc(el.dataset.placeholder) + '</span>';
    else {
      const a = Math.min(s.a, s.b), b = Math.max(s.a, s.b);
      h = '<span data-base="0">' + esc(s.text.slice(0, a)) + '</span>';
      if (a === b) { if (focused) h += '<span class="caret"></span>'; }
      else h += '<mark data-base="' + a + '">' + esc(s.text.slice(a, b)) + '</mark>';
      h += '<span data-base="' + b + '">' + esc(s.text.slice(b)) + '</span>';
    }
    el.innerHTML = h;
    el.classList.toggle('focused', focused);
    // Keep the caret visible inside the field (manual: never scroll the phone itself).
    const c = el.querySelector('.caret,mark');
    if (focused && c) {
      const top = c.offsetTop, bot = top + c.offsetHeight;
      if (top < el.scrollTop + 6) el.scrollTop = Math.max(0, top - 6);
      else if (bot > el.scrollTop + el.clientHeight - 6) el.scrollTop = bot - el.clientHeight + 6;
    }
  }
  // Text index under a point (caret placement, long-press word selection).
  function indexAt(el, x, y) {
    let node, off;
    if (document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(x, y); if (r) { node = r.startContainer; off = r.startOffset; } }
    else if (document.caretPositionFromPoint) { const p = document.caretPositionFromPoint(x, y); if (p) { node = p.offsetNode; off = p.offset; } }
    const s = cur();
    if (!node || !el.contains(node)) return s.text.length;
    const span = node.nodeType === 3 ? node.parentElement : node;
    if (span.classList.contains('ph')) return 0;
    if (span.dataset.base === undefined) return s.text.length;
    return Math.min(s.text.length, +span.dataset.base + (node.nodeType === 3 ? off : 0));
  }

  // ---------- Render ----------
  let typingTimer = 0;
  function render() {
    const s = cur();
    screen.dataset.kb = ui.kb ? 'on' : 'off';
    screen.dataset.chin = ui.chin ? 'up' : 'down';
    screen.dataset.app = ui.app;
    screen.dataset.muted = String(ui.muted);
    $('chin').hidden = !ui.chin;
    $('handle').setAttribute('aria-expanded', String(ui.chin));
    $('chin-mic').innerHTML = IC.mic(ui.muted);
    // Status bar: the same muted-mic glyph and ruby outline as the chin's control, with a word.
    if (ui.muted && !$('mic-ind').firstChild) $('mic-ind').innerHTML = IC.mic(true) + '<span aria-hidden="true">Mic off</span>';
    $('chin-mic').setAttribute('aria-pressed', String(ui.muted));
    $('chin-mic').setAttribute('aria-label', ui.muted ? 'Unmute microphone' : 'Mute microphone');
    kbEl.setAttribute('aria-hidden', String(!ui.kb));
    kbEl.inert = !ui.kb;
    $('msgs').hidden = ui.app !== 'messages';
    $('notes').hidden = ui.app !== 'notes';
    renderField($('f-msg'), fields.messages, ui.kb && ui.app === 'messages');
    renderField($('f-note'), fields.notes, ui.kb && ui.app === 'notes');
    $('c-send').disabled = !fields.messages.text.trim();
    buildKeys(); renderStrip();
    // Harness
    $('h-messages').setAttribute('aria-pressed', String(ui.app === 'messages'));
    $('h-notes').setAttribute('aria-pressed', String(ui.app === 'notes'));
    $('h-kb').textContent = ui.kb ? 'Hide keyboard' : 'Show keyboard';
    $('h-chin').textContent = ui.chin ? 'Lower chin' : 'Raise chin';
    $('h-mic').setAttribute('aria-pressed', String(ui.muted));
    const sel = s.a === s.b ? 'caret ' + s.a : 'selection ' + Math.min(s.a, s.b) + '–' + Math.max(s.a, s.b);
    $('h-state').innerHTML = 'Page <b>' + s.page + '</b> · shift <b>' + s.shift + '</b> · ' + sel + '<br>Undo ' + s.undo.length + ' · redo ' + s.redo.length + ' · clipboard ' + (s.clip ? '“' + esc(s.clip.slice(0, 18)) + '”' : '—');
    $('h-log').innerHTML = ui.log.slice(-8).reverse().map((e) => '<li>' + esc(e) + '</li>').join('');
    clearTimeout(typingTimer); fieldEl().classList.add('typing'); typingTimer = setTimeout(() => fieldEl().classList.remove('typing'), 500);
    requestAnimationFrame(measure);
    setTimeout(measure, 320);
  }
  // Live geometry readout, in design points (unscaled).
  function measure() {
    const k = scaleK(), sr = screen.getBoundingClientRect();
    const h = (el) => Math.round(el.getBoundingClientRect().height / k);
    const app = $('app'), content = ui.app === 'messages' ? $('thread') : $('f-note');
    const below = !ui.kb ? (ui.chin ? 'chin ' + 58 : 'handle overlay') : ui.chin ? 'raised chin 58' : 'rail 24';
    $('h-layout').innerHTML = 'Screen ' + Math.round(sr.width / k) + '×' + Math.round(sr.height / k) +
      '<br>App window <b>' + h(app) + '</b> (' + Math.round(100 * h(app) / (sr.height / k)) + '%) · content ' + h(content) +
      '<br>Keyboard ' + (ui.kb ? h(kbEl) : 0) + ' · below it: ' + below +
      '<br>Chin ' + (ui.chin ? 'raised (92, mic reachable)' : 'lowered (swipe up)');
  }

  // ---------- Actions ----------
  function toast(t) { const el = $('toast'); el.textContent = t; el.classList.add('on'); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('on'), 1700); }
  function showKb() { if (!ui.kb) { ui.kb = true; note('keyboard shown'); } render(); }
  function hideKb(why) { if (ui.kb) { ui.kb = false; ui.editHold = false; note('keyboard hidden (' + why + ')'); } render(); }
  function setChin(on, why) { on = !!on; if (ui.chin === on) return; ui.chin = on; note('chin ' + (on ? 'raised' : 'lowered') + (why ? ' (' + why + ')' : '')); render(); }
  function setMuted(m) { ui.muted = m; note(m ? 'mic muted (chin)' : 'mic unmuted (chin)'); toast(m ? 'Microphone muted' : 'Microphone on'); render(); }
  function send(text) {
    const p = document.createElement('p'); p.className = 'out'; p.textContent = text; $('thread').appendChild(p);
    note('sent “' + text.slice(0, 20) + '”');
    followBottom = true; thread.scrollTop = thread.scrollHeight; lastTop = thread.scrollTop; // sending returns to the newest message
  }
  // Reading position. The conversation follows the newest message only while it is already at the
  // bottom (or after a send). While reviewing history, the keyboard/chin shortening the window from
  // below leaves scrollTop alone, so the messages being read stay exactly where they are.
  const thread = $('thread');
  // Following stops only when the person scrolls up (scrollTop decreases) — never because the window
  // shrank mid-animation — and resumes when they are back at the bottom.
  let followBottom = true, lastTop = 0;
  const atBottom = () => thread.scrollHeight - thread.clientHeight - thread.scrollTop <= 4;
  thread.addEventListener('scroll', () => {
    if (atBottom()) followBottom = true;
    else if (thread.scrollTop < lastTop - 2) followBottom = false;
    lastTop = thread.scrollTop;
  }, { passive: true });
  const keepBottom = () => { if (followBottom) { thread.scrollTop = thread.scrollHeight; lastTop = thread.scrollTop; } };
  if (window.ResizeObserver) new ResizeObserver(keepBottom).observe(thread);
  requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });
  function doAction() {
    const s = cur();
    if (ui.app === 'messages') { const t = K.enter(s); if (t) send(t); }
    else K.enter(s);
    render();
  }
  function commitKey(k) {
    const s = cur(), t = now();
    ui.editHold = false;
    if (k === 'shift') K.shiftTap(s, t);
    else if (k === '?123') K.setPage(s, 'sym1');
    else if (k === '=\\<') K.setPage(s, 'sym2');
    else if (k === 'ABC') K.setPage(s, 'letters');
    else if (k === 'space') K.space(s, t);
    else if (k === 'action') return doAction();
    else if (k === 'bksp') K.backspace(s);
    else K.type(s, k);
    render();
  }

  // ---------- Key input: slide-to-correct, preview pop, backspace repeat, space-bar cursor ----------
  let press = null;
  const keyAt = (x, y) => { const el = document.elementFromPoint(x, y); return el && el.closest && el.closest('#keys .key'); };
  function showPop(el) {
    if (!el || !el.dataset.ch) { pop.classList.remove('on'); return; }
    const k = scaleK(), kr = kbEl.getBoundingClientRect(), r = el.getBoundingClientRect();
    pop.textContent = el.dataset.ch;
    pop.style.left = ((r.left - kr.left + r.width / 2) / k - 26) + 'px';
    pop.style.top = ((r.top - kr.top) / k - 58) + 'px';
    pop.classList.add('on');
  }
  function setDown(el) { keysEl.querySelectorAll('.key.down').forEach((x) => x.classList.remove('down')); if (el) el.classList.add('down'); }
  function endPress(commit) {
    const p = press; if (!p) return;
    press = null; clearTimeout(p.timer); clearInterval(p.rep);
    pop.classList.remove('on'); setDown(null); kbEl.classList.remove('cursor-mode');
    if (!commit) return;
    const k = p.el && p.el.dataset.k;
    if (p.kind === 'bksp') { if (p.swipe) { K.deleteWord(cur()); render(); } else if (!p.repeated) commitKey('bksp'); return; }
    if (p.kind === 'space') { if (p.mode === 'none') commitKey('space'); else render(); return; }
    if (k) commitKey(k);
  }
  keysEl.addEventListener('pointerdown', (e) => {
    const el = e.target.closest('.key'); if (!el) return;
    e.preventDefault();
    if (press) endPress(true); // two thumbs: the earlier key commits when the next one lands
    const k = el.dataset.k;
    press = { id: e.pointerId, el, x0: e.clientX, y0: e.clientY, lastX: e.clientX, kind: k === 'bksp' ? 'bksp' : k === 'space' ? 'space' : 'key', mode: 'none', repeated: false, swipe: false };
    try { keysEl.setPointerCapture(e.pointerId); } catch (_) {}
    setDown(el); showPop(el);
    if (press.kind === 'bksp') {
      const p = press; let n = 0;
      p.timer = setTimeout(() => {
        p.repeated = true;
        const tick = () => { n++; if (n > 12) K.deleteWord(cur()); else K.backspace(cur()); render(); };
        tick(); p.rep = setInterval(() => { tick(); if (n === 13) { clearInterval(p.rep); p.rep = setInterval(tick, 170); } }, 75);
      }, 420);
    }
    if (press.kind === 'space') {
      const p = press;
      p.timer = setTimeout(() => { if (p.mode === 'none') { p.mode = 'select'; kbEl.classList.add('cursor-mode'); ui.editHold = true; note('space hold: drag to select'); render(); } }, 380);
    }
  });
  keysEl.addEventListener('pointermove', (e) => {
    const p = press; if (!p || e.pointerId !== p.id) return;
    const k = scaleK(), dx = (e.clientX - p.x0) / k;
    if (p.kind === 'bksp') { if (dx < -28 && !p.repeated) { p.swipe = true; clearTimeout(p.timer); } return; }
    if (p.kind === 'space') {
      if (p.mode === 'none' && Math.abs(dx) > 10) { p.mode = 'cursor'; clearTimeout(p.timer); kbEl.classList.add('cursor-mode'); p.lastX = e.clientX; }
      if (p.mode !== 'none') {
        const steps = Math.trunc((e.clientX - p.lastX) / k / 9);
        if (steps) { const s = cur(); for (let i = 0; i < Math.abs(steps); i++) K.move(s, Math.sign(steps), p.mode === 'select'); p.lastX += steps * 9 * k; render(); }
      }
      return;
    }
    const over = keyAt(e.clientX, e.clientY);
    if (over && over !== p.el) { p.el = over; setDown(over); showPop(over); }
  });
  keysEl.addEventListener('pointerup', (e) => { if (press && e.pointerId === press.id) endPress(true); });
  keysEl.addEventListener('pointercancel', () => endPress(false));
  keysEl.addEventListener('contextmenu', (e) => e.preventDefault());

  // ---------- Strip: pick / tools; swipe down hides ----------
  let stripDown = null, stripSwallow = false;
  strip.addEventListener('pointerdown', (e) => { stripDown = { y: e.clientY, x: e.clientX }; stripSwallow = false; });
  window.addEventListener('pointermove', (e) => {
    if (!stripDown) return;
    const k = scaleK(), dy = (e.clientY - stripDown.y) / k;
    if (Math.abs(dy) > 10 || Math.abs(e.clientX - stripDown.x) / k > 10) stripSwallow = true;
  });
  window.addEventListener('pointerup', (e) => {
    if (!stripDown) return;
    const dy = (e.clientY - stripDown.y) / scaleK(); stripDown = null;
    if (dy > 30) hideKb('swipe down');
  });
  strip.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
  strip.addEventListener('click', (e) => {
    if (stripSwallow) { stripSwallow = false; return; }
    const b = e.target.closest('button'); if (!b) return;
    const s = cur();
    if (b.dataset.pick !== undefined) { K.pick(s, b.dataset.pick); render(); return; }
    const t = b.dataset.tool;
    if (t === 'hide') return hideKb('⌄ key');
    ({ cut: K.cut, copy: K.copy, paste: K.paste, all: K.selectAll, undo: K.undo, redo: K.redo })[t](s);
    if (t === 'copy') toast('Copied');
    // Edit tools stay up after any of them (so Undo is one tap away) until the next keystroke.
    ui.editHold = true;
    render();
  });

  // ---------- Fields: tap places the caret and opens the keyboard; long-press selects a word ----------
  let pointerFocus = false;
  function bindField(el, app) {
    let fp = null;
    el.addEventListener('pointerdown', (e) => {
      if (e.button) return;
      // Read the caret index now: once the keyboard opens the field moves, so the point would drift.
      fp = { x: e.clientX, y: e.clientY, moved: false, long: false, at: indexAt(el, e.clientX, e.clientY) };
      pointerFocus = true; setTimeout(() => { pointerFocus = false; }, 0);
      const p = fp;
      p.timer = setTimeout(() => {
        if (p.moved) return;
        p.long = true; if (ui.app !== app) ui.app = app;
        const s = cur(), i = p.at;
        if (s.text) K.selectWord(s, Math.max(0, i)); ui.editHold = true;
        note('long-press: select'); showKb();
      }, 460);
    });
    el.addEventListener('pointermove', (e) => { if (fp && Math.hypot(e.clientX - fp.x, e.clientY - fp.y) > 8) fp.moved = true; });
    el.addEventListener('pointerup', (e) => {
      const p = fp; fp = null; if (!p) return;
      clearTimeout(p.timer);
      if (p.long || p.moved) return;
      ui.app = app;
      const s = cur();
      K.setSel(s, p.at);
      ui.editHold = false;
      el.focus({ preventScroll: true });
      if (!ui.kb) note('field tapped');
      showKb();
    });
    el.addEventListener('pointercancel', () => { if (fp) clearTimeout(fp.timer); fp = null; });
    // Keyboard focus (Tab) opens the keyboard; pointer focus waits for the tap to finish.
    el.addEventListener('focus', () => { if (!pointerFocus && !ui.kb && ui.app === app) { note('field focused (keyboard)'); showKb(); } });
  }
  bindField($('f-msg'), 'messages');
  bindField($('f-note'), 'notes');
  $('c-send').addEventListener('click', () => { const s = fields.messages; const t = K.enter(s); if (t) send(t); render(); });
  // A tap on blank conversation space hides the keyboard (draft kept). Taps on messages, the header,
  // scrolls, drags and long-presses never do.
  let blank = null;
  thread.addEventListener('pointerdown', (e) => { blank = e.target === thread && !e.button ? { x: e.clientX, y: e.clientY, t: now(), top: thread.scrollTop } : null; });
  thread.addEventListener('pointermove', (e) => { if (blank && Math.hypot(e.clientX - blank.x, e.clientY - blank.y) > 8) blank = null; });
  thread.addEventListener('pointerup', (e) => {
    const b = blank; blank = null;
    if (!b || !ui.kb || e.target !== thread || now() - b.t > 450 || Math.abs(thread.scrollTop - b.top) > 2) return;
    hideKb('tap on blank conversation space');
  });
  thread.addEventListener('pointercancel', () => { blank = null; });

  // ---------- Rose chin: swipe up from the bottom edge raises it, with or without the keyboard ----------
  let edge = null, swallowClick = false;
  window.addEventListener('click', (e) => { if (swallowClick) { e.stopPropagation(); e.preventDefault(); swallowClick = false; } }, true);
  function edgeStart(e) {
    if (e.button || e.target.closest('#kb')) return;
    const r = screen.getBoundingClientRect(), k = scaleK(), up = (r.bottom - e.clientY) / k;
    const zone = ui.chin ? 92 : ui.kb ? 24 : 40;
    if (up <= zone && !(ui.chin && e.target.closest('.plate'))) edge = { y: e.clientY, t: now(), moved: false };
    else if (ui.chin && up <= 92) edge = { y: e.clientY, t: now(), moved: false, plate: true };
  }
  screen.addEventListener('pointerdown', edgeStart);
  window.addEventListener('pointermove', (e) => { if (edge && Math.abs(e.clientY - edge.y) / scaleK() > 8) edge.moved = true; });
  window.addEventListener('pointerup', (e) => {
    const d = edge; edge = null; if (!d) return;
    const dy = (e.clientY - d.y) / scaleK(), v = dy / Math.max(1, now() - d.t);
    if (d.moved) { swallowClick = true; setTimeout(() => { swallowClick = false; }, 0); }
    if (!ui.chin) {
      if (dy < -220 || (dy < -120 && v < -0.9)) { hideKb('leaving app'); toast('Home · outside this study (the draft is kept)'); note('long swipe: Home'); }
      else if (dy < -24) setChin(true, 'swipe up');
    } else if (dy > 22 || (dy > 10 && v > 0.35)) setChin(false, 'swipe down');
    else if (dy < -120) { hideKb('leaving app'); setChin(false); toast('Home · outside this study (the draft is kept)'); }
  });
  window.addEventListener('pointercancel', () => { edge = null; });
  // Real touch: an edge swipe must not become a scroll.
  let edgeTouch = false;
  screen.addEventListener('touchstart', (e) => {
    const r = screen.getBoundingClientRect(), k = scaleK(), up = (r.bottom - e.touches[0].clientY) / k;
    edgeTouch = e.touches.length === 1 && (up <= (ui.chin ? 92 : ui.kb ? 24 : 40) || !!e.target.closest('#kb'));
  }, { passive: true });
  screen.addEventListener('touchmove', (e) => { if (edgeTouch && e.cancelable) e.preventDefault(); }, { passive: false });
  $('chin-mic').addEventListener('click', () => setMuted(!ui.muted));
  $('chin-rose').addEventListener('click', () => { toast('Rose opens · the keyboard hides; the draft is kept'); note('chin: Rose'); hideKb('leaving app'); setChin(false); });
  $('chin-switch').addEventListener('click', () => { toast('Switcher opens · the draft is kept'); note('chin: switcher'); hideKb('leaving app'); setChin(false); });
  $('handle').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp') { e.preventDefault(); setChin(true, 'Arrow Up'); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setChin(false, 'Arrow Down'); }
  });

  // ---------- Hardware keyboard (desktop reviewers) ----------
  // Text input goes to the editor only when the editor itself has focus. Focused controls (⌄, a
  // suggestion, an edit tool, the chin's buttons, the harness) keep their native Enter/Space activation.
  document.addEventListener('keydown', (e) => {
    if (e.target.closest && e.target.closest('.harness')) return;
    const s = cur(), mod = e.metaKey || e.ctrlKey;
    if (e.key === 'Escape') { if (ui.chin) setChin(false, 'Esc'); else hideKb('Esc'); return; }
    if (document.activeElement !== fieldEl()) return;
    if (!ui.kb) { if (e.key.length === 1 && !mod) showKb(); else return; }
    if (mod) {
      const m = { z: e.shiftKey ? K.redo : K.undo, y: K.redo, a: K.selectAll, c: K.copy, x: K.cut, v: K.paste }[e.key.toLowerCase()];
      if (m) { e.preventDefault(); m(s); render(); }
      return;
    }
    if (e.key === 'Backspace') { e.preventDefault(); if (e.altKey) K.deleteWord(s); else K.backspace(s); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); doAction(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); K.move(s, e.key === 'ArrowLeft' ? -1 : 1, e.shiftKey); render(); }
    else if (e.key === ' ') { e.preventDefault(); K.space(s, now()); render(); }
    else if (e.key.length === 1) { e.preventDefault(); if (s.shift === 'once' && e.key !== e.key.toLowerCase()) s.shift = 'off'; K.type(s, e.key); render(); }
  });

  // ---------- Harness ----------
  const switchApp = (a) => { if (ui.app !== a) { ui.app = a; ui.editHold = false; keysKey = ''; note('app: ' + a); } render(); };
  $('h-messages').onclick = () => switchApp('messages');
  $('h-notes').onclick = () => switchApp('notes');
  $('h-kb').onclick = () => (ui.kb ? hideKb('harness') : showKb());
  $('h-chin').onclick = () => setChin(!ui.chin, 'harness');
  $('h-mic').onclick = () => setMuted(!ui.muted);
  $('h-reset').onclick = () => {
    Object.assign(ui, { app: 'messages', kb: false, chin: false, muted: false, editHold: false, log: [] });
    fields.messages = K.initial(); fields.notes = K.initial({ multiline: true, text: NOTE });
    $('thread').querySelectorAll('p.out').forEach((p, i) => { if (i > 0) p.remove(); });
    keysKey = ''; render();
  };
  $('h-reduced').onchange = (e) => { reducedOverride = e.target.checked; document.documentElement.classList.toggle('static-motion', reduced()); };
  document.documentElement.classList.toggle('static-motion', reduced());
  $('h-toggle').addEventListener('click', () => {
    const open = !document.documentElement.classList.contains('harness-open');
    document.documentElement.classList.toggle('harness-open', open);
    $('h-toggle').setAttribute('aria-expanded', String(open));
  });

  // ---------- Fit: desktop stage, or phone mode (fills a real phone) ----------
  const phoneParam = new URLSearchParams(location.search).get('phone');
  const phoneMode = () => phoneParam === '1' || (phoneParam !== '0' && (window.innerWidth <= 540 || matchMedia('(pointer: coarse) and (max-width: 900px)').matches));
  function fit() {
    const root = document.documentElement, phone = phoneMode();
    root.classList.toggle('phone-mode', phone);
    let k;
    if (phone) {
      const vv = window.visualViewport, vw = vv ? vv.width : innerWidth, vh = vv ? vv.height : innerHeight;
      const sh = Math.round(Math.max(640, Math.min(900, vh * 384 / vw)));
      root.style.setProperty('--screen-h', sh + 'px');
      k = Math.min(vw / 384, vh / sh);
    } else {
      root.style.removeProperty('--screen-h');
      const st = $('stage');
      k = Math.min(1, (st.clientHeight - 32) / 856, (st.clientWidth - 32) / 408);
    }
    $('fit').style.setProperty('--fit', Math.max(0.3, k).toFixed(4));
    requestAnimationFrame(measure);
  }
  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', () => setTimeout(fit, 250));
  fit(); render();
  window.__kb = { ui, fields, K, render, measure };
})();
