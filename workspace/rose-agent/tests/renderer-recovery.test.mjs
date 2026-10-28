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
 assert.deepEqual(owners.sort(),['MacScreenPanel.java','TaskHtmlView.java','VoiceService.java'],'known owners; a new one needs a handler too');
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
 for(const part of ['addJavascriptInterface(new Bridge(),"Native")','shouldInterceptRequest','onPermissionRequest','appassets.androidplatform.net'])
  assert.ok(create.includes(part),`createVoiceWeb keeps ${part}`);
 assert.equal((voice.match(/new WebView\(this\)/g)||[]).length,1,'one place creates the voice WebView');
 assert.match(source('ConversationState.java'),/case "renderer_gone":return Failure\.DISCONNECTED/,'the surface understands the reason');
});

test('result cards and the Mac screen recover locally',()=>{
 const html=source('TaskHtmlView.java'),card=source('TaskStatusCard.java'),mac=source('MacScreenPanel.java');
 assert.match(html,/removeView\(TaskHtmlView\.this\);\s*dispose\(\)/,'the dead card view is removed then destroyed');
 assert.match(card,/htmlLostFor=/,'the card falls back to its text result for that task');
 assert.match(mac,/removeView\(dead\);dead\.destroy\(\)/,'the dead panel view is removed then destroyed');
 assert.match(mac,/renderer\.allow\(SystemClock\.elapsedRealtime\(\)\)\)\{createWeb\(\);return;\}/,'recreated through the same setup, within a budget');
 assert.match(mac,/Mac screen unavailable/,'otherwise an honest unavailable state');
 assert.ok(!/web\.evaluateJavascript/.test(mac.replace(/(if\([^)]*web!=null[^)]*\)|&&web!=null\))web\.evaluateJavascript/g,'')),'every panel call checks for a live view');
});
