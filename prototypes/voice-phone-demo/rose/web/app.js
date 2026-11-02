// Rose dashboard. Everything on screen is derived from the server's event
// stream (/ws/events: a hello with the current state, the bus history, then
// live events), so a reload replays the history and lands in the same place.
// Audio goes through audio.js; this file only adds playback-position hooks
// (to highlight the words being spoken) and a stop-reconnecting rule for when
// the server hands the audio to another tab.

import { RoseAudio } from '/audio.js';

const WPS = 2.6;          // speaking rate for the playback estimate when no browser audio
const TARGET_MS = 600;    // p50 target: end of speech -> first audio
const EXAMPLES = [
  "Text Sam I'm running ten minutes late",
  "What's on my calendar this afternoon?",
  'Set an alarm for 6:30 tomorrow',
  'Turn on do not disturb',
  'How much flour for seven if four needs one and a half cups?',
  "Where's the nearest pharmacy?",
  'Play my chill mix',
];
const INCOMING = [
  {app: 'Messages', title: 'Mom', text: 'Can you pick up milk on the way home?'},
  {app: 'Messages', title: 'Alex', text: 'Running 5 min late to the review'},
];
const STATE_LABEL = {
  loading: 'Loading', listening: 'Listening', user_speaking: 'Hearing you',
  thinking: 'Thinking', speaking: 'Speaking',
};
const ICON = {
  message: 'M4 5h16v11H8l-4 4z',
  reset: 'M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4',
  phone: 'M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM11 18h2',
  think: 'M12 3a6 6 0 0 0-3.5 10.9V17h7v-3.1A6 6 0 0 0 12 3zM10 21h4',
  cut: 'M6 6l12 12M18 6L6 18',
};

const $ = (id) => document.getElementById(id);
const convo = $('convo');

function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', ICON[name]);
  svg.append(p);
  return svg;
}

function dots() {
  const d = h('span', 'dots');
  d.append(h('i'), h('i'), h('i'));
  return d;
}

function pill(cls, text) { return h('span', `pill ${cls}`, text); }
const words = (t) => (t || '').trim().split(/\s+/).filter(Boolean);
const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// ------------------------------------------------------------------ state
const S = {
  status: 'loading', mode: '', ready: false, replay: false,
  replies: new Map(),   // reply id -> reply view model
  users: new Map(),     // turn id -> element
  segOwner: new Map(),  // segment id -> reply id
  active: new Set(),    // replies being spoken
  partial: null,        // live transcript bubble
  partialGen: 0,        // bumped whenever the user's speech moves on
  tasks: new Map(),
  taskEls: new Map(),
  taskOrder: '',
  turns: new Map(),     // reply id -> latency record
  lastRose: null,
  phone: {want: 0, loading: false, again: false, lastTap: 0},
  audio: null, starting: false,
  busy: null,
};

// ----------------------------------------------------------- event stream
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/events`);
  ws.onopen = () => $('conn').classList.add('on');
  ws.onclose = () => {
    $('conn').classList.remove('on');
    $('state-label').textContent = 'Reconnecting to the server';
    setTimeout(connect, 1000);
  };
  ws.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    S.replay = !!m.replay;
    const fn = H[m.kind];
    if (fn) {
      try { fn(m); } catch (err) { console.error(m.kind, err); }
    }
  };
}

const H = {
  hello(m) {
    clearView(false);
    S.turns.clear();
    applyState(m.state || {});
  },
  replay_done() {
    S.replay = false;
    scrollDown(true);
    renderHud();
    loadPhone();
    fetchPhone();
  },
  ready(m) {
    S.ready = true;
    if (!S.replay && m.seconds) toast(`Rose is ready (loaded in ${m.seconds} s)`, 'ok');
  },
  status(m) { setStatus(m.state, m.detail); },
  vad(m) {
    $('orb').style.setProperty('--vad', Math.max(0, Math.min(1, m.p)));
    $('vad').style.left = `${Math.round(Math.max(0, Math.min(1, m.p)) * 100)}%`;
  },
  level(m) { if (!S.audio) setLevel(m.rms); },
  speech_start() { setPartial('', false); },
  partial(m) { setPartial(m.text, false); },
  speech_pause() { S.partial?.el.classList.add('paused'); },
  speech_resume() { S.partialGen++; S.partial?.el.classList.remove('paused'); },
  noise() { removePartial(); },
  turn_end(m) {
    if (!S.replay) {
      if (m.text) setPartial(m.text, true); else removePartial();
    }
    expirePartial();  // a backchannel gets no user_turn
  },
  user_turn(m) {
    removePartial();
    const el = h('div', 'msg user');
    el.append(h('div', 'bubble', m.text));
    insert(el);
    S.users.set(m.id, el);
  },
  rose_partial(m) {
    const r = reply(m.id);
    r.draft = m.text;
    renderReply(r);
  },
  rose_say(m) {
    const r = reply(m.id);
    if (!r.segs.some((s) => s.seg === m.seg)) r.segs.push({seg: m.seg, text: m.text || m.spoken || ''});
    S.segOwner.set(m.seg, r.id);
    if (!r.done) S.active.add(r.id);
    renderReply(r);
  },
  rose_audio_start(m) {
    const r = reply(m.id);
    r.audioAt = m.t * 1000;
    if (m.latency_ms != null) r.latency = m.latency_ms;
    const rec = turnRec(m.id);
    if (m.tts_ms != null) rec.tts_ms = m.tts_ms;
    if (m.latency_ms != null) rec.latency_ms = m.latency_ms;
    if (!r.done) S.active.add(r.id);
    renderReply(r);
    renderHud();
  },
  rose_heard(m) { finishReply(m); },
  rose_turn(m) { finishReply(m); },
  barge(m) {
    const r = S.replies.get(S.lastRose);
    const orb = $('orb');
    if (m.stage === 'duck') {
      orb.classList.add('ducked');
      if (r && !r.done) r.barge = 'ducked';
    } else if (m.stage === 'resume') {
      orb.classList.remove('ducked');
      if (r && r.barge === 'ducked') {
        r.barge = 'kept';
        const why = m.reason || '';
        r.bargeWhy = why.startsWith('backchannel:') ? `through "${why.slice(12).trim()}"` : `(${why || 'noise'})`;
      }
    } else if (m.stage === 'stop') {
      orb.classList.remove('ducked');
      if (r && !r.done) {
        r.interrupted = true;
        r.stopMs = m.ms;
        if (r.heard == null) r.heardEst = estimateHeard(r);
        S.active.delete(r.id);
      }
    } else if (m.stage === 'abandon') {
      if (r && !r.segs.length && !r.done) {
        r.el.remove();
        S.replies.delete(r.id);
        S.active.delete(r.id);
        const last = [...S.users.values()].at(-1);
        last?.classList.add('carried');
      }
    }
    if (r && S.replies.has(r.id)) renderReply(r);
  },
  tool_call(m) {
    const r = reply(m.id);
    r.tools.push(m);
    renderReply(r);
  },
  task(m) { upsertTask(m.task); },
  phone(m) { phoneChanged(m); },
  notification(m) {
    const row = h('div', 'sys notify');
    const text = h('span');
    text.append(h('b', null, m.title || m.app || 'Notification'), ` ${m.text || ''}`);
    row.append(icon('message'), text);
    insert(row);
    if (!S.replay) setTimeout(() => loadPhone(), 6500);  // the banner times out
  },
  metrics(m) {
    const t = m.turn || {};
    if (t.id == null) return;
    Object.assign(turnRec(t.id), t, {hasMetrics: true});
    renderHud();
  },
  stats(m) { renderStats(m); },
  warning(m) { if (!S.replay) toast(m.message, 'warn'); },
  error(m) { if (!S.replay) toast(m.message, 'bad'); },
  reset() {
    clearView(true);
    const row = h('div', 'sys reset');
    row.append(icon('reset'), h('span', null, 'New conversation'));
    insert(row);
    loadPhone();
  },
  voice(m) { $('voice').value = m.voice; },
};

function applyState(st) {
  S.mode = st.mode || '';
  S.ready = !!st.ready;
  const tag = $('mode-tag');
  tag.hidden = S.mode !== 'ui-only';
  tag.textContent = 'UI only · scripted demo';
  if (S.mode === 'ui-only') $('say-text').placeholder = 'Type something; the scripted demo answers';
  const sel = $('voice');
  sel.textContent = '';
  for (const v of st.voices || []) sel.append(new Option(v, v));
  if (st.voice) sel.value = st.voice;
  if (st.status) setStatus(st.status.state, st.status.detail);
  for (const t of st.tasks || []) upsertTask(t);
  for (const t of st.metrics || []) if (t.id != null) Object.assign(turnRec(t.id), t, {hasMetrics: true});
  if (st.phone) caption(st.phone);
  if (st.stats) renderStats(st.stats);
}

// ---------------------------------------------------------------- status
function setStatus(state, detail) {
  S.status = state || 'listening';
  $('orb').dataset.state = S.status;
  const label = S.status === 'loading' ? (detail || 'Loading models') : (STATE_LABEL[S.status] || S.status);
  $('state-label').textContent = label;
  $('state-label').title = label;
  $('loadbar').hidden = S.status !== 'loading';
  if (S.status === 'listening' || S.status === 'thinking') $('orb').classList.remove('ducked');
  if (S.status !== 'user_speaking' && S.status !== 'loading') $('orb').style.setProperty('--vad', 0);
}

function setLevel(rms) {
  const db = 20 * Math.log10((rms || 0) + 1e-6);
  const pct = Math.max(0, Math.min(1, (db + 60) / 55));
  $('level').style.width = `${Math.round(pct * 100)}%`;
}

// ---------------------------------------------------------- conversation
function nearBottom() {
  return convo.scrollHeight - convo.scrollTop - convo.clientHeight < 140;
}

function scrollDown(force) {
  if (S.replay && !force) return;
  convo.scrollTop = convo.scrollHeight;
}

function insert(el) {
  if (S.replay) el.classList.add('still');  // history appears at once, no entry motion
  $('empty').hidden = true;
  const stick = nearBottom();
  convo.insertBefore(el, S.partial ? S.partial.el : null);
  while (convo.children.length > 400) convo.children[1].remove();  // [0] is the empty state
  if (stick) scrollDown();
}

function setPartial(text, final) {
  S.partialGen++;
  if (!S.partial) {
    const el = h('div', 'msg user partial');
    const b = h('div', 'bubble');
    el.append(b);
    S.partial = {el, b};
    $('empty').hidden = true;
    const stick = nearBottom();
    convo.append(el);
    if (stick) scrollDown();
  }
  const {el, b} = S.partial;
  b.textContent = text;
  if (!final) b.append(dots());
  el.classList.toggle('paused', false);
}

function removePartial() {
  S.partial?.el.remove();
  S.partial = null;
}

// Speech that ends without a user_turn (a backchannel Rose talked through)
// must not stay pinned below everything she says next.
function expirePartial() {
  const gen = S.partialGen;
  setTimeout(() => { if (S.partialGen === gen) removePartial(); }, S.replay ? 0 : 1500);
}

function reply(id) {
  let r = S.replies.get(id);
  if (r) return r;
  r = {id, segs: [], tools: [], draft: '', final: null, heard: null, heardEst: null,
       interrupted: false, done: false, audioAt: null, latency: null, play: null,
       barge: null, stopMs: null, lastIdx: null, announce: !S.users.has(id)};
  r.el = h('div', 'msg rose' + (r.announce ? ' announce' : ''));
  const row = h('div', 'row');
  r.bubble = h('div', 'bubble');
  row.append(h('div', 'avatar'), r.bubble);
  r.meta = h('div', 'meta');
  r.el.append(row, r.meta);
  insert(r.el);
  S.replies.set(id, r);
  S.lastRose = id;
  return r;
}

function finishReply(m) {
  const r = reply(m.id);
  if (m.kind === 'rose_heard') r.heard = m.text;
  else r.final = m.text;
  r.interrupted = r.interrupted || !!m.interrupted;
  r.done = true;
  S.active.delete(r.id);
  renderReply(r);
}

function segDur(s) { return Math.max(0.6, words(s.text).length / WPS); }

function playIndex(r) {
  if (r.play) {
    const i = r.segs.findIndex((s) => s.seg === r.play.seg);
    if (i >= 0) return i;
  }
  if (r.audioAt == null) return -1;
  let t = (Date.now() - r.audioAt) / 1000;
  for (let i = 0; i < r.segs.length; i++) {
    t -= segDur(r.segs[i]);
    if (t < 0) return i;
  }
  return r.segs.length - 1;
}

function estimateHeard(r) {
  if (r.audioAt == null) return 0;
  const idx = playIndex(r);
  let n = 0;
  for (let i = 0; i < idx; i++) n += words(r.segs[i].text).length;
  if (r.play) return n + Math.floor(words(r.segs[idx]?.text).length / 2);
  let t = (Date.now() - r.audioAt) / 1000;
  for (let i = 0; i < idx; i++) t -= segDur(r.segs[i]);
  return n + Math.max(0, Math.floor(t * WPS));
}

function renderReply(r) {
  const b = r.bubble;
  b.textContent = '';
  const heardN = r.interrupted ? (r.heard != null ? words(r.heard).length : r.heardEst) : null;
  if (r.segs.length && heardN != null) {
    let left = heardN;
    let cut = false;
    for (const s of r.segs) {
      const ws = words(s.text);
      const k = Math.max(0, Math.min(ws.length, left));
      left -= ws.length;
      if (k) b.append(h('span', 'seg spoken', ws.slice(0, k).join(' ') + ' '));
      if (k < ws.length) {
        if (!cut) { b.append(h('span', 'cut', '— ')); cut = true; }
        b.append(h('span', 'unheard', ws.slice(k).join(' ') + ' '));
      }
    }
  } else if (r.segs.length) {
    const idx = r.done ? Infinity : playIndex(r);
    r.lastIdx = idx;
    r.segs.forEach((s, i) => {
      const cls = i < idx ? 'spoken' : i === idx ? 'speaking' : 'pending';
      b.append(h('span', `seg ${cls}`, s.text + ' '));
    });
  } else {
    const text = r.final ?? r.heard ?? r.draft;
    if (text) b.append(h('span', r.interrupted ? 'unheard' : r.done ? '' : 'draft', text));
    if (!r.done) b.append(dots());
  }
  r.meta.textContent = '';
  for (const t of r.tools) {
    const args = t.arguments || {};
    const first = args.goal ?? args.question ?? args.instruction ?? args.task_id ?? '';
    r.meta.append(pill('tool', `${t.name}${first !== '' ? ': ' + first : ''}`));
  }
  if (r.latency != null) r.meta.append(pill('lat', `${Math.round(r.latency)} ms to voice`));
  if (r.barge === 'kept') r.meta.append(pill('ok', `kept going ${r.bargeWhy}`));
  if (r.barge === 'ducked' && !r.done) r.meta.append(pill('', 'ducked'));
  if (r.interrupted) {
    r.meta.append(pill('cutoff', r.stopMs != null ? `interrupted · stopped in ${r.stopMs} ms` : 'interrupted'));
  }
}

function onPlayer(m) {
  if (m.type !== 'played' && m.type !== 'stopped') return;
  const r = S.replies.get(S.segOwner.get(m.seg));
  if (!r || r.done) return;
  r.play = {seg: m.seg};
  if (playIndex(r) !== r.lastIdx) renderReply(r);
}

// ------------------------------------------------------------------ tasks
function upsertTask(t) {
  if (!t || t.id == null) return;
  S.tasks.set(t.id, t);
  renderTask(t);
  orderTasks();
}

function taskElapsed(t) {
  if (t.status === 'running' && t.started > 1e9) return Date.now() / 1000 - t.started;
  if (t.finished && t.started) return t.finished - t.started;
  return (t.ms || 0) / 1000;
}

function fmtS(s) { return s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`; }

function stepText(s) {
  if (typeof s === 'string') return s;
  if (s.what) return s.what;
  if (s.text) return s.text;
  if (Array.isArray(s.actions)) return s.actions.map((a) => a.name).join(', ');
  const {ms, n, ...rest} = s;
  return JSON.stringify(rest);
}

function renderTask(t) {
  let card = S.taskEls.get(t.id);
  if (!card) {
    card = h('div');
    card.dataset.still = S.replay ? '1' : '';
    S.taskEls.set(t.id, card);
  }
  card.className = `task ${t.status}${card.dataset.still ? ' still' : ''}`;
  const head = h('div', 'task-head');
  head.append(icon(t.kind === 'think' ? 'think' : 'phone'), h('span', 'id', `#${t.id}`),
              h('span', null, t.kind === 'think' ? 'Thinking' : 'Phone task'), h('span', 'grow'));
  const el = h('span', 'elapsed', fmtS(taskElapsed(t)));
  head.append(el, h('span', `status ${t.status}`, t.status));
  const steps = h('ol', 'steps');
  for (const s of t.steps || []) {
    const failed = s.stop || (Array.isArray(s.actions) && s.actions.some((a) => a.ok === false));
    const li = h('li', failed ? 'fail' : '');
    li.append(h('span', 'n', s.n ?? ''), h('span', 'what', stepText(s)), h('span', 'ms', s.ms != null ? `${s.ms} ms` : ''));
    li.title = s.screen ? `screen: ${s.screen}` : '';
    steps.append(li);
  }
  if (t.status === 'running') {
    const li = h('li', 'live');
    li.append(h('span', 'n', ''), h('span', 'what', t.detail || 'working'), h('span', 'ms', ''));
    steps.append(li);
  }
  card.replaceChildren(head, h('div', 'task-goal', t.goal), steps);
  if (t.result) card.append(h('div', 'task-result', t.result));
}

function orderTasks() {
  const list = [...S.tasks.values()]
    .sort((a, b) => ((b.status === 'running') - (a.status === 'running')) || b.id - a.id)
    .slice(0, 12);
  const key = list.map((t) => t.id).join(',');
  const running = list.filter((t) => t.status === 'running').length;
  $('task-count').textContent = S.tasks.size ? `${running} running · ${S.tasks.size} total` : '';
  if (key === S.taskOrder) return;
  S.taskOrder = key;
  const box = $('tasks');
  box.textContent = '';
  if (!list.length) {
    box.append(h('div', 'none', 'No tasks yet. Ask Rose to do something on the phone.'));
    return;
  }
  for (const t of list) box.append(S.taskEls.get(t.id));
}

// ------------------------------------------------------------------- phone
function phoneChanged(m) {
  if (typeof m.version === 'number') S.phone.want = Math.max(S.phone.want, m.version);
  if (S.replay) return;
  loadPhone();
  const box = m.box || m.last_tap;
  if (m.what === 'action') {
    if (box) ripple(box);
    fetchPhone(box ? null : m.version);
  } else if (m.what === 'reset') {
    fetchPhone();
  }
}

function loadPhone() {
  const p = S.phone;
  if (p.loading) { p.again = true; return; }
  p.loading = true;
  const img = new Image();
  img.onload = img.onerror = () => {
    if (img.naturalWidth) $('phone-img').src = img.src;
    p.loading = false;
    if (p.again) { p.again = false; loadPhone(); }
  };
  img.src = `/phone.png?v=${p.want}&t=${Date.now()}`;
}

async function fetchPhone(tapVersion) {
  try {
    const s = await (await fetch('/api/phone', {cache: 'no-store'})).json();
    caption(s);
    if (tapVersion != null && s.last_tap && s.version >= tapVersion && s.version > S.phone.lastTap) {
      S.phone.lastTap = s.version;
      ripple(s.last_tap, s.size);
    }
  } catch { /* the next event retries */ }
}

function ripple(box, size = [390, 844]) {
  const [W, H] = size;
  const [x0, y0, x1, y1] = box;
  const layer = $('taps');
  const rect = h('div', 'tap-box');
  Object.assign(rect.style, {left: `${x0 / W * 100}%`, top: `${y0 / H * 100}%`,
                             width: `${(x1 - x0) / W * 100}%`, height: `${(y1 - y0) / H * 100}%`});
  const dot = h('div', 'tap-dot');
  Object.assign(dot.style, {left: `${(x0 + x1) / 2 / W * 100}%`, top: `${(y0 + y1) / 2 / H * 100}%`});
  layer.append(rect, dot);
  setTimeout(() => { rect.remove(); dot.remove(); }, 1100);
}

function caption(s) {
  const bits = [s.title || s.screen || 'Phone'];
  if (s.view && s.view !== 'main') bits.push(`task view ${s.view.replace(/^task/, '#')}`);
  if (s.toast) bits.push(s.toast);
  if (s.status?.dnd) bits.push('Do Not Disturb');
  $('phone-caption').textContent = bits.join(' · ');
}

// --------------------------------------------------------------------- hud
function turnRec(id) {
  let rec = S.turns.get(id);
  if (!rec) { rec = {id}; S.turns.set(id, rec); }
  return rec;
}

function derive(rec) {
  const v2v = rec.voice_to_voice_ms ?? rec.latency_ms;
  const ep = rec.endpoint_ms;
  const llm = rec.llm_ttft_ms ?? (rec.llm_first_token_ms != null && ep != null
    ? rec.llm_first_token_ms - ep : rec.llm_first_token_ms);
  const tts = rec.tts_first_audio_ms ?? rec.tts_ms
    ?? (v2v != null && rec.first_speech_text_ms != null ? v2v - rec.first_speech_text_ms : null);
  return {v2v, endpoint: ep, asr: rec.asr_ms, llm, tts};
}

const ROWS = [
  ['v2v', 'Voice to voice', 'end of speech → first audio'],
  ['endpoint', 'Endpoint wait', 'silence before commit'],
  ['asr', 'ASR final', 'whole utterance'],
  ['llm', 'LLM first token', 'commit → first token'],
  ['tts', 'TTS first audio', 'first chunk → audio'],
];

function grade(key, v) {
  if (v == null) return 'dim';
  if (key !== 'v2v') return '';
  return v <= TARGET_MS ? 'good' : v <= 900 ? 'meh' : 'slow';
}

function fmtMs(v) { return v == null ? '—' : `${Math.round(v)}`; }

function renderHud() {
  if (S.replay) return;
  const recs = [...S.turns.values()]
    .filter((r) => r.hasMetrics && !r.announcement && (r.voice_to_voice_ms ?? r.latency_ms) != null)
    .sort((a, b) => a.id - b.id).slice(-50);
  $('turn-count').textContent = recs.length ? `${recs.length} turn${recs.length > 1 ? 's' : ''} · ms` : '';
  const last = recs.length ? derive(recs.at(-1)) : {};
  const all = recs.map(derive);
  const body = $('hud').tBodies[0];
  body.textContent = '';
  for (const [key, label, hint] of ROWS) {
    const tr = h('tr');
    const name = h('td', null, label);
    name.append(h('small', null, hint));
    const med = median(all.map((d) => d[key]).filter((v) => v != null));
    tr.append(name, h('td', grade(key, last[key]), fmtMs(last[key])), h('td', grade(key, med), fmtMs(med)));
    body.append(tr);
  }
  renderStack(last);
}

function renderStack(d) {
  const box = $('hud-bar');
  box.textContent = '';
  if (d.v2v == null) {
    box.append(h('div', 'legend', 'Waiting for the first spoken reply.'));
    return;
  }
  const scale = Math.max(d.v2v, 900);
  const parts = [['ep', 'endpoint', d.endpoint], ['llm', 'LLM', d.llm], ['tts', 'TTS', d.tts]];
  const known = parts.reduce((a, [, , v]) => a + (v > 0 ? v : 0), 0);
  parts.splice(2, 0, ['txt', 'first clause', Math.max(0, d.v2v - known)]);
  const wrap = h('div', 'stack-wrap');
  const bar = h('div', 'stack');
  const legend = h('div', 'legend');
  for (const [cls, name, v] of parts) {
    if (!(v > 0)) continue;
    const seg = h('span', cls);
    seg.style.width = `${v / scale * 100}%`;
    seg.title = `${name}: ${Math.round(v)} ms`;
    bar.append(seg);
    const item = h('span');
    item.append(h('i', cls), `${name} ${Math.round(v)}`);
    legend.append(item);
  }
  const target = h('div', 'target');
  target.style.left = `${TARGET_MS / scale * 100}%`;
  target.title = `target ${TARGET_MS} ms`;
  wrap.append(bar, target);
  box.append(wrap, legend);
}

function renderStats(m) {
  const parts = [];
  if (m.mode === 'ui-only') parts.push('ui-only');
  const rt = m.runtime;
  if (rt) {
    const total = Object.values(rt.busy_s || {}).reduce((a, b) => a + b, 0);
    const now = performance.now() / 1000;
    if (S.busy) {
      const pct = (total - S.busy.total) / Math.max(0.2, now - S.busy.t) * 100;
      parts.push(`gpu ${Math.max(0, Math.min(100, pct)).toFixed(0)}%`);
    }
    S.busy = {total, t: now};
    if (rt.current) parts.push(`job ${rt.current}`);
    const w = rt.wait_ms || {};
    if (w.speak?.p50 != null) parts.push(`speak wait ${w.speak.p50} ms`);
  }
  if (m.listener?.vad_ms_p50 != null) parts.push(`vad ${m.listener.vad_ms_p50} ms`);
  if (m.keepalive) parts.push(`keepalive ${m.keepalive.pings}`);
  if (m.audio?.output) parts.push(`audio: ${m.audio.output}`);
  $('stats').textContent = parts.join(' · ');
  $('stats').title = JSON.stringify(m);
}

// ---------------------------------------------------------------- reset
function clearView(keepHud) {
  removePartial();
  for (const el of [...convo.children]) if (el.id !== 'empty') el.remove();
  $('empty').hidden = false;
  S.replies.clear();
  S.users.clear();
  S.segOwner.clear();
  S.active.clear();
  S.tasks.clear();
  S.taskEls.clear();
  S.taskOrder = null;
  S.lastRose = null;
  orderTasks();
  if (!keepHud) S.turns.clear();
  renderHud();
}

// ---------------------------------------------------------------- audio
class DashAudio extends RoseAudio {
  _connect() {
    super._connect();
    const ws = this.ws;
    const reconnect = ws.onclose;
    ws.onclose = (e) => {
      if (e.code === 4001 || e.code === 4003) this.onClosedByServer?.(e);
      else reconnect(e);
    };
  }

  _fromPlayer(m) {
    super._fromPlayer(m);
    onPlayer(m);
  }
}

function setMicUi(on) {
  const btn = $('mic');
  btn.classList.toggle('live', on);
  btn.setAttribute('aria-pressed', String(on));
  $('mic-label').textContent = on ? 'Stop microphone' : 'Start microphone';
  if (!on) setLevel(0);
  $('mic-hint').textContent = on
    ? 'Live. Headphones work best; otherwise the browser cancels Rose\'s echo.'
    : 'Wear headphones, or rely on the browser\'s echo cancellation.';
}

async function stopMic() {
  const a = S.audio;
  S.audio = null;
  setMicUi(false);
  if (a) await a.stop().catch(() => {});
}

async function toggleMic() {
  if (S.starting) return;
  if (S.audio) { await stopMic(); return; }
  S.starting = true;
  $('mic').classList.add('busy');
  const a = new DashAudio({
    onLevel: (rms) => setLevel(rms),
    onStatus: (s) => {
      if (s === 'reconnecting' && S.audio === a) $('mic-hint').textContent = 'Audio link lost; reconnecting.';
      if (s === 'live' && S.audio === a) setMicUi(true);
    },
  });
  a.onClosedByServer = (e) => {
    toast(e.code === 4001 ? 'Another dashboard took over the audio.'
                          : 'The server is not using browser audio (--local-audio or --no-browser-audio).', 'warn');
    if (S.audio === a) stopMic();
  };
  try {
    await a.start();
    S.audio = a;
    setMicUi(true);
  } catch (err) {
    const why = err && err.name === 'NotAllowedError' ? 'Microphone permission was denied.'
      : `Could not start audio: ${err && err.message ? err.message : err}`;
    toast(why, 'bad');
    await a.stop().catch(() => {});
  } finally {
    S.starting = false;
    $('mic').classList.remove('busy');
  }
}

function isTyping(el) {
  if (!el || !(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') return true;
  return el.tagName === 'INPUT' && !['button', 'checkbox', 'radio', 'submit', 'reset'].includes(el.type);
}

// ----------------------------------------------------------------- http
async function post(path, body) {
  const res = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                 body: JSON.stringify(body || {})});
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

async function say(text) {
  text = text.trim();
  if (!text) return;
  const btn = $('say-btn');
  btn.disabled = true;
  try {
    await post('/api/say', {text});
    $('say-text').value = '';
  } catch (err) {
    toast(`Could not send: ${err.message}`, 'bad');
  } finally {
    btn.disabled = false;
  }
}

function toast(text, kind = '') {
  const el = h('div', `toast ${kind}`, text);
  $('toasts').append(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 7000 : 4000);
}

// --------------------------------------------------------------- pairing
// "Pair phone": a one-time QR code (10 minutes, single use) from /api/pair/new
// that Rose Agent scans to reach this Mac over Wi-Fi or Tailscale; the list of
// paired phones with Remove buttons. The server keeps the code; this page only
// shows the QR image and polls /api/pair/status until a phone uses it.
// note: a message that replaces the countdown until the next code (e.g. the lockout).
const PAIR = {open: false, expiresAt: 0, done: false, note: '', poll: null, tick: null, busy: false};

async function getJson(path) {
  const res = await fetch(path, {cache: 'no-store'});
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function routeOf(host) {
  if (host.endsWith('.ts.net')) return 'Tailscale';
  const m = /^(\d+)\.(\d+)\.\d+\.\d+$/.exec(host);
  return m && +m[1] === 100 && +m[2] >= 64 && +m[2] <= 127 ? 'Tailscale' : 'Wi-Fi';
}

function ago(ms) {
  if (!ms) return 'never';
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ms).toLocaleDateString();
}

function pairError(text) {
  const el = $('pair-error');
  el.hidden = !text;
  el.textContent = text || '';
}

function pairCountdown() {
  const left = PAIR.expiresAt - Date.now();
  const box = $('pair-qr-box');
  if (PAIR.done) return;
  if (PAIR.note) { $('pair-count').textContent = PAIR.note; return; }
  if (!PAIR.expiresAt) { $('pair-count').textContent = '—'; return; }
  if (left <= 0) {
    box.classList.add('stale');
    $('pair-count').textContent = 'Expired. Make a new code.';
    return;
  }
  box.classList.remove('stale');
  const s = Math.ceil(left / 1000);
  $('pair-count').textContent = `Valid for ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}, once`;
}

async function newPairCode() {
  if (PAIR.busy) return;
  PAIR.busy = true;
  PAIR.done = false;
  PAIR.note = '';
  $('pair-done').hidden = true;
  $('pair-new').disabled = true;
  pairError('');
  try {
    const inv = await post('/api/pair/new', {});
    const img = $('pair-qr');
    img.onload = () => {
      // Whole device pixels per module keep the edges sharp (the PNG is 8 px per module).
      const modules = img.naturalWidth / 8;
      const per = Math.max(3, Math.floor(300 / modules));
      img.style.width = img.style.height = `${modules * per}px`;
    };
    img.src = inv.png;
    $('pair-name').textContent = inv.serverName;
    // The phone's confirm screen shows the same ID (the first 16 hex of the certificate pin) to compare.
    $('pair-id').textContent = (inv.pin || '').slice(0, 16).match(/.{1,4}/g)?.join(' ') || '';
    const hosts = $('pair-hosts');
    hosts.replaceChildren(...inv.hosts.map((host) => {
      const row = h('div', 'pair-host');
      row.append(h('span', 'mono', host), h('span', 'pill', routeOf(host)));
      return row;
    }));
    PAIR.expiresAt = inv.expiresAt;
    PAIR.note = '';  // a status poll from before this code may have set the lockout note
  } catch (err) {
    PAIR.expiresAt = 0;
    $('pair-qr').removeAttribute('src');
    $('pair-qr-box').classList.add('stale');
    pairError(err.message);
  } finally {
    PAIR.busy = false;
    $('pair-new').disabled = false;
    pairCountdown();
  }
}

async function loadPairedDevices() {
  const list = $('pair-devices');
  let devices;
  try {
    devices = (await getJson('/api/pair/devices')).devices.filter((d) => !d.revoked);
  } catch (err) {
    list.replaceChildren(h('li', 'none', `Could not load paired phones: ${err.message}`));
    return [];
  }
  if (!devices.length) {
    list.replaceChildren(h('li', 'none', 'None yet.'));
    return devices;
  }
  list.replaceChildren(...devices.map((d) => {
    const li = h('li', 'pair-device');
    const text = h('div', 'pair-device-text');
    text.append(h('b', null, d.name), h('span', 'muted', d.model ? ` ${d.model}` : ''));
    const seen = d.lastAddress ? `${ago(d.lastSeen)} via ${routeOf(d.lastAddress)} (${d.lastAddress})` : ago(d.lastSeen);
    text.append(h('div', 'muted small', `Paired ${new Date(d.pairedAt).toLocaleDateString()} · last seen ${seen}`));
    const rm = h('button', 'ghost danger', 'Remove');
    rm.type = 'button';
    rm.onclick = async () => {
      if (!confirm(`Remove ${d.name}? It will lose its connection to Rose until it is paired again.`)) return;
      try {
        await post('/api/pair/revoke', {deviceId: d.id});
        toast(`Removed ${d.name}`, 'ok');
      } catch (err) {
        toast(`Could not remove: ${err.message}`, 'bad');
      }
      loadPairedDevices();
    };
    li.append(text, rm);
    return li;
  }));
  return devices;
}

async function pollPair() {
  if (!PAIR.open || PAIR.done) return;
  let st;
  try { st = await getJson('/api/pair/status'); } catch { return; }
  if (st.pairedDeviceId) {
    PAIR.done = true;
    const name = st.pairedDeviceName || 'your phone';
    const done = $('pair-done');
    done.textContent = `Paired with ${name}`;
    done.hidden = false;
    $('pair-count').textContent = 'Used';
    loadPairedDevices();
    toast(`Paired with ${name}`, 'ok');
    setTimeout(() => { if (PAIR.done) closePair(); }, 2000);
  } else if (!st.pending && st.state === 'locked') {
    PAIR.expiresAt = 0;
    PAIR.note = 'Too many wrong attempts. Make a new code.';
    $('pair-qr-box').classList.add('stale');
    pairCountdown();
  }
}

function openPair() {
  if (PAIR.open) return;
  PAIR.open = true;
  $('pair-modal').hidden = false;
  $('pair-close').focus();
  newPairCode();
  loadPairedDevices();
  PAIR.poll = setInterval(pollPair, 1000);
  PAIR.tick = setInterval(pairCountdown, 500);
}

function closePair() {
  PAIR.open = false;
  PAIR.done = false;
  PAIR.note = '';
  clearInterval(PAIR.poll);
  clearInterval(PAIR.tick);
  $('pair-modal').hidden = true;
  $('pair').focus();
}

// ----------------------------------------------------------------- setup
function setup() {
  const chips = $('chips');
  for (const text of EXAMPLES) {
    const b = h('button', 'chip', text);
    b.type = 'button';
    b.onclick = () => say(text);
    chips.append(b);
  }
  const sim = $('sim');
  for (const n of INCOMING) {
    const b = h('button', 'chip notify');
    b.type = 'button';
    b.append(h('b', null, `${n.title}:`), n.text);
    b.onclick = () => post('/api/notify', n).catch((err) => toast(err.message, 'bad'));
    sim.append(b);
  }
  $('say-form').addEventListener('submit', (e) => { e.preventDefault(); say($('say-text').value); });
  $('mic').addEventListener('click', () => toggleMic());
  $('reset').addEventListener('click', () => post('/api/reset', {}).catch((err) => toast(err.message, 'bad')));
  $('pair').addEventListener('click', () => openPair());
  $('pair-close').addEventListener('click', () => closePair());
  $('pair-new').addEventListener('click', () => newPairCode());
  $('pair-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) closePair(); });
  $('voice').addEventListener('change', async (e) => {
    const sel = e.target;
    try {
      await post('/api/voice', {voice: sel.value});
    } catch (err) {
      toast(err.message, 'bad');
    }
  });
  document.addEventListener('keydown', (e) => {
    if (PAIR.open && e.key === 'Escape') { closePair(); return; }
    if (PAIR.open) return;  // the modal owns the keyboard
    if (e.code !== 'Space' || e.repeat || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
    e.preventDefault();
    if (e.target instanceof HTMLElement && e.target !== document.body) e.target.blur();
    toggleMic();
  });
  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && !isTyping(e.target)) e.preventDefault();
  });

  // Word highlighting and task timers between events.
  let tick = 0;
  setInterval(() => {
    for (const id of S.active) {
      const r = S.replies.get(id);
      if (!r || r.done) { S.active.delete(id); continue; }
      if (r.segs.length && playIndex(r) !== r.lastIdx) renderReply(r);
    }
    if (++tick % 4 === 0) {
      for (const t of S.tasks.values()) {
        if (t.status !== 'running') continue;
        const el = S.taskEls.get(t.id)?.querySelector('.elapsed');
        if (el) el.textContent = fmtS(taskElapsed(t));
      }
    }
  }, 120);
  setInterval(() => loadPhone(), 30000);  // the status-bar clock

  renderHud();
  connect();
}

setup();
