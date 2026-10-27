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
 assert.deepEqual(stops.map(s=>s.sessionId),['session-1']);assert.equal(stops[0].taskId,null);
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

test('an agent-closed workspace can be reopened; a deadline expiry ends the task instead',async()=>{
 const device=controller();const stops=[];
 const closing=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await closing.execute({action:'open_app',packageName:'maps'});await closing.execute({action:'home'});
 assert.equal((await closing.execute({action:'open_app',packageName:'maps'})).status,'opened');
 assert.equal(stops.length,0);
 const expired=controller({elapsed:600001});
 const phone=new BackgroundPhone({send:expired.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await phone.execute({action:'open_app',packageName:'maps'});expired.ownerStop('deadline');
 assert.equal((await phone.execute({action:'open_app',packageName:'maps'})).status,'stopped_by_owner');
 assert.deepEqual(stops,[{sessionId:'session-1',stopReason:'deadline',taskId:null}]);assert.equal(expired.starts(),1);
});

test('schema 3: each start carries the task ID and a user_stopped refusal ends the task',async()=>{
 const calls=[];const stops=[];
 const send=async c=>{calls.push(c);if(c.operation==='get_state')return {status:'stopped',sessionId:null,stopReason:'user',stoppedSessionId:'session-9',stoppedTaskId:'task-1'};
  if(c.operation==='start')return {status:'user_stopped',stopReason:'user',stoppedSessionId:'session-9'};throw Error('unexpected '+c.operation);};
 const phone=new BackgroundPhone({send,enabled:()=>true,taskId:()=>'task-1',onOwnerStop:e=>stops.push(e)});
 const result=await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'});
 assert.equal(calls.find(c=>c.operation==='start').params.taskId,'task-1');
 assert.equal(result.status,'stopped_by_owner');assert.equal(result.actionExecuted,false);
 assert.deepEqual(stops,[{sessionId:'session-9',stopReason:'user',taskId:'task-1'}]);
 assert.equal((await phone.execute({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{}})).status,'stopped_by_owner');
 assert.equal(calls.filter(c=>c.packageName==='dev.pixel.calculator.agent').length,0);
 phone.beginTask();
});

test('an agent stop reported by a schema 3 controller is not mistaken for the owner',async()=>{
 const device=controller();const stops=[];
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await phone.execute({action:'open_app',packageName:'maps'});device.ownerStop('agent');
 assert.equal((await phone.execute({action:'open_app',packageName:'maps'})).status,'opened');assert.equal(stops.length,0);
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
 assert.equal(launcher.current.state,'stopping');assert.deepEqual(events.sort(),['abort','close','interrupt']);assert.equal(stop.summary(),'Task stopped');
 const blocked=stop.guard({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate'});
 assert.equal(blocked.status,'task_stopped');assert.equal(blocked.actionExecuted,false);
 const again=await stop.request({source:'notification'});
 assert.equal(again.status,'stopping');assert.equal(again.duplicate,true);assert.equal(events.length,3);
 launcher.progress('Opening Calculator');launcher.result('41.40');
 assert.equal(launcher.current.progress,'');assert.equal(launcher.current.result,'','no result is shown after Stop');
 launcher.finish('interrupted','Task stopped');assert.equal(launcher.current.state,'interrupted');
 assert.equal(stop.guard({action:'tap_label'}).status,'task_stopped','late actions stay denied after the terminal state');assert.equal(stop.active(),true);
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
 assert.match(stop.summary(),/did not confirm it closed \(close_failed: cleanup_failed\)/);
});

test('a refused start for an older task neither stops nor locks out the current task',async()=>{
 const launcher=task();let calls=0;const reported=[];
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>calls++,abortReview:()=>{},closeWorkspace:async()=>calls++});
 let requested;let live='task-A';
 const send=async c=>{if(c.operation==='get_state')return {status:'idle'};if(c.operation==='start'){requested=c.params.taskId;live=launcher.current.id;return {status:'user_stopped',actionExecuted:false,rejectedTaskId:'task-A',stopReason:'user',current:{status:'idle'}};}throw Error('unexpected');};
 const phone=new BackgroundPhone({enabled:()=>true,taskId:()=>live,onOwnerStop:e=>reported.push(stop.request({taskId:e.taskId||undefined})),send});
 const result=await phone.execute({action:'open_app',packageName:'maps'});
 assert.equal(requested,'task-A');assert.equal(result.status,'not_executed');assert.equal(result.rejectedTaskId,'task-A');
 assert.equal(reported.length,0);assert.equal(phone.stoppedByOwner,null);assert.equal(launcher.current.state,'running');assert.equal(calls,0);
});

test('TaskStop answers stale_task for an owner-stop report naming another task',async()=>{
 const launcher=task();let calls=0;
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>calls++,abortReview:()=>{},closeWorkspace:async()=>calls++});
 assert.equal((await stop.request({taskId:'task-A',source:'workspace_notification'})).status,'stale_task');
 assert.equal(launcher.current.state,'running');assert.equal(calls,0);
});

test('physical phone-screen actions are refused after an undetected owner Stop',async()=>{
 const device=controller();const stops=[];
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'});device.ownerStop('user');
 const before=device.calls.length;
 const tapped=await phone.execute({action:'tap',display:'phone',nodeId:1,snapshotId:'phone-frame'});
 assert.equal(tapped.status,'stopped_by_owner');
 assert.deepEqual(device.calls.slice(before).filter(c=>c.packageName!=='dev.pixel.backgroundcontrol'),[],'nothing reached the owner\'s screen');
 assert.equal(stops.length,1);
});

test('schema 2: home after an undetected owner Stop does not unlock a replacement workspace',async()=>{
 const device=controller();const stops=[];
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true,onOwnerStop:e=>stops.push(e)});
 await phone.execute({action:'open_app',packageName:'maps'});device.ownerStop();
 assert.equal((await phone.execute({action:'home'})).status,'stopped_by_owner');
 assert.equal((await phone.execute({action:'open_app',packageName:'maps'})).status,'stopped_by_owner');
 await phone.close();
 assert.equal((await phone.execute({action:'open_app',packageName:'maps'})).status,'stopped_by_owner','a backend close keeps the latch');
 assert.equal(device.starts(),1);assert.equal(stops.length,1);
});

test('a backend Stop latches the workspace through close, even without a task ID, until a new task',async()=>{
 const device=controller();
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true,taskId:()=>null});
 await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'});
 await phone.endForStop();
 for(const command of [{action:'open_app',packageName:'dev.pixel.calculator.agent'},{action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{expression:'1+1'}},{action:'tap_label',label:'='},{action:'screenshot',display:'phone'}])
  assert.equal((await phone.execute(command)).status,'stopped_by_owner',command.action);
 assert.equal(device.starts(),1);assert.equal(device.calls.filter(c=>c.packageName==='dev.pixel.calculator.agent').length,0);
 phone.beginTask();assert.equal(phone.state().session,null,'a new task starts without the old session');
 assert.equal((await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'})).status,'opened');
});

test('Stop announces speech cancellation for the task before awaiting the worker',async()=>{
 const launcher=task();const order=[];
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),announce:e=>order.push('announce:'+e.taskId),interrupt:()=>new Promise(r=>setTimeout(()=>{order.push('interrupt');r();},5)),abortReview:()=>{},closeWorkspace:async()=>{}});
 const pending=stop.request({});assert.deepEqual(order,['announce:'+launcher.current.id]);
 await pending;assert.deepEqual(order,['announce:'+launcher.current.id,'interrupt']);
});

test('work queued behind an in-flight action is refused once Stop latches, even before the close runs',async()=>{
 let release;const held=new Promise(r=>release=r);const ran=[];
 const send=async c=>{if(c.packageName==='dev.pixel.calculator.agent'){if(c.params.expression==='1+1')await held;ran.push(c.params.expression);return {status:'completed'};}
  if(c.operation==='get_state')return {status:'ready',sessionId:'s1',targetPackage:'dev.pixel.calculator.agent'};if(c.operation==='start')return {status:'ready',sessionId:'s1',targetPackage:c.params.packageName};if(c.operation==='stop')return {status:'stopped',stopReason:'agent'};return {status:'ok',packageName:'dev.pixel.calculator.agent',snapshotId:'x',nodes:[]};};
 const phone=new BackgroundPhone({send,enabled:()=>true});
 await phone.execute({action:'open_app',packageName:'dev.pixel.calculator.agent'});
 const x=phone.execute({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{expression:'1+1'}});
 const y=phone.execute({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{expression:'2+2'}});
 await new Promise(r=>setImmediate(r));
 const closing=phone.endForStop();release();
 assert.equal((await y).status,'stopped_by_owner');await x;await closing;
 assert.deepEqual(ran,['1+1'],'only the action already running before Stop completed');
});

test('PhoneCommands withdraws undelivered commands on Stop',async()=>{
 const {PhoneCommands}=await import('./phone.mjs');
 const commands=new PhoneCommands();const c=commands.create({action:'screenshot'});
 assert.equal(commands.pending().length,1);
 assert.equal(commands.cancelPending({status:'task_stopped',actionExecuted:false}),1);
 assert.equal(commands.pending().length,0);assert.equal(commands.get(c.id).status,'cancelled');assert.equal(commands.get(c.id).result.status,'task_stopped');
 assert.equal(commands.complete(c.id,{status:'ok'}).status,'cancelled','a late completion does not resurrect it');
});

test('task generations: only turns and task IDs of the current request are authorized',async()=>{
 const launcher=task();const published=[];
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{},onBegin:id=>published.push(id)});
 assert.equal(stop.authorize({taskId:launcher.current.id}),null,'before begin(), the displayed task is the generation');
 assert.equal(stop.authorize({taskId:'anything'}).status,'stale_task');assert.equal(stop.authorize({required:true}).status,'stale_task','a task path with no identity fails closed');
 stop.begin('task-A');stop.addTurn('turn-A1');
 assert.equal(stop.authorize({taskId:'task-A'}),null);assert.equal(stop.authorize({turnId:'turn-A1'}),null);assert.equal(stop.authorize({}),null,'identity optional unless required');
 stop.begin('task-B');stop.addTurn('turn-B1');
 assert.equal(stop.authorize({taskId:'task-A'}).status,'stale_task');assert.equal(stop.authorize({turnId:'turn-A1'}).status,'stale_task');
 assert.equal(stop.authorize({turnId:'turn-unknown'}).status,'stale_task');assert.equal(stop.authorize({turnId:'turn-B1'}),null);
 assert.deepEqual(published,['task-A','task-B']);
});

test('controller start, command and stop carry the originating task ID; non-task work carries none',async()=>{
 const device=controller();
 const phone=new BackgroundPhone({send:device.send,enabled:()=>true});
 await phone.execute({action:'open_app',packageName:'maps'},{taskId:'task-A'});
 await phone.execute({action:'screenshot'},{taskId:'task-A'});
 await phone.close('task-A');
 const ops=device.calls.filter(c=>['start','command','stop'].includes(c.operation));
 assert.ok(ops.length>=3);assert.ok(ops.every(c=>c.params.taskId==='task-A'),JSON.stringify(ops.map(c=>[c.operation,c.params.taskId])));
 device.calls.length=0;
 await phone.execute({action:'list_apps'});await phone.close();
 assert.ok(device.calls.every(c=>c.params?.taskId===undefined),'non-task calls are not labelled with any task');
});

test('a schema 3 stale_task refusal from the controller is reported as not executed',async()=>{
 const send=async c=>{if(c.operation==='get_state')return {status:'ready',sessionId:'s1',targetPackage:'maps'};
  if(c.operation==='command')return {status:'stale_task',actionExecuted:false,rejectedTaskId:'task-A',current:{status:'ready',taskId:'task-B'}};return {status:'ok'};};
 const phone=new BackgroundPhone({send,enabled:()=>true});
 const result=await phone.execute({action:'screenshot'},{taskId:'task-A'});
 assert.equal(result.status,'not_executed');assert.equal(result.actionExecuted,false);assert.equal(result.rejectedTaskId,'task-A');
});

test('worker output and tool calls are accepted only from turns of the current, unstopped generation',async()=>{
 const launcher=task();
 const stop=new TaskStop({task:()=>launcher.current,stopping:()=>launcher.stopping(),interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{}});
 stop.begin('task-A');stop.addTurn('turn-A');
 assert.equal(stop.acceptsOutput('turn-A'),true);assert.equal(stop.ownsTurn('turn-A'),true);
 await stop.request({});
 assert.equal(stop.acceptsOutput('turn-A'),false,'no output after Stop, even from the current turn');
 assert.equal(stop.guard({action:'tool:web_lookup'}).status,'task_stopped','tool calls of the stopped turn are refused');
 stop.begin('task-B');stop.addTurn('turn-B');
 assert.equal(stop.acceptsOutput('turn-A'),false);assert.equal(stop.ownsTurn('turn-A'),false);assert.equal(stop.authorize({turnId:'turn-A'}).status,'stale_task');
 assert.equal(stop.acceptsOutput('turn-B'),true);assert.equal(stop.guard({action:'tool:web_lookup'}),null);
});
