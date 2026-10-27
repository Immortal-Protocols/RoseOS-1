import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {LauncherTask} from './launcher-task.mjs';
import {createTaskHtmlTools,validateTaskHtml,taskHtmlPolicy} from './task-html.mjs';
import {TaskWidgetGenerator} from './task-widget.mjs';
function fixture(t){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-html-'));const state=new LauncherTask(path.join(dir,'state.json'));
 t.after(async()=>{await state.flush();fs.rmSync(dir,{recursive:true,force:true});});
 let mode='html';const tools=createTaskHtmlTools({state,mode:()=>mode});state.start('Compare these options','html');
 return {state,tools,dir,setMode:m=>mode=m,call:(html,id='one')=>tools.handleToolCall({tool:'present_task_visualization',threadId:'thread',callId:id,arguments:{html}})};
}
test('the task model supplies HTML during work, but it becomes a result only on completion',async t=>{
 const f=fixture(t);const r=await f.call('<h2>Two options</h2>');assert.equal(r.success,true);assert.equal(f.state.current.state,'running');
 f.state.result('Ordinary spoken answer');f.state.finish('completed');assert.equal(f.state.current.result,'Ordinary spoken answer');assert.equal(f.state.current.visualization.html,'<h2>Two options</h2>');
});
test('HTML tool is idempotent and old task calls cannot overwrite newer work',async t=>{
 const f=fixture(t);await f.call('<p>First</p>');f.state.start('New task','html');await f.call('<p>Overwrite</p>');assert.equal(f.state.current.visualization,undefined);
});
test('disabled modes and invalid external embeds cannot install a visualization',async t=>{
 const f=fixture(t);f.setMode('native');assert.equal((await f.call('<p>Hi</p>')).success,false);f.setMode('html');
 assert.equal((await f.call('<iframe src="https://example.com"></iframe>','two')).success,false);assert.equal(f.state.current.visualization,undefined);
 for(const html of ['','x'.repeat(32001),'<script src="https://example.com/x.js"></script>','<form></form>'])assert.throws(()=>validateTaskHtml(html));
 assert.equal(validateTaskHtml('<svg viewBox="0 0 40 10"><rect width="20" height="10"/></svg>'),'<svg viewBox="0 0 40 10"><rect width="20" height="10"/></svg>');
});
test('a verification retry clears the previous visual; a status followup preserves it',async t=>{
 const f=fixture(t);await f.call('<p>First</p>');f.state.followup('What are you doing?');assert.ok(f.state.current.visualization);f.state.running();assert.equal(f.state.current.visualization,undefined);
});
test('HTML mode never invokes the separate formatting model and omission is explicit',async t=>{
 const f=fixture(t),configFile=path.join(f.dir,'config.json');fs.writeFileSync(configFile,'{"mode":"html","enabled":false}');let calls=0;
 const formatter=new TaskWidgetGenerator({state:f.state,configFile,client:{responses:{create(){calls++;}}}});
 f.state.finish('completed','Finished without a visual');formatter.observe(f.state.snapshot());assert.equal(calls,0);assert.equal(f.state.current.widgetStatus,'unavailable');
 assert.match(taskHtmlPolicy('html'),/present_task_visualization/);assert.match(taskHtmlPolicy('native'),/Do not call/);
});
test('all three display modes survive configuration and preserve the native rollback',t=>{
 const f=fixture(t),configFile=path.join(f.dir,'config.json');const formatter=new TaskWidgetGenerator({state:f.state,configFile,client:{}});
 for(const mode of ['html','native','text']){formatter.setMode(mode);assert.equal(formatter.mode(),mode);assert.equal(formatter.enabled(),mode==='native');}
 formatter.setEnabled(true);assert.equal(formatter.mode(),'native');
});
test('HTML WebView has no native bridge and blocks network, frames, forms and file access',()=>{
 const source=fs.readFileSync(new URL('../app/src/dev/pixel/voice/TaskHtmlView.java',import.meta.url),'utf8');
 assert.ok(!source.includes('addJavascriptInterface'));assert.match(source,/setAllowFileAccess\(false\)/);assert.match(source,/setAllowContentAccess\(false\)/);
 for(const policy of ["connect-src 'none'","frame-src 'none'","form-action 'none'","default-src 'none'"])assert.ok(source.includes(policy));
 assert.match(source,/request\.hasGesture\(\)/);
});
