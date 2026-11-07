// Renderer loss (Core R19). All of the app's WebViews share one renderer process, and an unhandled
// onRenderProcessGone kills the whole app, so every WebView owner must handle it. Source checks only:
// no Android runtime is exercised here.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dir=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,dir),'utf8');
const owners=fs.readdirSync(dir).filter(f=>f.endsWith('.java')&&/new WebView\(|extends WebView\b/.test(source(f)));

test('every app WebView owner handles renderer loss',()=>{
 assert.deepEqual(owners.sort(),['TaskHtmlView.java','VoiceService.java'],'known owners; a new one needs a handler too (the Mac screen panel is removed)');
 for(const name of owners){
  const text=source(name);
  const clients=text.match(/setWebViewClient\(new WebViewClient\(\)\{/g)?.length||0;
  const handlers=[...text.matchAll(/public boolean onRenderProcessGone\([^)]*\)\{[^]*?return true;\s*\}/g)].length;
  assert.ok(clients>0&&handlers===clients,`${name}: ${handlers} handler(s) returning true for ${clients} client(s)`);
 }
});

test('voice renderer loss ends the call truthfully and leaves tasks alone',()=>{
 const voice=source('VoiceService.java');
 const body=voice.slice(voice.indexOf('void voiceRendererGone('),voice.indexOf('\n }',voice.indexOf('void voiceRendererGone(')));
 for(const step of ['dead.destroy()','loaded=false','pendingCommand=null','live=false','connected=false','muted=false','lastEndReason="renderer_gone"','new Bridge().audio(false)','createVoiceWeb('])
  assert.ok(body.includes(step),`voiceRendererGone: ${step}`);
 assert.ok(!/task\/stop|taskStop|"end"/.test(body),'no task Stop and no voice command replayed');
 // The recreated view is built by the same code as at startup: same bridge, request filter and permissions.
 const create=voice.slice(voice.indexOf('void createVoiceWeb('),voice.indexOf('final RendererRecovery voiceRenderer'));
 for(const part of ['addJavascriptInterface(new Bridge(callbacks.open()),"Native")','shouldInterceptRequest','onPermissionRequest','appassets.androidplatform.net'])
  assert.ok(create.includes(part),`createVoiceWeb keeps ${part}`);
 assert.equal((voice.match(/new WebView\(this\)/g)||[]).length,1,'one place creates the voice WebView');
 assert.match(source('ConversationState.java'),/case "renderer_gone":return Failure\.DISCONNECTED/,'the surface understands the reason');
});

test('result cards recover locally',()=>{
 const html=source('TaskHtmlView.java'),card=source('TaskStatusCard.java');
 assert.match(html,/removeView\(TaskHtmlView\.this\);\s*dispose\(\)/,'the dead card view is removed then destroyed');
 assert.match(card,/htmlLostFor=/,'the card falls back to its text result for that task');
});

test('R20: voice bridge callbacks reach only the view generation that issued them',()=>{
 const voice=source('VoiceService.java');
 assert.match(voice,/addJavascriptInterface\(new Bridge\(callbacks\.open\(\)\),"Native"\)/,'each new voice view opens a new generation for its own bridge');
 const request=voice.slice(voice.indexOf('public void request('),voice.indexOf('\n',voice.indexOf('public void request(')));
 assert.match(request,/runOnUiThread\(\(\)->callbacks\.run\(generation,\(\)->web\.evaluateJavascript\(js,null\)\)\)/,'request completions are checked when they run');
 const resume=voice.slice(voice.indexOf('public void readerResume('),voice.indexOf('\n',voice.indexOf('public void readerResume(')));
 assert.match(resume,/callbacks\.run\(generation,/,'readerResume answers only its own page');
 const destroy=voice.slice(voice.indexOf('public void onDestroy('));
 assert.ok(destroy.indexOf('callbacks.close()')>=0&&destroy.indexOf('callbacks.close()')<destroy.indexOf('web.destroy()'),'teardown closes ownership before destroying the view');
 const bridge=voice.slice(voice.indexOf('class Bridge {'),voice.indexOf('public void onDestroy('));
 assert.ok(!/\(\)->web\.evaluateJavascript/.test(bridge.replace(/callbacks\.run\(generation,\(\)->web\.evaluateJavascript/g,'')),'no bridge callback evaluates into whichever view is current');
});
