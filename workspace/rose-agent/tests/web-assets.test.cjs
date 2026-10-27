const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
test('all startup script assets exist and are permitted by the WebView loader',()=>{
 const html=fs.readFileSync(root+'/app/assets/index.html','utf8');
 const java=fs.readFileSync(root+'/app/src/dev/pixel/voice/VoiceService.java','utf8');
 const allowed=new Set([...java.matchAll(/p\.equals\("(\/[^" ]+)"\)/g)].map(m=>m[1]));
 for(const match of html.matchAll(/<script src="([^"]+)"/g)){
  assert.ok(fs.existsSync(root+'/app/assets/'+match[1]),'Missing asset '+match[1]);
  assert.ok(allowed.has('/'+match[1]),'WebView blocks '+match[1]);
 }
});
