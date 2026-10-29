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
    pages.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault(); dispatch({ type: 'homePage', value: state.homePage + (e.key === 'ArrowRight' ? 1 : -1) });
      const first = $('home-track').children[state.homePage].querySelector('.app'); if (first) first.focus({ preventScroll: true });
    });
  }

  // ---------- View transitions ----------
  let shownView = 'home';
  const viewEl = (v) => $('v-' + v);
  function centerOf(el) { const r = el.getBoundingClientRect(), s = screen.getBoundingClientRect(), k = s.width / screen.offsetWidth; return { x: (r.left + r.width / 2 - s.left) / k, y: (r.top + r.height / 2 - s.top) / k }; }
  function flyRose(toSlot) {
    const canvas = $('rose-canvas');
    const from = canvas.isConnected && canvas.parentElement !== park ? canvas.getBoundingClientRect() : null;
    if (toSlot) renderer.attach(toSlot); else { park.appendChild(canvas); park.appendChild($('rose-fallback')); }
    if (!toSlot || !from || !from.width || reduced()) return;
    const to = canvas.getBoundingClientRect();
    if (!to.width) return;
    const dx = (from.left + from.width / 2) - (to.left + to.width / 2), dy = (from.top + from.height / 2) - (to.top + to.height / 2);
    const k = screen.getBoundingClientRect().width / screen.offsetWidth;
    const s = from.width / to.width, base = 'translate(-50%,-50%)';
    const frames = [{ transform: base + ' translate(' + dx / k + 'px,' + dy / k + 'px) scale(' + s + ')' }, { transform: base }];
    for (const el of [canvas, $('rose-fallback')]) el.animate(frames, { duration: 560, easing: 'cubic-bezier(.2,.8,.2,1)' });
  }
  function showView(next) {
    const prev = shownView;
    if (prev === next) return;
    shownView = next;
    const a = viewEl(prev), b = viewEl(next);
    b.dataset.active = 'true';
    screen.dataset.view = next;
    flyRose(roseSlotFor(next));
    const done = () => { if (shownView !== prev) a.dataset.active = 'false'; };
    if (reduced()) { done(); return; }
    const ease = 'cubic-bezier(.2,.8,.2,1)';
    if (next === 'rose' && prev === 'home') {
      const c = centerOf($('tray-rose'));
      const bg = b.querySelector('.ivory-bg');
      b.style.zIndex = 3;
      bg.animate([{ clipPath: 'circle(0px at ' + c.x + 'px ' + c.y + 'px)' }, { clipPath: 'circle(1100px at ' + c.x + 'px ' + c.y + 'px)' }], { duration: 620, easing: ease });
      for (const el of b.querySelectorAll('.rose-head,.rose-caption,.rose-output')) el.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 420, delay: 180, easing: ease, fill: 'backwards' });
      for (const el of b.querySelectorAll('.tray .well')) el.animate([{ opacity: 0, transform: 'scale(.86)' }, { opacity: 1, transform: 'none' }], { duration: 360, delay: 120, easing: ease, fill: 'backwards' });
      later(() => { b.style.zIndex = ''; done(); }, 640);
    } else if (prev === 'rose' && next === 'home') {
      const c = centerOf($('tray-rose'));
      a.style.zIndex = 3;
      const bg = a.querySelector('.ivory-bg');
      bg.animate([{ clipPath: 'circle(1100px at ' + c.x + 'px ' + c.y + 'px)' }, { clipPath: 'circle(0px at ' + c.x + 'px ' + c.y + 'px)' }], { duration: 560, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' });
      for (const el of a.querySelectorAll('.rose-head,.rose-caption,.rose-output,.tray .well')) el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 220, easing: 'linear', fill: 'forwards' });
      later(() => { a.style.zIndex = ''; done(); for (const el of a.querySelectorAll('.ivory-bg,.rose-head,.rose-caption,.rose-output,.tray .well')) el.getAnimations().forEach((x) => x.cancel()); }, 580);
    } else {
      b.style.zIndex = 3;
      b.animate([{ opacity: 0, transform: 'scale(.985)' }, { opacity: 1, transform: 'none' }], { duration: 300, easing: ease });
      later(() => { b.style.zIndex = ''; done(); }, 310);
    }
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
    for (const n of state.notices) {
      if (shownNotices.has(n.id)) continue;
      const el = document.createElement(n.kind === 'task' ? 'button' : 'div');
      el.className = 'notice ' + n.kind;
      el.innerHTML = noticeIcon(n) + '<span>' + n.text + (n.kind === 'task' ? '<small>Tap to see Rose’s apps</small>' : '') + '</span>';
      if (n.kind === 'task') el.addEventListener('click', () => { dispatch({ type: 'dismissNotice', value: n.id }); dispatch({ type: 'agents' }); });
      host.appendChild(el); shownNotices.set(n.id, el);
      $('announcer').textContent = n.text;
      later(() => dispatch({ type: 'dismissNotice', value: n.id }), n.kind === 'task' ? 4200 : 2200);
    }
  }

  // ---------- Render ----------
  function render() {
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

    // Rose
    const mic = $('rose-mic');
    mic.innerHTML = state.muted ? I.UI.micOff : I.UI.mic;
    mic.dataset.muted = String(state.muted);
    mic.setAttribute('aria-pressed', String(state.muted));
    mic.setAttribute('aria-label', state.muted ? 'Microphone muted. Unmute' : 'Microphone on. Mute');
    mic.disabled = state.connection !== 'connected';
    $('rose-end').innerHTML = '<span class="end">' + I.UI.end + '</span>';
    $('rose-end').disabled = !callLive;
    $('rose-agents').innerHTML = I.UI.grid;
    const caption = state.connection === 'connecting' ? 'Connecting…' : state.connection === 'connected' ? (state.speaking ? 'Rose speaking' : state.muted ? 'Microphone muted' : 'Listening') : '';
    $('rose-caption').textContent = caption;
    renderResult();
    renderNotices();

    // Lock
    $('lock').hidden = !state.locked;
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
      if (fx.type === 'reset') { epoch++; for (const t of timers) clearTimeout(t); timers.clear(); startClock(); }
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
  $('rose-mic').addEventListener('click', () => dispatch({ type: 'mute' }));
  $('rose-end').addEventListener('click', () => { dispatch({ type: 'endCall' }); if (state.view === 'home') $('tray-rose').focus({ preventScroll: true }); });
  $('rose-agents').addEventListener('click', () => dispatch({ type: 'agents' }));
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
    const k = Math.min(1, (stage.clientHeight - 32) / dh, (stage.clientWidth - 32) / dw);
    $('fit').style.setProperty('--fit', Math.max(0.3, k).toFixed(4));
  }
  window.addEventListener('resize', fit);

  buildHome(); bindHomeGestures();
  renderer.attach($('slot-tray'));
  render(); startClock();
  window.__rose = { getState: () => state, dispatch, renderer, ctx };
})();
