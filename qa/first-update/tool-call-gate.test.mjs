// Independent QA check (Chief review APP-005 item 1 / APP-006): after a Stop is acknowledged, a
// dynamic tool call from the stopped worker turn must not run any tool handler, phone or not.
// Uses the real server.mjs tool-call gate and the web_lookup dispatch line (extracted from source)
// with the real TaskStop; the web lookup itself is a recording stub (no network).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {AsyncLocalStorage} from 'node:async_hooks';

const dir=new URL('../../workspace/rose-agent/server/',import.meta.url);
const server=fs.readFileSync(new URL('server.mjs',dir),'utf8');
const marker=' // A tool call from a stopped or earlier worker turn never runs.';
// WIRING ADAPTER: App 2f1d6e2 gated inside handleToolCall (marker comment); App b51ddf9 wraps
// appRuntime.handleToolCall in a block that runs the original handler under phoneOrigin.
const WRAP='{const handle=appRuntime.handleToolCall;appRuntime.handleToolCall=request=>{';
const wrapped=server.includes(WRAP);
const has=fs.existsSync(new URL('task-stop.mjs',dir))&&(server.includes(marker)||wrapped);
const {TaskStop}=has?await import(new URL('task-stop.mjs',dir)):{};

// TURN-REGISTRATION ADAPTER: App 2749a76 binds a worker turn to its task at creation via
// TaskStop.bindTurn(turnId, taskId) (codex 'turnCreated'); earlier heads used addTurn(turnId).
const registerTurn=(ts,turnId,taskId)=>ts.bindTurn?ts.bindTurn(turnId,taskId):ts.addTurn?.(turnId);
function gate(){
 const ran=[];let current={id:'task-A',state:'running'};
 const taskStop=new TaskStop({task:()=>current,stopping:()=>{current={...current,state:'stopping'};},interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{}});
 const context=vm.createContext({taskStop,client:{},JSON,capture:{record:()=>{}},captureContext:()=>({}),
  webLookup:async()=>{ran.push('web_lookup');return {usage:null};}});
 if(wrapped){
  const s=server.indexOf(WRAP);const block=server.slice(s,server.indexOf('\n',s));
  context.AsyncLocalStorage=AsyncLocalStorage;
  vm.runInContext('var phoneOrigin=new AsyncLocalStorage();var appRuntime={handleToolCall:async request=>{if(request.tool===\'web_lookup\'){await webLookup(client,request.arguments);return {success:true};}return {success:true};}};'+block+'\nglobalThis.handle=r=>appRuntime.handleToolCall(r);',context);
 }else{
  const start=server.indexOf(marker),lookup=server.indexOf("if(request.tool==='web_lookup')",start);
  const body=server.slice(start,server.indexOf('\n',lookup));
  vm.runInContext('globalThis.handle=async request=>{'+body+'\nreturn {success:true,fellThrough:true};};',context);
 }
 return {ran,taskStop,handle:r=>context.handle(r),setCurrent:c=>{current=c;}};
}

test('after Stop is acknowledged, a tool call from the stopped turn A runs no handler',{skip:!has&&'no tool-call gate in this tree'},async()=>{
 const g=gate();
 g.taskStop.begin('task-A');registerTurn(g.taskStop,'turn-A','task-A');
 const control=await g.handle({tool:'web_lookup',turnId:'turn-A',arguments:{query:'tip'}});
 assert.deepEqual(g.ran,['web_lookup'],'control: before Stop the tool runs');assert.equal(control.success,true);
 await g.taskStop.request({taskId:'task-A',source:'app'});
 await g.handle({tool:'web_lookup',turnId:'turn-A',arguments:{query:'tip'}});
 assert.deepEqual(g.ran,['web_lookup'],'stopped turn A ran a tool after the Stop acknowledgment');
});

test('a tool call from turn A after fresh B begins runs no handler; a turn-B call runs',{skip:!has&&'no tool-call gate in this tree'},async()=>{
 const g=gate();
 g.taskStop.begin('task-A');registerTurn(g.taskStop,'turn-A','task-A');
 await g.taskStop.request({taskId:'task-A',source:'app'});
 g.setCurrent({id:'task-B',state:'running'});g.taskStop.begin('task-B');registerTurn(g.taskStop,'turn-B','task-B');
 await g.handle({tool:'web_lookup',turnId:'turn-A',arguments:{query:'old'}});
 assert.deepEqual(g.ran,[],'turn A ran a tool after B began');
 await g.handle({tool:'web_lookup',turnId:'turn-B',arguments:{query:'new'}});
 assert.deepEqual(g.ran,['web_lookup'],'turn B tool must run');
});

test('a stale A tool call after B begins but before B\'s turn is registered runs no handler',{skip:!has&&'no tool-call gate in this tree'},async()=>{
 const g=gate();
 g.taskStop.begin('task-A');registerTurn(g.taskStop,'turn-A','task-A');
 await g.taskStop.request({taskId:'task-A',source:'app'});
 g.setCurrent({id:'task-B',state:'running'});g.taskStop.begin('task-B');
 await g.handle({tool:'web_lookup',turnId:'turn-A',arguments:{query:'stale'}});
 assert.deepEqual(g.ran,[],'stale A tool call ran in the window before B\'s turn was registered');
});

test('a tool call with no turn ID runs no handler while a task is current',{skip:!has&&'no tool-call gate in this tree'},async()=>{
 const g=gate();
 g.taskStop.begin('task-A');registerTurn(g.taskStop,'turn-A','task-A');
 await g.handle({tool:'web_lookup',arguments:{query:'no turn'}});
 assert.deepEqual(g.ran,[],'a tool call without a turn ID ran');
});
