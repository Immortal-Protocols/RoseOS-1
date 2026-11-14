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
          { id: 'grocery', label: 'Add missing items to grocery list', kind: 'agent', task: 'saturday', ask: 'Rose will compare the recipe with your Notes list and add what’s missing.' }] },
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
          { id: 'prep', label: 'Summarise prep notes', kind: 'agent', task: 'compare', ask: 'Rose will read your notes and files for the review and write a short summary.' }] },
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
    latency: 800, failNext: false, effects: {}, listening: false, sheet: null, interpreted: null };

  function compose(key, variant) {
    // Deterministic "regeneration": a different arrangement of the same intent; pinned cards stay first.
    const base = COMPOSITIONS[key];
    let cards = base.cards.slice();
    if (variant % 2 === 1) cards = cards.slice(1).concat(cards[0]);
    if (variant % 3 === 2) cards.reverse();
    const pinned = ws.layout ? ws.layout.cards.filter((c) => ws.pins.has(c.id)) : [];
    const rest = cards.filter((c) => !pinned.some((p) => p.id === c.id));
    return { key, title: base.title, why: base.why, variant, cards: pinned.concat(rest), at: Date.now() };
  }

  // Simulated on-device generation: delay + optional failure. The last layout stays until success.
  function generate(key, how) {
    const id = ++ws.req;
    ws.phase = 'loading'; ws.error = null; render();
    const delay = ws.latency, fail = ws.failNext;
    setTimeout(() => {
      if (id !== ws.req) return;                    // superseded by a newer request
      if (fail) { ws.failNext = false; syncHarness(); ws.phase = 'failed'; ws.error = { key, how }; note('workspace: generation failed (kept last layout)'); render(); return; }
      const next = compose(key, how === 'regenerate' ? ws.variant + 1 : 0);
      if (ws.layout) ws.history.push({ layout: ws.layout, pins: new Set(ws.pins) });
      ws.layout = next; ws.variant = next.variant; ws.phase = 'idle';
      ws.recent = [key].concat(ws.recent.filter((k) => k !== key)).slice(0, 3);
      note('workspace: ' + how + ' → ' + next.title + ' (variant ' + next.variant + ')');
      render();
    }, delay);
  }
  function intentOf(text) { for (const [re, k] of INTENTS) if (re.test(text)) return k; return 'guess'; }
  function submit(text) { const k = intentOf(text); ws.interpreted = { text, key: k }; generate(k, 'request'); }
  function arrange() { ws.interpreted = null; generate('guess', 'arrange'); }
  function regenerate() { if (ws.layout) generate(ws.layout.key, 'regenerate'); }
  function undo() {
    const h = ws.history.pop(); if (!h) return;
    ws.layout = h.layout; ws.pins = h.pins; ws.variant = h.layout.variant; ws.phase = 'idle'; ws.error = null;
    note('workspace: undo → ' + h.layout.title); render();
  }
  function togglePin(id) {
    if (ws.layout) ws.history.push({ layout: ws.layout, pins: new Set(ws.pins) });
    if (ws.pins.has(id)) ws.pins.delete(id); else ws.pins.add(id);
    note('workspace: ' + (ws.pins.has(id) ? 'pinned ' : 'unpinned ') + id); render();
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
    note('direct action: ' + item.label); render();
  }
  function askAgent(item) { ws.sheet = item; render(); const b = $('ws-sheet-go'); if (b) b.focus({ preventScroll: true }); }
  function confirmAgent() {
    const item = ws.sheet; ws.sheet = null;
    const s = R.getState(), t = s.tasks[item.task];
    if (t && ['idle', 'stopped', 'done'].includes(t.state)) R.dispatch({ type: 'startTask', id: item.task });
    ws.effects['agent-' + item.id] = true;
    note('Rose task started: ' + item.label); toast('Rose is on it · see Rose’s work'); render();
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
    const on = (a.effect === 'timer' && fx.timer) || (a.effect === 'play' && fx.play) || (a.effect === 'focus' && fx.focus) || (a.effect === 'dnd' && fx.dnd) || (a.effect === 'dim' && fx.dim) || (a.effect === 'reply' && fx.reply) || fx['agent-' + a.id];
    if (a.kind === 'agent') return '<button class="ws-act agent" data-agent="' + a.id + '"' + (on ? ' data-on="true"' : '') + '>' + pearl + '<span>' + esc(a.label) + '</span><small>' + (on ? 'Rose is working' : 'Rose task · asks first') + '</small></button>';
    let label = a.label;
    if (a.effect === 'timer' && fx.timer) { const left = Math.max(0, fx.timer.end - Date.now()); label = 'Timer · ' + Math.floor(left / 60000) + ':' + String(Math.floor(left / 1000) % 60).padStart(2, '0'); }
    return '<button class="ws-act" data-direct="' + a.id + '" aria-pressed="' + !!on + '"><span>' + esc(label) + '</span><small>' + (on ? 'On' : 'Direct') + '</small></button>';
  }
  function skeleton() { return '<div class="ws-skel" aria-hidden="true">' + [96, 120, 150, 110].map((h) => '<i style="height:' + h + 'px"></i>').join('') + '</div>'; }
  function render() {
    const page = $('ws-page'); if (!page) return;
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
    const key = JSON.stringify([ws.layout && ws.layout.at, ws.layout && ws.layout.cards.map((c) => c.id), [...ws.pins], ws.effects, ws.phase === 'loading']);
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
    const h = $('h-wsstate'); if (h) h.textContent = 'Workspace: ' + (ws.layout ? ws.layout.title + ' v' + (ws.layout.variant + 1) : 'empty') + ' · ' + ws.phase + ' · pins ' + ws.pins.size + ' · undo ' + ws.history.length;
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
    if (!on || !track) return;
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
      if (b.dataset.agent) { const it = all.find((x) => x.id === b.dataset.agent); if (it && !ws.effects['agent-' + it.id]) askAgent(it); }
    });
    page.addEventListener('change', (e) => { const c = e.target.dataset.check; if (c) { ws.effects['chk-' + c] = e.target.checked; } });
    render();
  }
  function fieldChanged() {}
  function toast(t) { const el = $('sys-toast'); if (!el) return; el.textContent = t; el.classList.add('on'); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('on'), 2200); }
  function note(t) { const el = $('h-syslog'); if (el) { const li = document.createElement('li'); li.textContent = t; el.prepend(li); while (el.children.length > 8) el.lastChild.remove(); } }
  function syncHarness() { const f = $('h-wsfail'); if (f) f.checked = ws.failNext; }

  // A conversational request while another app is in front: the page prepares in the background.
  function prepareFromConversation(key) {
    ws.interpreted = { text: 'Rose, set up my workspace for ' + COMPOSITIONS[key].title.toLowerCase(), key };
    generate(key, 'request');
    toast('Workspace ready soon: ' + COMPOSITIONS[key].title + ' · swipe right from Home');
    note('conversation: prepared ' + key + ' without leaving ' + R.getState().view);
  }

  function init(rose) {
    R = rose; I = window.RoseIcons;
    mount(document.getElementById('home-track'), document.getElementById('device').dataset.theme === 'oxblood');
    setInterval(() => { if (ws.effects.timer && mounted && R.getState().homePage === -1) { const c = $('ws-canvas'); delete c.dataset.key; render(); } }, 1000);
    const lat = $('h-wslat'); if (lat) lat.onchange = (e) => { ws.latency = +e.target.value; };
    const fail = $('h-wsfail'); if (fail) fail.onchange = (e) => { ws.failNext = e.target.checked; };
    const prep = $('h-wsprep'); if (prep) prep.onclick = () => prepareFromConversation('cooking');
    const go = $('h-wsgo'); if (go) go.onclick = () => R.dispatch({ type: 'homePage', value: -1, min: -1, max: 3 });
  }
  window.RoseWorkspace = { mount, render: () => render(), submit, arrange, regenerate, undo, fieldChanged, state: () => ws, prepareFromConversation, COMPOSITIONS };
  if (window.__rose) init(window.__rose);
})();
