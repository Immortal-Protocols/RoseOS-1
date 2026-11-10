// Rose HiFi — switcher, Rose's apps (agent windows), window detail, human app views
// and the task-specific Stop confirmation. Rendering only; all state is in RoseModel.
(function () {
  'use strict';
  let ctx = null, built = false;
  const LABEL = { idle: 'Not started', running: 'Working', attention: 'Needs your choice', stopping: 'Stopping…', stopped: 'Stopped', done: 'Done' };
  const SHORT = Object.assign({}, LABEL, { attention: 'Choose' });
  const OBSERVE = {
    running: 'You’re watching. Rose keeps working in this window.',
    attention: 'Rose is waiting for your choice before continuing.',
    stopping: 'Rose is stopping this task.',
    stopped: 'Rose has stopped working here.',
    done: 'Rose finished this task.',
    idle: 'Rose hasn’t started here.',
  };
  // The pending decision for a task that needs attention (simulated).
  const CHOICES = {
    saturday: { q: 'Which time works for the garden?', a: ['9:00 · quieter', '11:00 · after coffee'] },
    reply: { q: 'Where should you meet Jamie?', a: ['Garden gate', 'Riverside café'] },
    compare: { q: 'Which matters more?', a: ['Quiet seating', 'Shorter walk'] },
  };
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------- Procedural app content (shared by cards, windows and app views) ----------
  // Procedural garden/riverside "photos": layered SVG with soft gradients. Original.
  const SCENES = [
    { sky: ['#9cc9f2', '#e6f2fb'], far: '#8fb3a0', mid: '#5f9a5c', near: '#3f7b42', water: false, path: true, flowers: '#ef8fb0', trees: [[18, 46, 9], [80, 44, 11]] },
    { sky: ['#a9d1f4', '#eef6fc'], far: '#9db9ab', mid: '#6aa25f', near: '#4b8a4a', water: true, path: false, flowers: null, trees: [[12, 42, 8], [30, 44, 6], [88, 41, 10]] },
    { sky: ['#b3d6f2', '#f3f8fc'], far: '#a5bfb0', mid: '#6f9f63', near: '#3e7342', water: false, path: true, flowers: '#b391e3', trees: [[50, 38, 14]] },
    { sky: ['#8fc0ec', '#e3f0fa'], far: '#93b4a6', mid: '#5b955a', near: '#447f46', water: true, path: false, flowers: '#f3c25d', trees: [[70, 43, 9], [86, 45, 7]] },
    { sky: ['#a2cdf3', '#eaf4fb'], far: '#9ab8a5', mid: '#64a05e', near: '#3a7440', water: false, path: false, flowers: '#ec7a8f', trees: [[20, 40, 12], [62, 44, 8]] },
    { sky: ['#b8d9f3', '#f5f9fd'], far: '#a7c1b2', mid: '#73a668', near: '#4a8549', water: true, path: true, flowers: null, trees: [[8, 44, 7], [92, 42, 9]] },
  ];
  function landscape(i) {
    const s = SCENES[i % SCENES.length], id = 'g' + i;
    let svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 75" preserveAspectRatio="xMidYMid slice">' +
      '<defs><linearGradient id="' + id + 's" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + s.sky[0] + '"/><stop offset="1" stop-color="' + s.sky[1] + '"/></linearGradient>' +
      '<linearGradient id="' + id + 'n" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + s.mid + '"/><stop offset="1" stop-color="' + s.near + '"/></linearGradient></defs>' +
      '<rect width="100" height="75" fill="url(#' + id + 's)"/>' +
      '<ellipse cx="30" cy="14" rx="14" ry="3.2" fill="#fff" opacity=".7"/><ellipse cx="74" cy="10" rx="10" ry="2.4" fill="#fff" opacity=".55"/>' +
      '<path d="M0 40 C18 31 32 35 50 33 S82 29 100 36 V75 H0Z" fill="' + s.far + '" opacity=".85"/>' +
      '<path d="M0 46 C20 40 40 44 60 42 S88 40 100 44 V75 H0Z" fill="url(#' + id + 'n)"/>';
    for (const [x, y, r] of s.trees) svg += '<ellipse cx="' + x + '" cy="' + (y - r * 0.6) + '" rx="' + r * 0.62 + '" ry="' + r + '" fill="#2f5e35"/><ellipse cx="' + (x - r * 0.18) + '" cy="' + (y - r * 0.9) + '" rx="' + r * 0.3 + '" ry="' + r * 0.5 + '" fill="#4e8a4f" opacity=".7"/>';
    if (s.water) svg += '<path d="M-5 58 C25 50 45 62 70 54 S100 52 105 55 V64 C80 60 60 70 35 64 S5 66 -5 66Z" fill="#8cc3ea"/><path d="M10 58 C25 55 40 60 55 57" stroke="#fff" stroke-width=".6" opacity=".6" fill="none"/>';
    if (s.path) svg += '<path d="M44 75 C48 64 54 56 62 48 L64 48 C60 57 58 66 60 75Z" fill="#d8c6a2"/>';
    if (s.flowers) for (let k = 0; k < 26; k++) { const fx = (k * 37 + i * 11) % 100, fy = 60 + ((k * 23 + i * 7) % 14); svg += '<circle cx="' + fx + '" cy="' + fy + '" r="' + (0.9 + (k % 3) * 0.35) + '" fill="' + s.flowers + '" opacity=".9"/>'; }
    svg += '</svg>';
    return "url('data:image/svg+xml," + encodeURIComponent(svg) + "') center/cover";
  }
  const PHOTO = SCENES.map((_, i) => landscape(i));
  const photo = (i, cls) => '<i class="ph ' + (cls || '') + '" style="background:' + PHOTO[i % PHOTO.length] + '"></i>';
  function typed(text, step, per) { return esc(text.slice(0, Math.max(0, Math.min(text.length, step * per)))); }

  function content(kind, step, opts) {
    const live = opts && opts.live;
    const caret = live ? '<b class="caret" aria-hidden="true"></b>' : '';
    switch (kind) {
      case 'browser': {
        const q = 'botanic garden opening hours';
        return '<div class="c-browser"><div class="url">' + (step ? typed(q, step, 6) + caret : 'Search or enter address') + '</div>' +
          photo(0, 'hero') + '<h4>' + (step < 3 ? 'Search' : 'Botanic Garden') + '</h4>' +
          (step >= 3 ? '<p>Open daily · 9:00 – 17:00</p>' : '') + (step >= 5 ? '<p>Rose garden · lakeside path</p>' : '') +
          (live && step < 8 ? '<span class="pointer" style="left:' + (30 + (step % 3) * 18) + '%;top:' + (54 + (step % 2) * 14) + '%"></span>' : '') + '</div>';
      }
      case 'maps': {
        const dash = Math.max(0, 1 - step / 8);
        // Portrait-authored map so the destination pin survives every window crop.
        return '<div class="c-maps"><svg viewBox="0 0 120 150" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><rect x="-200" y="-200" width="520" height="550" fill="#eef2ea"/>' +
          '<path d="M-200 40h520M-200 92h520M-200 128h520M-40 -200v550M24 -200v550M70 -200v550M104 -200v550M150 -200v550" stroke="#dde3d8" stroke-width="3.2"/>' +
          '<path d="M-200 170C-80 150 -20 130 10 116C28 104 32 92 44 78s40-30 50-46 30-40 60-46 80-20 160-30" stroke="#9cc6ef" stroke-width="15" fill="none"/>' +
          '<rect x="76" y="100" width="30" height="24" rx="5" fill="#cfe5c4"/><rect x="8" y="16" width="26" height="18" rx="4" fill="#cfe5c4"/>' +
          '<path class="route" d="M30 132C38 120 46 112 56 104s14-22 22-36 8-24 12-32" pathLength="1" stroke="#2f6be6" stroke-width="3.4" stroke-linecap="round" stroke-dasharray="0.04 0.03" fill="none" style="opacity:' + (step ? 1 : 0) + ';clip-path:inset(' + (dash * 100).toFixed(0) + '% 0 0 0)"/>' +
          '<circle cx="30" cy="132" r="5" fill="#2f6be6" stroke="#fff" stroke-width="2"/>' +
          '<path d="M90 22a7 7 0 0 1 7 7c0 5-7 12-7 12s-7-7-7-12a7 7 0 0 1 7-7z" fill="#e5533e"/><circle cx="90" cy="29" r="2.4" fill="#fff"/></svg>' +
          '<span class="pill">' + (step >= 4 ? '12 min walk' : 'Finding route') + '</span></div>';
      }
      case 'notes': {
        const lines = ['Garden at 9:00', 'Riverside walk', 'Bring a book', 'Leave the afternoon open'];
        return '<div class="c-notes"><h4>Saturday</h4>' + lines.map((l, i) => '<p><i class="box"></i>' + typed(l, step - i * 1.5, 7) + (live && Math.floor(step - i * 1.5) > 0 && step - i * 1.5 < 4 ? caret : '') + '</p>').join('') + '</div>';
      }
      case 'calendar': {
        const blocks = [[1, 1, 2, '#c9dbff'], [2, 3, 1, '#d4f0d8'], [1, 4, 2, '#c9dbff'], [3, 2, 1, '#fde7b0'], [2, 5, 1, '#d4f0d8']];
        return '<div class="c-cal"><div class="cal-head"><b>Sat 31</b><span>Oct</span></div><div class="cal-grid">' +
          blocks.slice(0, Math.min(blocks.length, Math.ceil(step / 1.6))).map((b) => '<i style="grid-column:' + (b[0] + 1) + ';grid-row:' + b[1] + ' / span ' + b[2] + ';background:' + b[3] + '"></i>').join('') + '</div></div>';
      }
      case 'mail': {
        const body = 'Hi Jamie — Saturday works for me. Shall we meet at the garden gate at ten? We could walk along the river after.';
        return '<div class="c-mail"><p class="to"><span>To</span> Jamie</p><p class="subj">Re: Saturday</p><p class="body">' + typed(body, step, 15) + caret + '</p><span class="pill">Draft · not sent</span></div>';
      }
      case 'files': {
        const f = ['Weekend ideas.md', 'Places.md', 'Garden map.pdf', 'Café notes.md'];
        return '<div class="c-files">' + f.map((n, i) => '<p class="' + (i < Math.ceil(step / 2) ? 'hit' : '') + '"><i></i>' + esc(n) + '</p>').join('') + '</div>';
      }
      case 'editor': {
        const code = ['const cafes = [', '  { name: "Fern House", seats: 40 },', '  { name: "Harbor Cup", seats: 18 },', '];', 'quietest(cafes) // Fern House'];
        return '<div class="c-editor">' + code.map((l, i) => '<p>' + typed(l, step - i, 12) + '</p>').join('') + caret + '</div>';
      }
      case 'photos': return '<div class="c-photos">' + [0, 1, 2, 3, 4, 5].map((i) => photo(i)).join('') + '</div>';
      case 'camera': return '<div class="c-camera"><i class="vf"></i><span class="shutter"></span></div>';
      case 'settings': return '<div class="c-list">' + ['Network & internet', 'Connected devices', 'Display', 'Sound & vibration', 'Privacy', 'Rose'].map((x) => '<p>' + x + '<span>›</span></p>').join('') + '</div>';
      case 'clock': return '<div class="c-big"><b>9:41</b><span>Thursday, October 29</span></div>';
      case 'weather': return '<div class="c-big"><b>18°</b><span>Clear · light wind</span></div>';
      case 'messages': return '<div class="c-msg"><p class="in">Saturday still good?</p><p class="out">Yes — garden at ten?</p><p class="in">Perfect.</p></div>';
      case 'music': return '<div class="c-music">' + photo(4, 'art') + '<b>Morning Walk</b><span>Quiet Tracks</span></div>';
      case 'calculator': return '<div class="c-big"><b>42</b><span>6 × 7</span></div>';
      case 'contacts': return '<div class="c-list">' + ['Alex Rivera', 'Jamie Chen', 'Morgan Lee', 'Sam Ortiz'].map((x) => '<p>' + x + '</p>').join('') + '</div>';
      case 'phone': return '<div class="c-list">' + ['Jamie Chen · yesterday', 'Morgan Lee · Tuesday', 'Garden café · Monday'].map((x) => '<p>' + x + '</p>').join('') + '</div>';
      case 'media': return '<div class="c-music">' + photo(1, 'art') + '<b>Riverside.mp4</b><span>2:14 / 6:30</span></div>';
      default: return '<div class="c-list"><p>Ready</p></div>';
    }
  }
  const kindOf = (appId) => ctx.M.APPS[appId].kind;
  const windowContent = (wid, s, live) => {
    const w = ctx.M.WINDOWS[wid], t = s.tasks[w.task];
    return content(kindOf(w.app), t.state === 'idle' ? 0 : t.step, { live: live && t.state === 'running' && !ctx.reduced() });
  };

  // ---------- Shared pieces ----------
  function stopButton(taskId, s, extra) {
    const t = s.tasks[taskId], title = ctx.M.TASKS[taskId].title;
    const can = ctx.M.STOPPABLE.includes(t.state);
    return '<button class="stop-btn ' + (extra || '') + '" data-stop="' + taskId + '" ' + (can ? '' : 'disabled') + ' aria-label="Stop ' + esc(title) + '">' + ctx.I.UI.stop + '</button>';
  }
  const dot = (state) => '<span class="sdot" data-state="' + state + '" aria-hidden="true"></span>';
  function windowCard(wid, s, cls) {
    const w = ctx.M.WINDOWS[wid], t = s.tasks[w.task], app = ctx.M.APPS[w.app];
    return '<article class="wcard ' + (cls || '') + '" data-window="' + wid + '" data-state="' + t.state + '">' +
      '<button class="wopen" data-open-window="' + wid + '" aria-label="Inspect ' + esc(app.name + ' — ' + w.label + ', ' + ctx.M.TASKS[w.task].title + ', ' + LABEL[t.state]) + '"></button>' +
      '<header><span class="mini-ico">' + ctx.I.appArt(w.app) + '</span><span class="wl">' + esc(w.label) + '</span></header>' +
      '<div class="wbody" aria-hidden="true">' + windowContent(wid, s, true) + '</div>' +
      '<footer>' + dot(t.state) + '<span class="wstate">' + esc(SHORT[t.state]) + '</span>' + stopButton(w.task, s) + '</footer>' +
      '<span class="active-edge" aria-hidden="true"></span></article>';
  }
  function visibleWindows(s) { return ctx.M.WINDOW_ORDER.filter((wid) => s.tasks[ctx.M.WINDOWS[wid].task].state !== 'idle'); }

  // ---------- Build static scaffolds once ----------
  function build() {
    const { $ } = ctx;
    $('v-switcher').innerHTML =
      '<div class="wall" aria-hidden="true"></div><div class="sw-veil" aria-hidden="true"></div>' +
      '<div class="sw-human" id="sw-human" role="list" aria-label="Your apps" tabindex="0"></div>' +
      '<svg class="links" id="sw-links" aria-hidden="true"><defs><linearGradient id="link-grad" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff8fa3"/><stop offset=".45" stop-color="#9fb8ff"/><stop offset="1" stop-color="#5d8dff"/></linearGradient></defs><g id="sw-routes"></g></svg>' +
      '<div class="sw-link"><div class="sw-rose"><span class="sw-halo" aria-hidden="true"></span><div class="rose-slot" id="slot-switcher"></div></div>' +
      '<span class="sw-link-label" id="sw-link-label"></span></div>' +
      '<div class="sw-agent" id="sw-agent" role="list" aria-label="Rose’s background apps" tabindex="0"></div>';
    $('v-agents').innerHTML =
      '<div class="ivory-bg" aria-hidden="true"></div><header class="ag-head"><span class="ag-pearl" aria-hidden="true"></span><button class="back-btn" id="ag-head-back" aria-label="Back to Rose">' + ctx.I.UI.back + '</button></header><div class="surface agents-surface"><h2 class="sr">Rose’s apps</h2><div class="agrid" id="agrid"></div><p class="a-empty" id="a-empty">Rose isn’t using any apps right now.</p></div>' +
      '<div class="tray" role="toolbar" aria-label="Rose’s apps controls"><button class="well" id="ag-back" aria-label="Back">' + ctx.I.UI.back + '</button><button class="well" id="ag-mid"></button><span class="well selected" id="ag-self" role="img" aria-label="Rose’s apps, current screen">' + ctx.I.UI.grid + '</span></div>';
    $('v-window').innerHTML = '<div class="ivory-bg" aria-hidden="true"></div><div class="surface detail" id="wdetail"></div>';
    $('v-app').innerHTML = '<div class="ivory-bg" aria-hidden="true"></div><div class="surface detail" id="appdetail"></div>';

    $('ag-back').addEventListener('click', () => ctx.dispatch({ type: 'back' }));
    $('ag-head-back').addEventListener('click', () => ctx.dispatch({ type: 'back' }));
    $('ag-mid').addEventListener('click', () => { const s = ctx.getState(); ctx.dispatch({ type: s.connection === 'disconnected' ? 'openRose' : 'endCall' }); });
    document.addEventListener('click', onClick);
    // The app's own bar works like an app's: tabs switch, the composer sends.
    $('appdetail').addEventListener('click', (e) => {
      const tab = e.target.closest('.ab-tab'); if (!tab) return;
      tab.parentElement.querySelectorAll('.ab-tab').forEach((t) => t.setAttribute('aria-selected', String(t === tab)));
    });
    $('appdetail').addEventListener('submit', (e) => {
      e.preventDefault();
      const f = e.target.closest('[data-composer]'), input = f && f.querySelector('.ab-input'), msg = f && f.parentElement.querySelector('.c-msg');
      if (!input || !msg || !input.value.trim()) return;
      const p = document.createElement('p'); p.className = 'out'; p.textContent = input.value.trim(); msg.appendChild(p);
      input.value = ''; msg.scrollTop = msg.scrollHeight;
    });
    for (const id of ['sw-human', 'sw-agent']) bindCarousel($(id), id === 'sw-human' ? 'human' : 'agent');
    $('sw-agent').addEventListener('scroll', () => requestAnimationFrame(routeWire), { passive: true });
    $('v-switcher').addEventListener('click', (e) => { if (e.target.classList.contains('sw-veil') || e.target.classList.contains('wall')) ctx.dispatch({ type: 'back' }); });
    built = true;
  }

  function onClick(e) {
    const s = ctx.getState();
    if (s.locked) return;
    const stop = e.target.closest('[data-stop]');
    if (stop && !stop.disabled) { e.stopPropagation(); lastStopButton = stop.dataset.stop; const wc = stop.closest('[data-window]'); lastStopWindow = wc ? wc.dataset.window : null; ctx.dispatch({ type: 'askStop', id: stop.dataset.stop }); return; }
    const w = e.target.closest('[data-open-window]');
    if (w) { ctx.dispatch({ type: 'openWindow', value: w.dataset.openWindow }); return; }
    const h = e.target.closest('[data-open-app]');
    if (h) { ctx.dispatch({ type: 'openApp', value: h.dataset.openApp }); return; }
    if (e.target.closest('[data-back]')) { ctx.dispatch({ type: 'back' }); return; }
    const choose = e.target.closest('[data-choose]');
    if (choose) { ctx.dispatch({ type: 'taskEvent', id: choose.dataset.choose, value: 'resume' }); return; }
    if (e.target.closest('[data-confirm-stop]')) { ctx.dispatch({ type: 'confirmStop' }); return; }
    if (e.target.closest('[data-cancel-stop]')) { ctx.dispatch({ type: 'cancelStop' }); return; }
  }

  // Independent carousels: native scroll + snap; mouse drag; keyboard arrows.
  function bindCarousel(el, which) {
    let drag = null, block = false, raf = 0;
    el.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse' || e.button !== 0 || e.target.closest('.stop-btn')) return; drag = { x: e.clientX, left: el.scrollLeft, id: e.pointerId, moved: false }; });
    el.addEventListener('pointermove', (e) => {
      if (!drag) return; const dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) > 6) { drag.moved = true; block = true; el.setPointerCapture(drag.id); el.classList.add('dragging'); }
      if (drag.moved) el.scrollLeft = drag.left - dx;
    });
    const end = () => { if (!drag) return; const moved = drag.moved; drag = null; el.classList.remove('dragging'); if (moved) snap(el); };
    el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
    el.addEventListener('click', (e) => { if (block) { e.stopPropagation(); e.preventDefault(); block = false; } }, true);
    el.addEventListener('keydown', (e) => {
      if (e.target !== el || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
      e.preventDefault(); step(el, e.key === 'ArrowRight' ? 1 : -1);
    });
    el.addEventListener('scroll', () => {
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; depth(el); const i = centerIndex(el); const s = ctx.getState(); const key = which === 'agent' ? 'agentCarousel' : 'humanCarousel'; if (i !== s[key]) ctx.dispatch({ type: 'carousel', which, value: i }); });
    }, { passive: true });
  }
  const cards = (el) => [...el.children].filter((c) => !c.hidden);
  // Human cards snap to the centre; agent cards snap to the leading edge.
  // Classic: agent cards start-aligned. Oxblood (study 13): both carousels are centred fans.
  const oxTheme = () => document.getElementById('device').dataset.theme === 'oxblood';
  const startAligned = (el) => el.classList.contains('sw-agent') && !oxTheme();
  function anchor(el, card) {
    if (startAligned(el)) return card.offsetLeft - parseFloat(getComputedStyle(el).paddingLeft);
    return card.offsetLeft + card.offsetWidth / 2 - el.clientWidth / 2;
  }
  function centerIndex(el) {
    let best = 0, d = Infinity;
    cards(el).forEach((card, i) => { const m = Math.abs(anchor(el, card) - el.scrollLeft); if (m < d) { d = m; best = i; } });
    return best;
  }
  function scrollToIndex(el, i, instant) {
    const card = cards(el)[i]; if (!card) return;
    const left = anchor(el, card), smooth = !(instant || ctx.reduced());
    el.scrollTo({ left, behavior: smooth ? 'smooth' : 'instant' });
    // A re-render during a smooth scroll can cancel it part-way; land exactly on the card.
    if (smooth) setTimeout(() => { if (Math.abs(el.scrollLeft - left) > 2 && Math.abs(el.scrollLeft - left) < el.scrollWidth) el.scrollTo({ left, behavior: 'instant' }); }, 650);
  }
  function step(el, dir) { scrollToIndex(el, Math.max(0, Math.min(cards(el).length - 1, centerIndex(el) + dir))); }
  function snap(el) { scrollToIndex(el, centerIndex(el)); }
  // Side cards turn away slightly, like the reference; transform-only for performance.
  function depth(el) {
    const ox = oxTheme();
    if (!el.classList.contains('sw-human') && !ox) return;
    const c = el.scrollLeft + el.clientWidth / 2;
    const agent = el.classList.contains('sw-agent');
    for (const card of cards(el)) {
      const off = (card.offsetLeft + card.offsetWidth / 2 - c) / card.offsetWidth;
      const k = Math.max(-1.4, Math.min(1.4, off));
      if (ox) {
        // Study 13 fan: neighbours tuck behind the centre card and turn strongly away. Static
        // geometry, so it stays under reduced motion.
        const t = agent ? [40, 40, 0.2] : [62, 44, 0.16];
        card.style.transform = 'perspective(760px) translateX(' + (-k * t[0]).toFixed(1) + 'px) rotateY(' + (-k * t[1]).toFixed(1) + 'deg) scale(' + (1 - Math.min(1, Math.abs(k)) * t[2]).toFixed(3) + ')';
      } else card.style.transform = ctx.reduced() ? '' : 'perspective(900px) rotateY(' + (-k * 22).toFixed(1) + 'deg) scale(' + (1 - Math.min(1, Math.abs(k)) * 0.12).toFixed(3) + ')';
      card.style.zIndex = String(10 - Math.round(Math.abs(k) * 4));
    }
  }

  // Connector: one route from Rose to every visible working card. Light packets flow
  // along each route (CSS dash animation, no JS loop) and a ring pulses where it lands.
  // Nothing moves unless a task is actually running.
  let lastRouteKey = '';
  function routeWire() {
    const { $ } = ctx;
    const view = $('v-switcher'), svg = $('sw-links'), g = $('sw-routes');
    if (!svg || !view.isConnected || view.dataset.active !== 'true') return;
    const vr = view.getBoundingClientRect();
    if (!vr.width) return;
    const k = vr.width / view.offsetWidth;
    const W = view.offsetWidth, H = view.offsetHeight;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    const rr = view.querySelector('.sw-rose').getBoundingClientRect(), ar = $('sw-agent').getBoundingClientRect();
    const ox = (rr.right - vr.left) / k - 10, oy = (rr.top + rr.height / 2 - vr.top) / k;
    const cards = [...$('sw-agent').querySelectorAll('.wcard[data-state=running]')].map((c) => c.getBoundingClientRect())
      .filter((r) => r.right > ar.left + 12 && r.left < ar.right - 12).slice(0, 3);
    const pts = cards.map((r) => ({ x: (Math.max(ar.left + 20, Math.min(ar.right - 20, r.left + r.width / 2)) - vr.left) / k, y: (r.top - vr.top) / k + 1 }));
    const key = pts.map((p) => p.x.toFixed(0) + ',' + p.y.toFixed(0)).join('|') + W;
    if (key === lastRouteKey) return;
    lastRouteKey = key;
    const grad = $('link-grad'); grad.setAttribute('x1', ox); grad.setAttribute('x2', W - 20);
    if (!pts.length) { g.innerHTML = '<path class="track idle" d="M' + ox + ' ' + oy + 'h48"/>'; return; }
    g.innerHTML = pts.map((p, i) => {
      // Horizontal run out of Rose, then a smooth bend down onto the card.
      const bend = Math.max(ox + 30, p.x - 34);
      const d = 'M' + ox.toFixed(1) + ' ' + oy.toFixed(1) + 'H' + bend.toFixed(1) + 'C' + (bend + 26).toFixed(1) + ' ' + oy.toFixed(1) + ' ' + p.x.toFixed(1) + ' ' + (oy + 8).toFixed(1) + ' ' + p.x.toFixed(1) + ' ' + p.y.toFixed(1);
      const delay = (-i * 0.37).toFixed(2) + 's';
      return '<path class="track" d="' + d + '"/>' +
        '<path class="glow" pathLength="100" style="animation-delay:' + delay + '" d="' + d + '"/>' +
        '<path class="pulse" pathLength="100" style="animation-delay:' + delay + '" d="' + d + '"/>' +
        '<path class="comet" pathLength="100" style="animation-delay:' + (-i * 0.6).toFixed(2) + 's" d="' + d + '"/>' +
        '<circle class="land" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="5"/><circle class="land-ring" style="animation-delay:' + delay + '" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="5"/>';
    }).join('');
  }
  window.addEventListener('resize', () => { lastRouteKey = ''; });

  // ---------- Renderers ----------
  let lastStopWindow = null, lastViewSeen = 'home';
  let lastHumanKey = '', lastAgentKey = '', lastGridKey = '', lastWindowKey = '', lastAppKey = '', lastStopButton = null, lastSheet = null;
  function renderSwitcher(s) {
    const { $ } = ctx;
    const hk = s.recentApps.join(',');
    const human = $('sw-human');
    if (hk !== lastHumanKey) {
      lastHumanKey = hk;
      human.innerHTML = s.recentApps.map((id) => '<div class="hcard" role="listitem"><button class="hopen" data-open-app="' + id + '" aria-label="Open ' + esc(ctx.M.APPS[id].name) + '"></button><header><span class="mini-ico">' + ctx.I.appArt(id) + '</span><span>' + esc(ctx.M.APPS[id].name) + '</span></header><div class="hbody" aria-hidden="true">' + content(kindOf(id), 8) + '</div></div>').join('');
      requestAnimationFrame(() => { scrollToIndex(human, Math.min(s.humanCarousel, s.recentApps.length - 1), true); depth(human); });
    }
    const vis = visibleWindows(s);
    const agent = $('sw-agent');
    const ak = vis.map((w) => w + ':' + s.tasks[ctx.M.WINDOWS[w].task].state + ':' + s.tasks[ctx.M.WINDOWS[w].task].step).join('|') + ctx.reduced();
    if (ak !== lastAgentKey) {
      const keepFocus = agent.contains(document.activeElement) ? document.activeElement.getAttribute('data-stop') || document.activeElement.getAttribute('data-open-window') : null;
      lastAgentKey = ak;
      // Same windows in the same order: refresh each card in place so an in-flight scroll/snap is
      // not interrupted by replacing the carousel's children.
      const items = [...agent.querySelectorAll(':scope > .aitem')];
      if (vis.length && items.length === vis.length && items.every((it, i) => it.firstElementChild && it.firstElementChild.dataset.window === vis[i])) {
        items.forEach((it, i) => { it.innerHTML = windowCard(vis[i], s, 'small'); });
      } else agent.innerHTML = vis.length ? vis.map((w) => '<div role="listitem" class="aitem">' + windowCard(w, s, 'small') + '</div>').join('') : '<p class="sw-empty">No background work</p>';
      if (keepFocus) { const el = agent.querySelector('[data-stop="' + keepFocus + '"],[data-open-window="' + keepFocus + '"]'); if (el) el.focus({ preventScroll: true }); }
      requestAnimationFrame(() => { if (s.view === 'switcher' && !agent.dataset.placed) { scrollToIndex(agent, Math.min(oxTheme() && s.agentCarousel === 0 && vis.length >= 3 ? 1 : s.agentCarousel, vis.length - 1), true); agent.dataset.placed = '1'; } depth(agent); });
    }
    requestAnimationFrame(routeWire);
    const running = ctx.M.taskActivity(s).length;
    $('v-switcher').dataset.active_work = running ? 'true' : 'false';
    $('v-switcher').classList.toggle('working', running > 0 && !ctx.reduced());
    $('sw-link-label').textContent = running ? running + (running === 1 ? ' task' : ' tasks') + ' in progress' : vis.length ? 'No active work' : '';
  }

  function renderAgents(s) {
    const { $ } = ctx;
    const vis = visibleWindows(s);
    const key = vis.map((w) => w + ':' + s.tasks[ctx.M.WINDOWS[w].task].state + ':' + s.tasks[ctx.M.WINDOWS[w].task].step).join('|') + s.layout + ctx.reduced();
    if (key !== lastGridKey) {
      const grid = $('agrid');
      const focusKey = grid.contains(document.activeElement) ? (document.activeElement.dataset.stop ? 's:' + document.activeElement.closest('.wcard').dataset.window : 'o:' + document.activeElement.dataset.openWindow) : null;
      lastGridKey = key;
      grid.innerHTML = vis.map((w) => windowCard(w, s)).join('');
      $('a-empty').hidden = vis.length > 0;
      if (focusKey) { const [k, w] = focusKey.split(':'); const card = grid.querySelector('[data-window="' + w + '"]'); const el = card && card.querySelector(k === 's' ? '[data-stop]' : '[data-open-window]'); if (el && !el.disabled) el.focus({ preventScroll: true }); else if (card) card.querySelector('[data-open-window]').focus({ preventScroll: true }); }
    }
    const mid = $('ag-mid'), live = s.connection !== 'disconnected';
    mid.innerHTML = live ? '<span class="end">' + ctx.I.UI.end + '</span>' : '<span class="rose-dot">' + ctx.I.UI.rose + '</span>';
    mid.setAttribute('aria-label', live ? 'End call' : 'Open Rose');
  }

  function renderWindow(s) {
    const { $ } = ctx;
    if (!s.window) return;
    const w = ctx.M.WINDOWS[s.window], t = s.tasks[w.task], app = ctx.M.APPS[w.app];
    const key = s.window + t.state + t.step + ctx.reduced();
    if (key === lastWindowKey) return;
    const act = document.activeElement;
    const refocus = $('wdetail').contains(act) ? (act.matches('[data-stop]') ? 'stop' : act.matches('[data-choose]') ? 'choose' : 'back') : null;
    lastWindowKey = key;
    const siblings = ctx.M.TASKS[w.task].windows.map((x) => ctx.M.APPS[ctx.M.WINDOWS[x].app].name).join(', ');
    $('wdetail').innerHTML =
      '<button class="back-btn" data-back aria-label="Back">' + ctx.I.UI.back + '</button>' +
      '<div class="app-head"><span class="tile">' + ctx.I.appArt(w.app) + '</span><div><h2>' + esc(app.name) + '</h2><p class="sub">' + esc(w.label) + '</p></div></div>' +
      '<div class="detail-frame" data-state="' + t.state + '">' + windowContent(s.window, s, true) + '</div>' +
      '<section class="task-strip"><div>' + dot(t.state) + '<b>' + esc(ctx.M.TASKS[w.task].title) + '</b><span>' + esc(LABEL[t.state]) + ' · uses ' + esc(siblings) + '</span></div>' + stopButton(w.task, s, 'wide') + '</section>' +
      (t.state === 'attention' ? '<section class="choice" aria-label="Rose needs your choice"><p>' + esc(CHOICES[w.task].q) + '</p><div>' + CHOICES[w.task].a.map((x) => '<button class="sbtn" data-choose="' + w.task + '">' + esc(x) + '</button>').join('') + '</div></section>' : '') +
      '<p class="observe">' + esc(OBSERVE[t.state]) + '</p>';
    if (refocus) {
      const d = $('wdetail'), stopB = d.querySelector('[data-stop]'), choose = d.querySelector('[data-choose]');
      const el = refocus === 'back' ? d.querySelector('.back-btn') : refocus === 'choose' && choose ? choose : !stopB.disabled ? stopB : d.querySelector('.back-btn');
      el.focus({ preventScroll: true });
    }
  }

  function renderApp(s) {
    const { $ } = ctx;
    if (!s.app || s.app === lastAppKey) return;
    lastAppKey = s.app;
    const app = ctx.M.APPS[s.app];
    $('appdetail').innerHTML =
      '<button class="back-btn" data-back aria-label="Back">' + ctx.I.UI.back + '</button>' +
      '<div class="app-head"><span class="tile">' + ctx.I.appArt(s.app) + '</span><h2>' + esc(app.name) + '</h2></div>' +
      '<div class="detail-frame human"><div class="app-body">' + content(app.kind, 8) + '</div>' + appBar(app.kind) + '</div>';
  }

  // Each app's own bottom bar (tab bar, composer or search field). When the system edge rises, the
  // app window gets shorter and this bar rides up with its bottom; the app keeps working normally.
  const TABS = {
    browser: ['Home', 'Tabs', 'Saved', 'Menu'], mail: ['Inbox', 'Drafts', 'Sent', 'Search'],
    photos: ['Library', 'Albums', 'Search'], music: ['Listen', 'Library', 'Search'], media: ['Videos', 'Library', 'Search'],
    clock: ['Alarm', 'Clock', 'Timer', 'Stopwatch'], phone: ['Favourites', 'Recents', 'Contacts', 'Keypad'],
    contacts: ['Contacts', 'Groups', 'Search'], calendar: ['Today', 'Calendars', 'Inbox'], files: ['Recent', 'Browse', 'Shared'],
    notes: ['All notes', 'Folders', 'New'], weather: ['Today', 'Week', 'Map'],
  };
  const TAB_ON = { clock: 1, phone: 1 };
  function appBar(kind) {
    // Messages uses the system keyboard (system.js): a custom-rendered field, never a native input.
    if (kind === 'messages') return '<div class="app-bar composer" data-composer><button type="button" class="ab-plus" aria-label="Add attachment" tabindex="-1">+</button><div class="field ab-field" id="f-msg" role="textbox" aria-label="Message" aria-multiline="false" tabindex="0" data-placeholder="Message"></div><button type="button" class="ab-send" id="ab-send" aria-label="Send" disabled>↑</button></div>';
    if (kind === 'maps') return '<div class="app-bar composer"><input type="search" class="ab-input" placeholder="Search Maps" aria-label="Search Maps" autocomplete="off"></div>';
    const tabs = TABS[kind];
    if (!tabs) return '';
    const on = TAB_ON[kind] || 0;
    return '<nav class="app-bar tabs" role="tablist" aria-label="App sections">' + tabs.map((t, i) => '<button type="button" role="tab" class="ab-tab" aria-selected="' + (i === on) + '"><i aria-hidden="true"></i><span>' + esc(t) + '</span></button>').join('') + '</nav>';
  }

  function renderSheet(s) {
    const host = ctx.$('sheet-host');
    if (!s.pendingStop) {
      if (lastSheet) {
        host.replaceChildren(); lastSheet = null;
        const view = document.querySelector('.view[data-active=true]:not([id=v-home])') || document.querySelector('.view[data-active=true]');
        const card = lastStopWindow && view && view.querySelector('[data-window="' + lastStopWindow + '"]');
        const target = (card && (card.querySelector('[data-stop]:not(:disabled)') || card.querySelector('[data-open-window]'))) ||
          (view && (view.querySelector('[data-stop="' + lastStopButton + '"]:not(:disabled)') || view.querySelector('.back-btn,[data-open-window]')));
        if (target) target.focus({ preventScroll: true });
      }
      return;
    }
    if (lastSheet === s.pendingStop) return;
    lastSheet = s.pendingStop;
    const task = ctx.M.TASKS[s.pendingStop];
    const apps = task.windows.map((x) => ctx.M.APPS[ctx.M.WINDOWS[x].app].name).join(', ');
    const others = Object.keys(s.tasks).filter((id) => id !== s.pendingStop && ctx.M.STOPPABLE.includes(s.tasks[id].state)).length;
    host.innerHTML = '<div class="scrim" data-cancel-stop></div><section class="sheet" role="alertdialog" aria-modal="true" aria-labelledby="sheet-title" aria-describedby="sheet-desc">' +
      '<h3 id="sheet-title">Stop “' + esc(task.title) + '”?</h3>' +
      '<p id="sheet-desc">Rose will stop this task in ' + esc(apps) + '.' + (others ? ' Your other work keeps going.' : '') + '</p>' +
      '<div class="sheet-actions"><button class="sbtn" data-cancel-stop id="sheet-cancel">Keep working</button><button class="sbtn danger" data-confirm-stop>Stop task</button></div></section>';
    ctx.$('sheet-cancel').focus({ preventScroll: true });
    host.querySelector('.sheet').addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      const btns = [...host.querySelectorAll('.sheet button')];
      const i = btns.indexOf(document.activeElement);
      e.preventDefault();
      btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
    });
  }

  window.RoseViews = {
    render(s, c) {
      ctx = c; if (!built) build();
      renderSwitcher(s);
      renderAgents(s);
      renderWindow(s); renderApp(s); renderSheet(s);
      if (s.view === 'switcher' && lastViewSeen !== 'switcher') requestAnimationFrame(() => { const h = ctx.$('sw-human'); scrollToIndex(h, Math.min(s.humanCarousel, s.recentApps.length - 1), true); depth(h); });
    if (s.view !== 'switcher') delete ctx.$('sw-agent').dataset.placed;
    lastViewSeen = s.view;
    },
    content,
  };
})();
