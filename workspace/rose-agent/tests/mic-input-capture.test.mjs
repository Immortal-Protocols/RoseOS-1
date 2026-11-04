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

// A track shaped like Chromium's MediaStreamTrack: enabled is an accessor on the prototype, the sent audio follows it.
class FakeTrack{constructor(){this._on=true;this.readyState='live';this.stopped=false;}get enabled(){return this._on;}set enabled(v){this._on=!!v;}
 clone(){const c=new FakeTrack();c._on=this._on;return c;}stop(){this.stopped=true;this.readyState='ended';}}
function gatedDevices(context,make=()=>new FakeTrack()){
 const opened=[];const devices={getUserMedia:async()=>{const t=make();opened.push(t);return {getAudioTracks:()=>[t]};}};
 return {devices,opened,gate:context.VoiceProviders.gateMicrophone(devices)};
}

test('held microphone tracks stay disabled while muted for their whole life, whatever the SDK does (LiveKit 2.22.4)',async()=>{
 const {devices,opened,gate}=gatedDevices(providerContext());
 gate.set(true);
 const stream=await devices.getUserMedia({audio:true});const t=stream.getAudioTracks()[0];
 assert.equal(t.enabled,false,'disabled before any SDK code sees it');
 // LiveKit restart()/setTrackMuted(false) while its own isMuted is still false: enabled = true (livekit :22781, :22982).
 t.enabled=true;assert.equal(t.enabled,false,'the SDK cannot re-enable it while input is muted');
 // A reacquired track (restart() calls getUserMedia again) is held from creation, then the SDK enables it.
 const again=(await devices.getUserMedia({audio:true})).getAudioTracks()[0];again.enabled=true;
 assert.equal(again.enabled,false,'a reacquired track is held too');
 const copy=t.clone();copy.enabled=true;assert.equal(copy.enabled,false,'a clone is held');
 // The SDK disables one of them itself (its own mute); input mute still holds both.
 again.enabled=false;
 // Unmute: each held track gets back exactly what the SDK last asked for, never more. LiveKit's unmute() returns early
 // when its isMuted is already false, so nothing else would re-enable a track it asked to be enabled.
 gate.set(false);assert.deepEqual(opened.map(x=>x.enabled),[true,false]);
 assert.equal(copy.enabled,true,'a clone follows its source\'s request');
 // Mute: every held live track is disabled at once, before the provider's asynchronous mute.
 gate.set(true);assert.deepEqual([t.enabled,again.enabled,copy.enabled],[false,false,false]);
 assert.equal(gate.held(),3);
});

test('a track that cannot be held while muted is stopped, never left live; unmuted it is left to the provider',async()=>{
 const stubborn=()=>{const t={readyState:'live',stopped:false,stop(){this.stopped=true;this.readyState='ended';}};
  Object.defineProperty(t,'enabled',{value:true,writable:true,configurable:false});return t;};
 const {devices,gate}=gatedDevices(providerContext(),stubborn);
 gate.set(true);const muted=(await devices.getUserMedia({audio:true})).getAudioTracks()[0];
 assert.equal(muted.stopped,true);
 gate.set(false);const live=(await devices.getUserMedia({audio:true})).getAudioTracks()[0];
 assert.equal(live.stopped,false);
});

test('one gate per devices; no media devices: nothing to gate',async()=>{
 const context=providerContext();const {devices,gate}=gatedDevices(context);
 assert.equal(context.VoiceProviders.gateMicrophone(devices),gate,'a second call returns the same gate');
 assert.equal(devices.getUserMedia.roseMicGate,gate);
 assert.equal(context.VoiceProviders.gateMicrophone(undefined).muted(),false);
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
 assert.match(command,/int seq=nextMuteSeq\(\);storeInputMuted\(mic\);if\(mic\)LocalRoseLink\.setMuted\(true\);if\(!loaded\)\{muteAckSeq=seq;return;\}web\.evaluateJavascript\("window\.setTrayMuted\("\+mic\+","\+seq\+"\)",null\);return;/,
  'a running Local Rose link takes a mute at once, before the page (it only gets its handle at live)');
 assert.ok(!/"window\.setTrayMuted\(true\)"/.test(command),'the old unsequenced call is gone');
 // Reports: acknowledged by sequence (never past the last request), and only a settled live change is stored.
 assert.match(voice,/if\(o\.has\("muteSeq"\)\)muteAckSeq=Math\.min\(muteRequestSeq,Math\.max\(muteAckSeq,o\.optInt\("muteSeq"\)\)\);/);
 // The first report of a newly live call stores nothing; a stored mute the call came up without is applied (towards muted).
 assert.match(voice,/reportedLiveConnected=now;if\(MicInputCapture\.muteOnConnect\(was,now,muted,stored\)\)\{int seq=nextMuteSeq\(\);runOnUiThread\(\(\)->\{if\(loaded\)web\.evaluateJavascript\("window\.setTrayMuted\(true,"\+seq\+"\)",null\);else muteAckSeq=Math\.max\(muteAckSeq,seq\);\}\);\}else if\(MicInputCapture\.storable\(was,now\)&&MicInputCapture\.storeReported\(live,connected,inputMuteSettled\(\),muted,stored\)\)storeInputMuted\(muted\);/);
 assert.match(voice,/@JavascriptInterface public boolean micInputMuted\(\)\{return storedInputMuted\(\);\}/);
 assert.match(voice,/getSharedPreferences\(MicInputCapture\.PREFS,0\)\.getBoolean\(MicInputCapture\.KEY,false\)/);
 // Never the platform's microphone mute or Sensor Privacy.
 assert.ok(!/setMicrophoneMute|SensorPrivacy/.test(voice+page+providers+link));
});

test('renderer loss ends the dead page Local Rose link and settles its requests; links are token-bound',()=>{
 const gone=voice.slice(voice.indexOf('void voiceRendererGone('),voice.indexOf('\n }',voice.indexOf('void voiceRendererGone(')));
 // Silent from its next frame at once; ended by its own token off the main thread (never a newer page's link).
 assert.ok(gone.includes('final int deadLink=LocalRoseLink.silence();if(deadLink>0)new Thread(()->LocalRoseLink.stop(deadLink),"local-rose-renderer-gone").start();'));
 assert.ok(gone.includes('muteAckSeq=muteRequestSeq;reportedLiveConnected=false;'));
 assert.ok(!/LocalRoseLink\.stop\(\);/.test(gone),'no blocking stop of whatever link runs');
 assert.match(link,/static int silence\(\) \{\s*LocalRoseLink link = current;\s*if \(link == null\) return 0;\s*link\.muted = true;\s*return link\.token;/);
 assert.match(voice,/@JavascriptInterface public int localRoseStart\(boolean muted\)\{.*return 0;\}final int token=LocalRoseLink\.start\(/);
 assert.match(voice,/@JavascriptInterface public void localRoseStop\(int token\)\{LocalRoseLink\.stop\(token\);\}/);
 const start=link.slice(link.indexOf('static synchronized int start('),link.indexOf('static void stop(int token)'));
 assert.ok(start.indexOf('if (old != null) old.end();')<start.indexOf('link.muted = muted;'),'a stale link is ended, never reused');
 assert.ok(!start.includes('if (current != null) return;'),'the new start\'s mute always applies');
 assert.match(link,/if \(current == null \|\| token <= 0 \|\| current\.token != token\) return;/);
});

test('tray mute/unmute never start voice, never load or reload the page, never take the pending slot (Home PR514)',async()=>{
 const command=voice.slice(voice.indexOf('void command(String c,String origin)'),voice.indexOf('void setResultCardMode('));
 const branch=command.slice(command.indexOf('Boolean mic=MicInputCapture.requested(c);'),command.indexOf('if(!loaded){pendingCommand=c;'));
 assert.ok(branch.length>0);
 for(const forbidden of ['loadUrl','pendingCommand','startForegroundService','startService','createVoiceWeb','userStarted','"talk"'])
  assert.ok(!branch.includes(forbidden),'the mic branch never: '+forbidden);
 assert.equal((branch.match(/return;/g)||[]).length,2,'unloaded and loaded both return inside the branch');
 // The glasses/voice-start block before it acts only on end/talk/glasses/auto_talk.
 const before=command.slice(0,command.indexOf('Boolean mic=MicInputCapture.requested(c);'));
 assert.ok(!/tray_mute|tray_unmute/.test(before));
 // The page: a request while idle or connecting never starts or restarts a call.
 const idle=loadPage();idle.run('var started=0;start=()=>{started++;};window.setTrayMuted(true,1);window.setTrayMuted(false,2)');
 assert.equal(idle.run('started'),0);assert.equal(idle.run('ready||starting'),false);
 const connecting=startablePage();connecting.run('start(false)');await settle();
 connecting.run('var before=provider.options;window.setTrayMuted(true,4);window.setTrayMuted(false,5)');
 assert.equal(connecting.run('provider.options===before'),true,'no second start while connecting');
 assert.equal(connecting.run('starting'),true);
});

test('review D1: on connect the provider is brought to the intent, whatever it was started with',async()=>{
 // Stored muted; unmute during the audio-route/config wait (provider starts listening); mute while connecting.
 const a=startablePage({micInputMuted:()=>true});
 a.run('start(false);window.setTrayMuted(false,1)');await settle();
 assert.equal(a.run('provider.options.initialMuted'),false);
 a.run(`window.setTrayMuted(true,2);var conv=${fakeConversation};provider.options.onSession({sessionId:'s'});provider.options.onConversationCreated(conv);conv.muted=false;provider.options.onConnect()`);
 assert.equal(a.run('conv.muted'),true,'muted on connect: capture never live under a muted UI');
 assert.equal(a.node('status').textContent,'Microphone muted');
 // Mirror: stored listening; mute during the wait (provider starts muted); unmute while connecting.
 const b=startablePage({micInputMuted:()=>false});
 b.run('start(false);window.setTrayMuted(true,1)');await settle();
 assert.equal(b.run('provider.options.initialMuted'),true);
 b.run(`window.setTrayMuted(false,2);var conv=${fakeConversation};provider.options.onSession({sessionId:'s'});provider.options.onConversationCreated(conv);conv.muted=true;provider.options.onConnect()`);
 assert.equal(b.run('conv.muted'),false,'listening on connect: never silent under a listening UI');
});

test('review D4: a request after a routing or guardian handover wins over the handed-over mute',async()=>{
 for(const newer of [false,true]){
  const p=startablePage({micInputMuted:()=>false});
  p.run(`var closing;conversation={muted:true,setMuted(v){this.muted=!!v;},inputLevel:()=>0,outputLevel:()=>0,close(){return new Promise(r=>closing=r);},sendContext(){},sendMessage(){},announce(){}};ready=true;starting=false;ended=false;muted=true;muteAck=3;`);
  p.run('window.changeVoiceRouting("live")');await settle();
  if(newer)p.run('window.setTrayMuted(false,4)');
  p.run('closing()');await settle();await settle();
  assert.equal(p.run('provider.options.initialMuted'),!newer,newer?'the newer unmute wins':'with no newer request the handed-over mute is kept');
 }
});

test('verify F2: mute then unmute while connecting leaves the published microphone as the SDK left it, live',async()=>{
 const {devices,gate}=gatedDevices(providerContext());
 const t=(await devices.getUserMedia({audio:true})).getAudioTracks()[0];
 gate.set(true);assert.equal(t.enabled,false);
 gate.set(false);assert.equal(t.enabled,true,'listening UI, listening microphone (LiveKit would not re-enable it)');
});

test('review D5: a microphone published before the conversation exists is disabled at once by a mute',async()=>{
 const {devices,gate}=gatedDevices(providerContext());
 const t=(await devices.getUserMedia({audio:true})).getAudioTracks()[0];
 assert.equal(t.enabled,true,'published listening');
 gate.set(true);
 assert.equal(t.enabled,false,'muted before any provider handle or setMicMuted exists');
 t.enabled=true;assert.equal(t.enabled,false,'and the SDK cannot re-enable it');
});

test('verify F1/F3-F6: Local Rose starts fail closed, mute is lock-free, sequence first, reports at once',()=>{
 assert.match(voice,/final int token=LocalRoseLink\.start\(VoiceService\.this,m->localRoseEvent\(generation,m\),muted\|\|storedInputMuted\(\)\);/,
  'a request stored just before this start still reaches the new link');
 const command=voice.slice(voice.indexOf('void command(String c,String origin)'),voice.indexOf('void setResultCardMode('));
 assert.match(command,/if\(mic!=null\)\{int seq=nextMuteSeq\(\);storeInputMuted\(mic\);if\(mic\)LocalRoseLink\.setMuted\(true\);/,
  'the sequence is taken before the store; Java mutes a running link eagerly, and only ever towards muted');
 assert.match(voice,/synchronized int nextMuteSeq\(\)\{return \+\+muteRequestSeq;\}/);
 assert.ok(!/\+\+muteRequestSeq/.test(voice.replace('synchronized int nextMuteSeq(){return ++muteRequestSeq;}','')),'one increment site');
 assert.match(link,/private static volatile LocalRoseLink current;/);
 for(const sig of ['static int silence()','static void setMuted(boolean muted)','static void stop(int token)','static void stop()'])
  assert.ok(link.includes(sig),'not synchronized: '+sig);
 assert.match(link,/synchronized \(LocalRoseLink\.class\) \{\s*if \(current == null \|\| token <= 0 \|\| current\.token != token\) return;\s*ended = current;\s*current = null;\s*\}\s*ended\.end\(\);/,
  'a link ends outside the lock');
 // The page reports at once on connect and on every mute change, so the first live report is the call as connected.
 assert.match(page,/function reportState\(\)\{Native\.state\(JSON\.stringify\(/);
 assert.match(page,/\$\('orb'\)\.classList\.add\('live'\);reportState\(\);/);
 assert.match(page,/'Listening · speak naturally';reportState\(\);diagnostic\(\);\}/);
});

test('verify G1/G2: a mute during a start, and a renderer lost during a start, never leave a link capturing',()=>{
 // G1: setMuted records the request even with no published link; start re-reads it after publishing, before begin().
 assert.match(link,/static void setMuted\(boolean muted\) \{\s*requestedMute = muted;\s*LocalRoseLink link = current;/);
 const start=link.slice(link.indexOf('static synchronized int start('),link.indexOf('static void stop(int token)'));
 assert.ok(start.indexOf('current = link;')<start.indexOf('if (requestedMute) link.muted = true;'));
 assert.ok(start.indexOf('if (requestedMute) link.muted = true;')<start.indexOf('link.begin();'),'before the first frame');
 assert.match(link,/private static volatile boolean requestedMute;/);
 // G2: the dead page is recorded before silence(); a start that returns into a dead page ends its own link.
 const gone=voice.slice(voice.indexOf('void voiceRendererGone('),voice.indexOf('\n }',voice.indexOf('void voiceRendererGone(')));
 assert.ok(gone.indexOf('goneGeneration=Math.max(goneGeneration,callbacks.current());')<gone.indexOf('LocalRoseLink.silence()'));
 assert.match(voice,/if\(generation<=goneGeneration\)\{new Thread\(\(\)->LocalRoseLink\.stop\(token\),"local-rose-renderer-gone"\)\.start\(\);return 0;\}return token;\}/);
 assert.match(voice,/volatile int goneGeneration;/);
});
