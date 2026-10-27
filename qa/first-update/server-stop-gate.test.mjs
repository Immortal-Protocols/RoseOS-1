// Independent QA server-layer Stop check (Chief review ROS-FIRST-UPDATE-CHIEF-REVIEW-QA-002).
// Uses the real server.mjs fastPhone and /task/stop + /phone/commands route bodies (extracted
// from source text, as App's own server-stop-gate test does), wired to the real TaskStop,
// LauncherTask and BackgroundPhone over a fake controller that follows Controller.java.
// Requires a tree with App PR #3 (task-stop.mjs). Offline only; not VM or device evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';

const dir=new URL('../../workspace/rose-agent/server/',import.meta.url);
const has=fs.existsSync(new URL('task-stop.mjs',dir));
const {TaskStop}=has?await import(new URL('task-stop.mjs',dir)):{};
const {LauncherTask}=await import(new URL('launcher-task.mjs',dir));
const {BackgroundPhone}=await import(new URL('background-phone.mjs',dir));
const CONTROLLER='dev.pixel.backgroundcontrol', CALCULATOR='dev.pixel.calculator.agent';

function world(){
 const server=fs.readFileSync(new URL('server.mjs',dir),'utf8');
 const fastPhoneSrc=server.slice(server.indexOf('async function fastPhone('),server.indexOf('async function rawFastPhone('));
 const route=name=>{const s=server.indexOf(" if(url.pathname==='"+name+"'){");assert.ok(s>0,name);return server.slice(s,server.indexOf('\n',s));};
 const log=[];let seq=0;const hold=new Map();
 const holdFor=expr=>{let release;const promise=new Promise(r=>release=r);hold.set(expr,{promise,release});return release;};
 const ctl={status:'stopped',sessionId:null,taskId:null,target:null,stopReason:'',stoppedSessionId:null};
 const userStopped=new Set();
 const state=()=>({status:ctl.status,sessionId:ctl.sessionId,targetPackage:ctl.target,taskId:ctl.taskId,stopReason:ctl.stopReason,stoppedSessionId:ctl.stoppedSessionId,elapsedMs:1000});
 const rec=why=>{if(ctl.stopReason==='user'&&why!=='user'&&ctl.stoppedSessionId===ctl.sessionId)return;ctl.stopReason=why;ctl.stoppedSessionId=ctl.sessionId;if(why==='user'&&ctl.taskId)userStopped.add(ctl.taskId);};
 const send=async c=>{
  if(c.packageName===CALCULATOR){const h=hold.get(c.params?.expression);if(h){log.push('calculator-begin:'+c.params.expression);await h.promise;}log.push('calculator:'+(c.params?.expression??c.operation));return {status:'completed',result:{status:'verified'}};}
  if(c.packageName!==CONTROLLER){log.push('physical:'+c.action);return {status:'ok'};}
  if(c.operation==='get_state')return state();
  if(c.operation==='start'){const t=c.params.taskId??null;if(t&&userStopped.has(t))return {status:'user_stopped',rejectedTaskId:t,actionExecuted:false,current:state()};
   ctl.status='ready';ctl.sessionId='session-'+(++seq);ctl.taskId=t;ctl.target=c.params.packageName;ctl.stopReason='';ctl.stoppedSessionId=null;log.push('start:'+ctl.sessionId+':'+t);return state();}
  // OS 3fdd843: command/stop tagged for a task other than the workspace's are refused; untagged = legacy.
  const tagged=c.params?.taskId??null;
  if((c.operation==='stop'||c.operation==='command')&&tagged&&tagged!==ctl.taskId)return {status:userStopped.has(tagged)?'user_stopped':'stale_task',actionExecuted:false,rejectedTaskId:tagged,current:state()};
  if(c.operation==='stop'){rec('agent');ctl.status='stopped';return state();}
  if(c.operation==='command'){if(ctl.status!=='ready'||c.params.sessionId!==ctl.sessionId)return {status:'error',error:'Session not ready: '+ctl.status};log.push('ui:'+c.params.action);return {status:'ok',packageName:ctl.target,sessionId:ctl.sessionId,snapshotId:'s'+seq,nodes:[]};}
  throw Error('unexpected '+c.operation);
 };
 const launcher=new LauncherTask(path.join(fs.mkdtempSync(path.join(os.tmpdir(),'qa-stop-')),'task.json'));
 let taskStop;
 const backgroundPhone=new BackgroundPhone({send,enabled:()=>true,sleep:async()=>{},taskId:()=>launcher.active()?launcher.current.id:null,onOwnerStop:({taskId})=>void taskStop.request({taskId:taskId||undefined,source:'workspace_notification'})});
 const pendingPhone=new Map();
 // WIRING ADAPTER: mirrors server.mjs construction at the tested head; optional hooks are ignored by older code.
 taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>{},abortReview:()=>{},
  announce:()=>{},cancelQueued:()=>{backgroundPhone.latchStop?.();},onBegin:()=>{},
  closeWorkspace:async()=>{const s=await (backgroundPhone.endForStop?backgroundPhone.endForStop():backgroundPhone.close());if(s?.status==='cleanup_failed')throw Error('cleanup_failed');}});
 const responses=[];
 const context=vm.createContext({taskStop,backgroundEnabled:true,backgroundPhone,crypto,setImmediate,Date,Error,checkPausedApp:()=>{},
  autoGenerator:{active:false},visualTrial:false,busy:true,genericUiActions:new Set(),visualGuard:{},measurePhone:()=>{},
  phone:{create:c=>({id:'c'+Math.random(),action:c.action,status:'pending'}),complete:()=>{},get:()=>({status:'pending'})},
  phonePush:{publish:()=>{}},rawVisualCaptures:new Map(),decisionsConfig:{read:()=>({enabled:false})},
  rawFastPhone:async c=>{log.push('raw:'+c.action);return {status:'ok'};},answer:(res,status,body)=>responses.push({status,body})});
 vm.runInContext(fastPhoneSrc,context);
 for(const name of ['/phone/commands','/task/stop'])vm.runInContext('globalThis['+JSON.stringify(name)+']=async(url,data,res,req)=>{'+route(name)+'};',context);
 const callNoWait=(name,data,headers={})=>context[name]({pathname:name},data,{},{headers});
 const call=async(name,data,headers={})=>{await context[name]({pathname:name},data,{},{headers});await new Promise(r=>setTimeout(r,25));return responses.at(-1);};
 // Mirrors server.mjs dispatch() for a new explicit user request when no worker is active.
 const newRequest=text=>{backgroundPhone.beginTask?.();launcher.start(text);taskStop.begin(launcher.current.id);return launcher.current.id;};
 // Mirrors server.mjs finishAgentTurn() after a stopped turn ends.
 // Mirrors finishAgentTurn() for the stopped task (A). launcher.finish is a no-op if B already replaced A.
 const turnEnds=async()=>{await backgroundPhone.close();launcher.finish('interrupted',taskStop.summary());};
 return {log,launcher,taskStop,fastPhone:c=>context.fastPhone(c),call,newRequest,turnEnds,ctl,holdFor,callNoWait};
}

test('acknowledged /task/stop -> terminal interrupted -> late Calculator, open_app and raw command are all denied; fresh B allowed',{skip:!has&&'tree has no task-stop.mjs (App PR #3 not present)'},async()=>{
 const w=world();
 const A=w.newRequest('What is 84.50 plus a 15% tip?');
 await w.fastPhone({action:'open_app',packageName:CALCULATOR});
 const ack=await w.call('/task/stop',{taskId:A,source:'workspace_notification'});
 assert.equal(ack.body.status,'stopping');
 await w.turnEnds();
 assert.equal(w.launcher.current.state,'interrupted');
 const mark=w.log.length;
 const lateCalc=await w.fastPhone({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'84.50+15%'}});
 const lateOpen=await w.fastPhone({action:'open_app',packageName:CALCULATOR});
 await w.call('/phone/commands',{action:'app_command',packageName:CALCULATOR,operation:'continue',params:{suffix:'+1'}});
 const lateDisplay=await w.fastPhone({action:'tap',display:'phone',x:1,y:1});
 assert.deepEqual(w.log.slice(mark),[],'late canceled-task actions executed: '+JSON.stringify({lateCalc,lateOpen,lateDisplay}));
 const B=w.newRequest('What is 12 times 7?');
 const mark2=w.log.length;
 const opened=await w.fastPhone({action:'open_app',packageName:CALCULATOR});
 await w.fastPhone({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'12*7'}});
 assert.equal(opened.backgroundVerified,true,JSON.stringify(opened));
 assert.deepEqual(w.log.slice(mark2).map(e=>e.split(':')[0]),['start','ui','calculator']);
 assert.ok(w.log.slice(mark2)[0].endsWith(':'+B),'B workspace started under task B');
});

test('while task A is stopping (before terminal), late Calculator/open_app/raw are denied',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Calculate 20% of 50');
 await w.fastPhone({action:'open_app',packageName:CALCULATOR});
 await w.call('/task/stop',{taskId:A,source:'app'});
 const mark=w.log.length;
 await w.fastPhone({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'50*20%'}});
 await w.fastPhone({action:'open_app',packageName:CALCULATOR});
 await w.call('/phone/commands',{action:'screenshot'});
 assert.deepEqual(w.log.slice(mark),[]);
});

test('a duplicate Stop is not acknowledged before queued canceled-task work is barred',{skip:!has&&'no task-stop.mjs'},async()=>{
 // Chief review APP-002 sequence. X executes before Stop and is held; Y was admitted before Stop and is queued.
 const w=world();
 const A=w.newRequest('Two calculations');
 await w.fastPhone({action:'open_app',packageName:CALCULATOR});
 const releaseX=w.holdFor('1+1');
 const x=w.fastPhone({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'1+1'}});
 const y=w.fastPhone({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'2+2'}});
 await new Promise(r=>setImmediate(r));
 const first=w.taskStop.request({taskId:A,source:'workspace_notification'});
 const dup=await w.taskStop.request({taskId:A,source:'app'});
 const ackMark=w.log.length;
 assert.equal(dup.status,'stopping');
 releaseX();await Promise.all([x,y,first]);
 assert.deepEqual(w.log.slice(ackMark).filter(e=>e==='calculator:2+2'),[],'queued Y ran after the duplicate Stop acknowledgment; log after ack: '+JSON.stringify(w.log.slice(ackMark)));
});

// ---- Generation identity (Chief decision ROS-FIRST-UPDATE-CHIEF-GENERATION-001) ----
// INTERFACE ADAPTER (App PR #3 @ 2f1d6e2, documented in source): phone/control.py reads the
// published task generation and sends it as HTTP header X-Rose-Task on POST /phone/commands.
// Only this adapter tracks App's interface; the expectations below are unchanged.
const tagged=(w,command,taskId)=>w.call('/phone/commands',command,{'x-rose-task':taskId});
const untagged=(w,command)=>w.call('/phone/commands',command,{});

test('generation: after A is stopped and B is running, A-tagged commands are denied and B-tagged commands run',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Add 10 and 5');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 await w.call('/task/stop',{taskId:A,source:'workspace_notification'});
 await w.turnEnds();
 const B=w.newRequest('Multiply 6 by 7');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},B);
 const mark=w.log.length;
 await tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'10+5'}},A);
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 assert.deepEqual(w.log.slice(mark),[],'stale task-A commands executed while B runs');
 await tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'6*7'}},B);
 assert.deepEqual(w.log.slice(mark),['calculator:6*7'],'B must still run');
});

test('generation: a command tagged with an unknown generation fails closed in task paths',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Add 1 and 1');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 const mark=w.log.length;
 await tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'8+8'}},'not-a-real-task');
 assert.deepEqual(w.log.slice(mark),[]);
});

test('generation: canceled-A work queued before Stop does not run after fresh B begins',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Two calculations');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 const releaseX=w.holdFor('3+3');
 void tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'3+3'}},A);
 void tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'4+4'}},A);
 await new Promise(r=>setTimeout(r,25));
 const stop=w.taskStop.request({taskId:A,source:'app'});
 w.newRequest('Fresh request');
 const mark=w.log.length;
 releaseX();await stop.catch(()=>{});await new Promise(r=>setTimeout(r,25));
 assert.deepEqual(w.log.slice(mark).filter(e=>e==='calculator:4+4'),[],'queued A work ran after B began: '+JSON.stringify(w.log.slice(mark)));
});

test('a Stop of A still in progress when fresh B begins completes with a truthful acknowledgment',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Slow calculation');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 const releaseX=w.holdFor('5+5');
 void tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'5+5'}},A);
 await new Promise(r=>setTimeout(r,25));
 const stop=w.taskStop.request({taskId:A,source:'app'});
 w.newRequest('Fresh request');
 releaseX();
 const ack=await stop;
 assert.equal(ack.status,'stopping');assert.equal(ack.taskId,A);
});

test('generation: an intentional non-task command still works with no task running',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const r=await w.fastPhone({action:'control_status'});
 assert.notEqual(r?.status,'task_stopped');
});

// Separate assessment (not part of tagged stale-A protection): commands that carry NO generation.
// Chief requires canceled/unknown task-path generations to fail closed; an untagged task-path
// command from stopped task A arriving while B runs is reported here on its own.
test('legacy fallback (assessment): an UNTAGGED task-path command from stopped A while B runs is denied',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Add 2 and 2');
 await untagged(w,{action:'open_app',packageName:CALCULATOR});
 await w.call('/task/stop',{taskId:A,source:'app'});
 await w.turnEnds();
 w.newRequest('Another request');
 await untagged(w,{action:'open_app',packageName:CALCULATOR});
 const mark=w.log.length;
 await untagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'2+2'}});
 assert.deepEqual(w.log.slice(mark),[],'untagged command from A executed under B: '+JSON.stringify(w.log.slice(mark)));
});

test('generation: an HTTP command for A accepted before Stop, dispatched after B begins, is not run',{skip:!has&&'no task-stop.mjs'},async()=>{
 // Chief review APP-004 item 3: admission validates X-Rose-Task, execution happens later in setImmediate.
 const w=world();
 const A=w.newRequest('Add 7 and 7');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 const mark=w.log.length;
 // Admit A's command (route returns after scheduling dispatch), then Stop A and begin B before dispatch runs.
 const admitted=w.callNoWait('/phone/commands',{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'7+7'}},{'x-rose-task':A});
 const stop=w.taskStop.request({taskId:A,source:'app'});
 w.newRequest('Fresh request B');
 await admitted;await stop.catch(()=>{});await new Promise(r=>setTimeout(r,40));
 assert.deepEqual(w.log.slice(mark).filter(e=>e.startsWith('calculator')),[],'A command accepted before Stop ran after B began: '+JSON.stringify(w.log.slice(mark)));
});

test('generation: a late cleanup from stopped A does not close task B\'s workspace',{skip:!has&&'no task-stop.mjs'},async()=>{
 // Chief review APP-004 item 4: A's turn-end close() arriving after B opened its workspace.
 const w=world();
 const A=w.newRequest('Add 3 and 4');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},A);
 await w.call('/task/stop',{taskId:A,source:'app'});
 const B=w.newRequest('Multiply 3 by 4');
 await tagged(w,{action:'open_app',packageName:CALCULATOR},B);
 const bSession=w.ctl.sessionId;
 await w.turnEnds();
 assert.equal(w.ctl.status,'ready','B workspace was closed by A\'s late cleanup');assert.equal(w.ctl.sessionId,bSession);
 const mark=w.log.length;
 await tagged(w,{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'3*4'}},B);
 assert.deepEqual(w.log.slice(mark),['calculator:3*4'],'B must remain usable');
});

// ---- Immutable origin (Chief ruling ROS-FIRST-UPDATE-CHIEF-ORIGIN-001) ----
// ORIGIN PROTOCOL ADAPTER: headers a phone tool sends to prove which task ORIGINATED it while
// presenting a task ID. At App 2f1d6e2 the only protocol is a bare X-Rose-Task value, so the origin
// argument cannot be expressed and is ignored. When App documents an origin-bound credential (e.g.
// a per-turn token bound at worker/tool launch), change only this adapter; expectations stay.
const originHeaders=(_w,_originTask,presentedTask)=>({'x-rose-task':presentedTask});

test('origin: a delayed A-origin command presenting B\'s task ID gains no authority; B-origin command runs',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Add 9 and 9');
 await w.call('/phone/commands',{action:'open_app',packageName:CALCULATOR},originHeaders(w,A,A));
 await w.call('/task/stop',{taskId:A,source:'app'});
 await w.turnEnds();
 const B=w.newRequest('Subtract 2 from 9');
 await w.call('/phone/commands',{action:'open_app',packageName:CALCULATOR},originHeaders(w,B,B));
 const mark=w.log.length;
 // A's late tool read B's ID from mutable shared state and presents it.
 await w.call('/phone/commands',{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'9+9'}},originHeaders(w,A,B));
 assert.deepEqual(w.log.slice(mark),[],'A-origin command presenting B\'s ID ran with B\'s authority');
 await w.call('/phone/commands',{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'9-2'}},originHeaders(w,B,B));
 assert.deepEqual(w.log.slice(mark),['calculator:9-2'],'B-origin command must run');
});

test('origin: an untagged task-mutating command with no trusted origin is refused even while a task runs',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 const A=w.newRequest('Add 4 and 4');
 await w.call('/phone/commands',{action:'open_app',packageName:CALCULATOR},originHeaders(w,A,A));
 const mark=w.log.length;
 await w.call('/phone/commands',{action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'4+4'}},{});
 await w.call('/phone/commands',{action:'open_app',packageName:CALCULATOR},{});
 assert.deepEqual(w.log.slice(mark).filter(e=>!e.startsWith('ui:inspect')),[],'untagged mutating command ran without trusted origin: '+JSON.stringify(w.log.slice(mark)));
});

test('origin: an untagged non-task read (control_status) is still answered while a task runs',{skip:!has&&'no task-stop.mjs'},async()=>{
 const w=world();
 w.newRequest('Any task');
 const r=await w.call('/phone/commands',{action:'control_status'},{});
 assert.notEqual(r?.body?.result?.status,'task_stopped');assert.notEqual(r?.body?.result?.status,'stale_task');
});
