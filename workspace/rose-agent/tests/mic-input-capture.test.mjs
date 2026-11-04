// #410 item 6: Rose's microphone-input mute on the capture side (voice page, providers, VoiceService, LocalRoseLink).
// The page and providers run in the WebView harness with fakes; the native side is checked as source. No device.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {loadPage,fakeConversation} from './web-ui-harness.mjs';

const read=path=>fs.readFileSync(new URL(path,import.meta.url),'utf8');
const voice=read('../app/src/dev/pixel/voice/VoiceService.java');
const link=read('../app/src/dev/pixel/voice/LocalRoseLink.java');
const page=read('../app/assets/index.html');
const providers=read('../voice-providers/voice-providers.js');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

function startablePage(native={}){
 const p=loadPage({native});
 p.run(`var provider={};
  api=(method,route)=>Promise.resolve(route==='/voice/config'?{provider:'fake',voiceRouting:{mode:'live'}}:{});
  VoiceProviders.start=options=>{provider.options=options;return new Promise((resolve,reject)=>{provider.resolve=resolve;provider.reject=reject;});};`);
 return p;
}
function providerContext(native={}){
 const context={console,setTimeout,clearTimeout,setInterval,clearInterval,Promise,Map,Set,Date,JSON,Float32Array,Native:native};
 context.window=context;vm.createContext(context);
 vm.runInContext(fs.readFileSync(new URL('../voice-providers/voice-providers.js',import.meta.url),'utf8'),context);
 return context;
}

test('a new call starts from the stored mute, and a handed-over muted call stays muted',async()=>{
 for(const [stored,handed,expected] of [[true,false,true],[false,true,true],[true,true,true],[false,false,false]]){
  const p=startablePage({micInputMuted:()=>stored});
  p.run(`start(${handed})`);await settle();
  assert.equal(p.run('provider.options.initialMuted'),expected,`stored ${stored}, handed over ${handed}`);
  assert.equal(p.run('micGate.muted()'),expected,'the microphone gate starts in the same state');
 }
});

test('a mute requested while connecting reaches the provider before Rose listens, and is acknowledged',async()=>{
 const p=startablePage();
 p.run('start(false)');await settle();
 p.run(`var conv=${fakeConversation};provider.options.onSession({sessionId:'s1'});provider.options.onConversationCreated(conv);`);
 p.run('window.setTrayMuted(true,3)');
 assert.equal(p.run('conv.muted'),true,'an existing provider is muted at once');
 p.run('conv.muted=false;provider.options.onConnect()');
 assert.equal(p.run('conv.muted'),true,'connect reconciles to the intent');
 assert.equal(p.node('status').textContent,'Microphone muted');
 assert.equal(p.run('muteAck'),3);
 // Idle: nothing live to change; the request is still acknowledged, and the next start reads the store.
 const idle=loadPage();idle.run('window.setTrayMuted(true,9)');
 assert.equal(idle.run('muted'),false);assert.equal(idle.run('muteAck'),9);
});

test('the state report carries the acknowledged request',()=>{
 assert.match(page,/ready,muted,muteSeq:muteAck,connected:ready,level:micLevel/);
});

test('input mute never silences Rose: task progress, Sky narration and the guardian timer',()=>{
 assert.match(page,/if\(guardianMode\|\|!ready\|\|!conversation\|\|globalThis\.taskStopSpeechPending/);
 assert.match(page,/if\(data\.guideEnabled!==false&&\(introduction\|\|/);
 assert.match(page,/if\(!ready\|\|!conversation\|\|!data\|\|data\.active===false/);
 assert.match(providers,/guardianTimer=setTimeout\(\(\)=>\{if\(closed\|\|current!==inputGeneration/);
 // The input meter and the "Microphone muted" status still follow input mute.
 assert.match(page,/const input=active&&!muted\?measuredVolume/);
});

test('getUserMedia gate: a microphone opened while input is muted starts disabled; the gate never enables',async()=>{
 const tracks=[];const devices={getUserMedia:async()=>{const t={enabled:true};tracks.push(t);return {getAudioTracks:()=>[t]};}};
 const context=providerContext();
 const gate=context.VoiceProviders.gateMicrophone(devices);
 gate.set(true);await devices.getUserMedia({audio:true});assert.equal(tracks[0].enabled,false);
 gate.set(false);await devices.getUserMedia({audio:true});assert.equal(tracks[1].enabled,true);assert.equal(tracks[0].enabled,false,'never re-enabled by the gate');
 const again=context.VoiceProviders.gateMicrophone(devices);again.set(true);
 assert.equal(devices.getUserMedia.roseMicGate,true,'installed once');
 assert.equal(context.VoiceProviders.gateMicrophone(undefined).muted(),false,'no media devices: nothing to gate');
});

test('ElevenLabs applies a muted start as soon as the conversation exists, before connect',async()=>{
 for(const initialMuted of [true,false]){
  const calls=[];const context=providerContext();
  context.ElevenConversation={startSession:async o=>{const raw={setMicMuted:v=>calls.push('mute:'+v),endSession:async()=>{},getInputVolume:()=>0,getOutputVolume:()=>0};o.onConversationCreated(raw);calls.push('created');o.onConnect?.();calls.push('connect');return raw;}};
  await context.VoiceProviders.start({provider:'elevenlabs',api:async()=>({token:'t',history:''}),tools:{},initialMuted,onConversationCreated:()=>calls.push('page'),onConnect:()=>{}});
  assert.deepEqual(calls,initialMuted?['mute:true','page','created','connect']:['page','created','connect']);
 }
});

test('Local Rose: close stops only the link its own start made',async()=>{
 const calls=[];let token=0;
 const context=providerContext({localRoseStart:m=>{calls.push(['start',m]);return ++token+40;},localRoseStop:t=>calls.push(['stop',t]),localRoseMute:m=>calls.push(['mute',m])});
 const first=context.VoiceProviders.start({provider:'local',initialMuted:true,onConnect(){},onConversationCreated(){}});
 context.localRoseEvent({type:'status',status:'live'});const handle=await first;
 await handle.close();
 assert.deepEqual(calls,[['start',true],['stop',41]]);
});

test('VoiceService: mic requests are stored first, never take the pending slot, and are acknowledged',()=>{
 const command=voice.slice(voice.indexOf('void command(String c,String origin)'),voice.indexOf('void setResultCardMode('));
 const mic=command.indexOf('Boolean mic=MicInputCapture.requested(c);');
 assert.ok(mic>0&&mic<command.indexOf('if(!loaded){pendingCommand=c;'),'before the single pending slot');
 assert.match(command,/storeInputMuted\(mic\);int seq=\+\+muteRequestSeq;if\(!loaded\)\{muteAckSeq=seq;return;\}web\.evaluateJavascript\("window\.setTrayMuted\("\+mic\+","\+seq\+"\)",null\);return;/);
 assert.ok(!/"window\.setTrayMuted\(true\)"/.test(command),'the old unsequenced call is gone');
 // Reports: acknowledged by sequence (never past the last request), and only a settled live change is stored.
 assert.match(voice,/if\(o\.has\("muteSeq"\)\)muteAckSeq=Math\.min\(muteRequestSeq,Math\.max\(muteAckSeq,o\.optInt\("muteSeq"\)\)\);if\(MicInputCapture\.storeReported\(live,connected,inputMuteSettled\(\),muted,storedInputMuted\(\)\)\)storeInputMuted\(muted\);/);
 assert.match(voice,/@JavascriptInterface public boolean micInputMuted\(\)\{return storedInputMuted\(\);\}/);
 assert.match(voice,/getSharedPreferences\(MicInputCapture\.PREFS,0\)\.getBoolean\(MicInputCapture\.KEY,false\)/);
 // Never the platform's microphone mute or Sensor Privacy.
 assert.ok(!/setMicrophoneMute|SensorPrivacy/.test(voice+page+providers+link));
});

test('renderer loss ends the dead page Local Rose link and settles its requests; links are token-bound',()=>{
 const gone=voice.slice(voice.indexOf('void voiceRendererGone('),voice.indexOf('\n }',voice.indexOf('void voiceRendererGone(')));
 assert.ok(gone.includes('LocalRoseLink.stop();muteAckSeq=muteRequestSeq;'));
 assert.match(voice,/@JavascriptInterface public int localRoseStart\(boolean muted\)\{.*return 0;\}return LocalRoseLink\.start\(/);
 assert.match(voice,/@JavascriptInterface public void localRoseStop\(int token\)\{LocalRoseLink\.stop\(token\);\}/);
 const start=link.slice(link.indexOf('static synchronized int start('),link.indexOf('static synchronized void stop(int token)'));
 assert.ok(start.indexOf('current.end();')<start.indexOf('link.muted = muted;'),'a stale link is ended, never reused');
 assert.ok(!start.includes('if (current != null) return;'),'the new start\'s mute always applies');
 assert.match(link,/if \(current == null \|\| token <= 0 \|\| current\.token != token\) return;/);
});
