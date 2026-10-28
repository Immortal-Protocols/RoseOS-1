// Every Native.* method a page calls must exist on the Java bridge that page is loaded with.
// Most page calls are Native.name?.(...), so a renamed or removed bridge method would
// otherwise become a silent no-op rather than an error.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {pageScripts} from './web-ui-harness.mjs';

const read=path=>fs.readFileSync(new URL(path,import.meta.url),'utf8');

// Public @JavascriptInterface methods of the named inner bridge class.
function bridgeMethods(javaPath,className='Bridge'){
 const source=read(javaPath);
 const start=source.search(new RegExp(`class ${className}\\b`));
 assert.ok(start>=0,`${javaPath} has no ${className} class`);
 return new Set([...source.slice(start).matchAll(/@JavascriptInterface\s+public\s+[\w<>[\]]+\s+(\w+)\s*\(/g)].map(m=>m[1]));
}

function calledNames(sources){
 const called=new Map();
 for(const {name,source} of sources)for(const m of source.matchAll(/\bNative\.([A-Za-z_]\w*)/g)){
  if(!called.has(m[1]))called.set(m[1],new Set());
  called.get(m[1]).add(name);
 }
 return called;
}

function assertContract(called,bridge,label){
 assert.ok(called.size>0,`${label}: no Native calls found; the page parse is broken`);
 const missing=[...called].filter(([name])=>!bridge.has(name)).map(([name,files])=>`${name} (${[...files].join(', ')})`);
 assert.deepEqual(missing,[],`${label}: called but not exposed by the bridge`);
}

test('the voice page calls only methods VoiceService.Bridge exposes',()=>{
 const bridge=bridgeMethods('../app/src/dev/pixel/voice/VoiceService.java');
 // pageScripts() is the page's own script list; the generated ElevenLabs bundle never calls Native.
 assertContract(calledNames(pageScripts()),bridge,'index.html');
});

test('the Mac screen page calls only methods MacScreenPanel.Bridge exposes',()=>{
 const bridge=bridgeMethods('../app/src/dev/pixel/voice/MacScreenPanel.java');
 const sources=['mac-screen.html','mac-screen.js'].map(name=>({name,source:read('../app/assets/'+name)}));
 assertContract(calledNames(sources),bridge,'mac-screen.html');
});
