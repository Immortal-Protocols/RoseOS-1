import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {AsyncLocalStorage} from 'node:async_hooks';
import {TaskStop} from './task-stop.mjs';
import {LauncherTask} from './launcher-task.mjs';

// Exercise the actual server phone entry points without starting the server: the origin
// rules, fastPhone, /phone/commands and /task/stop are sliced from server.mjs.
const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
const between=(from,to)=>{const start=server.indexOf(from),end=server.indexOf(to,start);assert.ok(start>0&&end>start,from);return server.slice(start,end);};
const phoneFunctions=between('function phoneCommandRejection(','function checkPausedApp(')+between('// Origin of phone work,','async function rawFastPhone(');
const route=name=>{const start=server.indexOf(" if(url.pathname==='"+name+"'){");assert.ok(start>0,name);return server.slice(start,server.indexOf('\n',start));};
const CALC='dev.pixel.calculator.agent';
const evaluate=expression=>({action:'app_command',packageName:CALC,operation:'evaluate',params:{expression}});

function fixture({backgroundEnabled}){
 const launcher=new LauncherTask(path.join(fs.mkdtempSync(path.join(os.tmpdir(),'stop-gate-')),'task.json'));
 const executed=[],responses=[];
 const taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>executed.push('interrupt'),abortReview:()=>{},closeWorkspace:async()=>executed.push('close')});
 const context=vm.createContext({taskStop,backgroundEnabled,crypto,setImmediate,Date,Error,Object,Set,AsyncLocalStorage,
  checkPausedApp:()=>{},peerIdentity:req=>req?.phone?{id:'nathan-pixel'}:null,autoGenerator:{active:false},visualTrial:false,busy:true,taskStarted:0,lastRoute:null,
  backgroundPhone:{execute:async(c,o={})=>{executed.push('background:'+c.action+(o.taskId?'@'+o.taskId:'')+(c.taskId?':leaked':''));return {status:'ok'};}},
  rawFastPhone:async c=>{executed.push('raw:'+c.action+(c.taskId?':leaked':''));return {status:'ok'};},
  phone:{create:c=>{executed.push('created:'+c.action);return {id:'c1',action:c.action,status:'pending'};},complete:()=>{}},
  measurePhone:()=>{},phonePush:{publish:()=>executed.push('pushed')},rawVisualCaptures:new Map(),
  decisionsConfig:{read:()=>({enabled:false})},genericUiActions:new Set(),visualGuard:{},
  answer:(res,status,body)=>responses.push({status,body}),
 });
 vm.runInContext(phoneFunctions+';globalThis.fastPhone=fastPhone;globalThis.nonTaskPhone=nonTaskPhone;globalThis.phoneOrigin=phoneOrigin;',context);
 for(const name of ['/phone/commands','/task/stop'])vm.runInContext('globalThis['+JSON.stringify(name)+']=async(url,data,res,req)=>{'+route(name)+'};',context);
 const call=async(name,data,req={})=>{await context[name]({pathname:name},data,{},req);await new Promise(r=>setImmediate(r));return responses.at(-1);};
 // Mirrors dispatch(): an explicit new user request starts a generation.
 const newRequest=text=>{launcher.start(text);taskStop.begin(launcher.current.id);return launcher.current.id;};
 // A worker tool call runs bound to its own generation (handleToolCall wraps it the same way).
 const inTask=(taskId,command)=>context.phoneOrigin.run({taskId},()=>context.fastPhone(command));
 return {launcher,taskStop,context,executed,responses,call,newRequest,inTask,fastPhone:c=>context.fastPhone(c)};
}

for(const backgroundEnabled of [true,false]){
 test('after POST /task/stop no phone path of the task executes (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});const A=f.newRequest('What is 34.50 plus a 20% tip?');
  assert.equal((await f.inTask(A,{action:'open_app',packageName:CALC})).status,'ok');
  assert.equal((await f.call('/task/stop',{taskId:A,source:'tray'})).body.status,'stopping');assert.equal(f.launcher.current.state,'stopping');
  f.executed.length=0;
  assert.equal((await f.inTask(A,evaluate('34.50+20%'))).status,'task_stopped');
  assert.equal((await f.call('/phone/commands',{action:'screenshot'})).body.result.status,'task_stopped','legacy observation is Stop-gated');
  assert.deepEqual(f.executed,[],'nothing was created, pushed or executed after Stop');
 });
}

test('the origin generation reaches the controller and never the phone payload',async()=>{
 const f=fixture({backgroundEnabled:true});const A=f.newRequest('Calculate');
 await f.inTask(A,{action:'open_app',packageName:CALC});await f.fastPhone({action:'list_apps',taskId:A});
 assert.deepEqual(f.executed,['background:open_app@'+A,'background:list_apps@'+A]);
});

test('the shell tool path (control.py over HTTP) may only observe; a client-supplied taskId is never trusted',async()=>{
 for(const backgroundEnabled of [true,false]){
  const f=fixture({backgroundEnabled});const A=f.newRequest('Calculate');
  for(const command of [evaluate('1+1'),{action:'open_app',packageName:CALC},{action:'tap_label',label:'='},{...evaluate('1+1'),taskId:A}]){
   const r=await f.call('/phone/commands',command);
   assert.equal(r.body.result.status,'not_executed',JSON.stringify(command));assert.match(r.body.result.reason,/registered app tools/);
  }
  const look=await f.call('/phone/commands',{action:'screenshot',taskId:'forged'});
  assert.notEqual(look.body.result?.status,'not_executed','read-only observation runs');
  assert.ok(f.executed.every(e=>!/:leaked/.test(e)&&!/@/.test(e)),'no client taskId reached the phone or labelled the workspace: '+JSON.stringify(f.executed));
 }
});

test('POST /task/stop for a stale task leaves the running task untouched',async()=>{
 const f=fixture({backgroundEnabled:false});f.newRequest('Calculate');
 assert.equal((await f.call('/task/stop',{taskId:'older',source:'tray'})).body.status,'stale_task');assert.equal(f.launcher.current.state,'running');
});

test('Stop stays in force through the terminal state; explicit B runs while canceled A stays blocked',async()=>{
 const f=fixture({backgroundEnabled:true});const A=f.newRequest('Calculate a tip');
 await f.call('/task/stop',{taskId:A,source:'workspace_notification'});
 f.launcher.finish('interrupted','Task stopped');f.executed.length=0;
 for(const command of [evaluate('34.50+20%'),{action:'open_app',packageName:CALC}])assert.equal((await f.inTask(A,command)).status,'task_stopped');
 const B=f.newRequest('Explicit new request B');
 assert.equal((await f.inTask(B,{action:'open_app',packageName:CALC})).status,'ok');
 assert.equal((await f.inTask(A,evaluate('1+1'))).status,'stale_task','A work that starts after B begins keeps A as its origin');
 assert.equal((await f.call('/task/stop',{taskId:A})).body.status,'stale_task','a late Stop for A cannot stop B');
 assert.equal(f.launcher.current.state,'running');assert.deepEqual(f.executed,['background:open_app@'+B]);
});

test('work created for A and executed after B begins is refused by its bound origin',async()=>{
 const f=fixture({backgroundEnabled:true});const A=f.newRequest('Calculate A');
 let release;const gate=new Promise(r=>release=r);
 const delayed=f.context.phoneOrigin.run({taskId:A},async()=>{await gate;return f.context.fastPhone(evaluate('2+2'));});
 await f.call('/task/stop',{taskId:A});const B=f.newRequest('Explicit request B');f.executed.length=0;
 release();assert.equal((await delayed).status,'stale_task');assert.deepEqual(f.executed,[]);
 assert.equal((await f.inTask(B,evaluate('6*7'))).status,'ok');
});

test('task paths without an origin, or with an unknown one, fail closed',async()=>{
 const f=fixture({backgroundEnabled:false});const A=f.newRequest('Calculate');
 assert.equal((await f.fastPhone({action:'screenshot'})).status,'stale_task','untagged in-process caller');
 assert.equal((await f.inTask('not-a-real-task',{action:'screenshot'})).status,'stale_task');
 assert.equal((await f.inTask(A,{action:'screenshot'})).status,'ok');
 assert.deepEqual(f.executed,['raw:screenshot']);
});

test('the enumerated non-task callers run untagged, even while a task is stopping',async()=>{
 for(const backgroundEnabled of [true,false]){
  const f=fixture({backgroundEnabled});const A=f.newRequest('Calculate');await f.call('/task/stop',{taskId:A});f.executed.length=0;
  // The paired phone's own voice tools (media, Apple Music, clock) are user-initiated.
  const voice=await f.call('/phone/commands',{action:'media_control',app:'apple_music',operation:'pause'},{phone:true});
  assert.ok(!['task_stopped','stale_task','not_executed'].includes(voice.body.result?.status));
  // Debug bridge, app generation and startup catalog loading use nonTaskPhone.
  assert.equal((await f.context.nonTaskPhone({action:'list_apps'})).status,'ok');
  assert.ok(f.executed.some(e=>/media_control/.test(e))&&f.executed.some(e=>/list_apps/.test(e)),JSON.stringify(f.executed));
 }
});

test('the Stop gate is reopened only where a new user request starts a task',()=>{
 assert.equal([...server.matchAll(/taskStop\.begin\(/g)].length,1);
 const dispatch=server.slice(server.indexOf('async function dispatch('),server.indexOf('async function submitLuna('));
 assert.match(dispatch,/if\(!codex\.active\)\{backgroundPhone\.beginTask\(\);launcherTask\.start\(displayRequest,taskWidgets\.mode\(\)\);taskStop\.begin\(launcherTask\.current\.id\);/);
});

test('origin is bound at every in-process, output and turn boundary',()=>{
 assert.match(server,/appRuntime\.handleToolCall=request=>\{const stale=taskStop\.authorize\(\{turnId:request\.turnId\}\)\|\|taskStop\.guard\(\{action:'tool:'\+request\.tool\}\);if\(stale\)return Promise\.resolve\(.*\);return phoneOrigin\.run\(\{taskId:taskStop\.current\(\)\},\(\)=>handle\(request\)\);\};/);
 assert.match(server,/codex\.on\('toolActivity',\(method,params\)=>\{if\(method==='turn\/started'\)taskStop\.addTurn\(params\.turn\?\.id\);\}\);/);
 assert.match(server,/codex\.on\('message',\(text,phase,turnId\)=>\{\n \/\/ .*\n if\(!taskStop\.acceptsOutput\(turnId\)\)\{/);
 assert.match(server,/codex\.on\('done',turn=>\{\n \/\/ .*\n if\(!taskStop\.ownsTurn\(turn\.id\)\)\{/);
 assert.match(server,/new DebugBridge\(\{dispatch:data=>nonTaskPhone\(data\),/);
 assert.match(server,/new AppGenerationDriver\(\{store:generationStore,phone:nonTaskPhone,/);
 assert.match(server,/await phoneOrigin\.run\(NON_TASK,\(\)=>loadAppToolCatalog\(/);
 assert.match(server,/const jobTask=taskStop\.current\(\),taskPhone=\(data,genericVisual\)=>fastPhone\(\{\.\.\.data,taskId:jobTask\},genericVisual\);/);
 assert.match(server,/checkPausedApp\(data\);delete data\.taskId;const stopped=phoneCommandRejection\(req,data\);/);
 assert.match(fs.readFileSync(new URL('./codex.mjs',import.meta.url),'utf8'),/this\.emit\('message',p\.item\.text,p\.item\.phase,p\.turnId\)/);
});
