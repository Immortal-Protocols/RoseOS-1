// EventDelivery (server.mjs /voice/event): ported unchanged from the retired
// tests/event-delivery.mjs, whose page-relay half tested a removed client retry.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventDelivery} from './event-delivery.mjs';

test('concurrent and replayed handoffs run once; restart uncertainty and stale sessions fail closed; events stay ordered',async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-event-test-'));
 try{
  const file=path.join(temp,'receipts.jsonl'),inbox=new EventDelivery(file);
  const event={type:'session.delegation.created',event_id:'e1',clientSessionId:'current',delegation:{id:'d1',target:'client'}};
  let runs=0,finish;
  const gate=new Promise(resolve=>finish=resolve);
  const handle=async()=>{runs++;await gate;return {stage:'task_started'};};
  const first=inbox.receive(event,()=>'current',handle);
  const retry=inbox.receive(event,()=>'current',handle);
  await Promise.resolve();assert.equal(runs,1);finish();
  assert.equal((await first).stage,'task_started');await retry;assert.equal(runs,1);
  assert.equal((await inbox.receive(event,()=>'current',handle)).duplicate,true);
  const restarted=new EventDelivery(file);
  assert.equal((await restarted.receive(event,()=>'current',handle)).duplicate,true);assert.equal(runs,1);
  const stale=await inbox.receive({...event,event_id:'e2',delegation:{id:'d2'},clientSessionId:'old'},()=>'current',handle);
  assert.equal(stale.ok,false);assert.equal(runs,1);
  fs.appendFileSync(file,JSON.stringify({key:'delegation:uncertain',stage:'received'})+'\n');
  const interrupted=new EventDelivery(file);
  const uncertain=await interrupted.receive({...event,delegation:{id:'uncertain'}},()=>'current',handle);
  assert.equal(uncertain.ok,false);assert.match(uncertain.error,/restarted/);assert.equal(runs,1);
  let transcript='';
  await Promise.all([
   inbox.receive({type:'session.input_transcript.delta',event_id:'t1'},()=>'current',async()=>{await Promise.resolve();transcript='Open Amazon';}),
   inbox.receive({...event,delegation:{id:'d3'}},()=>'current',async()=>assert.equal(transcript,'Open Amazon')),
  ]);
 }finally{fs.rmSync(temp,{recursive:true,force:true});}
});
