// APP-HIFI-CONVERSATION-1029-P: the conversation screen draws the Rose presence from Core's RoseVisualState
// with real VoiceService observations, replaces the old petal Presence, and keeps every existing control.
// Source checks of the real Android files; RosePresenceModelTest executes the mapping. No device.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dir=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,dir),'utf8');
const activity=source('RoseConversationActivity.java'),view=source('RosePresenceView.java'),model=source('RosePresenceModel.java');

test('the presence is Core\'s RoseVisualState drawn from real, fresh VoiceService levels',()=>{
 const refresh=activity.slice(activity.indexOf('void refresh(){'),activity.indexOf('void renderTask('));
 assert.match(refresh,/presence\.show\(RoseVisualState\.from\(state,/,'one call into Core\'s adapter; no mapping duplicated here');
 assert.match(refresh,/RoseVisualState\.fresh\(s\.trayInputLevel,seen,now\)/);assert.match(refresh,/RoseVisualState\.fresh\(s\.trayOutputLevel,seen,now\)/);
 assert.match(refresh,/seen=s==null\?0L:s\.trayAudioObservedAt/,'levels are dated by VoiceService\'s own observation time');
 assert.match(refresh,/s==null\?Float\.NaN:/,'no service, no levels: nothing is inferred');
 assert.match(refresh,/reducedMotion\(\),resumed\)\)/,'reduced motion and visibility come from the activity');
 for(const text of [view,model])assert.ok(!/ConversationState|\.muted\b|trayOutputLevel|trayInputLevel|SPEAKING_LEVEL/.test(text),
  'the renderer reads only RoseVisualState: no second state mapping');
});

test('the old petal presence is gone, not kept as an alternative',()=>{
 assert.ok(!/class Presence\b|petal|translucent petals|ValueAnimator/.test(activity),'Presence class and its animation removed');
 assert.match(activity,/RosePresenceView presence;/);assert.match(activity,/presence=new RosePresenceView\(this\)/);
});

test('the renderer is visual only and suspends when not visible',()=>{
 assert.ok(!/send\(|startForegroundService|VoiceService\.|task\/stop|new Intent|evaluateJavascript|WebView|GLSurfaceView|EGL/.test(view),'no command, task, bridge or GL dependency');
 assert.match(view,/onDetachedFromWindow\(\)\{running=false;/);assert.match(view,/onWindowVisibilityChanged[^]*?running=false/);
 assert.match(view,/if\(model\.step\(dt\)\)postInvalidateOnAnimation\(\);else running=false;/,'frames only while changing');
 assert.match(view,/setContentDescription\(d\)/,'state is described for accessibility');
 assert.match(activity,/onPause\(\)\{resumed=false;[^}]*presence\.show\(RoseVisualState\.from\(state,Float\.NaN,Float\.NaN,true,false\)\)/,'leaving the screen stops the animation');
 // The study's state meaning, recorded once in the model.
 assert.match(model,/GAP_JOINED=\.012f,GAP_NARROW=\.10f,GAP_WIDE=\.38f/);assert.match(model,/RING_CLOSE=1\.075f,RING_BROAD=1\.55f,RING_ENERGY=\.12f/);
});

test('voice, results, Watch/Stop, notices and Update Rose Agent are unchanged',()=>{
 assert.match(activity,/primary\.show\(state\.muted\?"🔇":"🎙",state\.muted\?"Unmute":"Mute"/);
 assert.match(activity,/primary\.show\("🎙","Speak","Start voice conversation"\)/);assert.match(activity,/end\.setVisibility\(state\.canEnd\(\)\?View\.VISIBLE:View\.GONE\)/);
 assert.match(activity,/setOnClickListener\(v->openWorkspace\(\)\)/);assert.match(activity,/setPositiveButton\("Stop task",\(d,w\)->sendStop\(id\)\)/);
 assert.match(activity,/add\("Update Rose Agent"\)\.setOnMenuItemClickListener\(i->\{checkForUpdate\(\);return true;\}\)/);
 assert.match(activity,/presentedScroll\.setVisibility\(t\.visible\?View\.VISIBLE:View\.GONE\)/,'results stay empty when absent');
 assert.ok(!/Interrupt|EditText|Listening…|Speaking…/.test(activity),'no composer, Interrupt or listening/speaking notice');
});
