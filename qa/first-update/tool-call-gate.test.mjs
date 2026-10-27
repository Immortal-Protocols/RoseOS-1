// Independent QA check (Chief review APP-005 item 1 / APP-006): after a Stop is acknowledged, a
// dynamic tool call from the stopped worker turn must not run any tool handler, phone or not.
// Uses the real server.mjs tool-call gate and the web_lookup dispatch line (extracted from source)
// with the real TaskStop; the web lookup itself is a recording stub (no network).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const dir=new URL('../../workspace/rose-agent/server/',import.meta.url);
const server=fs.readFileSync(new URL('server.mjs',dir),'utf8');
const marker=' // A tool call from a stopped or earlier worker turn never runs.';
const has=fs.existsSync(new URL('task-stop.mjs',dir))&&server.includes(marker);
const {TaskStop}=has?await import(new URL('task-stop.mjs',dir)):{};

function gate(){
 const start=server.indexOf(marker),lookup=server.indexOf("if(request.tool==='web_lookup')",start);
 const body=server.slice(start,server.indexOf('\n',lookup));
 const ran=[];let current={id:'task-A',state:'running'};
 const taskStop=new TaskStop({task:()=>current,stopping:()=>{current={...current,state:'stopping'};},interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{}});
 const context=vm.createContext({taskStop,client:{},JSON,capture:{record:()=>{}},captureContext:()=>({}),
  webLookup:async()=>{ran.push('web_lookup');return {usage:null};}});
 vm.runInContext('globalThis.handle=async request=>{'+body+'\nreturn {success:true,fellThrough:true};};',context);
 return {ran,taskStop,handle:r=>context.handle(r),setCurrent:c=>{current=c;}};
}

test('after Stop is acknowledged, a tool call from the stopped turn A runs no handler',{skip:!has&&'no tool-call gate in this tree'},async()=>{
 const g=gate();
 g.taskStop.begin('task-A');g.taskStop.addTurn('turn-A');
 const control=await g.handle({tool:'web_lookup',turnId:'turn-A',arguments:{query:'tip'}});
 assert.deepEqual(g.ran,['web_lookup'],'control: before Stop the tool runs');assert.equal(control.success,true);
 await g.taskStop.request({taskId:'task-A',source:'app'});
 await g.handle({tool:'web_lookup',turnId:'turn-A',arguments:{query:'tip'}});
 assert.deepEqual(g.ran,['web_lookup'],'stopped turn A ran a tool after the Stop acknowledgment');
});
