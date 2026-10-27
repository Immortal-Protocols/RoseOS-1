// Independent QA check (Chief ruling CHIEF-STOP-001; Chief review APP-005 item 3): the ElevenLabs
// stop-speech gate in voice-providers.js must not make canceled task A's audio audible again while
// A output is still pending. Runs the real speechGate() (extracted) with a fake clock and a fake
// conversation. Offline only: proves the client logic, not provider/Android audio delivery.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const file=new URL('../../workspace/rose-agent/voice-providers/voice-providers.js',import.meta.url);
const src=fs.readFileSync(file,'utf8');
const has=src.includes('function speechGate(');

function load(){
 const start=src.indexOf('function speechGate(');const end=src.indexOf('\n }\n',start)+3;
 const timers=[];let now=0;
 const context=vm.createContext({setTimeout:(f,ms)=>{const t={f,at:now+ms};timers.push(t);return t;},clearTimeout:t=>{const i=timers.indexOf(t);if(i>=0)timers.splice(i,1);}});
 vm.runInContext(src.slice(start,end)+'\nglobalThis.speechGate=speechGate;',context);
 const advance=ms=>{now+=ms;for(const t of [...timers].filter(t=>t.at<=now)){timers.splice(timers.indexOf(t),1);t.f();}};
 return {gate:context.speechGate(),advance};
}

test('canceled A audio still pending after 12 s is not made audible again',{skip:!has&&'no speechGate in this tree'},()=>{
 const {gate,advance}=load();const volume=[];
 gate.attach({setVolume:({volume:v})=>volume.push(v)});
 gate.stop();
 assert.deepEqual(volume,[0],'control: Stop mutes output');
 // The provider is still speaking canceled A (no mode change to listening) past the cap.
 advance(12500);
 assert.equal(volume.at(-1),0,'A output became audible again while still pending: volumes '+JSON.stringify(volume));
});
