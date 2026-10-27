import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
test('every locally referenced voice script is admitted by the Android asset handler',()=>{
 const html=fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8');
 const java=fs.readFileSync(new URL('../app/src/dev/pixel/voice/VoiceService.java',import.meta.url),'utf8');
 const scripts=[...html.matchAll(/<script\s+src="([^"]+)"/g)].map(m=>m[1]);
 assert.ok(scripts.includes('luna-first.js'));
 for(const script of scripts){assert.ok(java.includes(`p.equals("/${script}")`),`Blocked script: ${script}`);assert.ok(fs.existsSync(new URL('../app/assets/'+script,import.meta.url)),`Missing script: ${script}`);}
});
