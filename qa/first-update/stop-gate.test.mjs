// Independent QA acceptance check for the First Update one-pager criterion:
// "stop an in-progress task without further agent actions after Stop is acknowledged".
// Test-only harness: it drives the real BackgroundPhone against a fake controller that
// follows os/background-control/agent-pip Controller.java semantics (owner Stop closes
// the session and reports status "stopped"; start opens a fresh session). The owner
// Stop also reports stopReason "user", the field proposed in ROS-FIRST-UPDATE-OS-001-M003;
// code that ignores the field behaves exactly as at 01c5d8f.
// Offline only. This is not VM or device evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {BackgroundPhone} from '../../workspace/rose-agent/server/background-phone.mjs';

const CONTROLLER='dev.pixel.backgroundcontrol', CALCULATOR='dev.pixel.calculator.agent';

function fakePhone(){
 const log=[];let seq=0;
 const ctl={status:'stopped',sessionId:null,targetPackage:null,stopReason:''};
 const state=()=>({status:ctl.status,sessionId:ctl.sessionId,targetPackage:ctl.targetPackage,stopReason:ctl.stopReason});
 const send=async c=>{
  if(c.packageName===CALCULATOR){log.push({kind:'calculator',operation:c.operation});return {status:'completed',result:{status:'verified',entry:'0'}};}
  if(c.action!=='app_command'||c.packageName!==CONTROLLER){log.push({kind:'physical',action:c.action});return {status:'ok'};}
  switch(c.operation){
   case 'get_state':return state();
   case 'start':ctl.status='ready';ctl.sessionId='session-'+(++seq);ctl.targetPackage=c.params.packageName;ctl.stopReason='';log.push({kind:'start',sessionId:ctl.sessionId});return state();
   case 'stop':ctl.status='stopped';ctl.sessionId=null;ctl.stopReason='agent';return state();
   case 'command':
    if(ctl.status!=='ready'||c.params.sessionId!==ctl.sessionId)return {status:'error',error:'Session not ready: '+ctl.status};
    log.push({kind:'ui',action:c.params.action,sessionId:ctl.sessionId});
    return {status:'ok',packageName:ctl.targetPackage,sessionId:ctl.sessionId,snapshotId:'snap-'+seq,nodes:[]};
  }
  throw Error('unexpected controller operation '+c.operation);
 };
 // StopReceiver -> Controller.stop: closes the session; nothing is sent to the backend.
 const ownerStop=()=>{ctl.status='stopped';ctl.sessionId=null;ctl.stopReason='user';return log.length;};
 return {log,ownerStop,phone:new BackgroundPhone({send,enabled:()=>true,sleep:async()=>{}})};
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
