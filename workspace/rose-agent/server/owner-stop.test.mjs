import test from 'node:test';
import assert from 'node:assert/strict';
import {BackgroundPhone} from './background-phone.mjs';
import {TaskStop} from './task-stop.mjs';
import {LauncherTask} from './launcher-task.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Mirrors os/background-control/agent-pip Controller: stop() keeps the last
// session ID and elapsed time and reports status "stopped"; start() makes a
// fresh session. The notification Stop action calls stop() on the phone only.
function controller({elapsed=5000}={}){
 const calls=[];let status='idle',target=null,id=null,n=0,stopReason;
 const send=async c=>{calls.push(c);
  if(c.packageName==='dev.pixel.calculator.agent')return {status:'completed',result:{status:'verified',result:true,error:false,entry:'41.4'}};
  if(c.packageName!=='dev.pixel.backgroundcontrol')return {status:'ok',physical:true};
  const state=()=>({status,sessionId:id,targetPackage:target,elapsedMs:elapsed,...(stopReason?{stopReason}:{})});
  if(c.operation==='get_state')return state();
  if(c.operation==='start'){status='ready';target=c.params.packageName;id='session-'+(++n);stopReason=undefined;return state();}
  if(c.operation==='stop'){status='stopped';stopReason='agent';return state();}
  if(c.operation==='command'){if(status!=='ready')return {status:'error',error:'Background session is not ready: '+status};return {status:'ok',packageName:target,snapshotId:'snap-'+n,nodes:[{nodeId:1,text:'7'}]};}
  throw Error('unexpected operation '+c.operation);};
 return {calls,send,ownerStop(reason){status='stopped';stopReason=reason;},starts:()=>calls.filter(c=>c.operation==='start').length};
}

test('owner Stop on the workspace notification is not silently replaced by a new session',async()=>{
 const device=controller();const stops=[];
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 assert.equal((await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'})).status,'opened');
 device.ownerStop();
 const reopened=await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'});
 assert.equal(reopened.status,'stopped_by_owner');assert.equal(reopened.actionExecuted,false);
 assert.equal(device.starts(),1,'no replacement workspace was started');
 assert.deepEqual(stops.map(s=>s.sessionId),['session-1']);
});

test('owner Stop is detected by a UI action in the stopped workspace',async()=>{
 const device=controller();const stops=[];
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'});
 device.ownerStop('user');
 const tapped=await phone.execute({action:'screenshot'});
 assert.equal(tapped.status,'stopped_by_owner');assert.equal(tapped.actionExecuted,false);
 assert.equal(stops.length,1);
});

test('workspace expiry and agent-closed sessions keep the existing reopen behavior',async()=>{
 const expired=controller({elapsed:600001});const stops=[];
 const phone=new BackgroundPhone({send:expired.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await phone.execute({action:'open_app',packageName:'maps'});expired.ownerStop();
 assert.equal((await phone.execute({action:'open_app',packageName:'maps'})).status,'opened');
 const device=controller();const closing=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await closing.execute({action:'open_app',packageName:'maps'});await closing.execute({action:'home'});
 assert.equal((await closing.execute({action:'open_app',packageName:'maps'})).status,'opened');
 assert.equal(stops.length,0);
});

function task(){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'owner-stop-'));
 const launcher=new LauncherTask(path.join(dir,'task.json'));launcher.start('Add a 20% tip to 34.50 in Calculator');
 return launcher;
}

test('Stop is acknowledged once, interrupts the worker, closes the workspace and blocks later phone actions',async()=>{
 const launcher=task();const events=[];
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>events.push('interrupt'),abortReview:()=>events.push('abort'),closeWorkspace:async()=>events.push('close'),now:()=>1000});
 assert.equal(stop.guard({action:'tap_label'}),null);
 const ack=await stop.request({taskId:launcher.current.id,source:'tray'});
 assert.equal(ack.status,'stopping');assert.equal(ack.taskId,launcher.current.id);assert.equal(ack.requestedAt,1000);
 assert.equal(launcher.current.state,'stopping');assert.deepEqual(events.sort(),['abort','close','interrupt']);
 const blocked=stop.guard({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate'});
 assert.equal(blocked.status,'task_stopped');assert.equal(blocked.actionExecuted,false);
 const again=await stop.request({source:'notification'});
 assert.equal(again.status,'stopping');assert.equal(again.duplicate,true);assert.equal(events.length,3);
 launcher.progress('Opening Calculator');launcher.result('41.40');
 assert.equal(launcher.current.progress,'');assert.equal(launcher.current.result,'','no result is shown after Stop');
 launcher.finish('interrupted','Task stopped');assert.equal(launcher.current.state,'interrupted');
 assert.equal(stop.guard({action:'tap_label'}),null,'work after the stopped task ended is not blocked');assert.equal(stop.active(),true);
 stop.begin();assert.equal(stop.guard({action:'tap_label'}),null);
});

test('Stop for another or finished task does not touch the current work',async()=>{
 const launcher=task();let calls=0;
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>calls++,abortReview:()=>calls++,closeWorkspace:async()=>calls++});
 assert.equal((await stop.request({taskId:'older-task'})).status,'stale_task');
 launcher.finish('completed','41.40');
 assert.equal((await stop.request({})).status,'not_active');
 assert.equal(calls,0);assert.equal(stop.guard({action:'tap'}),null);
});

test('a failed interrupt or workspace close keeps the gate closed and reports it',async()=>{
 const launcher=task();
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>{throw Error('turn/interrupt timed out');},abortReview:()=>{},closeWorkspace:async()=>{throw Error('cleanup_failed');}});
 const ack=await stop.request({});
 assert.equal(ack.status,'stopping');assert.equal(ack.worker,'interrupt_failed: turn/interrupt timed out');assert.equal(ack.workspace,'close_failed: cleanup_failed');
 assert.equal(stop.guard({action:'tap'}).status,'task_stopped');
});
