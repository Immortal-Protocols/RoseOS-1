// APP-BUG-OPEN-001: a reported "open ChatGPT on the phone display" refusal. The shell tool
// (control.py over HTTP) cannot prove its task, so it may only observe; the registered
// in-process tool route carries its worker turn's task. Both entry points are sliced from
// server.mjs and run without starting the server; the phone executor is a recorder.
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
import {taskSetupPolicy} from './task-setup.mjs';

const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
const between=(from,to)=>{const start=server.indexOf(from),end=server.indexOf(to,start);assert.ok(start>0&&end>start,from);return server.slice(start,end);};
const line=from=>{const start=server.indexOf(from);assert.ok(start>0,from);return server.slice(start,server.indexOf('\n',start));};
const phoneFunctions=line('const phoneOrigin=')+'\n'+line('const readOnlyPhoneActions=')+'\n'
 +between('function phoneCommandRejection(','function checkPausedApp(')+between('function legacyReadPhone(','async function rawFastPhone(');
const commandsRoute=line(" if(url.pathname==='/phone/commands'){");
const toolCallWrapper=line('{const handle=appRuntime.handleToolCall;appRuntime.handleToolCall=');
const CHATGPT='com.openai.chatgpt';
const REASON='This shell phone tool cannot prove which task it belongs to, so it may only observe. Use the registered app tools for open_app.';

function fixture({backgroundEnabled}){
 const launcher=new LauncherTask(path.join(fs.mkdtempSync(path.join(os.tmpdir(),'unbound-open-')),'task.json'));
 const executed=[],responses=[];
 const taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{}});
 const describe=(c,o={})=>c.action+(c.packageName?' '+c.packageName:'')+(c.display?' display='+c.display:'')+(o.taskId?' @'+o.taskId:'')+(c.taskId?' leaked':'');
 // The registered tool handler a worker turn reaches: it launches through fastPhone like
 // app-tool-catalog's foreground routes, keeping the requested display.
 const appRuntime={handleToolCall:async request=>({success:true,result:await context.fastPhone({action:'open_app',...request.arguments})})};
 const context=vm.createContext({taskStop,backgroundEnabled,appRuntime,crypto,setImmediate,Date,Error,Object,Set,Promise,JSON,AsyncLocalStorage,
  checkPausedApp:()=>{},peerIdentity:req=>req?.phone?{id:'paired-phone'}:null,autoGenerator:{active:false},visualTrial:false,busy:true,taskStarted:0,lastRoute:null,
  backgroundPhone:{execute:async(c,o)=>{executed.push('background:'+describe(c,o));return {status:'opened',packageName:c.packageName};}},
  rawFastPhone:async c=>{executed.push('raw:'+describe(c));return {status:'opened',packageName:c.packageName};},
  phone:{create:c=>{executed.push('created:'+describe(c));return {id:'c1',action:c.action,status:'pending'};},complete:()=>{}},
  measurePhone:()=>{},phonePush:{publish:()=>executed.push('pushed')},rawVisualCaptures:new Map(),
  decisionsConfig:{read:()=>({enabled:false})},genericUiActions:new Set(),visualGuard:{},
  answer:(res,status,body)=>responses.push({status,body}),
 });
 vm.runInContext(phoneFunctions+';globalThis.fastPhone=fastPhone;'+toolCallWrapper,context);
 vm.runInContext('globalThis.postCommand=async(url,data,res,req)=>{'+commandsRoute+'};',context);
 const shell=async data=>{await context.postCommand({pathname:'/phone/commands'},data,{},{});await new Promise(r=>setImmediate(r));return responses.at(-1);};
 const newRequest=text=>{launcher.start(text);taskStop.begin(launcher.current.id);return launcher.current.id;};
 const tool=(turnId,args)=>context.appRuntime.handleToolCall({tool:'open_app_test',turnId,arguments:args});
 return {taskStop,executed,shell,newRequest,tool};
}

for(const backgroundEnabled of [true,false]){
 test('the reported shell launch is refused before any phone work, with the exact reported reason (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});const A=f.newRequest('Open ChatGPT');
  for(const request of [{action:'open_app',packageName:CHATGPT,display:'phone'},{action:'open_app',packageName:CHATGPT},{action:'open_app',packageName:CHATGPT,display:'phone',taskId:A}]){
   const {status,body}=await f.shell(request);
   assert.equal(status,201,JSON.stringify(request));
   assert.deepEqual({...body.result},{status:'not_executed',actionExecuted:false,reason:REASON},'a client-supplied taskId does not bind the shell call: '+JSON.stringify(request));
  }
  assert.deepEqual(f.executed,[],'nothing was created, pushed or executed on either display');
 });

 test('a registered tool call of the current turn launches on the requested display exactly once (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});const A=f.newRequest('Open ChatGPT');f.taskStop.bindTurn('turn-A',A);
  const reply=await f.tool('turn-A',{packageName:CHATGPT,display:'phone'});
  assert.equal(reply.result.status,'opened');
  assert.deepEqual(f.executed,[backgroundEnabled?'background:open_app '+CHATGPT+' display=phone @'+A:'raw:open_app '+CHATGPT+' display=phone'],
   'the in-process origin reaches the controller, and no task ID enters the phone payload');
 });

 test('a registered call from a missing, unregistered, earlier or stopped turn never launches (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});const A=f.newRequest('Open ChatGPT');f.taskStop.bindTurn('turn-A',A);
  const refused=async turnId=>JSON.parse((await f.tool(turnId,{packageName:CHATGPT,display:'phone'})).contentItems[0].text).status;
  assert.equal(await refused(undefined),'stale_task');
  assert.equal(await refused('turn-unknown'),'stale_task');
  const B=f.newRequest('Something else');f.taskStop.bindTurn('turn-B',B);
  assert.equal(await refused('turn-A'),'stale_task','a turn of the earlier task');
  await f.taskStop.request({taskId:B,source:'test'});
  assert.equal(await refused('turn-B'),'task_stopped');
  assert.deepEqual(f.executed,[]);
 });
}

// The worker policy names each command it offers through control.py. Every offered mutation is
// one the server refuses on that path, so a request such as "open ChatGPT on the phone" can only
// end in the refusal above. Known defect: the policy still offers them, and no registered tool
// launches an arbitrary installed app (catalog routes launch only their own package, in the
// background workspace). Remove the todo once the policy and the registered tools agree.
const readOnly=vm.runInNewContext(line('const readOnlyPhoneActions=').replace('const readOnlyPhoneActions=','').replace(/;$/,'')+'.add("home").add("back")');
const offeredShellMutations=policy=>{
 const list=policy.match(/Phone tools: Use python3 rose-agent\/phone\/control\.py\. Tools: ([^\n]*?)\. Read /)?.[1]||'';
 const offered=[...list.replace(/\([^)]*\)/g,'').matchAll(/(?:^|, )([a-z_]+)/g)].map(m=>m[1]).filter(name=>!readOnly.has(name));
 if(/control\.py --display phone followed by the normal command/.test(policy))offered.push('--display phone');
 return offered;
};
test('the reported request shape is the one the worker policy prescribes',()=>{
 const policy=taskSetupPolicy({backgroundEnabled:true,appToolsEnabled:true,controlPath:'control',visualPath:'visual'});
 assert.match(policy,/To inspect or operate the physical phone screen[^.]*, use python3 rose-agent\/phone\/control\.py --display phone followed by the normal command/);
 assert.match(policy,/Phone tools: Use python3 rose-agent\/phone\/control\.py\. Tools: [^\n]*open_app PACKAGE_NAME/);
});
test('the worker policy offers no shell mutation the server refuses',{todo:'APP-BUG-OPEN-001: policy and registered tools disagree; see the Notion bug record'},()=>{
 for(const backgroundEnabled of [true,false]){
  const policy=taskSetupPolicy({backgroundEnabled,appToolsEnabled:true,controlPath:'control',visualPath:'visual'});
  assert.deepEqual(offeredShellMutations(policy),[],'background '+backgroundEnabled);
 }
});
