import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {TaskStop} from './task-stop.mjs';
import {LauncherTask} from './launcher-task.mjs';

// Exercise the actual server phone entry points without starting the server.
const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
const phoneFunctions=server.slice(server.indexOf('async function fastPhone('),server.indexOf('function logDecision('));
const route=name=>{const start=server.indexOf(" if(url.pathname==='"+name+"'){");assert.ok(start>0,name);return server.slice(start,server.indexOf('\n',start));};

function fixture({backgroundEnabled}){
 const launcher=new LauncherTask(path.join(fs.mkdtempSync(path.join(os.tmpdir(),'stop-gate-')),'task.json'));
 launcher.start('What is 34.50 plus a 20% tip?');
 const executed=[],responses=[];
 const taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>executed.push('interrupt'),abortReview:()=>{},closeWorkspace:async()=>executed.push('close')});
 const context=vm.createContext({taskStop,backgroundEnabled,crypto,setImmediate,Date,Error,
  checkPausedApp:()=>{},autoGenerator:{active:false},visualTrial:false,busy:true,taskStarted:0,lastRoute:null,
  backgroundPhone:{execute:async c=>{executed.push('background:'+c.action);return {status:'ok'};}},
  phone:{create:c=>{executed.push('created:'+c.action);return {id:'c1',action:c.action,status:'pending'};},complete:()=>{},get:()=>({status:'completed',result:{status:'ok'}})},
  measurePhone:()=>{},phonePush:{publish:()=>executed.push('pushed')},rawVisualCaptures:new Map(),
  decisionsConfig:{read:()=>({enabled:false})},genericUiActions:new Set(),visualGuard:{},
  answer:(res,status,body)=>responses.push({status,body}),
 });
 vm.runInContext(phoneFunctions,context);
 for(const name of ['/phone/commands','/task/stop'])vm.runInContext('globalThis['+JSON.stringify(name)+']=async(url,data,res,req)=>{'+route(name)+'};',context);
 const call=(name,data,headers={})=>context[name]({pathname:name},data,{},{headers});
 return {launcher,context,executed,responses,call};
}

for(const backgroundEnabled of [true,false]){
 test('after POST /task/stop no agent phone path executes (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});
  assert.equal((await f.context.fastPhone({action:'open_app',packageName:'dev.pixel.calculator.agent'})).status,'ok');
  f.executed.length=0;
  await f.call('/task/stop',{taskId:f.launcher.current.id,source:'tray'});
  assert.equal(f.responses.at(-1).body.status,'stopping');assert.equal(f.launcher.current.state,'stopping');
  f.executed.length=0;
  const direct=await f.context.fastPhone({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{expression:'34.50+20%'}});
  assert.equal(direct.status,'task_stopped');assert.equal(direct.actionExecuted,false);
  await f.call('/phone/commands',{action:'tap_label',label:'='});
  const posted=f.responses.at(-1);
  assert.equal(posted.status,201);assert.equal(posted.body.status,'completed');assert.equal(posted.body.result.status,'task_stopped');
  assert.deepEqual(f.executed,[],'nothing was created, pushed or executed after Stop');
 });
}

test('POST /task/stop for a stale task leaves the running task untouched',async()=>{
 const f=fixture({backgroundEnabled:true});
 await f.call('/task/stop',{taskId:'older',source:'tray'});
 assert.equal(f.responses.at(-1).body.status,'stale_task');assert.equal(f.launcher.current.state,'running');
 await f.call('/phone/commands',{action:'screenshot'});
 assert.equal(f.responses.at(-1).body.status,'pending');
});

test('acknowledged Stop stays in force through the terminal state until an explicit new task',async()=>{
 const f=fixture({backgroundEnabled:true});
 const taskA=f.launcher.current.id;
 await f.call('/task/stop',{taskId:taskA,source:'workspace_notification'});
 f.launcher.finish('interrupted','Task stopped');assert.equal(f.launcher.current.state,'interrupted');
 f.executed.length=0;
 for(const command of [{action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{expression:'34.50+20%'}},{action:'open_app',packageName:'dev.pixel.calculator.agent'}])
  assert.equal((await f.context.fastPhone(command)).status,'task_stopped',command.action);
 await f.call('/phone/commands',{action:'tap_label',label:'='});
 assert.equal(f.responses.at(-1).body.result.status,'task_stopped');
 assert.deepEqual(f.executed,[]);
 // dispatch() calls taskStop.begin() only for an explicit new user request.
 f.launcher.start('Explicit new request B');f.context.taskStop.begin(f.launcher.current.id);
 assert.equal((await f.context.fastPhone({action:'open_app',packageName:'dev.pixel.calculator.agent'})).status,'ok');
 assert.equal((await f.call('/task/stop',{taskId:taskA}),f.responses.at(-1).body.status),'stale_task','a late Stop for A cannot stop B');
 assert.equal(f.launcher.current.state,'running');
});

test('the Stop gate is reopened only where a new user request starts a task',()=>{
 const begins=[...server.matchAll(/taskStop\.begin\(/g)];
 assert.equal(begins.length,1);
 const dispatch=server.slice(server.indexOf('async function dispatch('),server.indexOf('async function submitLuna('));
 assert.match(dispatch,/if\(!codex\.active\)\{backgroundPhone\.beginTask\(\);launcherTask\.start\(displayRequest,taskWidgets\.mode\(\)\);taskStop\.begin\(launcherTask\.current\.id\);/);
});

test('phone commands naming another task generation fail closed; untagged legacy commands keep the Stop gate only',async()=>{
 const f=fixture({backgroundEnabled:false});
 const taskA=f.launcher.current.id;f.context.taskStop.begin(taskA);
 await f.call('/phone/commands',{action:'screenshot'},{'x-rose-task':taskA});
 assert.equal(f.responses.at(-1).body.status,'pending','the current generation runs');
 f.launcher.start('Explicit new request B');f.context.taskStop.begin(f.launcher.current.id);
 f.executed.length=0;
 await f.call('/phone/commands',{action:'tap_label',label:'='},{'x-rose-task':taskA});
 assert.equal(f.responses.at(-1).body.result.status,'stale_task');assert.deepEqual(f.executed,[],'stale task A did not run under B');
 await f.call('/phone/commands',{action:'screenshot'},{'x-rose-task':'unknown-task'});
 assert.equal(f.responses.at(-1).body.result.status,'stale_task');
 await f.call('/phone/commands',{action:'screenshot'});
 assert.equal(f.responses.at(-1).body.status,'pending','an untagged legacy command is not relabelled, only Stop-gated');
});

test('dynamic tool calls from another worker turn are refused before any tool runs',()=>{
 const handler=server.slice(server.indexOf('handleToolCall:async request=>{'),server.indexOf('handleToolCall:async request=>{')+400);
 assert.match(handler,/^handleToolCall:async request=>\{\n \/\/ .*\n \{const stale=taskStop\.authorize\(\{turnId:request\.turnId\}\);if\(stale\)return/);
 assert.match(server,/codex\.on\('toolActivity',\(method,params\)=>\{if\(method==='turn\/started'\)taskStop\.addTurn\(params\.turn\?\.id\);\}\);/);
});
