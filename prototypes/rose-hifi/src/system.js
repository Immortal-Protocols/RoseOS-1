// Rose HiFi — system surfaces shared by Oxblood and Glacier (GLACIER-1E-1110):
//  • the F10 keyboard (text model: prototypes/rose-keyboard/src/keyboard.js, read-only) for the
//    Messages composer and notification replies;
//  • the F09 notification shade (grouped/expanded/empty, quick settings, inline reply with honest
//    sending/sent/failure, dismiss with Undo, Keep draft / Discard);
//  • Nathan's approved V3 return control: compact, named, bottom-left of Rose content above the chin.
// Prototype only. The microphone state shown here mirrors the prototype's universal mic.
(function () {
  'use strict';
  const K = window.RoseKeys, I = window.RoseIcons;
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const now = () => performance.now();
  let R = null, screen = null, device = null;          // window.__rose, #screen, #device
  const scaleK = () => { const r = screen.getBoundingClientRect(); return r.height / screen.offsetHeight || 1; };
  const reduced = () => screen.classList.contains('static-motion');
  const log = [];
  const note = (e) => { log.push(e); if (log.length > 40) log.shift(); const el = $('h-syslog'); if (el) el.innerHTML = log.slice(-8).reverse().map((x) => '<li>' + esc(x) + '</li>').join(''); };

  // =====================================================================================
  // Keyboard
  // =====================================================================================
  const kb = { open: false, target: null, editHold: false, keysKey: '' };
  const fields = { msg: K.initial(), reply: K.initial() };
  const fieldEl = (t) => (t === 'reply' ? $('f-reply') : $('f-msg'));
  const svg = (body, fill) => '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="' + (fill || 'none') + '" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>';
  const IC = {
    shift: (st) => svg('<path d="M12 4 L20 12 H15.5 V18 H8.5 V12 H4 Z"/>' + (st === 'lock' ? '<path d="M8.5 21 H15.5"/>' : ''), st === 'off' ? 'none' : 'currentColor'),
    bksp: svg('<path d="M8 5 H20 V19 H8 L2.5 12 Z"/><path d="M11 9 L16 14 M16 9 L11 14"/>'),
    send: svg('<path d="M12 19 V5 M6 11 L12 5 L18 11"/>'),
    mic: (m) => svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>' + (m ? '<path d="M4 4 L20 20" stroke-width="2"/>' : '')),
    chev: (up) => svg(up ? '<path d="M6 15 L12 9 L18 15"/>' : '<path d="M6 9 L12 15 L18 9"/>'),
    back: svg('<path d="M14 6 L8 12 L14 18"/>'),
    x: svg('<path d="M7 7 L17 17 M17 7 L7 17"/>'),
    person: svg('<circle cx="12" cy="9" r="3.5"/><path d="M5 19c1.4-3.2 4-4.6 7-4.6s5.6 1.4 7 4.6"/>'),
    msg: svg('<path d="M5 6h14v9H9l-4 3z"/>'),
    cal: svg('<rect x="4.5" y="5.5" width="15" height="14" rx="2"/><path d="M4.5 10h15M9 3.5v4M15 3.5v4"/>'),
    wifi: svg('<path d="M3.5 9.5a12 12 0 0 1 17 0M6.5 12.5a7.5 7.5 0 0 1 11 0M9.5 15.5a3 3 0 0 1 5 0"/><circle cx="12" cy="18.5" r=".9" fill="currentColor"/>'),
    bt: svg('<path d="M7 7l10 10-5 4V3l5 4L7 17"/>'),
    torch: svg('<path d="M8 3h8v4l-2 3v11h-4V10L8 7z"/><path d="M12 13v2"/>'),
    moon: svg('<path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>'),
    sun: svg('<circle cx="12" cy="12" r="3.5"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>'),
  };

  function keyHtml(k, s) {
    const action = kb.target === 'reply' || kb.target === 'msg';
    const label = { shift: 'Shift', bksp: 'Delete', '?123': 'Symbols', ABC: 'Letters', '=\\<': 'More symbols', space: 'Space', action: 'Send' }[k];
    if (k === 'shift') return '<button class="key fn" data-k="shift" data-state="' + s.shift + '" aria-label="Shift, ' + (s.shift === 'off' ? 'off' : s.shift === 'lock' ? 'caps lock' : 'on') + '">' + IC.shift(s.shift) + '</button>';
    if (k === 'bksp') return '<button class="key fn" data-k="bksp" aria-label="Delete">' + IC.bksp + '</button>';
    if (k === 'space') return '<button class="key space" data-k="space" aria-label="Space">space</button>';
    if (k === 'action') return '<button class="key action" data-k="action" aria-label="' + (action ? 'Send' : 'Return') + '">' + IC.send + '</button>';
    if (label) return '<button class="key fn" data-k="' + esc(k) + '" aria-label="' + label + '">' + esc(k) + '</button>';
    const shown = s.page === 'letters' && s.shift !== 'off' ? k.toUpperCase() : k;
    return '<button class="key" data-k="' + esc(k) + '" data-ch="' + esc(shown) + '" aria-label="' + esc(shown) + '">' + esc(shown) + '</button>';
  }
  function buildKeys() {
    const s = fields[kb.target || 'msg'], id = s.page + s.shift + kb.target;
    if (id === kb.keysKey) return;
    kb.keysKey = id;
    $('keys').innerHTML = K.LAYOUTS[s.page].map((row, ri) => '<div class="krow' + (ri === 1 && s.page === 'letters' ? ' mid' : '') + '">' + row.map((k) => keyHtml(k, s)).join('') + '</div>').join('');
  }
  function renderStrip() {
    const s = fields[kb.target || 'msg'], strip = $('strip');
    const editing = s.a !== s.b || kb.editHold;
    let h;
    if (editing) {
      const sel = s.a !== s.b;
      const t = (id, label, on) => '<button class="tool" data-tool="' + id + '"' + (on ? '' : ' disabled') + '>' + label + '</button>';
      h = '<div class="tools">' + t('cut', 'Cut', sel) + t('copy', 'Copy', sel) + t('paste', 'Paste', !!s.clip) + '<span class="sep" aria-hidden="true"></span>' +
        t('all', 'Select all', s.text.length > 0) + '<span class="sep" aria-hidden="true"></span>' + t('undo', 'Undo', s.undo.length > 0) + t('redo', 'Redo', s.redo.length > 0) + '</div>';
      strip.setAttribute('aria-label', 'Editing tools');
    } else {
      const sg = K.suggestions(s);
      const slots = sg.length === 1 ? [undefined, sg[0], undefined] : [0, 1, 2].map((i) => sg[i]);
      h = slots.map((x, i) => x ? '<button class="sg ' + x.kind + (i === 1 ? ' best' : '') + '" data-pick="' + esc(x.text) + '" aria-label="' + (x.kind === 'typed' ? 'Keep ' : x.kind === 'correct' ? 'Correct to ' : 'Insert ') + esc(x.text) + '"><span>' + esc(x.text) + '</span></button>' : '<span class="sg" aria-hidden="true"></span>').join('');
      strip.setAttribute('aria-label', 'Suggestions');
    }
    strip.classList.toggle('editing', editing);
    strip.innerHTML = h + '<button class="hide" data-tool="hide" aria-label="Hide keyboard">' + IC.chev(false) + '</button>';
  }
  function renderField(el, s, focused) {
    if (!el) return;
    let h;
    if (!s.text) h = (focused ? '<span class="caret"></span>' : '') + '<span class="ph" data-base="0">' + esc(el.dataset.placeholder || '') + '</span>';
    else {
      const a = Math.min(s.a, s.b), b = Math.max(s.a, s.b);
      h = '<span data-base="0">' + esc(s.text.slice(0, a)) + '</span>';
      if (a === b) { if (focused) h += '<span class="caret"></span>'; }
      else h += '<mark data-base="' + a + '">' + esc(s.text.slice(a, b)) + '</mark>';
      h += '<span data-base="' + b + '">' + esc(s.text.slice(b)) + '</span>';
    }
    el.innerHTML = h;
    el.classList.toggle('focused', focused);
    const c = el.querySelector('.caret,mark');
    if (focused && c) {
      const top = c.offsetTop, bot = top + c.offsetHeight;
      if (top < el.scrollTop + 4) el.scrollTop = Math.max(0, top - 4);
      else if (bot > el.scrollTop + el.clientHeight - 4) el.scrollTop = bot - el.clientHeight + 4;
    }
  }
  function indexAt(el, x, y, s) {
    let node, off;
    if (document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(x, y); if (r) { node = r.startContainer; off = r.startOffset; } }
    else if (document.caretPositionFromPoint) { const p = document.caretPositionFromPoint(x, y); if (p) { node = p.offsetNode; off = p.offset; } }
    if (!node || !el.contains(node)) return s.text.length;
    const span = node.nodeType === 3 ? node.parentElement : node;
    if (span.classList.contains('ph')) return 0;
    if (span.dataset.base === undefined) return s.text.length;
    return Math.min(s.text.length, +span.dataset.base + (node.nodeType === 3 ? off : 0));
  }
  function renderKb() {
    screen.dataset.kb = kb.open ? 'on' : 'off';
    const k = $('kb'); k.setAttribute('aria-hidden', String(!kb.open)); k.inert = !kb.open;
    renderField($('f-msg'), fields.msg, kb.open && kb.target === 'msg');
    renderField($('f-reply'), fields.reply, kb.open && kb.target === 'reply');
    const send = $('ab-send'); if (send) send.disabled = !fields.msg.text.trim();
    const rs = $('reply-send'); if (rs) rs.disabled = !fields.reply.text.trim() || shade.reply.state === 'sending';
    if (kb.open) { buildKeys(); renderStrip(); }
    const st = $('h-sysstate');
    if (st) { const s = fields[kb.target || 'msg']; st.innerHTML = 'Keyboard <b>' + (kb.open ? 'shown · ' + kb.target : 'hidden') + '</b> · page ' + s.page + ' · shift ' + s.shift + '<br>Shade <b>' + (shade.open ? 'open' : 'closed') + '</b> · reply ' + shade.reply.state + '<br>Return <b>' + (ret.origin ? ret.state + ' → ' + originName() : 'none') + '</b>'; }
  }
  function showKb(target) {
    if (!kb.open || kb.target !== target) { kb.open = true; kb.target = target; kb.keysKey = ''; note('keyboard shown (' + target + ')'); }
    renderKb(); followAfterLayout();
  }
  function hideKb(why) {
    if (!kb.open) return;
    kb.open = false; kb.editHold = false; note('keyboard hidden (' + why + ')');
    renderKb(); followAfterLayout();
  }
  function cur() { return fields[kb.target || 'msg']; }
  function sendMsg() {
    const t = K.enter(fields.msg); if (!t) return;
    const thread = msgThread(); if (thread) { const p = document.createElement('p'); p.className = 'out'; p.textContent = t; thread.appendChild(p); follow.msg = true; thread.scrollTop = thread.scrollHeight; }
    note('sent “' + t.slice(0, 20) + '”');
  }
  function doAction() {
    if (kb.target === 'msg') sendMsg();
    else if (kb.target === 'reply') { replySend(); return; }
    renderKb();
  }
  function commitKey(k) {
    const s = cur(), t = now();
    kb.editHold = false;
    if (k === 'shift') K.shiftTap(s, t);
    else if (k === '?123') K.setPage(s, 'sym1');
    else if (k === '=\\<') K.setPage(s, 'sym2');
    else if (k === 'ABC') K.setPage(s, 'letters');
    else if (k === 'space') K.space(s, t);
    else if (k === 'action') return doAction();
    else if (k === 'bksp') K.backspace(s);
    else K.type(s, k);
    if (kb.target === 'reply' && shade.reply.state === 'failed') shade.reply.state = 'draft';
    renderKb(); if (kb.target === 'reply') renderShadeReplyStatus();
  }
  // Key input: preview pop, slide, rollover, backspace repeat / swipe-delete, space-bar cursor.
  let press = null;
  function setDown(el) { $('keys').querySelectorAll('.key.down').forEach((x) => x.classList.remove('down')); if (el) el.classList.add('down'); }
  function showPop(el) {
    const pop = $('kpop');
    if (!el || !el.dataset.ch) { pop.classList.remove('on'); return; }
    const k = scaleK(), kr = $('kb').getBoundingClientRect(), r = el.getBoundingClientRect();
    pop.textContent = el.dataset.ch;
    pop.style.left = ((r.left - kr.left + r.width / 2) / k - 26) + 'px';
    pop.style.top = ((r.top - kr.top) / k - 58) + 'px';
    pop.classList.add('on');
  }
  function endPress(commit) {
    const p = press; if (!p) return;
    press = null; clearTimeout(p.timer); clearInterval(p.rep);
    $('kpop').classList.remove('on'); setDown(null); $('kb').classList.remove('cursor-mode');
    if (!commit) return;
    if (p.kind === 'bksp') { if (p.swipe) { K.deleteWord(cur()); renderKb(); } else if (!p.repeated) commitKey('bksp'); return; }
    if (p.kind === 'space') { if (p.mode === 'none') commitKey('space'); else renderKb(); return; }
    if (p.el && p.el.dataset.k) commitKey(p.el.dataset.k);
  }
  function bindKeys() {
    const keysEl = $('keys');
    keysEl.addEventListener('pointerdown', (e) => {
      const el = e.target.closest('.key'); if (!el) return;
      e.preventDefault();
      if (press) endPress(true);
      const k = el.dataset.k;
      press = { id: e.pointerId, el, x0: e.clientX, lastX: e.clientX, kind: k === 'bksp' ? 'bksp' : k === 'space' ? 'space' : 'key', mode: 'none', repeated: false, swipe: false };
      try { keysEl.setPointerCapture(e.pointerId); } catch (_) {}
      setDown(el); showPop(el);
      const p = press;
      if (p.kind === 'bksp') {
        let n = 0;
        p.timer = setTimeout(() => {
          p.repeated = true;
          const tick = () => { n++; if (n > 12) K.deleteWord(cur()); else K.backspace(cur()); renderKb(); };
          tick(); p.rep = setInterval(() => { tick(); if (n === 13) { clearInterval(p.rep); p.rep = setInterval(tick, 170); } }, 75);
        }, 420);
      }
      if (p.kind === 'space') p.timer = setTimeout(() => { if (p.mode === 'none') { p.mode = 'select'; $('kb').classList.add('cursor-mode'); kb.editHold = true; renderKb(); } }, 380);
    });
    keysEl.addEventListener('pointermove', (e) => {
      const p = press; if (!p || e.pointerId !== p.id) return;
      const k = scaleK(), dx = (e.clientX - p.x0) / k;
      if (p.kind === 'bksp') { if (dx < -28 && !p.repeated) { p.swipe = true; clearTimeout(p.timer); } return; }
      if (p.kind === 'space') {
        if (p.mode === 'none' && Math.abs(dx) > 10) { p.mode = 'cursor'; clearTimeout(p.timer); $('kb').classList.add('cursor-mode'); p.lastX = e.clientX; }
        if (p.mode !== 'none') {
          const steps = Math.trunc((e.clientX - p.lastX) / k / 9);
          if (steps) { const s = cur(); for (let i = 0; i < Math.abs(steps); i++) K.move(s, Math.sign(steps), p.mode === 'select'); p.lastX += steps * 9 * k; renderKb(); }
        }
        return;
      }
      const el = document.elementFromPoint(e.clientX, e.clientY), over = el && el.closest && el.closest('#keys .key');
      if (over && over !== p.el) { p.el = over; setDown(over); showPop(over); }
    });
    keysEl.addEventListener('pointerup', (e) => { if (press && e.pointerId === press.id) endPress(true); });
    keysEl.addEventListener('pointercancel', () => endPress(false));
    keysEl.addEventListener('contextmenu', (e) => e.preventDefault());
    // Strip: picks/tools; swipe down hides; never steals focus from the editor.
    const strip = $('strip');
    let sd = null, swallow = false;
    strip.addEventListener('pointerdown', (e) => { sd = { x: e.clientX, y: e.clientY }; swallow = false; });
    window.addEventListener('pointermove', (e) => { if (sd && (Math.abs(e.clientY - sd.y) > 10 || Math.abs(e.clientX - sd.x) > 10)) swallow = true; });
    window.addEventListener('pointerup', (e) => { if (!sd) return; const dy = (e.clientY - sd.y) / scaleK(); sd = null; if (dy > 30) hideKb('swipe down'); });
    strip.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
    strip.addEventListener('click', (e) => {
      if (swallow) { swallow = false; return; }
      const b = e.target.closest('button'); if (!b) return;
      const s = cur();
      if (b.dataset.pick !== undefined) { K.pick(s, b.dataset.pick); renderKb(); return; }
      const t = b.dataset.tool;
      if (t === 'hide') return hideKb('⌄ key');
      ({ cut: K.cut, copy: K.copy, paste: K.paste, all: K.selectAll, undo: K.undo, redo: K.redo })[t](s);
      kb.editHold = true; renderKb();
    });
  }
  // Fields: tap places the caret (index read at pointer-down) and opens the keyboard; long-press selects.
  let pointerFocus = false;
  function bindField(el, target) {
    if (!el || el.dataset.bound) return;
    el.dataset.bound = '1';
    let fp = null;
    el.addEventListener('pointerdown', (e) => {
      if (e.button) return;
      if (target === 'reply' && shade.reply.state === 'sending') return;
      fp = { x: e.clientX, y: e.clientY, moved: false, long: false, at: indexAt(el, e.clientX, e.clientY, fields[target]) };
      pointerFocus = true; setTimeout(() => { pointerFocus = false; }, 0);
      const p = fp;
      p.timer = setTimeout(() => { if (p.moved) return; p.long = true; const s = fields[target]; if (s.text) K.selectWord(s, Math.max(0, p.at)); kb.editHold = true; showKb(target); }, 460);
    });
    el.addEventListener('pointermove', (e) => { if (fp && Math.hypot(e.clientX - fp.x, e.clientY - fp.y) > 8) fp.moved = true; });
    el.addEventListener('pointerup', () => {
      const p = fp; fp = null; if (!p) return;
      clearTimeout(p.timer);
      if (p.long || p.moved) return;
      K.setSel(fields[target], p.at); kb.editHold = false;
      el.focus({ preventScroll: true });
      showKb(target);
    });
    el.addEventListener('pointercancel', () => { if (fp) clearTimeout(fp.timer); fp = null; });
    el.addEventListener('focus', () => { if (!pointerFocus && !(kb.open && kb.target === target)) showKb(target); });
  }
  // Hardware keys only while an editor itself has focus; focused controls keep Enter/Space.
  function onKey(e) {
    if (e.target.closest && e.target.closest('.harness')) return;
    const a = document.activeElement, target = a && a.id === 'f-msg' ? 'msg' : a && a.id === 'f-reply' ? 'reply' : null;
    if (!target || e.key === 'Escape') return;
    const s = fields[target], mod = e.metaKey || e.ctrlKey;
    if (!kb.open || kb.target !== target) { if (e.key.length === 1 && !mod) showKb(target); else return; }
    if (mod) {
      const m = { z: e.shiftKey ? K.redo : K.undo, y: K.redo, a: K.selectAll, c: K.copy, x: K.cut, v: K.paste }[e.key.toLowerCase()];
      if (m) { e.preventDefault(); m(s); renderKb(); }
      return;
    }
    if (e.key === 'Backspace') { e.preventDefault(); if (e.altKey) K.deleteWord(s); else K.backspace(s); renderKb(); }
    else if (e.key === 'Enter') { e.preventDefault(); doAction(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); K.move(s, e.key === 'ArrowLeft' ? -1 : 1, e.shiftKey); renderKb(); }
    else if (e.key === ' ') { e.preventDefault(); K.space(s, now()); renderKb(); }
    else if (e.key.length === 1) { e.preventDefault(); if (s.shift === 'once' && e.key !== e.key.toLowerCase()) s.shift = 'off'; K.type(s, e.key); renderKb(); }
  }

  // Messages reading position: follow the newest message only at the bottom or after a send.
  const follow = { msg: true, last: 0 };
  const msgThread = () => document.querySelector('#v-app .c-msg');
  function bindThread() {
    const t = msgThread(); if (!t || t.dataset.bound) return;
    t.dataset.bound = '1'; t.tabIndex = -1;
    const atBottom = () => t.scrollHeight - t.clientHeight - t.scrollTop <= 4;
    t.addEventListener('scroll', () => { if (atBottom()) follow.msg = true; else if (t.scrollTop < follow.last - 2) follow.msg = false; follow.last = t.scrollTop; }, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(() => { if (follow.msg) { t.scrollTop = t.scrollHeight; follow.last = t.scrollTop; } }).observe(t);
    // A tap on blank conversation space hides the keyboard; messages, scrolls and drags never do.
    let blank = null;
    t.addEventListener('pointerdown', (e) => { blank = e.target === t && !e.button ? { x: e.clientX, y: e.clientY, t: now(), top: t.scrollTop } : null; });
    t.addEventListener('pointermove', (e) => { if (blank && Math.hypot(e.clientX - blank.x, e.clientY - blank.y) > 8) blank = null; });
    t.addEventListener('pointerup', (e) => { const b = blank; blank = null; if (b && kb.open && e.target === t && now() - b.t < 450 && Math.abs(t.scrollTop - b.top) <= 2) hideKb('tap on blank conversation space'); });
    requestAnimationFrame(() => { t.scrollTop = t.scrollHeight; follow.last = t.scrollTop; });
  }
  function followAfterLayout() { const t = msgThread(); if (t && follow.msg) requestAnimationFrame(() => { t.scrollTop = t.scrollHeight; follow.last = t.scrollTop; }); }

  // =====================================================================================
  // Notification shade (F09 v1)
  // =====================================================================================
  const shade = { open: false, expanded: false, items: null, undo: null, reply: { id: null, state: 'idle' }, failNext: false, qs: { wifi: true, bt: false, torch: false, dnd: false }, bright: 62 };
  const ITEMS = () => [
    { id: 'rose-review', kind: 'rose', title: 'Rose', time: 'now', head: 'Needs your review', body: 'Draft reply to Jamie is ready', action: 'Review', dismissible: true },
    { id: 'msgs', kind: 'group', title: 'Messages', time: 'now', items: [
      { id: 'm-jamie', from: 'Jamie', text: 'Can we meet at three?', time: 'now', reply: true },
      { id: 'm-sam', from: 'Sam', text: 'Shared a photo', time: '5m ago' }], dismissible: true },
    { id: 'cal', kind: 'cal', title: 'Calendar', time: 'in 20m', head: 'Garden walk', body: 'In 20 minutes · Botanic Garden', action: 'Open', dismissible: true },
    { id: 'rose-done', kind: 'rose', title: 'Rose', time: '2m ago', head: 'Finished', body: 'Your Saturday plan is ready', action: 'View result', dismissible: true },
  ];
  shade.items = ITEMS();
  function shadeHtml() {
    const muted = R.getState().muted;
    const tile = (id, ic, label) => '<button class="qs" data-qs="' + id + '" aria-pressed="' + shade.qs[id] + '" aria-label="' + label + '">' + IC[ic] + '</button>';
    let h = '<div class="sh-top"><h2 id="sh-title">Notifications</h2><span class="sh-input" data-muted="' + muted + '" role="status">' + IC.mic(muted) + '<span>' + (muted ? 'Input muted' : 'Input enabled') + '</span></span></div>' +
      '<div class="sh-qs" role="group" aria-label="Quick settings">' + tile('wifi', 'wifi', 'Wi-Fi') + tile('bt', 'bt', 'Bluetooth') + tile('torch', 'torch', 'Flashlight') + tile('dnd', 'moon', 'Do not disturb') + '</div>' +
      '<label class="sh-bright">' + IC.sun + '<input type="range" id="sh-bright" min="0" max="100" value="' + shade.bright + '" aria-label="Brightness"></label>' +
      '<div class="sh-list" id="sh-list">';
    if (!shade.items.length) h += '<div class="sh-empty"><b>No notifications</b><span>You’re all caught up.</span></div>';
    for (const n of shade.items) {
      const x = n.dismissible ? '<button class="sh-x" data-dismiss="' + n.id + '" aria-label="Dismiss ' + esc(n.title) + '">' + IC.x + '</button>' : '';
      if (n.kind === 'group') {
        const open = shade.expanded;
        h += '<article class="sh-card group" data-id="' + n.id + '" data-open="' + open + '"><header><span class="sh-ico">' + IC.msg + '</span><b>' + esc(n.title) + ' · ' + n.items.length + '</b>' +
          '<button class="sh-chev" data-expand aria-expanded="' + open + '" aria-label="' + (open ? 'Collapse' : 'Expand') + ' Messages">' + IC.chev(open) + '</button>' + x + '</header>';
        if (!open) h += '<button class="sh-open sh-sum" data-open-app="messages">' + n.items.map((m) => '<span><b>' + esc(m.from) + '</b> ' + esc(m.text) + '</span>').join('') + '</button>';
        else h += n.items.map((m) => '<div class="sh-msg" data-mid="' + m.id + '"><span class="sh-av">' + IC.person + '</span><button class="sh-open" data-open-app="messages"><b>' + esc(m.from) + '</b><span>' + esc(m.text) + '</span></button><time>' + esc(m.time) + '</time>' +
          (m.reply ? (shade.reply.id === m.id ? replyHtml() : '<div class="sh-acts"><button class="sbtn sh-btn primary" data-reply="' + m.id + '">Reply</button><button class="sbtn sh-btn" data-markread="' + m.id + '">Mark read</button></div>') : '') + '</div>').join('');
        h += '</article>';
      } else {
        h += '<article class="sh-card" data-id="' + n.id + '"><header>' + (n.kind === 'rose' ? '<span class="sh-pearl" aria-hidden="true"></span>' : '<span class="sh-ico">' + IC.cal + '</span>') + '<b>' + esc(n.title) + '</b><time>' + esc(n.time) + '</time>' + x + '</header>' +
          '<button class="sh-open" data-act="' + n.id + '"><b>' + esc(n.head) + '</b><span>' + esc(n.body) + '</span></button>' +
          '<div class="sh-acts"><button class="sbtn sh-btn primary" data-act="' + n.id + '">' + esc(n.action) + '</button></div></article>';
      }
    }
    h += '</div><div class="sh-undo" id="sh-undo" role="status" aria-live="polite"></div><div class="sh-confirm" id="sh-confirm" hidden></div><button class="sh-handle" id="sh-handle" aria-label="Close notifications"><i></i></button>';
    return h;
  }
  function replyHtml() {
    return '<div class="sh-reply"><div class="sh-reply-row"><div class="field" id="f-reply" role="textbox" aria-label="Reply to Jamie" tabindex="0" data-placeholder="Reply to Jamie"></div>' +
      '<button class="sbtn sh-btn primary" id="reply-send" disabled>Send</button></div><p class="sh-rstat" id="sh-rstat" role="status" aria-live="polite"></p></div>';
  }
  function renderShade() {
    const el = $('shade');
    el.hidden = !shade.open;
    screen.dataset.shade = shade.open ? 'open' : 'closed';
    if (!shade.open) return;
    const list = $('sh-list'), top = list ? list.scrollTop : 0;
    el.innerHTML = shadeHtml();
    $('sh-list').scrollTop = top;
    if ($('f-reply')) bindField($('f-reply'), 'reply');
    renderKb(); renderShadeReplyStatus();
    if (shade.undo) showUndo(shade.undo.text);
  }
  function renderShadeReplyStatus() {
    const st = $('sh-rstat'); if (!st) return;
    const r = shade.reply.state;
    st.dataset.state = r;
    st.innerHTML = r === 'sending' ? 'Sending…' : r === 'failed' ? 'Couldn’t send · <button class="sh-link" id="reply-retry">Retry</button>' : '';
    const rs = $('reply-send'); if (rs) { rs.disabled = !fields.reply.text.trim() || r === 'sending'; rs.textContent = r === 'sending' ? 'Sending…' : 'Send'; }
  }
  // The shade covers the app: an app keyboard tucks away while it is open and comes back on close.
  function openShade(why) { if (shade.open) return; if (kb.open && kb.target === 'msg') { kb.stash = true; hideKb('shade opened'); } shade.open = true; note('shade opened (' + why + ')'); renderShade(); setTimeout(() => { const t = $('sh-title'); if (t) t.setAttribute('tabindex', '-1'); }, 0); }
  function closeShade(why) {
    if (!shade.open) return;
    if (kb.open && kb.target === 'reply') hideKb('shade closed');
    shade.open = false; note('shade closed (' + why + ')'); renderShade();
    if (kb.stash) { kb.stash = false; if (R.getState().view === 'app' && $('f-msg')) showKb('msg'); }
  }
  function replySend() {
    const text = fields.reply.text.trim(); if (!text || shade.reply.state === 'sending') return;
    shade.reply.state = 'sending'; renderShadeReplyStatus(); note('reply sending');
    const fail = shade.failNext;
    setTimeout(() => {
      if (fail) { shade.reply.state = 'failed'; note('reply failed (draft kept)'); renderShadeReplyStatus(); return; }
      // Actual acknowledgement: only now is the reply sent.
      shade.reply = { id: null, state: 'sent' }; fields.reply = K.initial();
      const g = shade.items.find((n) => n.id === 'msgs');
      if (g) { const m = g.items.find((x) => x.id === 'm-jamie'); if (m) { m.text = 'You: ' + text; m.reply = false; m.time = 'just now'; } }
      note('reply sent'); hideKb('reply sent'); renderShade(); showUndo('Reply sent');
    }, reduced() ? 60 : 900);
  }
  function showUndo(text, undoable) {
    const u = $('sh-undo'); if (!u) return;
    u.innerHTML = esc(text) + (undoable ? ' · <button class="sh-link" id="sh-undo-btn">Undo</button>' : '');
    u.classList.add('on'); clearTimeout(u._t); u._t = setTimeout(() => { u.classList.remove('on'); if (shade.undo && shade.undo.text === text) shade.undo = null; }, 4000);
  }
  function dismiss(id, force) {
    const idx = shade.items.findIndex((n) => n.id === id); if (idx < 0) return;
    const n = shade.items[idx];
    const dirty = n.kind === 'group' && shade.reply.id && fields.reply.text.trim();
    if (dirty && !force) {
      const c = $('sh-confirm'); c.hidden = false;
      c.innerHTML = '<p>Discard your reply to Jamie?</p><div><button class="sbtn sh-btn" id="sh-keep">Keep draft</button><button class="sbtn sh-btn danger" id="sh-discard">Discard</button></div>';
      $('sh-keep').focus({ preventScroll: true });
      return;
    }
    if (n.kind === 'group' && shade.reply.id) { if (kb.open && kb.target === 'reply') hideKb('dismissed'); shade.reply = { id: null, state: 'idle' }; fields.reply = K.initial(); }
    shade.items.splice(idx, 1);
    shade.undo = { item: n, idx, text: n.title + ' dismissed' };
    note('dismissed ' + id); renderShade(); showUndo(n.title + ' dismissed', true);
  }
  function bindShade() {
    const el = $('shade');
    el.addEventListener('mousedown', (e) => { if (e.target.closest('#strip')) e.preventDefault(); });
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.qs) { shade.qs[b.dataset.qs] = !shade.qs[b.dataset.qs]; b.setAttribute('aria-pressed', String(shade.qs[b.dataset.qs])); note('quick setting ' + b.dataset.qs); return; }
      if (b.hasAttribute('data-expand')) { shade.expanded = !shade.expanded; note('messages ' + (shade.expanded ? 'expanded' : 'collapsed')); renderShade(); return; }
      if (b.dataset.dismiss) return dismiss(b.dataset.dismiss);
      if (b.id === 'sh-keep') { $('sh-confirm').hidden = true; note('kept draft'); return; }
      if (b.id === 'sh-discard') { $('sh-confirm').hidden = true; return dismiss('msgs', true); }
      if (b.id === 'sh-undo-btn' && shade.undo) { shade.items.splice(shade.undo.idx, 0, shade.undo.item); shade.undo = null; note('undo dismiss'); renderShade(); return; }
      if (b.dataset.reply) { shade.reply = { id: b.dataset.reply, state: 'draft' }; renderShade(); const f = $('f-reply'); if (f) f.focus({ preventScroll: true }); showKb('reply'); return; }
      if (b.dataset.markread) { const g = shade.items.find((n) => n.id === 'msgs'); if (g) { g.items = g.items.filter((m) => m.id !== b.dataset.markread); if (!g.items.length) shade.items = shade.items.filter((n) => n !== g); } renderShade(); return; }
      if (b.id === 'reply-send' || b.id === 'reply-retry') return replySend();
      if (b.id === 'sh-handle') return closeShade('handle');
      if (b.dataset.openApp) { kb.stash = false; closeShade('opened source'); R.dispatch({ type: 'openApp', value: b.dataset.openApp }); return; }
      if (b.dataset.act) {
        const id = b.dataset.act; kb.stash = false; closeShade('opened ' + id);
        if (id === 'rose-review') { const s = R.getState(); if (s.tasks.reply.state === 'idle') R.dispatch({ type: 'startTask', id: 'reply' }); R.dispatch({ type: 'openWindow', value: 'w-mail' }); } // opens the exact task; never approves
        else if (id === 'rose-done') { R.dispatch({ type: 'openRose' }); }
        else if (id === 'cal') R.dispatch({ type: 'openApp', value: 'calendar' });
      }
    });
    el.addEventListener('input', (e) => { if (e.target.id === 'sh-bright') shade.bright = +e.target.value; });
    // Pull down from the top edge opens the shade; a drag up on its handle/header closes it.
    let pull = null;
    screen.addEventListener('pointerdown', (e) => {
      if (e.button || R.getState().locked) return;
      const r = screen.getBoundingClientRect(), k = scaleK(), y = (e.clientY - r.top) / k;
      if (!shade.open && y <= 36) pull = { y: e.clientY, open: true };
      else if (shade.open && (e.target.closest('#sh-handle') || e.target.closest('.sh-top'))) pull = { y: e.clientY, open: false };
    });
    window.addEventListener('pointerup', (e) => {
      const p = pull; pull = null; if (!p) return;
      const dy = (e.clientY - p.y) / scaleK();
      if (p.open && dy > 50) openShade('pull down');
      else if (!p.open && dy < -40) closeShade('swipe up');
    });
    screen.addEventListener('touchstart', (e) => { const r = screen.getBoundingClientRect(); pullTouch = !shade.open && (e.touches[0].clientY - r.top) / scaleK() <= 36; }, { passive: true });
    screen.addEventListener('touchmove', (e) => { if (pullTouch && e.cancelable) e.preventDefault(); }, { passive: false });
  }
  let pullTouch = false;

  // =====================================================================================
  // V3 return control
  // =====================================================================================
  const ret = { origin: null, state: 'idle', timer: 0, unavailable: false };
  const originName = () => ret.origin ? (ret.origin.view === 'app' ? window.RoseModel.APPS[ret.origin.app].name : window.RoseModel.APPS[window.RoseModel.WINDOWS[ret.origin.window].app].name) : '';
  const originArt = () => ret.origin ? I.appArt(ret.origin.view === 'app' ? ret.origin.app : window.RoseModel.WINDOWS[ret.origin.window].app, 'mono') : '';
  let lastView = null, lastCtx = null;
  // Read at the moment of leaving, from the live screen: scroll, follow state and keyboard.
  function snapshot(c) {
    const t = c.app === 'messages' ? msgThread() : null, d = c.view === 'app' ? $('appdetail') : $('wdetail');
    return { view: c.view, app: c.app, window: c.window, kb: kb.open && kb.target === 'msg', scroll: t ? t.scrollTop : null, follow: follow.msg, detailScroll: d ? d.scrollTop : 0 };
  }
  function renderReturn(s) {
    const b = $('rose-return'); if (!b) return;
    const show = s.view === 'rose' && !!ret.origin;
    b.hidden = !show;
    if (!show) return;
    const name = originName(), un = ret.unavailable;
    b.dataset.state = un ? 'unavailable' : ret.state;
    b.innerHTML = '<span class="rr-chev" aria-hidden="true">' + IC.back + '</span><span class="rr-ico" aria-hidden="true">' + originArt() + '</span><span class="rr-label">' +
      (un ? esc(name) + ' closed' : ret.state === 'pending' ? 'Returning…' : esc(name)) + '</span>';
    b.setAttribute('aria-label', un ? name + ' is no longer open' : ret.state === 'pending' ? 'Returning to ' + name + '. Activate Cancel to stay in Rose.' : 'Return to ' + name);
    $('rose-return-cancel').hidden = ret.state !== 'pending';
  }
  function returnTap() {
    if (!ret.origin) return;
    if (ret.unavailable) { toast(originName() + ' isn’t open anymore. Open it from Home.'); note('return: origin unavailable (stayed in Rose)'); return; }
    if (ret.state === 'pending') return;
    ret.state = 'pending'; note('return pending → ' + originName()); renderReturn(R.getState());
    ret.timer = setTimeout(() => {
      const o = ret.origin; if (!o || ret.state !== 'pending') return;
      ret.state = 'idle';
      R.dispatch({ type: 'returnTo', view: o.view, app: o.app, window: o.window });
      const s = R.getState();
      if (s.view !== o.view || s.app !== o.app || s.window !== o.window) { toast('Couldn’t return · stayed in Rose'); note('return failed'); return; }
      // Correlated success: restore the exact context, then the keyboard if it was up.
      const origin = o; ret.origin = null;
      requestAnimationFrame(() => {
        const t = msgThread();
        if (t && origin.scroll !== null) { follow.msg = origin.follow; t.scrollTop = origin.scroll; follow.last = t.scrollTop; }
        const d = origin.view === 'app' ? $('appdetail') : $('wdetail'); if (d) d.scrollTop = origin.detailScroll;
        if (origin.kb) { showKb('msg'); const f = $('f-msg'); if (f) f.focus({ preventScroll: true }); }
        note('returned to ' + (origin.app || origin.window));
      });
    }, reduced() ? 80 : 650);
  }
  function returnCancel() { if (ret.state !== 'pending') return; clearTimeout(ret.timer); ret.state = 'idle'; note('return cancelled (stayed in Rose)'); toast('Staying in Rose'); renderReturn(R.getState()); const b = $('rose-return'); if (b) b.focus({ preventScroll: true }); }
  function toast(t) { const el = $('sys-toast'); el.textContent = t; el.classList.add('on'); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('on'), 2200); }

  // =====================================================================================
  // Wiring
  // =====================================================================================
  function beforeRender(s) {
    // Leaving an app/window for Rose: remember the exact context (and whether the keyboard was up).
    if (lastView && s.view !== lastView) {
      if (s.view === 'rose' && (lastView === 'app' || lastView === 'window') && lastCtx) { ret.origin = snapshot(lastCtx); ret.state = 'idle'; note('origin kept: ' + (lastCtx.app || lastCtx.window)); }
      else if (s.view !== 'rose' && s.view !== 'agents') { if (ret.origin) note('return cleared'); ret.origin = null; clearTimeout(ret.timer); ret.state = 'idle'; }
      if (kb.open && kb.target === 'msg' && s.view !== 'app') { kb.open = false; kb.editHold = false; note('keyboard hidden (leaving app)'); }
    }
  }
  function afterRender(s) {
    if (s.view === 'app' && s.app === 'messages') {
      bindField($('f-msg'), 'msg'); bindThread();
      const sb = $('ab-send');
      if (sb && !sb.dataset.bound) { sb.dataset.bound = '1'; sb.addEventListener('mousedown', (e) => e.preventDefault()); sb.addEventListener('click', () => { sendMsg(); renderKb(); }); }
    }
    renderKb(); renderReturn(s);
    if (shade.open) { const chip = document.querySelector('.sh-input'); if (chip && chip.dataset.muted !== String(s.muted)) renderShade(); }
    lastView = s.view;
    if (s.view === 'app' || s.view === 'window') lastCtx = { view: s.view, app: s.app, window: s.window };
  }
  function escape() {
    if (ret.state === 'pending') { returnCancel(); return true; }
    if (shade.open) { const c = $('sh-confirm'); if (c && !c.hidden) { c.hidden = true; return true; } if (kb.open) { hideKb('Esc'); return true; } closeShade('Esc'); return true; }
    if (kb.open) { hideKb('Esc'); return true; }
    return false;
  }
  function init(rose) {
    R = rose; screen = $('screen'); device = $('device');
    screen.insertAdjacentHTML('beforeend',
      '<section class="kb" id="kb" aria-label="Keyboard" aria-hidden="true"><div class="strip" id="strip" role="toolbar" aria-label="Suggestions"></div><div class="keys" id="keys"></div><div class="kpop" id="kpop" aria-hidden="true"></div></section>' +
      '<div class="kb-rail" aria-hidden="true"></div>' +
      '<section class="shade" id="shade" role="dialog" aria-label="Notifications" hidden></section>' +
      '<div class="sys-toast" id="sys-toast" role="status" aria-live="polite"></div>');
    $('v-rose').insertAdjacentHTML('beforeend', '<div class="rose-return-wrap"><button class="rose-return" id="rose-return" hidden></button><button class="rr-cancel" id="rose-return-cancel" hidden>Cancel</button></div>');
    $('rose-return').addEventListener('click', returnTap);
    $('rose-return-cancel').addEventListener('click', returnCancel);
    bindKeys(); bindShade();
    document.addEventListener('keydown', onKey);
    // Harness (outside the phone).
    const hs = $('h-system');
    if (hs) {
      $('h-shade').onclick = () => (shade.open ? closeShade('harness') : openShade('harness'));
      $('h-empty').onclick = () => { shade.items = shade.items.length ? [] : ITEMS(); shade.undo = null; renderShade(); };
      $('h-fail').onchange = (e) => { shade.failNext = e.target.checked; };
      $('h-unavail').onchange = (e) => { ret.unavailable = e.target.checked; renderReturn(R.getState()); };
    }
    afterRender(R.getState());
  }
  window.RoseSystem = { init, beforeRender, afterRender, escape, kbOpen: () => kb.open, state: () => ({ kb: { ...kb }, fields, shade, ret }), openShade, closeShade, hideKb, showKb };
  if (window.__rose) init(window.__rose);
})();
