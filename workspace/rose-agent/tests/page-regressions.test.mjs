// Current-page replacements for the retired audio-start-order, status-audio-levels,
// media-buttons and mute-state glasses checks, run against the real app/assets page.
import test from 'node:test';
import assert from 'node:assert/strict';
import {loadPage,fakeConversation} from './web-ui-harness.mjs';

const calls=(page,name)=>page.calls.filter(c=>c[0]===name).map(c=>c.slice(1));
const settle=()=>new Promise(resolve=>setImmediate(resolve));

// The backend answers /voice/config, and VoiceProviders.start is recorded instead of connecting.
function startablePage(native){
 const page=loadPage({native});
 page.run(`var providerStarts=0;
  api=(method,route)=>Promise.resolve(route==='/voice/config'?{provider:'fake',voiceRouting:{mode:'live'}}:{});
  VoiceProviders.start=()=>{providerStarts++;return new Promise(()=>{});};`);
 return page;
}

function livePage(native){
 const page=loadPage({native});
 page.run(`var fake=${fakeConversation};var input=0,output=0;fake.inputLevel=()=>input;fake.outputLevel=()=>output;
  conversation=fake;ready=true;starting=false;ended=false;parked=false;muted=false;`);
 return page;
}

test('start routes audio first and opens the provider only once the native route is ready',async()=>{
 let prepared=false;
 const page=startablePage({audioPrepared:()=>prepared});
 page.run('start(false)');
 await settle();
 assert.deepEqual(calls(page,'audio')[0],[true],'native audio is requested before anything else');
 assert.equal(page.run('providerStarts'),0,'no provider while the route is not ready');
 const wait=page.timers.filter(t=>t.ms===50).at(-1);
 assert.ok(wait,'the route wait polls');
 prepared=true;wait.f();
 await settle();await settle();
 assert.equal(page.run('providerStarts'),1);
});

test('End while waiting for the audio route cancels the start without opening a provider',async()=>{
 const page=startablePage({audioPrepared:()=>false});
 page.run('var opened=start(false)');
 await settle();
 page.run('endConversation()');
 page.timers.filter(t=>t.ms===50).at(-1).f();
 await page.run('opened');
 assert.equal(page.run('providerStarts'),0);
 assert.equal(page.run('ready || starting'),false);
 assert.equal(page.node('status').textContent,'Ready when you are','the cancel is not shown as a failure');
});

test('native tray levels are the measured provider levels, bounded, with mute zeroing only the microphone',()=>{
 const page=livePage();
 const sample=(i,o)=>{page.run(`input=${i};output=${o};publishAudioLevels()`);return calls(page,'trayAudio').at(-1);};
 assert.deepEqual(sample(.18,.29),[.18,.29]);
 assert.deepEqual(sample(0,0),[0,0]);
 assert.deepEqual(sample(NaN,Infinity),[0,0],'invalid readings never reach the tray');
 assert.deepEqual(sample(-1,2),[0,1],'levels stay within 0..1');
 page.run('toggleMute()');
 assert.deepEqual(sample(.6,.3),[0,.3],'mute flattens the microphone, not Rose');
 page.run('toggleMute()');
 assert.deepEqual(sample(.4,.2),[.4,.2]);
 page.run('fake.inputLevel=()=>{throw Error("track detached")}');
 assert.deepEqual(sample(.4,.2),[0,.2],'a detached input does not stop output updates');
});

test('assistant speaking follows the measured output level',()=>{
 const page=livePage();
 page.run('input=0;output=.7;publishAudioLevels()');
 assert.equal(calls(page,'assistantSpeaking').at(-1)[0],true);
 page.run('output=0;publishAudioLevels()');
 assert.equal(calls(page,'assistantSpeaking').at(-1)[0],false);
});

test('ending the call clears native levels and later reads cannot animate it',()=>{
 const page=livePage();
 page.run('input=.9;output=.9;publishAudioLevels();endConversation()');
 assert.deepEqual(calls(page,'trayAudio').at(-1),[0,0]);
 assert.equal(calls(page,'assistantSpeaking').at(-1)[0],false);
 page.run('publishAudioLevels()');
 assert.deepEqual(calls(page,'trayAudio').at(-1),[0,0],'no live conversation, no levels');
});

test('browser media controls are routed to the native glasses handler',()=>{
 const handlers=new Map();
 const page=loadPage({navigator:{mediaSession:{setActionHandler:(name,fn)=>handlers.set(name,fn)}}});
 assert.deepEqual([...handlers.keys()].sort(),['nexttrack','pause','play']);
 handlers.get('pause')();handlers.get('play')();handlers.get('nexttrack')();
 assert.deepEqual(calls(page,'glassesMediaAction'),[['pause'],['play'],['next']]);
});

test('a glasses start during a live unmuted call keeps the microphone on',()=>{
 const page=livePage();
 page.run('window.startFromGlasses()');
 assert.equal(page.run('muted || fake.muted'),false);
 page.run('window.autoStartFromGlasses()');
 assert.equal(page.run('muted || fake.muted'),false,'automatic start never toggles a live call');
 page.run('toggleMute();window.autoStartFromGlasses()');
 assert.equal(page.run('muted && fake.muted'),true,'automatic start never unmutes either');
});
