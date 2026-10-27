// Independent QA acceptance check for the First Update one-pager criterion:
// "stop an in-progress task without further agent actions after Stop is acknowledged"
// (Chief ruling ROS-FIRST-UPDATE-CHIEF-STOP-001: includes tool/app actions and automatic restart).
// Test-only harness: it drives the real BackgroundPhone against a fake controller that follows
// os/background-control/agent-pip Controller.java + StopLedger semantics:
//  - Controller.stop keeps the last sessionId (state() reports `id`) and sets status "stopped";
//  - schema 3 (OS PR #1): stopReason user|agent|deadline, stoppedSessionId, stoppedTaskId; a later
//    agent/deadline stop of the same session keeps "user"; start({taskId}) of an owner-stopped task
//    returns status "user_stopped" without opening anything;
//  - schema 2 (01c5d8f, and the controller on a phone before the OS update): none of those fields.
// Implementation hooks are optional adapters: BackgroundPhone({taskId}) and beginTask() exist in
// App PR #3 and are ignored by older code. Offline only. This is not VM or device evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {BackgroundPhone} from '../../workspace/rose-agent/server/background-phone.mjs';

const CONTROLLER='dev.pixel.backgroundcontrol', CALCULATOR='dev.pixel.calculator.agent';

function fakePhone({schema=3}={}){
 const log=[];let seq=0;let task='task-A';
 const ctl={status:'stopped',sessionId:null,taskId:null,targetPackage:null,stopReason:'',stoppedSessionId:null,stoppedTaskId:null,started:0};
 const userStopped=new Set();
 const state=()=>{
  const s={status:ctl.status,sessionId:ctl.sessionId,targetPackage:ctl.targetPackage,elapsedMs:ctl.started?1000:0};
  return schema>=3?{...s,taskId:ctl.taskId,stopReason:ctl.stopReason,stoppedSessionId:ctl.stoppedSessionId,stoppedTaskId:ctl.stoppedTaskId}:s;
 };
 const record=why=>{
  if(ctl.stopReason==='user'&&why!=='user'&&ctl.stoppedSessionId===ctl.sessionId)return;
  ctl.stopReason=why;ctl.stoppedSessionId=ctl.sessionId;ctl.stoppedTaskId=ctl.taskId;
  if(why==='user'&&ctl.taskId)userStopped.add(ctl.taskId);
 };
 const send=async c=>{
  if(c.packageName===CALCULATOR){log.push({kind:'calculator',operation:c.operation});return {status:'completed',result:{status:'verified',entry:'0'}};}
  if(c.action!=='app_command'||c.packageName!==CONTROLLER){log.push({kind:'physical',action:c.action});return {status:'ok'};}
  switch(c.operation){
   case 'get_state':return state();
   case 'start':{
    const requested=schema>=3?c.params.taskId??null:null;
    // Rejection names the refused task explicitly, separately from any current workspace.
    if(requested&&userStopped.has(requested))return {...state(),status:'user_stopped',actionExecuted:false,rejectedTaskId:requested};
    if(ctl.status==='ready')record('agent');
    ctl.status='ready';ctl.sessionId='session-'+(++seq);ctl.taskId=requested;ctl.targetPackage=c.params.packageName;
    ctl.stopReason='';ctl.stoppedSessionId=null;ctl.stoppedTaskId=null;ctl.started=1;
    log.push({kind:'start',sessionId:ctl.sessionId,taskId:requested});return state();
   }
   case 'stop':record('agent');ctl.status='stopped';return state();
   case 'command':
    if(c.params.action==='home'&&ctl.status==='ready'&&c.params.sessionId===ctl.sessionId){record('agent');ctl.status='stopped';return state();}
    if(ctl.status!=='ready'||c.params.sessionId!==ctl.sessionId)return {status:'error',error:'Session not ready: '+ctl.status};
    log.push({kind:'ui',action:c.params.action,sessionId:ctl.sessionId});
    return {status:'ok',packageName:ctl.targetPackage,sessionId:ctl.sessionId,snapshotId:'snap-'+seq,nodes:[]};
  }
  throw Error('unexpected controller operation '+c.operation);
 };
 // StopReceiver -> Controller.stop(USER): closes the session; the backend is not called.
 const ownerStop=()=>{record('user');ctl.status='stopped';return log.length;};
 const deadline=()=>{record('deadline');ctl.status='stopped';return log.length;};
 const phone=new BackgroundPhone({send,enabled:()=>true,sleep:async()=>{},taskId:()=>task});
 // Explicit new user request. As in server.mjs finishAgentTurn, the previous task's turn ends
 // with backgroundPhone.close() before a new task begins; then a fresh task ID and reset hook.
 const newTask=async id=>{await phone.close();task=id;phone.beginTask?.();return log.length;};
 return {log,ownerStop,deadline,newTask,phone,ctl};
}
const executedAfter=(log,mark)=>log.slice(mark).filter(e=>e.kind==='calculator'||e.kind==='start'||e.kind==='ui'||e.kind==='physical');
const evaluate=e=>({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:e}});
const openCalc={action:'open_app',packageName:CALCULATOR};

for(const schema of [3,2]){
 const S=`[schema ${schema}] `;

 test(S+'control: before Stop, the agent can open Calculator in the background and evaluate',async()=>{
  const {log,phone}=fakePhone({schema});
  const opened=await phone.execute(openCalc);
  assert.equal(opened.backgroundVerified,true);
  await phone.execute(evaluate('84.50+15%'));
  assert.deepEqual(log.map(e=>e.kind),['start','ui','calculator']);
 });

 test(S+'after owner Stop, a Calculator app_command is not executed',async()=>{
  const {log,ownerStop,phone}=fakePhone({schema});
  await phone.execute(openCalc);const mark=ownerStop();
  const r=await phone.execute(evaluate('84.50+15%'));
  assert.deepEqual(executedAfter(log,mark),[],'Calculator executed after owner Stop: '+JSON.stringify(r));
 });

 test(S+'after owner Stop, open_app does not start a replacement workspace',async()=>{
  const {log,ownerStop,phone}=fakePhone({schema});
  await phone.execute(openCalc);const mark=ownerStop();
  const r=await phone.execute(openCalc);
  assert.deepEqual(executedAfter(log,mark),[],'New session after owner Stop: '+JSON.stringify(r));
 });

 test(S+'after owner Stop, background UI actions are not executed',async()=>{
  const {log,ownerStop,phone}=fakePhone({schema});
  await phone.execute(openCalc);const mark=ownerStop();
  await phone.execute({action:'tap_label',label:'='});
  await phone.execute({action:'back'});
  assert.deepEqual(executedAfter(log,mark),[]);
 });

 test(S+'after owner Stop, a physical phone-display action for the stopped task is not executed',async()=>{
  const {log,ownerStop,phone}=fakePhone({schema});
  await phone.execute(openCalc);const mark=ownerStop();
  const r=await phone.execute({action:'tap',display:'phone',x:10,y:10});
  assert.deepEqual(executedAfter(log,mark),[],'Physical action after owner Stop: '+JSON.stringify(r));
 });

 test(S+'after owner Stop, the agent closing the workspace (home) does not unlock a replacement session',async()=>{
  const {log,ownerStop,phone}=fakePhone({schema});
  await phone.execute(openCalc);const mark=ownerStop();
  await phone.execute({action:'home'});
  const r=await phone.execute(openCalc);
  assert.deepEqual(executedAfter(log,mark),[],'Replacement after owner Stop then home: '+JSON.stringify(r));
 });

 test(S+'agent-closed workspace (not an owner Stop) may be reopened in the same task',async()=>{
  const {log,phone}=fakePhone({schema});
  await phone.execute(openCalc);await phone.execute({action:'home'});const mark=log.length;
  await phone.execute(openCalc);
  assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['start','ui']);
 });

 test(S+'a fresh explicit task B after owner-stopped task A can open Calculator and evaluate',async()=>{
  const {log,ownerStop,newTask,phone}=fakePhone({schema});
  await phone.execute(openCalc);ownerStop();
  await phone.execute(evaluate('1+1'));
  const mark=await newTask('task-B');
  const opened=await phone.execute(openCalc);
  await phone.execute(evaluate('2+2'));
  assert.equal(opened.backgroundVerified,true,JSON.stringify(opened));
  assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['start','ui','calculator']);
 });

 test(S+'after the workspace deadline, a fresh explicit task can still run (no lockout)',async()=>{
  // Whether the same task may reopen after the deadline is a product choice and is not gated here.
  const {log,deadline,newTask,phone}=fakePhone({schema});
  await phone.execute(openCalc);deadline();
  const mark=await newTask('task-B');
  await phone.execute(openCalc);await phone.execute(evaluate('3+3'));
  assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['start','ui','calculator']);
 });
}

test('[schema 3] a stale start for owner-stopped task A is refused and task B keeps its workspace',async()=>{
 // Controller-level contract with a correct fake; the real Controller.java response is reviewed separately.
 const {log,ownerStop,newTask,phone,ctl}=fakePhone({schema:3});
 await phone.execute(openCalc);ownerStop();
 await newTask('task-B');await phone.execute(openCalc);
 const bSession=ctl.sessionId;const mark=log.length;
 const stale=await phone.send({action:'app_command',packageName:CONTROLLER,operation:'start',params:{packageName:CALCULATOR,taskId:'task-A'}});
 assert.equal(stale.status,'user_stopped');assert.equal(stale.rejectedTaskId,'task-A');
 assert.equal(ctl.sessionId,bSession);assert.equal(ctl.status,'ready');
 await phone.execute(evaluate('5+5'));
 assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['calculator']);
});
