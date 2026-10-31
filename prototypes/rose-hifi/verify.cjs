// Rose HiFi — dependency-free model checks. Usage: node prototypes/rose-hifi/verify.cjs
// Exercises src/model.js directly. Does not validate rendering, layout or gestures;
// see browser-check.cjs for real-browser evidence.
'use strict';
const assert = require('assert');
const M = require('./src/model.js');

let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log('ok ' + passed + ' - ' + name); };
const run = (s, ...events) => events.reduce((acc, e) => M.transition(acc, e).state, s);
const fresh = () => M.initialState();
const connected = () => run(fresh(), { type: 'openRose' }, { type: 'connected' });
const working = () => run(connected(), ...Object.keys(M.TASKS).map((id) => ({ type: 'startTask', id })));

test('eight windows belong to three tasks; every task window exists', () => {
  assert.strictEqual(M.WINDOW_ORDER.length, 8);
  for (const [id, t] of Object.entries(M.TASKS)) for (const w of t.windows) assert.strictEqual(M.WINDOWS[w].task, id);
});
test('opening Rose connects; cue limited to Connected', () => {
  const r = M.transition(run(fresh(), { type: 'openRose' }), { type: 'connected' });
  assert.strictEqual(r.state.connection, 'connected');
  assert.deepStrictEqual(r.effects.map((e) => e.text), ['Connected']);
});
test('only approved conversation cues can be emitted', () => {
  let s = connected(); const texts = [];
  for (const e of [{ type: 'mute' }, { type: 'speak' }, { type: 'mute' }, { type: 'speechEnd' }, { type: 'endCall' }]) {
    const r = M.transition(s, e); s = r.state;
    for (const fx of r.effects) if (fx.type === 'notice' && fx.kind === 'cue') texts.push(fx.text);
  }
  assert.deepStrictEqual(texts, ['Muted', 'Unmuted', 'Call ended']);
  for (const t of texts) assert.ok(M.CONVERSATION_CUES.includes(t));
});
test('Rose can speak while muted; speech never changes mute', () => {
  const s = run(connected(), { type: 'mute' }, { type: 'speak' });
  assert.ok(s.muted && s.speaking);
  assert.deepStrictEqual(M.rendererSignals(s), { mic: 0, connection: 'connected', voice: 'speaking' });
  const t = run(s, { type: 'speechEnd' });
  assert.ok(t.muted && !t.speaking);
});
test('mute does not stop speech; unmute does not start it', () => {
  const s = run(connected(), { type: 'speak' }, { type: 'mute' });
  assert.ok(s.speaking);
  const t = run(connected(), { type: 'mute' }, { type: 'mute' });
  assert.ok(!t.speaking && !t.muted);
});
test('renderer mapping follows the three-dimensions study', () => {
  assert.deepStrictEqual(M.rendererSignals(fresh()), { mic: 1, connection: 'disconnected', voice: 'quiet' });
  assert.strictEqual(M.rendererSignals(run(fresh(), { type: 'openRose' })).connection, 'connecting');
  assert.strictEqual(M.rendererSignals(connected()).voice, 'listening');
  assert.strictEqual(M.rendererSignals(run(connected(), { type: 'mute' })).voice, 'quiet');
});
test('end call returns Home, keeps tasks and their steps', () => {
  const before = run(working(), { type: 'tick' });
  const s = run(before, { type: 'endCall' });
  assert.strictEqual(s.view, 'home'); assert.strictEqual(s.connection, 'disconnected');
  assert.deepStrictEqual(s.tasks, before.tasks);
});
test('navigation, lock and layout never cancel tasks', () => {
  const start = working();
  const s = run(start, { type: 'home' }, { type: 'switcher' }, { type: 'openApp', value: 'photos' }, { type: 'lock' }, { type: 'unlock' },
    { type: 'layout', value: 'unfolded' }, { type: 'agents' }, { type: 'openWindow', value: 'w-maps' }, { type: 'back' }, { type: 'layout', value: 'folded' });
  for (const id of Object.keys(M.TASKS)) assert.strictEqual(s.tasks[id].state, 'running');
});
test('work continues while locked', () => {
  const s = run(working(), { type: 'lock' }, { type: 'tick' }, { type: 'tick' });
  assert.ok(s.locked); assert.strictEqual(s.tasks.saturday.step, 3);
});
test('locked phone ignores phone-surface input', () => {
  const s = run(working(), { type: 'lock' }, { type: 'askStop', id: 'reply' }, { type: 'openRose' }, { type: 'mute' });
  assert.strictEqual(s.pendingStop, null); assert.strictEqual(s.view, 'rose'); assert.ok(!s.muted);
});
test('stop requires confirmation; cancel preserves the task', () => {
  const s = run(working(), { type: 'askStop', id: 'reply' });
  assert.strictEqual(s.pendingStop, 'reply'); assert.strictEqual(s.tasks.reply.state, 'running');
  const c = run(s, { type: 'cancelStop' });
  assert.strictEqual(c.pendingStop, null); assert.strictEqual(c.tasks.reply.state, 'running');
});
test('confirm → Stopping → acknowledged Stopped; unrelated tasks continue', () => {
  const r = M.transition(run(working(), { type: 'askStop', id: 'reply' }), { type: 'confirmStop' });
  assert.strictEqual(r.state.tasks.reply.state, 'stopping');
  assert.deepStrictEqual(r.effects, [{ type: 'stopAck', id: 'reply' }]);
  let s = run(r.state, { type: 'tick' });
  assert.strictEqual(s.tasks.reply.state, 'stopping');
  assert.strictEqual(s.tasks.saturday.state, 'running'); assert.strictEqual(s.tasks.compare.state, 'running');
  s = run(s, { type: 'stopAck', id: 'reply' });
  assert.strictEqual(s.tasks.reply.state, 'stopped');
  const stepBefore = s.tasks.saturday.step;
  s = run(s, { type: 'tick' });
  assert.strictEqual(s.tasks.saturday.step, stepBefore + 1);
});
test('no stop-all: one confirmation stops exactly one task', () => {
  const s = run(working(), { type: 'askStop', id: 'saturday' }, { type: 'confirmStop' }, { type: 'stopAck', id: 'saturday' });
  assert.deepStrictEqual(Object.values(s.tasks).map((t) => t.state), ['stopped', 'running', 'running']);
});
test('a task that finishes while its Stop is pending closes the confirmation', () => {
  let s = run(working(), { type: 'askStop', id: 'reply' });
  for (let i = 0; i < M.MAX_STEP; i++) s = run(s, { type: 'tick' });
  assert.strictEqual(s.tasks.reply.state, 'done'); assert.strictEqual(s.pendingStop, null);
});
test('done and attention produce task notices, not conversation cues', () => {
  const r = M.transition(working(), { type: 'taskEvent', id: 'reply', value: 'attention' });
  assert.strictEqual(r.effects[0].kind, 'task');
  const d = M.transition(r.state, { type: 'taskEvent', id: 'reply', value: 'done' });
  assert.strictEqual(d.effects[0].kind, 'task'); assert.strictEqual(d.state.tasks.reply.state, 'done');
});
test('stopping or stopped tasks cannot be stopped again', () => {
  const s = run(working(), { type: 'askStop', id: 'reply' }, { type: 'confirmStop' });
  assert.strictEqual(run(s, { type: 'askStop', id: 'reply' }).pendingStop, null);
});
test('layout change preserves call, mute, view, carousel and open confirmation', () => {
  const s = run(working(), { type: 'mute' }, { type: 'agents' }, { type: 'carousel', which: 'agent', value: 3 }, { type: 'askStop', id: 'compare' }, { type: 'layout', value: 'unfolded' });
  assert.strictEqual(s.layout, 'unfolded'); assert.ok(s.muted); assert.strictEqual(s.connection, 'connected');
  assert.strictEqual(s.view, 'agents'); assert.strictEqual(s.agentCarousel, 3); assert.strictEqual(s.pendingStop, 'compare');
});
test('home page and back navigation are retained', () => {
  const s = run(fresh(), { type: 'homePage', value: 1 }, { type: 'openApp', value: 'notes' }, { type: 'back' });
  assert.strictEqual(s.view, 'home'); assert.strictEqual(s.homePage, 1);
  assert.strictEqual(run(fresh(), { type: 'homePage', value: 9 }).homePage, M.HOME_PAGES.length - 1);
});
test('leaving Rose keeps the call; returning does not reconnect', () => {
  const s = run(connected(), { type: 'home' });
  assert.strictEqual(s.connection, 'connected');
  const r = M.transition(s, { type: 'openRose' });
  assert.strictEqual(r.state.connection, 'connected'); assert.deepStrictEqual(r.effects, []);
});
test('reset restores the initial state', () => {
  assert.deepStrictEqual(run(working(), { type: 'reset' }), M.initialState());
});
// Rose's voice (output) mute: independent of microphone, call, speech, results and tasks.
test("voice (output) mute toggles only voiceMuted", () => {
  const before = run(working(), { type: 'speak' }, { type: 'result', value: 'saturday' });
  const r = M.transition(before, { type: 'voiceMute' });
  assert.strictEqual(r.state.voiceMuted, true);
  for (const k of ['muted', 'connection', 'speaking', 'result', 'view', 'pendingStop']) assert.deepStrictEqual(r.state[k], before[k], k);
  assert.deepStrictEqual(r.state.tasks, before.tasks);
  assert.deepStrictEqual(r.effects, [], 'no cue, no endVoice, no stopAck');
  assert.strictEqual(run(r.state, { type: 'voiceMute' }).voiceMuted, false);
});
test("voice mute works without a call and survives end call, navigation and layout", () => {
  let s = run(fresh(), { type: 'voiceMute' });
  assert.ok(s.voiceMuted); assert.strictEqual(s.connection, 'disconnected'); assert.ok(!s.muted);
  s = run(s, { type: 'openRose' }, { type: 'connected' }, { type: 'speak' }, { type: 'endCall' }, { type: 'switcher' }, { type: 'layout', value: 'unfolded' }, { type: 'home' });
  assert.ok(s.voiceMuted);
});
test("microphone mute never changes voice mute (and vice versa)", () => {
  const s = run(connected(), { type: 'voiceMute' }, { type: 'mute' });
  assert.ok(s.voiceMuted && s.muted);
  const t = run(s, { type: 'mute' }, { type: 'voiceMute' });
  assert.ok(!t.voiceMuted && !t.muted);
  assert.deepStrictEqual(M.rendererSignals(run(connected(), { type: 'voiceMute' })), M.rendererSignals(connected()), 'renderer mapping unchanged');
});
test("voice mute does not stop, confirm or cancel work", () => {
  const s = run(working(), { type: 'askStop', id: 'reply' }, { type: 'voiceMute' });
  assert.strictEqual(s.pendingStop, 'reply'); assert.strictEqual(s.tasks.reply.state, 'running');
  const t = run(working(), { type: 'voiceMute' }, { type: 'tick' });
  for (const id of Object.keys(M.TASKS)) assert.strictEqual(t.tasks[id].state, 'running');
});
test("locked phone ignores the voice control; reset clears it", () => {
  assert.ok(!run(fresh(), { type: 'lock' }, { type: 'voiceMute' }).voiceMuted);
  assert.ok(!run(run(fresh(), { type: 'voiceMute' }), { type: 'reset' }).voiceMuted);
});
console.log('\n' + passed + ' model checks passed');
