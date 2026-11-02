// Rose HiFi — interface. State lives in RoseModel; this file renders it, owns timers,
// view transitions and gestures, and drives the single shared Rose renderer.
(function () {
  'use strict';
  const M = window.RoseModel, I = window.RoseIcons;
  const $ = (id) => document.getElementById(id);
  const screen = $('screen'), device = $('device');
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  let state = M.initialState();
  let reducedOverride = false, epoch = 0;
  const timers = new Set();
  const reduced = () => reducedOverride || media.matches;

  const RESULTS = {
    saturday: {
      title: 'Two quiet places for Saturday.',
      body: ['The garden opens at nine. The riverside walk is nearby.'],
      facts: [['Botanic Garden', 'Opens 9:00 · 12 min walk'], ['Riverside path', 'Shaded · 8 min from the garden']],
    },
    reply: {
      title: 'Your reply to Jamie is drafted.',
      body: ['It says Saturday works and suggests the garden gate at ten.', 'Nothing has been sent.'],
      facts: [['To', 'Jamie'], ['Status', 'Draft · not sent']],
    },
    cafe: {
      title: 'Fern House is the quieter café.',
      body: ['Courtyard seating, away from the counter. Harbor Cup is closer, with fewer seats.'],
      facts: [['Fern House', 'Courtyard · open until 5 pm'], ['Harbor Cup', 'Waterfront · 6 min walk']],
    },
  };
  const RESULT_ORDER = Object.keys(RESULTS);

  function later(fn, ms) {
    const gen = epoch;
    const id = setTimeout(() => { timers.delete(id); if (gen === epoch) fn(); }, ms);
    timers.add(id); return id;
  }
  function cancel(id) { clearTimeout(id); timers.delete(id); }

  // ---------- Renderer ----------
  const renderer = window.RoseRenderer.create({ canvas: $('rose-canvas'), fallback: $('rose-fallback'), onStatus: () => setTimeout(updateRenderStatus, 0) });
  const park = document.createElement('div'); park.hidden = true; screen.appendChild(park);
  const fly = document.createElement('div'); fly.className = 'rose-fly'; fly.setAttribute('aria-hidden', 'true'); screen.appendChild(fly);
  let flyToken = 0;
  function roseSlotFor(view) {
    if (view === 'home') return $('slot-tray');
    if (view === 'rose') return $('slot-stage');
    if (view === 'switcher') return $('slot-switcher');
    return null;
  }

  // ---------- Home pages ----------
  function buildHome() {
    const track = $('home-track'), dots = $('page-dots');
    track.replaceChildren(); dots.replaceChildren();
    M.HOME_PAGES.forEach((apps, i) => {
      const page = document.createElement('div');
      page.className = 'home-page ' + (i === 0 ? 'sparse' : i === 1 ? 'full' : 'few');
      page.setAttribute('role', 'group'); page.setAttribute('aria-roledescription', 'page');
      page.setAttribute('aria-label', 'Page ' + (i + 1) + ' of ' + M.HOME_PAGES.length);
      if (i === 0) page.innerHTML = '<div class="home-date">Thursday, October 29</div>';
      const grid = document.createElement('div'); grid.className = 'app-grid';
      for (const id of apps) {
        const b = document.createElement('button');
        b.className = 'app'; b.dataset.app = id; b.setAttribute('aria-label', 'Open ' + M.APPS[id].name);
        b.innerHTML = '<span class="tile">' + I.appArt(id) + '</span><span class="label" aria-hidden="true">' + M.APPS[id].name + '</span>';
        grid.appendChild(b);
      }
      page.appendChild(grid); track.appendChild(page);
      const d = document.createElement('button');
      d.setAttribute('aria-label', 'Home page ' + (i + 1)); d.dataset.page = i; d.innerHTML = '<i></i>';
      dots.appendChild(d);
    });
  }

  let drag = null, suppressClick = false;
  function bindHomeGestures() {
    const pages = $('home-pages'), track = $('home-track');
    pages.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, id: e.pointerId, moved: false, w: pages.clientWidth };
    });
    pages.addEventListener('pointermove', (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) { drag.moved = true; pages.setPointerCapture(drag.id); track.classList.add('dragging'); }
      if (drag.moved) {
        const max = M.HOME_PAGES.length - 1, p = state.homePage;
        const resist = (p === 0 && dx > 0) || (p === max && dx < 0) ? 0.3 : 1;
        track.style.transform = 'translateX(' + (-p * drag.w + dx * resist) + 'px)';
      }
    });
    const end = (e) => {
      if (!drag || (e && e.pointerId !== drag.id)) return;
      const moved = drag.moved, dx = e ? e.clientX - drag.x : 0, w = drag.w; drag = null;
      track.classList.remove('dragging'); track.style.transform = '';
      if (moved) {
        suppressClick = true;
        const next = Math.abs(dx) > w * 0.18 ? state.homePage + (dx < 0 ? 1 : -1) : state.homePage;
        dispatch({ type: 'homePage', value: next });
        render();
      }
    };
    pages.addEventListener('pointerup', end);
    pages.addEventListener('pointercancel', () => { if (drag) { drag = null; track.classList.remove('dragging'); track.style.transform = ''; render(); } });
    pages.addEventListener('click', (e) => { if (suppressClick) { e.stopPropagation(); e.preventDefault(); suppressClick = false; } }, true);
    pages.addEventListener('dragstart', (e) => e.preventDefault());
    pages.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault(); dispatch({ type: 'homePage', value: state.homePage + (e.key === 'ArrowRight' ? 1 : -1) });
      const first = $('home-track').children[state.homePage].querySelector('.app'); if (first) first.focus({ preventScroll: true });
    });
  }

  // ---------- View transitions ----------
  let shownView = 'home';
  const viewEl = (v) => $('v-' + v);
  function flyRose(toSlot) {
    const canvas = $('rose-canvas');
    const from = canvas.isConnected && canvas.parentElement !== park ? canvas.getBoundingClientRect() : null;
    flyToken++;
    fly.getAnimations().forEach((x) => x.cancel());
    if (toSlot) renderer.attach(toSlot); else { park.appendChild(canvas); park.appendChild($('rose-fallback')); }
    if (!toSlot || !from || !from.width || reduced()) return;
    const to = canvas.getBoundingClientRect();
    if (!to.width) return;
    // Fly on a layer above both views so Rose stays visible for the whole move.
    const sr = screen.getBoundingClientRect(), k = sr.width / screen.offsetWidth;
    const toW = to.width / k;
    const box = (r) => ({ x: (r.left + r.width / 2 - sr.left) / k, y: (r.top + r.height / 2 - sr.top) / k });
    const a = box(from), b = box(to), s = from.width / to.width;
    fly.style.width = fly.style.height = toW + 'px';
    for (const el of [canvas, $('rose-fallback')]) fly.appendChild(el);
    const token = flyToken;
    const anim = fly.animate([
      { transform: 'translate(' + (a.x - toW / 2) + 'px,' + (a.y - toW / 2) + 'px) scale(' + s + ')' },
      { transform: 'translate(' + (b.x - toW / 2) + 'px,' + (b.y - toW / 2) + 'px)' },
    ], { duration: toSlot === $('slot-stage') ? OPEN_MS : toSlot === $('slot-tray') ? CLOSE_MS : 560, easing: toSlot === $('slot-tray') ? 'cubic-bezier(.3,0,.15,1)' : EASE, fill: 'forwards' });
    anim.finished.then(() => { if (token === flyToken) { renderer.attach(toSlot); anim.cancel(); } renderer.refresh(); }, () => {});
  }
  let transitionToken = 0;
  const after = (fn, ms) => { const token = transitionToken; setTimeout(() => { if (token === transitionToken) fn(); }, ms); };
  // Finish any interrupted transition: only the target view may stay active.
  function settleViews(keep) {
    for (const v of ['home', 'rose', 'switcher', 'agents', 'app', 'window']) {
      const el = viewEl(v);
      el.getAnimations({ subtree: true }).forEach((x) => x.cancel());
      el.style.zIndex = '';
      if (!keep.includes(v)) el.dataset.active = 'false';
    }
  }
  function showView(next, force) {
    const prev = shownView;
    if (prev === next && !force) return;
    transitionToken++;
    settleViews([prev, next]);
    shownView = next;
    const a = viewEl(prev), b = viewEl(next);
    b.dataset.active = 'true';
    screen.dataset.view = next;
    flyRose(roseSlotFor(next));
    pendingFocus = true;
    const done = () => { if (shownView !== prev) a.dataset.active = 'false'; };
    if (reduced() || prev === next) { done(); return; }
    if (next === 'rose' && prev === 'home') openRoseFromTray(a, b, done);
    else if (prev === 'rose' && next === 'home') closeRoseToTray(a, b, done);
    else {
      b.style.zIndex = 3;
      b.animate([{ opacity: 0, transform: 'scale(.98)' }, { opacity: 1, transform: 'none' }], { duration: 360, easing: EASE });
      after(() => { b.style.zIndex = ''; done(); }, 370);
    }
  }

  // Home ↔ Rose: the Rose glass well grows into the Rose surface (and shrinks back).
  // One curve drives the sheet, Rose's flight, the tray crossfade and the content.
  // Balanced in-out curve: motion is spread across the whole duration, not front-loaded.
  const EASE = 'cubic-bezier(.45,0,.2,1)';
  const OPEN_MS = 620, CLOSE_MS = 560;
  function wellInset() {
    const sr = screen.getBoundingClientRect(), k = sr.width / screen.offsetWidth, r = $('tray-rose').getBoundingClientRect();
    const l = (r.left - sr.left) / k, t = (r.top - sr.top) / k, w = r.width / k, h = r.height / k;
    const W = screen.offsetWidth, H = screen.offsetHeight;
    return 'inset(' + t + 'px ' + (W - l - w) + 'px ' + (H - t - h) + 'px ' + l + 'px round ' + (h / 2) + 'px)';
  }
  const FULL = 'inset(0px 0px 0px 0px round 46px)';
  function openRoseFromTray(home, rose, done) {
    rose.style.zIndex = 3;
    rose.querySelector('.ivory-bg').animate([{ clipPath: wellInset(), opacity: 0.75 }, { clipPath: FULL, opacity: 1 }], { duration: OPEN_MS, easing: EASE });
    // Plates are identical and aligned: crossfading the tray swaps only the wells.
    rose.querySelector('.tray').animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, delay: 60, easing: 'ease-out', fill: 'backwards' });
    rose.querySelectorAll('.tray .well').forEach((el, i) => el.animate([{ transform: 'scale(.9) translateY(3px)' }, { transform: 'none' }], { duration: 460, delay: 80 + i * 50, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' }));
    // Content appears once the sheet has covered it; the header sits highest, so it comes last.
    for (const el of rose.querySelectorAll('.rose-caption,.rose-output')) el.animate([{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], { duration: 420, delay: 300, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' });
    rose.querySelector('.rose-head').animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 320, delay: OPEN_MS - 160, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' });
    after(() => { rose.style.zIndex = ''; done(); home.getAnimations({ subtree: true }).forEach((x) => x.cancel()); }, OPEN_MS + 20);
  }
  function closeRoseToTray(rose, home, done) {
    rose.style.zIndex = 3;
    const ease = EASE;
    for (const el of rose.querySelectorAll('.rose-head,.rose-caption,.rose-output')) el.animate([{ opacity: 1 }, { opacity: 0, transform: 'translateY(8px)' }], { duration: 180, easing: 'ease-in', fill: 'forwards' });
    rose.querySelector('.tray').animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, delay: 120, easing: 'ease-in-out', fill: 'forwards' });
    const sheet = rose.querySelector('.ivory-bg');
    sheet.animate([{ clipPath: FULL }, { clipPath: wellInset() }], { duration: CLOSE_MS, easing: 'cubic-bezier(.3,0,.15,1)', fill: 'forwards', composite: 'replace' });
    sheet.animate([{ opacity: 1 }, { opacity: 1, offset: 0.6 }, { opacity: 0 }], { duration: CLOSE_MS, easing: 'linear', fill: 'forwards' });
    after(() => { rose.style.zIndex = ''; done(); rose.getAnimations({ subtree: true }).forEach((x) => x.cancel()); home.getAnimations({ subtree: true }).forEach((x) => x.cancel()); }, CLOSE_MS + 20);
  }

  // ---------- Focus: move on view change; restore when returning ----------
  let pendingFocus = false;
  const focusMemory = {};
  function describe(el) {
    if (!el || el === document.body) return null;
    if (el.id) return '#' + el.id;
    for (const a of ['data-app', 'data-open-window', 'data-open-app', 'data-page']) if (el.hasAttribute(a)) return '[' + a + '="' + el.getAttribute(a) + '"]';
    return null;
  }
  function rememberFocus(view) {
    const el = document.activeElement;
    if (viewEl(view).contains(el)) focusMemory[view] = describe(el);
  }
  function defaultFocus(view) {
    if (view === 'home') return $('tray-rose');
    if (view === 'rose') return !$('rose-mic').disabled ? $('rose-mic') : $('rose-end').disabled ? $('rose-agents') : $('rose-end');
    if (view === 'switcher') return $('sw-human');
    if (view === 'agents') return viewEl('agents').querySelector('[data-open-window]') || $('ag-back');
    return viewEl(view).querySelector('.back-btn');
  }
  function applyFocus() {
    if (!pendingFocus || state.locked || state.pendingStop) return;
    pendingFocus = false;
    // Never steal focus from the harness outside the phone.
    if (document.activeElement && document.activeElement.closest('.harness')) return;
    const mem = focusMemory[state.view];
    const el = (mem && viewEl(state.view).querySelector(mem)) || defaultFocus(state.view);
    if (el && !el.disabled) el.focus({ preventScroll: true });
  }

  // ---------- Rose output ----------
  let renderedResult;
  function renderResult() {
    if (renderedResult === state.result) return;
    renderedResult = state.result;
    const out = $('rose-output'), r = RESULTS[state.result];
    screen.dataset.result = r ? 'true' : 'false';
    if (!r) { out.replaceChildren(); return; }
    const esc = (t) => t.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    out.innerHTML = '<div class="reveal"><h3>' + esc(r.title) + '</h3>' + r.body.map((p) => '<p>' + esc(p) + '</p>').join('') +
      '<ul class="facts">' + r.facts.map((f) => '<li><b>' + esc(f[0]) + '</b><span>' + esc(f[1]) + '</span></li>').join('') + '</ul></div>';
  }

  // ---------- Notifications ----------
  const shownNotices = new Map();
  function noticeIcon(n) {
    if (n.kind === 'task') return /needs/.test(n.text) ? '<span class="ni attn">' + I.UI.alert + '</span>' : '<span class="ni done">' + I.UI.check + '</span>';
    if (n.text === 'Muted') return '<span class="ni">' + I.UI.micOff + '</span>';
    if (n.text === 'Call ended') return '<span class="ni end">' + I.UI.phone + '</span>';
    return '<span class="ni">' + I.UI.mic + '</span>';
  }
  function renderNotices() {
    const host = $('notices');
    const ids = new Set(state.notices.map((n) => n.id));
    for (const [id, el] of shownNotices) {
      if (ids.has(id) || el.classList.contains('leaving')) continue;
      if (reduced()) { el.remove(); shownNotices.delete(id); continue; }
      el.classList.add('leaving');
      el.addEventListener('animationend', () => { el.remove(); shownNotices.delete(id); }, { once: true });
    }
    // Keep the stack short so it never buries content: two notices, one over Rose's results.
    const cap = state.view === 'rose' ? 1 : 2;
    const visible = new Set(state.notices.slice(-cap).map((n) => n.id));
    for (const [id, el] of shownNotices) el.hidden = !visible.has(id);
    for (const n of state.notices) {
      if (shownNotices.has(n.id)) continue;
      const el = document.createElement(n.kind === 'task' ? 'button' : 'div');
      el.className = 'notice ' + n.kind;
      el.innerHTML = noticeIcon(n) + '<span>' + n.text + (n.kind === 'task' ? '<small>Tap to see Rose’s apps</small>' : '') + '</span>';
      if (n.kind === 'task') el.addEventListener('click', () => { dispatch({ type: 'dismissNotice', value: n.id }); dispatch({ type: 'agents' }); });
      el.hidden = !visible.has(n.id);
      host.appendChild(el); shownNotices.set(n.id, el);
      $('announcer').textContent = n.text;
      const dismiss = () => { if (el.matches(':hover,:focus-within')) later(dismiss, 1200); else dispatch({ type: 'dismissNotice', value: n.id }); };
      later(dismiss, n.kind === 'task' ? (/needs/.test(n.text) ? 6000 : 4200) : 2200);
    }
  }

  // ---------- Render ----------
  let wasLocked = false;
  function render() {
    if (shownView !== state.view) rememberFocus(shownView);
    device.classList.toggle('unfolded', state.layout === 'unfolded');
    document.documentElement.style.setProperty('--screen-w', state.layout === 'unfolded' ? '748px' : '384px');
    screen.classList.toggle('static-motion', reduced());
    showView(state.view);

    // Home
    $('home-track').style.transform = 'translateX(' + (-state.homePage * 100) + '%)';
    [...$('home-track').children].forEach((p, i) => { p.inert = i !== state.homePage; p.setAttribute('aria-hidden', String(i !== state.homePage)); });
    [...$('page-dots').children].forEach((d, i) => d.setAttribute('aria-current', String(i === state.homePage)));
    const callLive = state.connection !== 'disconnected';
    $('tray-rose').setAttribute('aria-label', callLive ? 'Return to Rose call' : 'Open Rose');
    // Rose's voice (output) mute: stable name + aria-pressed, icon and a visible "muted" mark.
    const vb = $('tray-voice');
    vb.innerHTML = (state.voiceMuted ? I.UI.voiceOff : I.UI.voice) + '<span class="voice-state" aria-hidden="true">' + (state.voiceMuted ? 'Muted' : '') + '</span>';
    vb.setAttribute('aria-pressed', String(state.voiceMuted));
    vb.dataset.voiceMuted = String(state.voiceMuted);
    vb.title = state.voiceMuted ? "Rose's voice is muted — tap to unmute" : "Mute Rose's voice";

    // Rose
    const mic = $('rose-mic');
    mic.innerHTML = state.muted ? I.UI.micOff : I.UI.mic;
    mic.dataset.muted = String(state.muted);
    mic.setAttribute('aria-pressed', String(state.muted));
    mic.setAttribute('aria-label', 'Mute microphone');
    mic.disabled = state.connection !== 'connected';
    $('rose-end').innerHTML = '<span class="end">' + I.UI.end + '</span>';
    $('rose-end').disabled = !callLive;
    $('rose-agents').innerHTML = I.UI.grid;
    // Background-work entry (Oxblood: upper right). Highlight only while a task is actually running.
    $('rose-work').dataset.working = String(M.taskActivity(state).length > 0);
    const caption = state.connection === 'connecting' ? 'Connecting…' : state.connection === 'connected' ? (state.speaking ? (state.voiceMuted ? 'Rose speaking · voice muted' : 'Rose speaking') : state.muted ? 'Microphone muted' : 'Listening') : '';
    $('rose-caption').textContent = caption;
    renderResult();
    renderNotices();

    // Lock
    $('lock').hidden = !state.locked;
    screen.classList.toggle('locked', state.locked);
    $('home-bar').inert = state.locked || !!state.pendingStop;
    $('notices').inert = state.locked || !!state.pendingStop;
    $('phone-views') && ($('phone-views').inert = state.locked);
    for (const v of ['home', 'rose', 'switcher', 'agents', 'app', 'window']) viewEl(v).inert = state.locked || !!state.pendingStop;

    // Renderer signals
    const sig = M.rendererSignals(state);
    renderer.setSignals(Object.assign(sig, { energy: 0.6 }));
    renderer.setReduced(reduced());

    // Harness
    $('h-folded').setAttribute('aria-pressed', String(state.layout === 'folded'));
    $('h-unfolded').setAttribute('aria-pressed', String(state.layout === 'unfolded'));
    $('h-lock').textContent = state.locked ? 'Unlock' : 'Lock';
    $('h-speak').disabled = state.connection !== 'connected';
    $('h-result').disabled = state.connection !== 'connected';
    $('h-tasklist').innerHTML = Object.entries(state.tasks).map(([id, t]) => '<div><span>' + M.TASKS[id].title + '</span><span>' + t.state + (t.state === 'running' ? ' · ' + t.step + '/' + M.MAX_STEP : '') + '</span></div>').join('');
    if (window.RoseViews) window.RoseViews.render(state, ctx);
    fit();
    if (pendingFocus) requestAnimationFrame(applyFocus);
    // Unlocking with a confirmation open returns focus to it.
    if (wasLocked && !state.locked && state.pendingStop) requestAnimationFrame(() => { const c = $('sheet-cancel'); if (c) c.focus({ preventScroll: true }); });
    if (!wasLocked && state.locked) requestAnimationFrame(() => $('unlock').focus({ preventScroll: true }));
    wasLocked = state.locked;
  }

  function updateRenderStatus() {
    const st = renderer.status();
    const f = st.frames.slice().sort((a, b) => a - b);
    const med = f.length > 20 ? f[Math.floor(f.length / 2)].toFixed(1) + ' ms' : '—';
    const p95 = f.length > 20 ? f[Math.floor(f.length * 0.95)].toFixed(1) + ' ms' : '—';
    const name = st.renderer ? st.renderer.replace(/^ANGLE \(/, '').replace(/\)$/, '').split(',').slice(0, 2).join(',') : '—';
    $('h-render').innerHTML = '<b>' + ({ webgl: 'WebGL live', 'static-forced': 'Static fallback (forced)', 'static-context-lost': 'Static fallback · context lost', 'static-unavailable': 'Static fallback · WebGL unavailable', starting: 'Starting' }[st.mode]) + '</b><br>' +
      'Renderer: ' + name + '<br>Canvas ' + st.canvas.width + '×' + st.canvas.height + ' · DPR ' + (window.devicePixelRatio || 1) + '<br>Frame interval median ' + med + ' · p95 ' + p95 + '<br><span>rAF timing, not GPU time or device proof.</span>';
  }
  setInterval(updateRenderStatus, 1000);

  // ---------- Effects & dispatch ----------
  let connectTimer, speechTimer, greetTimer;
  function dispatch(event) {
    const before = state;
    const out = M.transition(state, event);
    state = out.state;
    for (const fx of out.effects) {
      if (fx.type === 'connect') { cancel(connectTimer); connectTimer = later(() => dispatch({ type: 'connected' }), 1100); }
      if (fx.type === 'speech') { cancel(speechTimer); speechTimer = later(() => dispatch({ type: 'speechEnd' }), fx.ms); }
      if (fx.type === 'endVoice') { cancel(connectTimer); cancel(speechTimer); cancel(greetTimer); }
      if (fx.type === 'stopAck') later(() => dispatch({ type: 'stopAck', id: fx.id }), 1600);
      if (fx.type === 'reset') { epoch++; for (const t of timers) clearTimeout(t); timers.clear(); transitionToken++; settleViews(['home']); shownView = 'home'; viewEl('home').dataset.active = 'true'; screen.dataset.view = 'home'; flyRose($('slot-tray')); startClock(); }
    }
    // Task status announcements (not conversation cues).
    for (const id of Object.keys(state.tasks)) {
      const a = before.tasks[id].state, b = state.tasks[id].state;
      if (a === b) continue;
      const words = { stopping: 'stopping', stopped: 'stopped', running: a === 'attention' ? 'working again' : a === 'idle' ? 'started' : '' }[b];
      if (words) $('announcer').textContent = M.TASKS[id].title + ': ' + words + '.';
    }
    // First connection with an empty output: Rose answers with a formatted result.
    if (before.connection === 'connecting' && state.connection === 'connected' && !state.result) {
      cancel(greetTimer);
      greetTimer = later(() => { dispatch({ type: 'speak', ms: 4600 }); later(() => dispatch({ type: 'result', value: 'saturday' }), 700); }, 600);
    }
    render();
    return state;
  }
  function startClock() { later(() => { dispatch({ type: 'tick' }); startClock(); }, 2600); }
  const ctx = { dispatch, $, I, M, reduced, later, getState: () => state };

  // ---------- Events ----------
  document.addEventListener('click', (e) => {
    const app = e.target.closest('.app[data-app]');
    if (app) { dispatch({ type: 'openApp', value: app.dataset.app }); return; }
    const dot = e.target.closest('#page-dots button');
    if (dot) { dispatch({ type: 'homePage', value: +dot.dataset.page }); return; }
  });
  $('tray-rose').addEventListener('click', () => dispatch({ type: 'openRose' }));
  $('tray-overview').addEventListener('click', () => dispatch({ type: 'switcher' }));
  $('tray-voice').addEventListener('click', () => {
    dispatch({ type: 'voiceMute' });
    // Screen-reader confirmation only; not a visual conversation cue.
    $('announcer').textContent = state.voiceMuted ? "Rose's voice muted." : "Rose's voice on.";
  });
  $('rose-mic').addEventListener('click', () => dispatch({ type: 'mute' }));
  $('rose-end').addEventListener('click', () => { dispatch({ type: 'endCall' }); if (state.view === 'home') $('tray-rose').focus({ preventScroll: true }); });
  $('rose-agents').addEventListener('click', () => dispatch({ type: 'agents' }));
  $('rose-work').addEventListener('click', () => dispatch({ type: 'agents' }));
  $('home-bar').addEventListener('click', () => dispatch({ type: 'home' }));
  $('unlock').addEventListener('click', () => dispatch({ type: 'unlock' }));
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || state.locked) return;
    if (state.pendingStop) dispatch({ type: 'cancelStop' });
    else if (state.view !== 'home') dispatch({ type: 'back' });
  });

  // Harness
  $('h-folded').onclick = () => dispatch({ type: 'layout', value: 'folded' });
  $('h-unfolded').onclick = () => dispatch({ type: 'layout', value: 'unfolded' });
  $('h-lock').onclick = () => dispatch({ type: state.locked ? 'unlock' : 'lock' });
  $('h-reset').onclick = () => { shownNotices.forEach((el) => el.remove()); shownNotices.clear(); renderedResult = undefined; dispatch({ type: 'reset' }); };
  $('h-speak').onclick = () => dispatch({ type: 'speak', ms: 4200 });
  $('h-result').onclick = () => { const i = RESULT_ORDER.indexOf(state.result); dispatch({ type: 'result', value: RESULT_ORDER[(i + 1) % RESULT_ORDER.length] }); };
  $('h-tasks').onclick = () => { for (const id of Object.keys(M.TASKS)) dispatch({ type: 'startTask', id }); };
  $('h-attn').onclick = () => dispatch({ type: 'taskEvent', id: 'reply', value: 'attention' });
  $('h-done').onclick = () => { const id = Object.keys(state.tasks).find((k) => M.STOPPABLE.includes(state.tasks[k].state)); if (id) dispatch({ type: 'taskEvent', id, value: 'done' }); };
  // Bottom-tray exploration: opt-in via ?tray=1…10 or the harness selector; default unchanged.
  // Visual direction: Oxblood (Nathan, 2026-11-02) is the prototype default; ?theme=classic keeps the
  // previous HiFi look, where the ten tray options (?tray=N) remain available unchanged.
  const params = new URLSearchParams(location.search);
  function setTheme(v) {
    const t = v === 'classic' ? 'classic' : 'oxblood';
    device.dataset.theme = t;
    $('h-theme').value = t;
    $('h-tray').disabled = t === 'oxblood';
    if (t === 'oxblood') setTray('');
    renderer.refresh();
  }
  function setTray(v) {
    let n = /^(10|[1-9])$/.test(String(v)) ? String(v) : '';
    if (device.dataset.theme === 'oxblood') n = '';
    if (n) screen.dataset.tray = n; else delete screen.dataset.tray;
    $('h-tray').value = n;
    renderer.refresh();
  }
  $('h-tray').onchange = (e) => setTray(e.target.value);
  $('h-theme').onchange = (e) => setTheme(e.target.value);
  setTheme(params.get('theme') || (params.get('tray') ? 'classic' : 'oxblood'));
  setTray(params.get('tray'));
  $('h-reduced').checked = media.matches;
  $('h-reduced').onchange = (e) => { reducedOverride = e.target.checked; render(); };
  media.addEventListener('change', () => { $('h-reduced').checked = media.matches || reducedOverride; render(); });
  $('h-static').onchange = (e) => renderer.setForcedFallback(e.target.checked);
  let lostExt = null;
  $('h-lose').onclick = () => {
    if (!lostExt) { lostExt = renderer.loseContext(); $('h-lose').textContent = lostExt ? 'Restore WebGL context' : 'Context loss unavailable'; }
    else { renderer.restoreContext(lostExt); lostExt = null; $('h-lose').textContent = 'Lose WebGL context'; }
  };

  // Fit device to available stage at ordinary laptop heights.
  function fit() {
    const stage = $('stage'), w = state.layout === 'unfolded' ? 748 : 384;
    const dw = w + 24, dh = 832 + 24;
    let k;
    if (window.innerWidth <= 820) {
      // Narrow screens: fit by width and let the page scroll to the controls below.
      k = Math.min(1, (window.innerWidth - 16) / dw);
      stage.style.height = Math.ceil(dh * k + 16) + 'px';
    } else {
      stage.style.height = '';
      k = Math.min(1, (stage.clientHeight - 32) / dh, (stage.clientWidth - 32) / dw);
    }
    const kk = Math.max(0.3, k).toFixed(4);
    if ($('fit').style.getPropertyValue('--fit') !== kk) { $('fit').style.setProperty('--fit', kk); renderer.refresh(); }
  }
  window.addEventListener('resize', fit);
  // Glass sheen follows the pointer slightly, like light across a real display.
  let sheenRaf = 0;
  $('stage').addEventListener('pointermove', (e) => {
    if (reduced() || sheenRaf) return;
    sheenRaf = requestAnimationFrame(() => {
      sheenRaf = 0;
      const r = device.getBoundingClientRect();
      const x = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width - 0.5) * 2));
      const y = Math.max(-1, Math.min(1, ((e.clientY - r.top) / r.height - 0.5) * 2));
      const g = screen.querySelector('.glass'); g.style.setProperty('--sx', x.toFixed(3)); g.style.setProperty('--sy', y.toFixed(3));
    });
  });

  $('rose-stage').addEventListener('transitionend', () => renderer.refresh());
  window.addEventListener('pagehide', (e) => { if (!e.persisted) renderer.destroy(); });
  buildHome(); bindHomeGestures();
  renderer.attach($('slot-tray'));
  render(); startClock();
  window.__rose = { getState: () => state, dispatch, renderer, ctx };
})();
