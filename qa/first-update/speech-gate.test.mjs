// Independent QA speech-after-Stop check (Chief ruling ROS-FIRST-UPDATE-CHIEF-STOP-001: new
// task-result speech after the Stop acknowledgment is a forbidden agent action; the truthful Stop
// acknowledgment is allowed). Runs the real server.mjs worker-message handler and finishAgentTurn
// (extracted from source text) with the real TaskStop and LauncherTask; output channels are stubs
// that record what would be shown or spoken. Offline only: no voice provider, audio or device.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const dir=new URL('../../workspace/rose-agent/server/',import.meta.url);
const has=fs.existsSync(new URL('task-stop.mjs',dir));
const {TaskStop}=has?await import(new URL('task-stop.mjs',dir)):{};
const {LauncherTask}=await import(new URL('launcher-task.mjs',dir));

function world(){
 const server=fs.readFileSync(new URL('server.mjs',dir),'utf8');
 const onMessage=server.slice(server.indexOf("codex.on('message',"),server.indexOf('async function finishAgentTurn('));
 const finish=server.slice(server.indexOf('async function finishAgentTurn('),server.indexOf('\n}',server.indexOf('async function finishAgentTurn('))+2);
 const out=[];
 const launcher=new LauncherTask(path.join(fs.mkdtempSync(path.join(os.tmpdir(),'qa-speech-')),'task.json'));
 const taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{}});
 const handlers={};
 const context=vm.createContext({taskStop,launcherTask:launcher,visualTrial:false,visualGuard:{needsReview:()=>false},backgroundEnabled:false,
  backgroundPhone:{close:async()=>{}},capture:{record:()=>{}},captureContext:()=>({}),logDecision:()=>{},Date,Error,
  codex:{on:(e,f)=>{handlers[e]=f;}},pendingVisualAnswer:'',reviewAbort:null,lastRoute:null,taskStarted:0,evaluation:null,
  deliverAgentMessage:(text,phase)=>out.push({kind:'delivered',phase,text}),
  voiceTasks:{finish:failure=>[{failure}]},emit:(e,d)=>out.push({kind:'emit',event:e,data:d}),
  spokenProgress:{finish:()=>{},start:()=>{}},speakProgress:text=>out.push({kind:'spoken',text})});
 vm.runInContext('var pendingVisualAnswer="";'+onMessage+'\n'+finish+'\nglobalThis.finishAgentTurn=finishAgentTurn;',context);
 return {out,launcher,taskStop,message:(t,p)=>handlers.message(t,p),finishTurn:turn=>context.finishAgentTurn(turn)};
}

test('after Stop is acknowledged, a late worker result is neither shown nor spoken; only the Stop acknowledgment is',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 w.launcher.start('What is 84.50 plus a 15% tip?');w.taskStop.begin();
 w.message('Working on it','commentary');
 const ack=await w.taskStop.request({taskId:w.launcher.current.id,source:'app'});
 assert.equal(ack.status,'stopping');
 const mark=w.out.length;
 w.message('The total is 97.18','final_answer');
 await w.finishTurn({status:'interrupted'});
 const after=w.out.slice(mark);
 assert.equal(after.some(o=>o.kind==='delivered'),false,'late result delivered: '+JSON.stringify(after));
 assert.equal(JSON.stringify(after).includes('97.18'),false,'late result reached an output channel: '+JSON.stringify(after));
 for(const o of after.filter(o=>o.kind==='spoken'))assert.match(o.text,/stopped/i,'only Stop acknowledgment may be spoken');
});

// Known open item (App APP-001-M005, Chief ruling): audio already received by the voice client
// and playing at the acknowledgment is not cancelled from the task path. This needs a VM/device
// audio capture or a client-side cancel contract from App; it is NOT accepted offline.
test.todo('voice output already playing at the Stop acknowledgment is cancelled (requires App client contract or VM audio evidence)');
