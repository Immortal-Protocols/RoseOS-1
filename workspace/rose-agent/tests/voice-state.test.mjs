// Voice session, mute, tray and End behavior of the page script in app/assets/index.html,
// exercised with a fake voice provider. No device, provider or backend is used.
import test from 'node:test';
import assert from 'node:assert/strict';
import {loadPage,fakeConversation} from './web-ui-harness.mjs';

function livePage(){
 const page=loadPage();
 page.run(`var fake=${fakeConversation};conversation=fake;ready=true;starting=false;ended=false;parked=false;muted=false;`);
 return page;
}
const calls=(page,name)=>page.calls.filter(c=>c[0]===name).map(c=>c.slice(1));

// Replaces the provider with one the test resolves, and the backend with fixed replies.
function startablePage(){
 const page=loadPage();
 page.run(`var provider={};
  api=(method,route)=>Promise.resolve(route==='/voice/config'?{provider:'fake',voiceRouting:{mode:'live'}}:{});
  VoiceProviders.start=options=>{provider.options=options;return new Promise((resolve,reject)=>{provider.resolve=resolve;provider.reject=reject;});};`);
 return page;
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('mute and unmute change the provider input, controls and cues together',()=>{
 const page=livePage();
 page.run('toggleMute()');
 assert.equal(page.run('muted'),true);
 assert.equal(page.run('fake.muted'),true);
 assert.equal(page.node('mute').textContent,'Unmute');
 assert.equal(page.node('status').textContent,'Microphone muted');
 page.run('toggleMute()');
 assert.equal(page.run('fake.muted'),false);
 assert.equal(page.node('mute').textContent,'Mute');
 assert.equal(page.node('status').textContent,'Listening · speak naturally');
 assert.deepEqual(calls(page,'sessionCue'),[['mute'],['unmute']]);
});

test('mute is a no-op without a live session',()=>{
 const page=livePage();
 page.run('ready=false;toggleMute()');
 assert.equal(page.run('muted'),false);
 assert.equal(page.run('fake.muted'),false);
 page.run('ready=true;conversation=null;toggleMute()');
 assert.equal(page.run('muted'),false);
});

test('tray mute is idempotent and cannot act on a session that is not live',()=>{
 const page=livePage();
 page.run('window.setTrayMuted(true);window.setTrayMuted(true)');
 assert.equal(page.run('fake.muted'),true);
 page.run('window.setTrayMuted(false);window.setTrayMuted(false)');
 assert.equal(page.run('fake.muted'),false);
 assert.deepEqual(calls(page,'sessionCue'),[['mute'],['unmute']]);
 // Current behavior: a tray mute while the session is still starting is dropped, not deferred.
 page.run('ready=false;starting=true;window.setTrayMuted(true)');
 assert.equal(page.run('muted'),false);
});

test('a glasses double tap within 400 ms toggles mute once',()=>{
 const page=livePage();
 page.run('performance.now=()=>1000;window.glassesTap();performance.now=()=>1200;window.glassesTap();');
 assert.equal(page.run('muted'),true);
 page.run('performance.now=()=>1500;window.glassesTap();');
 assert.equal(page.run('muted'),false);
});

test('End closes the provider, releases audio and resets every voice flag',async()=>{
 const page=livePage();
 page.run('toggleMute();liveSessionId="s1";var before=generation;endConversation()');
 assert.equal(page.run('fake.closed'),true);
 for(const flag of ['ready','starting','muted'])assert.equal(page.run(flag),false,flag);
 assert.equal(page.run('ended && parked && conversation===null && liveSessionId===null'),true);
 assert.equal(page.run('generation'),page.run('before')+1);
 assert.equal(page.node('talk').disabled,false);
 assert.equal(page.node('mute').disabled,true);
 assert.equal(page.node('end').disabled,true);
 assert.equal(page.node('mute').textContent,'Mute');
 assert.equal(page.node('status').textContent,'Ready when you are');
 assert.deepEqual(calls(page,'audio').at(-1),[false]);
 assert.deepEqual(calls(page,'voiceLifecycle').map(c=>c[0]),['user_end','cleanup']);
});

test('start connects through the provider and reports Listening only on its onConnect',async()=>{
 const page=startablePage();
 page.run('var opened=start(false)');
 await settle();
 assert.equal(page.run('starting && !ready'),true);
 assert.equal(page.node('status').textContent,'Connecting voice…');
 page.run(`provider.options.onSession({sessionId:'s1'});provider.options.onConversationCreated(${fakeConversation});`);
 assert.equal(page.run('ready'),false,'creating the conversation is not connected');
 page.run('provider.options.onConnect()');
 assert.equal(page.run('ready && !starting && liveSessionId==="s1"'),true);
 assert.equal(page.node('status').textContent,'Listening · speak naturally');
 assert.equal(page.node('mute').disabled,false);
 assert.deepEqual(calls(page,'readyChime'),[[]]);
});

test('starting muted passes the mute to the provider and shows it on connect',async()=>{
 const page=startablePage();
 page.run('start(true)');
 await settle();
 assert.equal(page.run('provider.options.initialMuted'),true);
 page.run(`provider.options.onSession({sessionId:'s1'});provider.options.onConversationCreated(${fakeConversation});provider.options.onConnect()`);
 assert.equal(page.node('status').textContent,'Microphone muted');
 assert.equal(page.node('mute').textContent,'Unmute');
});

test('a provider that resolves after End is closed and cannot revive the session',async()=>{
 const page=startablePage();
 page.run('var opened=start(false)');
 await settle();
 page.run('endConversation()');
 page.run(`var late=${fakeConversation};provider.options.onConversationCreated(late);provider.options.onConnect();provider.resolve(late)`);
 await page.run('opened');
 assert.equal(page.run('late.closed'),true);
 assert.equal(page.run('ready || conversation!==null'),false);
 assert.equal(page.node('status').textContent,'Ready when you are');
});

test('a provider disconnect ends the session with a reconnect prompt and lifecycle reason',async()=>{
 const page=startablePage();
 page.run('start(false)');
 await settle();
 page.run(`provider.options.onSession({sessionId:'s1'});provider.options.onConversationCreated(${fakeConversation});provider.options.onConnect();provider.options.onDisconnect({reason:'network'})`);
 assert.equal(page.run('ready'),false);
 assert.equal(page.node('status').textContent,'Voice disconnected. Tap Talk to reconnect.');
 assert.deepEqual(calls(page,'voiceLifecycle').filter(c=>c[0]==='provider_disconnect'),[['provider_disconnect','network']]);
});

test('a start failure ends the session and shows the provider error',async()=>{
 const page=startablePage();
 page.run('var opened=start(false)');
 await settle();
 page.run(`provider.reject(new Error('End the active voice session first'))`);
 await page.run('opened');
 assert.equal(page.run('ready || starting'),false);
 assert.equal(page.node('status').textContent,'End the active voice session first');
 assert.equal(page.node('talk').disabled,false);
});

test('Talk while live unmutes instead of starting a second session',()=>{
 const page=livePage();
 page.run('toggleMute();window.startFromGlasses()');
 assert.equal(page.run('muted'),false);
 assert.equal(page.run('fake.muted'),false);
});
