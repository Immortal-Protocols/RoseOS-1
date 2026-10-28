// The conversation surface presents the server's single-task projection and must never stop it.
// Uses the real page script, the real LauncherTask model and the Android sources; no device or backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {loadPage,fakeConversation} from './web-ui-harness.mjs';
import {LauncherTask} from '../server/launcher-task.mjs';

const app=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,app),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('ending voice on the page sends no task Stop and keeps the task stream',async()=>{
 const page=loadPage();
 page.run(`var fake=${fakeConversation};conversation=fake;ready=true;starting=false;ended=false;liveSessionId='s1';`);
 page.run('endConversation()');await settle();
 const routes=page.calls.filter(c=>c[0]==='request').map(c=>c[3]);
 assert.ok(routes.includes('/voice-ended'),'the request capture sees voice end');
 assert.ok(!routes.includes('/task/stop'),'no /task/stop on voice end');
 assert.equal(page.calls.filter(c=>c[0]==='taskStateUnavailable').length,0,'task state is not dropped');
});

test('the server task snapshot carries every field and state the surface presents',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-'));
 let clock=1;const task=new LauncherTask(path.join(dir,'task.json'),{now:()=>clock++});
 const seen=[];const snap=()=>{const s=task.snapshot();seen.push(s);return s.current;};
 assert.equal(task.snapshot().current,null,'empty projection is current:null');
 task.start('What is 18% of 240?');const started=snap();
 task.setTitle(started.id,'Calculate eighteen percent tip');task.activity('Using Calculator');const running=snap();
 task.checking();snap();task.result('18% of 240 is **43.2**.');task.finish('completed');const done=snap();
 assert.equal(done.id,started.id,'progress and result belong to the same task id');
 assert.equal(running.activity,'Using Calculator');assert.equal(done.result,'18% of 240 is **43.2**.');assert.equal(done.activity,'');
 for(const s of seen)for(const key of ['id','state','title','activity','progress','result'])assert.ok(key in s.current,`snapshot has ${key}`);
 // Every lifecycle state the model can produce is handled explicitly by TaskPresentation.
 const handled=new Set([...source('TaskPresentation.java').matchAll(/case "([a-z]+)"/g)].map(m=>m[1]));
 const states=new Set(['running','checking','stopping','completed','failed','interrupted']);
 for(const state of states)assert.ok(handled.has(state),`TaskPresentation handles ${state}`);
 fs.rmSync(dir,{recursive:true,force:true});
});

test('task Stop is only the confirmed owner action, never voice End or mute',()=>{
 for(const name of ['TaskPresentation.java','ConversationState.java','TaskControl.java']){
  assert.ok(!/"\/task\/stop"/.test(source(name)),`${name} sends no task Stop request`);
 }
 const activity=source('RoseConversationActivity.java');
 // The single /task/stop request lives in sendStop, reached only from the confirmation's Stop button.
 assert.equal(activity.match(/"\/task\/stop"/g).length,1,'one Stop request');
 const sendStop=activity.slice(activity.indexOf('void sendStop('));
 assert.ok(sendStop.indexOf('"/task/stop"')>0&&sendStop.indexOf('"/task/stop"')<sendStop.indexOf('\n }'),'it is inside sendStop');
 assert.deepEqual([...activity.matchAll(/sendStop\(([^)]*)\)/g)].map(m=>m[1]).filter(a=>a!=='String id'),['id'],'sendStop has one caller');
 assert.match(activity,/setPositiveButton\("Stop task",\(d,w\)->sendStop\(id\)\)/,'that caller is the confirmation');
 assert.match(activity,/setNegativeButton\("Cancel",null\)/,'Cancel sends nothing');
 // Voice controls send only voice commands.
 const sends=[...activity.matchAll(/send\("([a-z_]+)"\)/g)].map(m=>m[1]);
 assert.ok(sends.every(c=>['talk','end','tray_mute','tray_unmute'].includes(c)),'voice controls send voice commands only');
 const server=fs.readFileSync(new URL('../server/server.mjs',import.meta.url),'utf8');
 const voiceEnded=server.match(/voice-ended'\)\{(.*?)return answer/s)?.[1];
 assert.ok(voiceEnded,'found /voice-ended handler');
 assert.ok(!/taskStop|launcherTask/.test(voiceEnded),'/voice-ended does not touch the task');
});

test('Stop answers from the real TaskStop are each handled by TaskControl',async()=>{
 const {TaskStop}=await import('../server/task-stop.mjs');
 const control=source('TaskControl.java');
 let current={id:'t1',state:'running'};
 const stop=new TaskStop({task:()=>current,stopping(){current={...current,state:'stopping'};},interrupt:async()=>{},abortReview(){},closeWorkspace:async()=>{throw new Error('timeout');}});
 const stale=await stop.request({taskId:'t0',source:'conversation'});
 const first=await stop.request({taskId:'t1',source:'conversation'});
 const again=await stop.request({taskId:'t1',source:'conversation'});
 current={id:'t1',state:'interrupted'};
 const finished=await stop.request({taskId:'t1',source:'conversation'});
 assert.equal(stale.status,'stale_task');assert.equal(first.status,'stopping');assert.equal(first.taskId,'t1');
 assert.ok(first.workspace.startsWith('close_failed'),'workspace close failure is reported');assert.equal(again.duplicate,true);assert.equal(finished.status,'not_active');
 for(const status of ['stale_task','not_active','stopping'])assert.ok(control.includes(`"${status}"`),`TaskControl handles ${status}`);
 assert.ok(control.includes('"close_failed"')&&control.includes('"interrupt_failed"'),'TaskControl reads the failure prefixes the server uses');
});

test('Watch carries the selected task and its session to the viewer, read after the tap (APP-FIX-004)',()=>{
 const activity=source('RoseConversationActivity.java');
 const open=activity.slice(activity.indexOf('void openWorkspace('),activity.indexOf('\n }',activity.indexOf('void openWorkspace(')));
 assert.match(open,/final String selected=shownTaskId;/,'the selected task id is captured at the tap');
 assert.ok(open.indexOf('controllerState()')>0&&open.indexOf('controllerState()')<open.indexOf('startActivity('),'controller state is read fresh before opening');
 assert.match(open,/TaskControl\.watchBinding\(selected,selected\.equals\(shownTaskId\),c\[0\],c\[1\],c\[2\]\)/,'binding from the fresh read for the tapped task');
 assert.match(open,/if\(binding==null\)\{[^}]*return;\}/,'no binding, no viewer');
 assert.match(open,/putExtra\(TaskControl\.EXTRA_WATCH_TASK_ID,binding\[0\]\)\.putExtra\(TaskControl\.EXTRA_WATCH_SESSION_ID,binding\[1\]\)/,'the viewer intent carries both ids');
 assert.equal((activity.match(/AgentWorkspaceActivity/g)||[]).length,1,'the conversation surface has one, bound, viewer launch');
 assert.ok(!/task\/stop|sendStop|send\("end"\)/.test(open),'opening the viewer stops nothing and ends no voice');
 // Existing unbound entries (app reveal and automatic view) are unchanged.
 for(const name of ['WorkspaceLauncher.java','AutoAgentPip.java'])assert.ok(!/WATCH_(TASK|SESSION)_ID/.test(source(name)),`${name} stays unbound`);
});

test('W1: the generic phone-command path claims the id before posting and never turns a slow wait into an error',()=>{
 const voice=source('VoiceService.java');
 const start=voice.indexOf('PhoneCommandGate gate=new PhoneCommandGate(');
 const path=voice.slice(start,voice.indexOf('private void observeOpenedApp('));
 assert.ok(start>0,'the bridge uses PhoneCommandGate');
 assert.ok(path.indexOf('gate.claim(id)')<path.indexOf('main.post('),'the id is claimed before the action is scheduled');
 assert.match(path,/String owned=gate\.claim\(id\);if\(owned!=null\)return owned;/,'an owned id answers from its receipt');
 assert.match(path,/return gate\.await\(task,id,5000\);/,'the bounded wait answers from the receipt');
 assert.ok(!/task\.get\(5,TimeUnit\.SECONDS\)/.test(voice),'no bare five-second get whose timeout becomes an error');
 assert.equal((voice.match(/savePhoneResult\(saved,id,result\);\s*final JSONObject completed=result;/g)||[]).length,1,'the posted action still saves its real result first');
});
