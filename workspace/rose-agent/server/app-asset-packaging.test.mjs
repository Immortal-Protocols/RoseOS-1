import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

test('every entry-page script is bundled and permitted by the Android WebView',()=>{
 const page=fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8');
 const service=fs.readFileSync(new URL('../app/src/dev/pixel/voice/VoiceService.java',import.meta.url),'utf8');
 const permitted=new Set([...service.matchAll(/p\.equals\("\/([^"\n]+)"\)/g)].map(m=>m[1]));
 for(const match of page.matchAll(/<script\s+src="([^"]+)"/g)){
  assert.ok(permitted.has(match[1]),'WebView does not permit '+match[1]);
  assert.ok(fs.existsSync(new URL('../app/assets/'+match[1],import.meta.url)),'Missing bundled asset '+match[1]);
 }
});
