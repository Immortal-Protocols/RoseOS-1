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
import {phoneUiToolDefinitions} from './phone-ui-tools.mjs';

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

// The worker policy must not prescribe a shell command the server refuses: control.py over HTTP
// can only observe. Its boundary names every refused control.py command, derived here from the
// real CLI and the server's read-only set rather than restated.
const readOnly=vm.runInNewContext(line('const readOnlyPhoneActions=').replace('const readOnlyPhoneActions=','').replace(/;$/,''));
const cli=fs.readFileSync(new URL('../phone/control.py',import.meta.url),'utf8');
const cliCommands=[...new Set([...cli.matchAll(/add_parser\('([a-z_]+)'/g)].map(m=>m[1])
 .concat(...[...cli.matchAll(/for name in \(([^)]*)\):\n\s+\w*=?sub\.add_parser\(name\)/g)].map(m=>[...m[1].matchAll(/'([a-z_]+)'/g)].map(n=>n[1]))))];
// messages_read goes to the Mac worker (/messages-worker/read), not /phone/commands.
const refusedCli=cliCommands.filter(name=>name!=='messages_read'&&!readOnly.has(name)).sort();
const policies=[true,false].flatMap(backgroundEnabled=>[true,false].map(appToolsEnabled=>({backgroundEnabled,appToolsEnabled,
 text:taskSetupPolicy({backgroundEnabled,appToolsEnabled,controlPath:'control',visualPath:'visual'})})));
const observationList=policy=>{
 const list=policy.match(/Phone tools: python3 rose-agent\/phone\/control\.py can only observe the phone\. Observation commands: ([^\n]*?)\. Read /)?.[1];
 assert.ok(list,'the phone tools section lists its observation commands');
 return [...list.replace(/\([^)]*\)/g,'').matchAll(/(?:^|, )([a-z_]+)/g)].map(m=>m[1]);
};

test('the CLI derivation sees the real command set',()=>{
 for(const name of ['open_app','tap_label','drag','screenshot','inspect_ui','home','messages_read'])assert.ok(cliCommands.includes(name),name);
 assert.ok(refusedCli.includes('open_app')&&refusedCli.includes('home')&&!refusedCli.includes('screenshot'));
});

test('the worker policy offers control.py only for observations the server runs',()=>{
 for(const {backgroundEnabled,appToolsEnabled,text} of policies){
  const label='background '+backgroundEnabled+', app tools '+appToolsEnabled;
  const offered=observationList(text);
  assert.deepEqual(offered.filter(name=>!readOnly.has(name)),[],label);
  assert.ok(offered.includes('screenshot')&&offered.includes('inspect_ui'),label);
  assert.doesNotMatch(text,/open_app PACKAGE_NAME|control\.py --display phone followed by the normal command|operate the physical phone screen/,label);
  assert.doesNotMatch(text,/Use the normal phone tools: open_app/,label);
 }
});

test('the policy states the refusal boundary and the registered equivalent of every refused control.py command',()=>{
 for(const {backgroundEnabled,appToolsEnabled,text} of policies){
  const label='background '+backgroundEnabled+', app tools '+appToolsEnabled;
  const boundary=text.match(/Phone control boundary: [^\n]*/)?.[0];
  assert.ok(boundary,label);
  const named=boundary.match(/control\.py commands with not_executed and actionExecuted=false: ([a-z_, ]+)\./)[1].split(', ').sort();
  assert.deepEqual(named,refusedCli,'the boundary names exactly the refused CLI commands: '+label);
  assert.match(boundary,/supersedes every other instruction, README or capability text/);
  assert.match(boundary,/never claim it ran/);
  // Each refused command has a registered request-bound equivalent, or is stated as unavailable.
  const registered=new Set(phoneUiToolDefinitions({background:backgroundEnabled}).map(t=>t.name));
  for(const name of refusedCli){
   if(registered.has('phone_'+name))assert.match(boundary,new RegExp('\\bphone_'+name+'\\b'),name+': '+label);
   else assert.match(boundary,new RegExp('no registered equivalent[^.]*'+({app_command:'app_command',clock_control:'clock',clock_timer_start:'clock',media_control:'media',apple_music_run:'music',voice_ducking:'ducking',setup_ui_control:'accessibility-setup'}[name]??name)),name+' is stated as unavailable: '+label);
  }
  assert.match(boundary,/run as the current request, honour Stop/);
 }
 const background=policies.find(p=>p.backgroundEnabled).text;
 assert.match(background,/with --display phone they read the physical phone screen instead/);
 assert.match(background,/The shell cannot open apps, tap, type, go home or act in any other way on either display/);
 assert.match(background,/set display to phone on any of them to operate the user's physical phone screen/);
});
