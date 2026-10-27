import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8');
const fn=source.match(/function playRemoteAudio\(\)\{[^\n]+/)[0];
let plays=0,pauses=0,mixStarts=0;
class Audio {pause(){pauses++}play(){plays++;return Promise.resolve()}}
const c=vm.createContext({startReplyMix:()=>mixStarts++,Audio,parked:true,remoteStream:{id:'reply'},audio:new Audio(),$:()=>({})});
const old=c.audio;vm.runInContext(fn,c);
vm.runInContext('playRemoteAudio()',c);assert.equal(plays,0,'prepared session must not open playback on the old route');
assert.equal(mixStarts,0);
c.parked=false;vm.runInContext('playRemoteAudio()',c);assert.equal(plays,1);assert.notEqual(c.audio,old);assert.equal(c.audio.srcObject,c.remoteStream);assert.equal(old.srcObject,null);assert.equal(pauses,1);
assert.equal(mixStarts,1);
console.log('PASS: prepared reply playback deferred; activation uses a fresh audio element');
