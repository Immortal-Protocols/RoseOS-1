// Rose HiFi — pure state model. No DOM access; exercised directly by verify.cjs.
// Axes that must stay independent: connection, microphone mute, Rose speech,
// background tasks and navigation. Only explicit task events change tasks.
'use strict';

const APPS = {
  firefox: { name: 'Firefox', kind: 'browser' },
  thunderbird: { name: 'Thunderbird', kind: 'mail' },
  vlc: { name: 'VLC', kind: 'media' },
  camera: { name: 'Camera', kind: 'camera' },
  settings: { name: 'Settings', kind: 'settings' },
  photos: { name: 'Photos', kind: 'photos' },
  maps: { name: 'Maps', kind: 'maps' },
  notes: { name: 'Notes', kind: 'notes' },
  calendar: { name: 'Calendar', kind: 'calendar' },
  files: { name: 'Files', kind: 'files' },
  clock: { name: 'Clock', kind: 'clock' },
  weather: { name: 'Weather', kind: 'weather' },
  messages: { name: 'Messages', kind: 'messages' },
  music: { name: 'Music', kind: 'music' },
  calculator: { name: 'Calculator', kind: 'calculator' },
  contacts: { name: 'Contacts', kind: 'contacts' },
  editor: { name: 'Editor', kind: 'editor' },
  phone: { name: 'Phone', kind: 'phone' },
  mail: { name: 'Mail', kind: 'mail' },
  system: { name: 'System apps', kind: 'settings' },
};

// Oxblood (study design 07): page 1 holds the default system apps as subdued monoline icons;
// page 2 keeps regular store apps with their own icons, which the OS does not restyle.
const HOME_PAGES_OXBLOOD = [
  ['phone', 'messages', 'maps', 'camera', 'mail', 'calendar', 'notes', 'system'],
  ['firefox', 'thunderbird', 'vlc', 'photos', 'weather', 'music', 'files', 'editor'],
  ['clock', 'calculator', 'contacts', 'settings'],
];
// System apps drawn in the shared monoline style under Oxblood.
const MONO_APPS = ['phone', 'messages', 'maps', 'camera', 'mail', 'calendar', 'notes', 'system', 'clock', 'calculator', 'contacts', 'settings'];

const HOME_PAGES = [
  ['vlc', 'camera', 'settings', 'firefox', 'thunderbird'],
  ['firefox', 'thunderbird', 'vlc', 'camera', 'settings', 'photos', 'maps', 'notes',
    'calendar', 'files', 'clock', 'weather', 'messages', 'music', 'calculator', 'contacts',
    'editor', 'phone'],
  ['photos', 'maps', 'notes', 'calendar'],
];

// One task can use several windows; eight windows are not eight tasks.
const TASKS = {
  saturday: { title: 'Plan a quiet Saturday', windows: ['w-browser', 'w-maps', 'w-notes', 'w-calendar'] },
  reply: { title: 'Draft a reply to Jamie', windows: ['w-mail', 'w-files'] },
  compare: { title: 'Compare two cafés', windows: ['w-research', 'w-editor'] },
};

const WINDOWS = {
  'w-browser': { app: 'firefox', task: 'saturday', label: 'Garden hours' },
  'w-maps': { app: 'maps', task: 'saturday', label: 'Walking route' },
  'w-notes': { app: 'notes', task: 'saturday', label: 'Saturday list' },
  'w-calendar': { app: 'calendar', task: 'saturday', label: 'Saturday' },
  'w-mail': { app: 'thunderbird', task: 'reply', label: 'Reply to Jamie' },
  'w-files': { app: 'files', task: 'reply', label: 'Weekend notes' },
  'w-research': { app: 'firefox', task: 'compare', label: 'Café reviews' },
  'w-editor': { app: 'editor', task: 'compare', label: 'Comparison' },
};
const WINDOW_ORDER = Object.keys(WINDOWS);

const STOPPABLE = ['running', 'attention'];
const CONVERSATION_CUES = ['Connected', 'Muted', 'Unmuted', 'Call ended'];
const MAX_STEP = 8;

function initialState() {
  const tasks = {};
  for (const id of Object.keys(TASKS)) tasks[id] = { state: 'idle', step: 0 };
  return {
    layout: 'folded',
    view: 'home',            // home | rose | switcher | agents | app | window
    back: [],                // navigation stack of {view, app, window}
    homePage: 0,
    app: null,
    window: null,
    connection: 'disconnected', // disconnected | connecting | connected
    muted: false,            // microphone (input) mute — only the call's mic control changes it
    voiceMuted: false,       // Rose's voice (output/speaker) mute — independent of mic, call, speech and tasks
    speaking: false,
    result: null,            // id of formatted result shown in Rose
    tasks,
    pendingStop: null,
    locked: false,
    notices: [],             // {id, kind:'cue'|'task', text, task?}
    noticeSerial: 0,
    humanCarousel: 0,
    agentCarousel: 0,
    recentApps: ['photos', 'firefox', 'camera'],
  };
}

const clone = (s) => JSON.parse(JSON.stringify(s));

function voiceSurround(s) {
  if (s.connection !== 'connected') return 'quiet';
  if (s.speaking) return 'speaking';
  return s.muted ? 'quiet' : 'listening';
}

// Exact WebGL mapping from prototypes/rose-three-dimensions (README):
// mic on → rose metal, muted → silver; connected joined, connecting narrow gap,
// disconnected wide gap; surround quiet / listening close / speaking broad.
function rendererSignals(s) {
  return {
    mic: s.muted ? 0 : 1,
    connection: s.connection,
    voice: voiceSurround(s),
  };
}

function taskActivity(s) {
  const active = [];
  for (const [id, t] of Object.entries(s.tasks)) if (t.state === 'running') active.push(id);
  return active;
}

function transition(previous, event) {
  const s = clone(previous);
  const effects = [];
  const notice = (kind, text, task) => {
    if (kind === 'cue' && !CONVERSATION_CUES.includes(text)) throw new Error('Unapproved conversation cue: ' + text);
    s.noticeSerial += 1;
    s.notices = s.notices.filter((n) => n.kind !== kind || kind === 'task').concat({ id: s.noticeSerial, kind, text, task: task || null });
    if (s.notices.length > 3) s.notices = s.notices.slice(-3);
    effects.push({ type: 'notice', id: s.noticeSerial, kind, text });
  };
  const go = (view, extra) => {
    if (s.view !== view || (extra && (extra.app !== s.app || extra.window !== s.window))) {
      s.back.push({ view: s.view, app: s.app, window: s.window });
      if (s.back.length > 12) s.back.shift();
    }
    s.view = view;
    s.app = extra && 'app' in extra ? extra.app : s.app;
    s.window = extra && 'window' in extra ? extra.window : s.window;
  };
  const task = event.id ? s.tasks[event.id] : null;
  if (s.locked && !['unlock', 'lock', 'reset', 'layout', 'tick', 'stopAck', 'connected', 'speechEnd', 'dismissNotice', 'taskEvent', 'startTask', 'speak', 'result'].includes(event.type)) {
    return { state: previous, effects: [] };
  }

  switch (event.type) {
    case 'reset': return { state: initialState(), effects: [{ type: 'reset' }] };
    case 'layout': if (['folded', 'unfolded'].includes(event.value)) s.layout = event.value; break;
    case 'lock': s.locked = true; break;
    case 'unlock': s.locked = false; break;
    case 'homePage': s.homePage = Math.max(0, Math.min((event.max || HOME_PAGES.length) - 1, event.value | 0)); break;

    case 'openRose':
      go('rose', { app: null, window: null });
      // Mic mute is universal: a call starts in whatever state the microphone is already in.
      if (s.connection === 'disconnected') { s.connection = 'connecting'; effects.push({ type: 'connect' }); }
      break;
    case 'connected':
      if (s.connection === 'connecting') { s.connection = 'connected'; notice('cue', 'Connected'); }
      break;
    case 'micMute':
      // Universal microphone mute (study 09, Home tray): works with or without a call and persists
      // across calls. Never touches Rose's speech, voice output, the call itself, results or tasks.
      s.muted = typeof event.value === 'boolean' ? event.value : !s.muted;
      notice('cue', s.muted ? 'Muted' : 'Unmuted');
      break;
    case 'voiceMute':
      // Output only: never touches mic mute, connection, speaking, results or tasks; no conversation cue.
      s.voiceMuted = typeof event.value === 'boolean' ? event.value : !s.voiceMuted;
      break;
    case 'mute':
      if (s.connection === 'connected') { s.muted = !s.muted; notice('cue', s.muted ? 'Muted' : 'Unmuted'); }
      break;
    case 'speak':
      // Rose may speak while the microphone is muted; speech never changes mute.
      if (s.connection === 'connected') { s.speaking = true; effects.push({ type: 'speech', ms: event.ms || 4200 }); }
      break;
    case 'speechEnd': s.speaking = false; break;
    case 'result': if (s.connection === 'connected' || event.force) s.result = event.value; break;
    case 'endCall':
      if (s.connection !== 'disconnected') {
        s.connection = 'disconnected'; s.speaking = false;
        notice('cue', 'Call ended'); effects.push({ type: 'endVoice' });
        // Classic returns Home; Oxblood (study 11) stays on Rose with Connect in the same place.
        if (s.view === 'rose' && !event.stay) { go('home', { app: null, window: null }); s.back = []; }
      }
      break;

    case 'home': go('home', { app: null, window: null }); s.back = []; break;
    case 'back': {
      const prev = s.back.pop();
      if (prev) { s.view = prev.view; s.app = prev.app; s.window = prev.window; } else s.view = 'home';
      break;
    }
    case 'switcher': if (s.view !== 'switcher') go('switcher'); break;
    case 'agents': go('agents', { window: null }); break;
    case 'openApp':
      if (APPS[event.value]) {
        go('app', { app: event.value, window: null });
        s.recentApps = [event.value].concat(s.recentApps.filter((a) => a !== event.value)).slice(0, 6);
        s.humanCarousel = 0; // the opened app becomes the front card
      }
      break;
    case 'openWindow': if (WINDOWS[event.value]) go('window', { window: event.value, app: null }); break;
    case 'carousel': {
      const key = event.which === 'agent' ? 'agentCarousel' : 'humanCarousel';
      s[key] = Math.max(0, event.value | 0);
      break;
    }

    case 'startTask':
      if (task && ['idle', 'stopped', 'done'].includes(task.state)) { task.state = 'running'; task.step = 1; }
      break;
    case 'tick':
      for (const [id, t] of Object.entries(s.tasks)) {
        if (t.state !== 'running') continue;
        t.step = Math.min(MAX_STEP, t.step + 1);
        if (t.step >= MAX_STEP && !event.hold) { t.state = 'done'; notice('task', TASKS[id].title + ' is ready.', id); if (s.pendingStop === id) s.pendingStop = null; }
      }
      break;
    case 'taskEvent':
      if (task && event.value === 'attention' && task.state === 'running') { task.state = 'attention'; notice('task', TASKS[event.id].title + ' needs your choice.', event.id); }
      else if (task && event.value === 'resume' && task.state === 'attention') task.state = 'running';
      else if (task && event.value === 'done' && STOPPABLE.includes(task.state)) { task.state = 'done'; task.step = MAX_STEP; notice('task', TASKS[event.id].title + ' is ready.', event.id); if (s.pendingStop === event.id) s.pendingStop = null; }
      break;
    case 'askStop': if (task && STOPPABLE.includes(task.state)) s.pendingStop = event.id; break;
    case 'cancelStop': s.pendingStop = null; break;
    case 'confirmStop': {
      const id = s.pendingStop;
      if (id && STOPPABLE.includes(s.tasks[id].state)) { s.tasks[id].state = 'stopping'; effects.push({ type: 'stopAck', id }); }
      s.pendingStop = null;
      break;
    }
    case 'stopAck': if (task && task.state === 'stopping') task.state = 'stopped'; break;
    case 'dismissNotice': s.notices = s.notices.filter((n) => n.id !== event.value); break;
  }
  return { state: s, effects };
}

const RoseModel = { APPS, HOME_PAGES, HOME_PAGES_OXBLOOD, MONO_APPS, TASKS, WINDOWS, WINDOW_ORDER, STOPPABLE, CONVERSATION_CUES, MAX_STEP, initialState, transition, rendererSignals, voiceSurround, taskActivity };
if (typeof module !== 'undefined') module.exports = RoseModel;
else window.RoseModel = RoseModel;
