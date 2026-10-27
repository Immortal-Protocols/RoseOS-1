// Independent QA acceptance check for the First Update one-pager criterion:
// "stop an in-progress task without further agent actions after Stop is acknowledged".
// Test-only harness: it drives the real BackgroundPhone against a fake controller that
// follows os/background-control/agent-pip Controller.java semantics (owner Stop closes
// the session and reports status "stopped" while keeping the last sessionId, as
// Controller.state() reports `id`; start opens a fresh session). The owner
// Stop also reports stopReason "user", the field proposed in ROS-FIRST-UPDATE-OS-001-M003;
// code that ignores the field behaves exactly as at 01c5d8f.
// Offline only. This is not VM or device evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {BackgroundPhone} from '../../workspace/rose-agent/server/background-phone.mjs';

const CONTROLLER='dev.pixel.backgroundcontrol', CALCULATOR='dev.pixel.calculator.agent';

function fakePhone(){
 const log=[];let seq=0;
 const ctl={status:'stopped',sessionId:null,targetPackage:null,stopReason:'',started:0,stoppedSessionId:null};
 const state=()=>({status:ctl.status,sessionId:ctl.sessionId,targetPackage:ctl.targetPackage,stopReason:ctl.stopReason,stoppedSessionId:ctl.stoppedSessionId,elapsedMs:ctl.started?1000:0});
 // StopLedger.stopped: a later agent/deadline stop of the same session keeps the owner's "user" reason.
 const record=why=>{if(ctl.stopReason==='user'&&why!=='user'&&ctl.stoppedSessionId===ctl.sessionId)return;ctl.stopReason=why;ctl.stoppedSessionId=ctl.sessionId;};
 const send=async c=>{
  if(c.packageName===CALCULATOR){log.push({kind:'calculator',operation:c.operation});return {status:'completed',result:{status:'verified',entry:'0'}};}
  if(c.action!=='app_command'||c.packageName!==CONTROLLER){log.push({kind:'physical',action:c.action});return {status:'ok'};}
  switch(c.operation){
   case 'get_state':return state();
   case 'start':ctl.status='ready';ctl.sessionId='session-'+(++seq);ctl.targetPackage=c.params.packageName;ctl.stopReason='';ctl.stoppedSessionId=null;ctl.started=1;log.push({kind:'start',sessionId:ctl.sessionId});return state();
   case 'stop':record('agent');ctl.status='stopped';return state();
   case 'command':
    if(c.params.action==='home'&&ctl.status==='ready'&&c.params.sessionId===ctl.sessionId){record('agent');ctl.status='stopped';return state();}
    if(ctl.status!=='ready'||c.params.sessionId!==ctl.sessionId)return {status:'error',error:'Session not ready: '+ctl.status};
    log.push({kind:'ui',action:c.params.action,sessionId:ctl.sessionId});
    return {status:'ok',packageName:ctl.targetPackage,sessionId:ctl.sessionId,snapshotId:'snap-'+seq,nodes:[]};
  }
  throw Error('unexpected controller operation '+c.operation);
 };
 // StopReceiver -> Controller.stop: closes the session; nothing is sent to the backend.
 // Controller.stop keeps `id`, so get_state still reports the stopped sessionId.
 const ownerStop=()=>{record('user');ctl.status='stopped';return log.length;};
 const deadline=()=>{record('deadline');ctl.status='stopped';};
 return {log,ownerStop,deadline,phone:new BackgroundPhone({send,enabled:()=>true,sleep:async()=>{}})};
}
const executedAfter=(log,mark)=>log.slice(mark).filter(e=>e.kind==='calculator'||e.kind==='start'||e.kind==='ui'||e.kind==='physical');

test('control: before Stop, the agent can open Calculator in the background and evaluate',async()=>{
 const {log,phone}=fakePhone();
 const opened=await phone.execute({action:'open_app',packageName:CALCULATOR});
 assert.equal(opened.backgroundVerified,true);
 await phone.execute({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'84.50+15%'}});
 assert.deepEqual(log.map(e=>e.kind),['start','ui','calculator']);
});

test('after owner Stop, a Calculator app_command is not executed',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 const mark=ownerStop();
 const r=await phone.execute({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'84.50+15%'}});
 assert.deepEqual(executedAfter(log,mark),[],'Calculator executed after owner Stop: '+JSON.stringify(r));
});

test('after owner Stop, open_app does not start a replacement workspace',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 const mark=ownerStop();
 const r=await phone.execute({action:'open_app',packageName:CALCULATOR});
 assert.deepEqual(executedAfter(log,mark),[],'New session after owner Stop: '+JSON.stringify(r));
});

test('after owner Stop, background UI actions are not executed',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 const mark=ownerStop();
 await phone.execute({action:'tap_label',label:'='});
 await phone.execute({action:'back'});
 assert.deepEqual(executedAfter(log,mark),[]);
});

test('after owner Stop, a physical phone-display action for the stopped task is not executed',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 const mark=ownerStop();
 const r=await phone.execute({action:'tap',display:'phone',x:10,y:10});
 assert.deepEqual(executedAfter(log,mark),[],'Physical action after owner Stop: '+JSON.stringify(r));
});

test('a controller without stopReason: owner Stop is still honoured before the deadline',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 const mark=ownerStop();
 // Simulate the 01c5d8f controller, which has no stopReason field.
 const send=phone.send;phone.send=async c=>{const r=await send(c);if(r&&'stopReason' in r){const {stopReason,...rest}=r;return rest;}return r;};
 await phone.execute({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'1+1'}});
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 assert.deepEqual(executedAfter(log,mark),[]);
});

// Starting a fresh task: implementations expose this differently. App PR #3 uses
// BackgroundPhone.beginTask(); at 01c5d8f there is no such hook and nothing is locked.
const newTask=phone=>phone.beginTask?.();

test('after owner Stop, the agent closing the workspace (home) does not unlock a replacement session',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 const mark=ownerStop();
 await phone.execute({action:'home'});
 const r=await phone.execute({action:'open_app',packageName:CALCULATOR});
 assert.deepEqual(executedAfter(log,mark),[],'Replacement after owner Stop then home: '+JSON.stringify(r));
});

test('agent-closed workspace (not an owner Stop) may be reopened in the same task',async()=>{
 const {log,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 await phone.execute({action:'home'});
 const mark=log.length;
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['start','ui']);
});

test('deadline-expired workspace (not an owner Stop) may be reopened in the same task',async()=>{
 const {log,deadline,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 deadline();const mark=log.length;
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['start','ui']);
});

test('a fresh explicit task B after owner-stopped task A can open Calculator and evaluate',async()=>{
 const {log,ownerStop,phone}=fakePhone();
 await phone.execute({action:'open_app',packageName:CALCULATOR});
 ownerStop();
 await phone.execute({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'1+1'}});
 newTask(phone);const mark=log.length;
 const opened=await phone.execute({action:'open_app',packageName:CALCULATOR});
 await phone.execute({action:'app_command',packageName:CALCULATOR,operation:'evaluate',params:{expression:'2+2'}});
 assert.equal(opened.backgroundVerified,true,JSON.stringify(opened));
 assert.deepEqual(executedAfter(log,mark).map(e=>e.kind),['start','ui','calculator']);
});
