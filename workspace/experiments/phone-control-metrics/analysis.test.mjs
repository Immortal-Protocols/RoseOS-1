import test from 'node:test';import assert from 'node:assert/strict';import {sequenceCandidates} from './analysis.mjs';import {PhoneMetrics} from './metrics.mjs';
const row=(id,action,previousId=null,extra={})=>({kind:'completed',id,action,previousId,taskStartedAt:1,resultStatus:'ok',betweenCommandsMs:100,totalMs:1000,...extra});
test('offline sequences retain command IDs and measured times, not estimated savings',()=>{
 const rows=[row('1','set_text'),row('2','inspect_ui','1'),row('3','ime_enter','2')];
 const [r]=sequenceCandidates(rows);assert.deepEqual(r.actions,['set_text','inspect_ui','ime_enter']);assert.equal(r.observedCommandMs,3000);assert.equal(r.candidateRoundTrips,2);assert.equal(r.requiresScreenReview,true);
});
test('failed commands, task changes, missing timing and long pauses break candidate runs',()=>{
 for(const extra of [{resultStatus:'error'},{taskStartedAt:2},{betweenCommandsMs:null},{betweenCommandsMs:10001}])assert.equal(sequenceCandidates([row('1','set_text'),row('2','ime_enter','1',extra)]).length,0);
});
test('telemetry records client execution and snapshot joins without field contents',()=>{
 const events=[];let time=0;const m=new PhoneMetrics({now:()=>++time,write:e=>events.push(e)});
 const a={id:'a',action:'inspect_ui'};m.created(a,{taskStartedAt:1});m.offered(a,'push');m.completed({...a,status:'completed',result:{status:'ok',snapshotId:'snapshot-a',validForMs:30000,nodes:[]}});
 const b={id:'b',action:'set_text',snapshotId:'snapshot-a',text:'private field'};m.created(b,{taskStartedAt:1});m.offered(b,'push');m.executed('b',{channel:'push',executionMs:50,receivedToStartMs:3});m.completed({...b,status:'completed',result:{status:'ok'}});
 const result=events.at(-1);assert.equal(result.usedPreviousObservation,true);assert.equal(result.phoneExecutionMs,50);assert.equal(result.deliveryChannel,'push');assert.ok(!JSON.stringify(events).includes('private field'));
});
