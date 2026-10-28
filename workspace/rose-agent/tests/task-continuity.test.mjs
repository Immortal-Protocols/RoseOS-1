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

test('neither the surface nor voice end can stop a task',()=>{
 for(const name of ['RoseConversationActivity.java','TaskPresentation.java','ConversationState.java']){
  const text=source(name);
  assert.ok(!/task\/stop|taskStop|"stop"/.test(text),`${name} has no task Stop path`);
 }
 const server=fs.readFileSync(new URL('../server/server.mjs',import.meta.url),'utf8');
 const voiceEnded=server.match(/voice-ended'\)\{(.*?)return answer/s)?.[1];
 assert.ok(voiceEnded,'found /voice-ended handler');
 assert.ok(!/taskStop|launcherTask/.test(voiceEnded),'/voice-ended does not touch the task');
});
