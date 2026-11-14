// Rose HiFi — Project 2: an adaptive workspace page one page left of Home (Home page −1).
// Prototype only. Generation is SIMULATED (a scripted local "composer" with a configurable delay
// and failure switch in the review harness) over SAMPLE data; every surface says so. Home, the dock
// and the Rose chin are unchanged. Direct actions act at once on sample data; Rose tasks always ask
// first and then run as Rose work (the existing task model), never silently.
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const now = () => performance.now();
  let R = null, I = null, mounted = null, enabled = false;

  // ---------- Sample compositions (simulated output of a local layout model) ----------
  const COMPOSITIONS = {
    cooking: {
      title: 'Cooking', why: 'Dinner prep · Thursday evening',
      cards: [
        { id: 'c-apps', type: 'apps', title: 'Kitchen apps', apps: ['clock', 'notes', 'music', 'firefox'] },
        { id: 'c-timer', type: 'actions', title: 'Quick actions', items: [
          { id: 'timer12', label: 'Start 12-min timer', kind: 'direct', effect: 'timer' },
          { id: 'playlist', label: 'Play “Slow Supper”', kind: 'direct', effect: 'play' },
          { id: 'grocery', label: 'Add missing items to grocery list', kind: 'agent', task: 'ws-grocery', ask: 'Rose will compare the recipe with your Notes list and add what’s missing.' }] },
        { id: 'c-recipes', type: 'carousel', title: 'Tonight’s recipes', items: [
          { t: 'Lemon herb pasta', s: '25 min · pantry' }, { t: 'Miso salmon', s: '30 min · 2 servings' }, { t: 'Roasted squash soup', s: '45 min · batch' }, { t: 'Green shakshuka', s: '20 min · eggs' }] },
        { id: 'c-ingr', type: 'list', title: 'Ingredients · Lemon herb pasta', check: true, items: ['Spaghetti 200 g', 'Lemon (zest + juice)', 'Parsley', 'Garlic, 2 cloves', 'Parmesan', 'Olive oil', 'Chilli flakes', 'Black pepper'] },
        { id: 'c-msg', type: 'notif', app: 'messages', title: 'Jamie', body: 'Running late — arriving at 7:15', actions: [{ id: 'reply-ok', label: 'Reply “See you then”', kind: 'direct', effect: 'reply' }] },
      ],
    },
    work: {
      title: 'Work', why: 'Focus block before your 3 pm',
      cards: [
        { id: 'w-cal', type: 'notif', app: 'calendar', title: 'Design review in 20 min', body: 'Rose HiFi · Room 3 / video', actions: [
          { id: 'join', label: 'Open invite', kind: 'direct', effect: 'open', app: 'calendar' },
          { id: 'prep', label: 'Summarise prep notes', kind: 'agent', task: 'ws-prep', ask: 'Rose will read your notes and files for the review and write a short summary.' }] },
        { id: 'w-apps', type: 'apps', title: 'Work apps', apps: ['mail', 'calendar', 'notes', 'files', 'editor'] },
        { id: 'w-focus', type: 'actions', title: 'Focus', items: [
          { id: 'focus25', label: 'Focus 25 min (Do not disturb)', kind: 'direct', effect: 'focus' },
          { id: 'draft', label: 'Draft a reply to Jamie', kind: 'agent', task: 'reply', ask: 'Rose will draft a reply in Mail. Nothing is sent without you.' }] },
        { id: 'w-today', type: 'list', title: 'Today', items: ['10:00 Stand-up', '11:30 Write handoff', '15:00 Design review', '16:30 1:1 with Sam', '17:30 Wrap up'] },
        { id: 'w-docs', type: 'carousel', title: 'Recent documents', items: [
          { t: 'HANDOFF-GLACIER.md', s: 'Edited 1 h ago' }, { t: 'Weekend ideas.md', s: 'Files' }, { t: 'Comparison', s: 'Editor' }, { t: 'Garden map.pdf', s: 'Files' }] },
      ],
    },
    unwinding: {
      title: 'Unwinding', why: 'Quiet evening · nothing scheduled',
      cards: [
        { id: 'u-listen', type: 'carousel', title: 'Listen', items: [
          { t: 'Morning Walk', s: 'Quiet Tracks' }, { t: 'Rain on Glass', s: 'Ambient' }, { t: 'Late Piano', s: 'Calm' }, { t: 'River Sounds', s: 'Nature' }] },
        { id: 'u-apps', type: 'apps', title: 'For tonight', apps: ['music', 'photos', 'weather', 'vlc'] },
        { id: 'u-act', type: 'actions', title: 'Wind down', items: [
          { id: 'dnd8', label: 'Do not disturb until 8 am', kind: 'direct', effect: 'dnd' },
          { id: 'dim', label: 'Warm, dim screen', kind: 'direct', effect: 'dim' },
          { id: 'sat', label: 'Plan a quiet Saturday', kind: 'agent', task: 'saturday', ask: 'Rose will check the garden hours, a walking route and your calendar.' }] },
        { id: 'u-read', type: 'list', title: 'Saved to read', items: ['The slow art of bread', 'Walking the river path', 'Notes on rest', 'A short history of gardens', 'Letters from the coast'] },
      ],
    },
  };
  // Sample Rose tasks for workspace actions that have no existing task. Each is named exactly like its
  // action, so the same title appears on the card, the confirm sheet and in Rose's work. They join the
  // task registry only when the person confirms (see registerTask in model.js).
  const WS_TASKS = {
    'ws-grocery': { title: 'Add missing items to grocery list', windows: {
      'ws-w-grocery': { app: 'notes', label: 'Grocery list', sample: { title: 'Grocery list', lines: ['Spaghetti 200 g', 'Lemons ×2', 'Parsley', 'Parmesan'] } },
      'ws-w-recipe': { app: 'firefox', label: 'Lemon herb pasta', sample: { query: 'lemon herb pasta recipe', heading: 'Lemon herb pasta', info: ['25 min · serves 2', '8 ingredients · 4 missing'] } } } },
    'ws-prep': { title: 'Summarise prep notes', windows: {
      'ws-w-prep': { app: 'notes', label: 'Design review prep', sample: { title: 'Design review prep', lines: ['Workspace page left of Home', 'Pins keep their place', 'Open: page indicator', 'Summary for 3 pm'] } },
      'ws-w-prepfiles': { app: 'files', label: 'Review files', sample: { files: ['Review agenda.md', 'Workspace notes.md', 'Captures.pdf', 'Open questions.md'] } } } },
  };
  function ensureTask(id) {
    const M = window.RoseModel, d = WS_TASKS[id];
    if (d && !M.TASKS[id]) {
      M.TASKS[id] = { title: d.title, windows: Object.keys(d.windows) };
      for (const [w, v] of Object.entries(d.windows)) { M.WINDOWS[w] = { app: v.app, task: id, label: v.label, sample: v.sample }; M.WINDOW_ORDER.push(w); }
    }
    if (d) R.dispatch({ type: 'registerTask', id });
  }
  const taskOf = (a) => { const s = R && R.getState(); return s && s.tasks[a.task]; };

  // Best guess: what the (simulated) composer proposes from time of day and recent context.
  COMPOSITIONS.guess = { title: 'Best guess', why: 'Thursday 6:40 pm · Jamie messaged · dinner time', cards: [
    COMPOSITIONS.cooking.cards[4], COMPOSITIONS.cooking.cards[1], COMPOSITIONS.unwinding.cards[0], COMPOSITIONS.cooking.cards[2], COMPOSITIONS.work.cards[3]] };
  const INTENTS = [
    [/cook|dinner|recipe|kitchen|food|meal/i, 'cooking'],
    [/work|meeting|focus|email|mail|docs?|review/i, 'work'],
    [/relax|unwind|calm|music|evening|quiet|rest|wind/i, 'unwinding'],
  ];

  // ---------- State ----------
  const ws = { layout: null, variant: 0, pins: new Set(), history: [], recent: [], phase: 'idle', error: null, req: 0,
    latency: 800, failNext: false, effects: {}, listening: false, sheet: null, interpreted: null, offer: null, store: '' };

  function compose(key, variant) {
    // Deterministic "regeneration": a different arrangement of the same intent. Pinned cards keep the
    // exact position they had; only the unpinned cards around them are rearranged.
    const base = COMPOSITIONS[key];
    let cards = base.cards.slice();
    if (variant % 2 === 1) cards = cards.slice(1).concat(cards[0]);
    if (variant % 3 === 2) cards.reverse();
    const prev = ws.layout ? ws.layout.cards : [];
    const pinned = prev.map((c, i) => [i, c]).filter(([, c]) => ws.pins.has(c.id));
    const out = cards.filter((c) => !pinned.some(([, p]) => p.id === c.id));
    for (const [i, c] of pinned) out.splice(Math.min(i, out.length), 0, c);
    return { key, title: base.title, why: base.why, variant, cards: out, at: Date.now() };
  }

  // Simulated on-device generation: delay + optional failure. The last layout stays until success.
  function generate(key, how) {
    const id = ++ws.req;
    ws.phase = 'loading'; ws.error = null; render();
    const delay = ws.latency, fail = ws.failNext;
    setTimeout(() => {
      if (id !== ws.req) return;                    // superseded by a newer request
      if (fail) { ws.failNext = false; syncHarness(); ws.phase = 'failed'; ws.error = { key, how }; if (ws.offer && ws.offer.req === id) ws.offer.phase = 'failed'; note('workspace: generation failed (kept last layout)'); render(); return; }
      const next = compose(key, how === 'regenerate' ? ws.variant + 1 : 0);
      if (ws.layout) ws.history.push({ layout: ws.layout, pins: new Set(ws.pins) });
      ws.layout = next; ws.variant = next.variant; ws.phase = 'idle';
      ws.recent = [key].concat(ws.recent.filter((k) => k !== key)).slice(0, 3);
      if (ws.offer && ws.offer.req === id) ws.offer.phase = 'ready';
      note('workspace: ' + how + ' → ' + next.title + ' (variant ' + next.variant + ')');
      save(); render();
    }, delay);
  }
  function intentOf(text) { for (const [re, k] of INTENTS) if (re.test(text)) return k; return 'guess'; }
  function submit(text) { const k = intentOf(text); ws.interpreted = { text, key: k }; generate(k, 'request'); }
  function arrange() { ws.interpreted = null; generate('guess', 'arrange'); }
  function regenerate() { if (ws.layout) generate(ws.layout.key, 'regenerate'); }
  function undo() {
    const h = ws.history.pop(); if (!h) return;
    ws.layout = h.layout; ws.pins = h.pins; ws.variant = h.layout.variant; ws.phase = 'idle'; ws.error = null;
    note('workspace: undo → ' + h.layout.title); save(); render();
  }
  function togglePin(id) {
    if (ws.layout) ws.history.push({ layout: ws.layout, pins: new Set(ws.pins) });
    if (ws.pins.has(id)) ws.pins.delete(id); else ws.pins.add(id);
    note('workspace: ' + (ws.pins.has(id) ? 'pinned ' : 'unpinned ') + id); save(); render();
  }

  // ---------- Actions: direct (immediate, sample data) vs Rose tasks (confirm → Rose work) ----------
  function doDirect(item) {
    const fx = ws.effects;
    if (item.effect === 'open' && item.app) { R.dispatch({ type: 'openApp', value: item.app }); return; }
    if (item.effect === 'timer') { fx.timer = { end: Date.now() + 12 * 60 * 1000 }; toast('Timer started · 12:00 (sample)'); }
    else if (item.effect === 'play') { fx.play = !fx.play; toast(fx.play ? 'Playing “Slow Supper” (sample)' : 'Paused'); }
    else if (item.effect === 'focus') { fx.focus = !fx.focus; toast(fx.focus ? 'Focus on · 25 min (sample)' : 'Focus off'); }
    else if (item.effect === 'dnd') { fx.dnd = !fx.dnd; toast(fx.dnd ? 'Do not disturb until 8 am (sample)' : 'Do not disturb off'); }
    else if (item.effect === 'dim') { fx.dim = !fx.dim; toast(fx.dim ? 'Warm, dim screen on (sample)' : 'Screen back to normal'); }
    else if (item.effect === 'reply') { fx.reply = true; toast('Replied “See you then” (sample)'); }
    note('direct action: ' + item.label); save(); render();
  }
  function askAgent(item) { ws.sheet = item; render(); const b = $('ws-sheet-go'); if (b) b.focus({ preventScroll: true }); }
  function confirmAgent() {
    const item = ws.sheet; ws.sheet = null;
    ensureTask(item.task);
    const t = R.getState().tasks[item.task];
    if (t && ['idle', 'stopped', 'done'].includes(t.state)) R.dispatch({ type: 'startTask', id: item.task });
    note('Rose task started: ' + window.RoseModel.TASKS[item.task].title + ' (' + item.task + ')'); toast('Rose is on it · see Rose’s work'); render();
  }

  // ---------- Rendering ----------
  const pearl = '<span class="ws-pearl" aria-hidden="true"></span>';
  function cardHtml(c) {
    const pinned = ws.pins.has(c.id);
    const head = '<header><b>' + esc(c.title) + '</b><button class="ws-pin" data-pin="' + c.id + '" aria-pressed="' + pinned + '" aria-label="' + (pinned ? 'Unpin ' : 'Pin ') + esc(c.title) + '">' + (pinned ? 'Pinned' : 'Pin') + '</button></header>';
    let body = '';
    if (c.type === 'apps') body = '<div class="ws-apps">' + c.apps.map((a) => '<button class="ws-app" data-open-app="' + a + '" aria-label="Open ' + esc(window.RoseModel.APPS[a].name) + '"><span class="tile">' + I.appArt(a, window.RoseModel.MONO_APPS.includes(a) ? 'mono' : '') + '</span><span>' + esc(window.RoseModel.APPS[a].name) + '</span></button>').join('') + '</div>';
    else if (c.type === 'actions') body = '<div class="ws-actions">' + c.items.map(actionHtml).join('') + '</div>';
    else if (c.type === 'carousel') body = '<div class="ws-carousel" role="list" aria-label="' + esc(c.title) + ' (scroll sideways)" tabindex="0">' + c.items.map((x, i) => '<div class="ws-slide" role="listitem"><span class="ws-thumb" data-i="' + (i % 4) + '"></span><b>' + esc(x.t) + '</b><span>' + esc(x.s) + '</span></div>').join('') + '</div>';
    else if (c.type === 'list') body = '<div class="ws-list" tabindex="0" aria-label="' + esc(c.title) + '">' + c.items.map((x, i) => c.check ? '<label class="ws-li"><input type="checkbox" data-check="' + c.id + ':' + i + '"' + (ws.effects['chk-' + c.id + ':' + i] ? ' checked' : '') + '> ' + esc(x) + '</label>' : '<div class="ws-li">' + esc(x) + '</div>').join('') + '</div>';
    else if (c.type === 'notif') body = '<div class="ws-notif"><span class="ws-nicon">' + I.appArt(c.app, 'mono') + '</span><div><b>' + esc(c.title) + '</b><span>' + esc(c.body) + '</span></div></div><div class="ws-actions">' + c.actions.map(actionHtml).join('') + '</div>';
    return '<article class="ws-card" data-card="' + c.id + '" data-type="' + c.type + '" data-pinned="' + pinned + '">' + head + body + '</article>';
  }
  function actionHtml(a) {
    const fx = ws.effects;
    if (a.kind === 'agent') {
      // State comes from the task model itself, so the card and Rose's work never disagree.
      const t = taskOf(a), st = t ? t.state : 'idle';
      const sub = st === 'running' ? 'Rose is working · in Rose’s work' : st === 'attention' ? 'Rose needs your choice · in Rose’s work' : st === 'done' ? 'Rose finished · in Rose’s work' : 'Rose task · asks first';
      return '<button class="ws-act agent" data-agent="' + a.id + '" data-task="' + a.task + '" data-state="' + st + '"' + (st !== 'idle' && st !== 'stopped' ? ' data-on="true"' : '') + '>' + pearl + '<span>' + esc(a.label) + '</span><small>' + sub + '</small></button>';
    }
    const on = (a.effect === 'timer' && fx.timer) || (a.effect === 'play' && fx.play) || (a.effect === 'focus' && fx.focus) || (a.effect === 'dnd' && fx.dnd) || (a.effect === 'dim' && fx.dim) || (a.effect === 'reply' && fx.reply);
    let label = a.label;
    if (a.effect === 'timer' && fx.timer) { const left = Math.max(0, fx.timer.end - Date.now()); label = 'Timer · ' + Math.floor(left / 60000) + ':' + String(Math.floor(left / 1000) % 60).padStart(2, '0'); }
    return '<button class="ws-act" data-direct="' + a.id + '" aria-pressed="' + !!on + '"><span>' + esc(label) + '</span><small>' + (on ? 'On' : 'Direct') + '</small></button>';
  }
  function skeleton() { return '<div class="ws-skel" aria-hidden="true">' + [96, 120, 150, 110].map((h) => '<i style="height:' + h + 'px"></i>').join('') + '</div>'; }
  function render() {
    const page = $('ws-page'); if (!page) { renderOffer(); return; }
    const s = R ? R.getState() : null;
    // Controls never move: the bar and status line have fixed geometry; only their state changes.
    const loading = ws.phase === 'loading', failed = ws.phase === 'failed';
    $('ws-arrange').disabled = loading; $('ws-regen').disabled = loading || !ws.layout; $('ws-undo').disabled = loading || !ws.history.length;
    const st = $('ws-status');
    st.dataset.state = ws.phase;
    st.innerHTML = loading ? '<span class="ws-spin" aria-hidden="true"></span>Composing on this phone… <em>simulated</em>'
      : failed ? 'Couldn’t arrange · kept your last layout <button class="ws-link" id="ws-retry">Retry</button>'
      : ws.layout ? esc(ws.layout.title) + ' · ' + esc(ws.interpreted ? '“' + ws.interpreted.text + '”' : ws.layout.why) + (ws.layout.variant ? ' · arrangement ' + (ws.layout.variant + 1) : '')
      : 'Ask for a workspace, or tap Arrange for me.';
    const canvas = $('ws-canvas');
    const tstate = s ? Object.entries(s.tasks).map(([k, v]) => k + ':' + v.state).join('|') : '';
    const key = JSON.stringify([ws.layout && ws.layout.at, ws.layout && ws.layout.cards.map((c) => c.id), [...ws.pins], ws.effects, ws.phase === 'loading', tstate]);
    if (canvas.dataset.key !== key) {
      const top = canvas.scrollTop, scrolls = [...canvas.querySelectorAll('.ws-carousel,.ws-list')].map((el) => [el.closest('.ws-card').dataset.card, el.scrollLeft, el.scrollTop]);
      canvas.dataset.key = key;
      canvas.innerHTML = (ws.layout ? ws.layout.cards.map(cardHtml).join('') : '<div class="ws-empty"><b>Your workspace</b><span>Rose composes this page from your apps, notifications and actions. Nothing here changes your Home.</span></div>') + (loading ? skeleton() : '');
      canvas.classList.toggle('loading', loading);
      canvas.scrollTop = top;
      for (const [id, l, t] of scrolls) { const el = canvas.querySelector('[data-card="' + id + '"] .ws-carousel,[data-card="' + id + '"] .ws-list'); if (el) { el.scrollLeft = l; el.scrollTop = t; } }
    }
    $('ws-recent').innerHTML = ws.recent.map((k) => '<button class="ws-chip" data-recent="' + k + '"' + (ws.layout && ws.layout.key === k ? ' aria-current="true"' : '') + '>' + esc(COMPOSITIONS[k].title) + '</button>').join('');
    const mic = $('ws-voice'), muted = s && s.muted;
    mic.dataset.muted = String(!!muted); mic.dataset.listening = String(ws.listening);
    mic.setAttribute('aria-label', muted ? 'Voice input unavailable: microphone muted from the chin' : ws.listening ? 'Listening (simulated)' : 'Ask by voice (simulated)');
    const sh = $('ws-sheet');
    sh.hidden = !ws.sheet;
    if (ws.sheet) { $('ws-sheet-title').textContent = 'Ask Rose to ' + ws.sheet.label.charAt(0).toLowerCase() + ws.sheet.label.slice(1) + '?'; $('ws-sheet-body').textContent = ws.sheet.ask + ' Rose keeps working in the background; you can stop it from Rose’s work.'; }
    if (s && R && window.RoseSystem) { const k = window.RoseSystem.kbState(); if (k.open && k.target === 'ws' && (s.view !== 'home' || s.homePage !== -1)) window.RoseSystem.hideKb('left workspace'); }
    const mk = $('ws-mark'); if (mk && s) mk.setAttribute('aria-current', String(s.view === 'home' && s.homePage === -1));
    renderOffer();
    const h = $('h-wsstate'); if (h) h.textContent = 'Workspace: ' + (ws.layout ? ws.layout.title + ' v' + (ws.layout.variant + 1) : 'empty') + ' · ' + ws.phase + ' · pins ' + ws.pins.size + ' · undo ' + ws.history.length + ' · ' + (ws.store || '');
  }
  function voice() {
    const s = R.getState();
    if (s.muted) { toast('Microphone muted · unmute from the chin to speak'); note('voice: refused, mic muted'); return; }
    if (ws.listening) return;
    ws.listening = true; render(); note('voice: listening (simulated)');
    setTimeout(() => { ws.listening = false; const said = 'Something relaxing for tonight'; window.RoseSystem.fields.ws = window.RoseKeys.initial({ text: said }); render(); submit(said); }, 1200);
  }

  // ---------- Mount / bind ----------
  function mount(track, on) {
    enabled = on;
    const old = $('ws-page'); if (old) old.remove();
    const oldMark = $('ws-mark'); if (oldMark) oldMark.remove();
    if (!on || !track) return;
    // A distinct workspace mark sits before Home's page dots and lights only on the workspace.
    const dots = $('page-dots');
    if (dots) dots.insertAdjacentHTML('afterbegin', '<button class="ws-mark" id="ws-mark" data-page="-1" aria-label="Workspace"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 .8 7.3 4.7 11.2 6 7.3 7.3 6 11.2 4.7 7.3.8 6 4.7 4.7Z"/></svg></button>');
    track.insertAdjacentHTML('afterbegin',
      '<section class="ws-page" id="ws-page" aria-label="Workspace">' +
      '<header class="ws-head"><h2>Workspace</h2><span class="ws-sim">Simulated generation · sample data</span></header>' +
      '<div class="ws-composer"><div class="field ws-field" id="f-ws" role="textbox" aria-label="Ask for a workspace" tabindex="0" data-placeholder="Ask for a workspace…"></div>' +
      '<button class="ws-voice" id="ws-voice" aria-label="Ask by voice (simulated)"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg></button></div>' +
      '<div class="ws-bar" role="toolbar" aria-label="Workspace controls"><button class="ws-btn primary" id="ws-arrange">Arrange for me</button><button class="ws-btn" id="ws-regen" disabled>Regenerate</button><button class="ws-btn" id="ws-undo" disabled>Undo</button></div>' +
      '<p class="ws-status" id="ws-status" role="status" aria-live="polite"></p>' +
      '<div class="ws-recent" id="ws-recent" aria-label="Recent layouts"></div>' +
      '<div class="ws-canvas" id="ws-canvas"></div>' +
      '<div class="ws-sheet" id="ws-sheet" role="dialog" aria-modal="true" aria-labelledby="ws-sheet-title" hidden><div class="ws-sheet-card">' + pearl + '<h3 id="ws-sheet-title"></h3><p id="ws-sheet-body"></p><div><button class="ws-btn" id="ws-sheet-cancel">Not now</button><button class="ws-btn primary" id="ws-sheet-go">Ask Rose</button></div></div></div>' +
      '</section>');
    mounted = $('ws-page');
    if (window.RoseSystem) window.RoseSystem.bindField($('f-ws'), 'ws');
    const page = mounted;
    page.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.id === 'ws-arrange') return arrange();
      if (b.id === 'ws-regen') return regenerate();
      if (b.id === 'ws-undo') return undo();
      if (b.id === 'ws-retry' && ws.error) return generate(ws.error.key, ws.error.how);
      if (b.id === 'ws-voice') return voice();
      if (b.id === 'ws-sheet-cancel') { ws.sheet = null; note('Rose task not started'); render(); return; }
      if (b.id === 'ws-sheet-go') return confirmAgent();
      if (b.dataset.pin) return togglePin(b.dataset.pin);
      if (b.dataset.recent) { const k = b.dataset.recent; if (ws.layout && ws.layout.key === k) return; ws.interpreted = null; return generate(k, 'recent'); }
      if (b.dataset.openApp) { R.dispatch({ type: 'openApp', value: b.dataset.openApp }); return; }
      const all = ws.layout ? ws.layout.cards.flatMap((c) => (c.items || []).concat(c.actions || [])) : [];
      if (b.dataset.direct) { const it = all.find((x) => x.id === b.dataset.direct); if (it) doDirect(it); return; }
      if (b.dataset.agent) {
        const it = all.find((x) => x.id === b.dataset.agent); if (!it) return;
        const t = taskOf(it);
        if (t && !['idle', 'stopped'].includes(t.state)) { R.dispatch({ type: 'agents' }); return; }   // already Rose's work: show it
        askAgent(it);
      }
    });
    page.addEventListener('change', (e) => { const c = e.target.dataset.check; if (c) { ws.effects['chk-' + c] = e.target.checked; save(); } });
    render();
  }
  function fieldChanged() {}
  function toast(t) { const el = $('sys-toast'); if (!el) return; el.textContent = t; el.classList.add('on'); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('on'), 2200); }
  function note(t) { const el = $('h-syslog'); if (el) { const li = document.createElement('li'); li.textContent = t; el.prepend(li); while (el.children.length > 8) el.lastChild.remove(); } }
  function syncHarness() { const f = $('h-wsfail'); if (f) f.checked = ws.failNext; }

  // A conversational request while another app is in front: the page prepares in the background.
  // It never leaves the current app: when the layout is ready it offers “Show workspace”.
  function prepareFromConversation(key) {
    ws.interpreted = { text: 'Rose, set up my workspace for ' + COMPOSITIONS[key].title.toLowerCase(), key };
    generate(key, 'request');
    ws.offer = { key, req: ws.req, phase: 'preparing' };
    note('conversation: preparing ' + key + ' without leaving ' + R.getState().view);
    render();
  }
  function onWorkspace() { const s = R && R.getState(); return !!s && s.view === 'home' && s.homePage === -1; }
  function renderOffer() {
    const el = $('ws-offer'); if (!el) return;
    if (ws.offer && onWorkspace()) ws.offer = null;            // reached the page: nothing left to offer
    const o = enabled ? ws.offer : null;
    el.hidden = !o;
    if (!o) { el.dataset.k = ''; return; }
    const k = o.phase + o.key; if (el.dataset.k === k) return; el.dataset.k = k;
    const title = esc(COMPOSITIONS[o.key].title);
    el.dataset.phase = o.phase;
    el.innerHTML = o.phase === 'preparing'
      ? '<span class="ws-spin" aria-hidden="true"></span><p>Preparing workspace: ' + title + ' <em>simulated</em></p><button class="ws-offer-x" id="ws-offer-x" aria-label="Dismiss">✕</button>'
      : o.phase === 'ready'
        ? '<p>Workspace ready: ' + title + '</p><button class="ws-btn primary" id="ws-offer-show">Show workspace</button><button class="ws-offer-x" id="ws-offer-x" aria-label="Dismiss">✕</button>'
        : '<p>Couldn’t prepare workspace · kept your last layout</p><button class="ws-offer-x" id="ws-offer-x" aria-label="Dismiss">✕</button>';
  }
  function showWorkspace() {
    ws.offer = null;
    R.dispatch({ type: 'home' }); R.dispatch({ type: 'homePage', value: -1, min: -1, max: 3 });
    note('offer: Show workspace (explicit)');
  }

  // ---------- Persistence (browser localStorage, standing in for on-device storage) ----------
  const STORE = 'rose.workspace.v1';
  const cardById = () => { const m = {}; for (const c of Object.values(COMPOSITIONS)) for (const x of c.cards) m[x.id] = x; return m; };
  const ser = (l) => l && { key: l.key, variant: l.variant, cards: l.cards.map((c) => c.id), at: l.at };
  function de(o, by) {
    if (!o || !COMPOSITIONS[o.key]) return null;
    const cards = (o.cards || []).map((id) => by[id]).filter(Boolean); if (!cards.length) return null;
    const b = COMPOSITIONS[o.key]; return { key: o.key, title: b.title, why: b.why, variant: o.variant | 0, cards, at: o.at || 0 };
  }
  function save() {
    const fx = {}; for (const [k, v] of Object.entries(ws.effects)) if (/^(timer|play|focus|dnd|dim|reply|chk-.*)$/.test(k) && v) fx[k] = v;
    try {
      localStorage.setItem(STORE, JSON.stringify({ v: 1, layout: ser(ws.layout), pins: [...ws.pins], recent: ws.recent, interpreted: ws.interpreted, effects: fx,
        history: ws.history.slice(-20).map((h) => ({ layout: ser(h.layout), pins: [...h.pins] })) }));
      ws.store = 'saved on this device (browser storage)';
    } catch (e) { ws.store = 'not saved: browser storage unavailable'; }
  }
  function load() {
    let o = null;
    try { const raw = localStorage.getItem(STORE); if (!raw) { ws.store = 'nothing saved yet'; return false; } o = JSON.parse(raw); } catch (e) { ws.store = 'not restored: browser storage unavailable'; return false; }
    if (!o || o.v !== 1) return false;
    const by = cardById();
    ws.layout = de(o.layout, by); ws.variant = ws.layout ? ws.layout.variant : 0;
    ws.pins = new Set((o.pins || []).filter((id) => by[id]));
    ws.history = (o.history || []).map((h) => ({ layout: de(h.layout, by), pins: new Set(h.pins || []) })).filter((h) => h.layout);
    ws.recent = (o.recent || []).filter((k) => COMPOSITIONS[k]);
    ws.interpreted = o.interpreted && COMPOSITIONS[o.interpreted.key] ? o.interpreted : null;
    ws.effects = o.effects || {}; if (ws.effects.timer && ws.effects.timer.end < Date.now()) delete ws.effects.timer;
    ws.store = 'restored from this device (browser storage)';
    note('workspace: restored after reload → ' + (ws.layout ? ws.layout.title : 'empty'));
    return true;
  }
  function reset() {
    try { localStorage.removeItem(STORE); } catch (e) {}
    Object.assign(ws, { layout: null, variant: 0, pins: new Set(), history: [], recent: [], phase: 'idle', error: null, effects: {}, sheet: null, interpreted: null, offer: null, store: 'cleared' });
    ws.req++; const c = $('ws-canvas'); if (c) delete c.dataset.key; note('workspace: reset (storage cleared)'); render();
  }

  function init(rose) {
    R = rose; I = window.RoseIcons;
    load();
    const scr = $('screen');
    if (scr && !$('ws-offer')) {
      scr.insertAdjacentHTML('beforeend', '<div class="ws-offer" id="ws-offer" role="status" aria-live="polite" hidden></div>');
      $('ws-offer').addEventListener('click', (e) => {
        if (e.target.closest('#ws-offer-show')) showWorkspace();
        else if (e.target.closest('#ws-offer-x')) { ws.offer = null; note('offer dismissed'); render(); }
      });
    }
    mount(document.getElementById('home-track'), document.getElementById('device').dataset.theme === 'oxblood');
    // The timer ticks in place: only its label changes, so cards are never replaced under a finger.
    setInterval(() => {
      const t = ws.effects.timer; if (!t || !mounted) return;
      const left = Math.max(0, t.end - Date.now());
      document.querySelectorAll('#ws-canvas .ws-act[data-direct][aria-pressed=true] > span').forEach((sp) => { if (/^Timer · /.test(sp.textContent)) sp.textContent = 'Timer · ' + Math.floor(left / 60000) + ':' + String(Math.floor(left / 1000) % 60).padStart(2, '0'); });
    }, 1000);
    const lat = $('h-wslat'); if (lat) lat.onchange = (e) => { ws.latency = +e.target.value; };
    const fail = $('h-wsfail'); if (fail) fail.onchange = (e) => { ws.failNext = e.target.checked; };
    const prep = $('h-wsprep'); if (prep) prep.onclick = () => prepareFromConversation('cooking');
    const rs = $('h-wsreset'); if (rs) rs.onclick = reset;
    const go = $('h-wsgo'); if (go) go.onclick = () => R.dispatch({ type: 'homePage', value: -1, min: -1, max: 3 });
  }
  window.RoseWorkspace = { mount, render: () => render(), submit, arrange, regenerate, undo, fieldChanged, state: () => ws, prepareFromConversation, showWorkspace, reset, COMPOSITIONS, WS_TASKS };
  if (window.__rose) init(window.__rose);
})();
