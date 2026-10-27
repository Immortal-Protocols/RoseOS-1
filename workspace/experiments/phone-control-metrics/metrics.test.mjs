import test from 'node:test';import assert from 'node:assert/strict';
import {PhoneMetrics,observationSummary} from './metrics.mjs';
const screen=(snapshotId='a',text='Secret label')=>({status:'ok',snapshotId,validForMs:30000,packageName:'test.app',truncated:false,nodes:[{id:1,text,actions:['tap'],bounds:[0,0,30,30]}]});
function fixture(write){let now=1000;const rows=[];const m=new PhoneMetrics({write:write||((e)=>rows.push(e)),now:()=>now});return{m,rows,at:t=>now=t,done:(c,result)=>m.completed({...c,status:'completed',result})};}
test('records queue, completion, result pickup once without changing command payload',()=>{
 const f=fixture(),c={id:'a',action:'tap_label',label:'Secret label'};const original=JSON.stringify(c);
 f.m.created(c);f.at(1400);f.m.offered(c);f.m.offered(c);f.at(1600);f.done(c,{status:'action_accepted',observation:screen()});f.done(c,{status:'error'});f.at(1700);f.m.collected(c);f.m.collected(c);
 assert.equal(JSON.stringify(c),original);assert.deepEqual(f.rows.map(x=>x.kind),['created','offered','completed','collected']);
 const r=f.rows[2];assert.equal(r.queueWaitMs,400);assert.equal(r.dispatchToResultMs,200);assert.equal(r.totalMs,600);assert.equal(f.rows[3].resultPickupMs,100);
 assert.ok(!JSON.stringify(f.rows).includes('Secret label'));
});
test('identical fresh observation after action is a candidate, not every inspection',()=>{
 const f=fixture();let a={id:'a',action:'tap_label'};f.m.created(a,{taskStartedAt:1});f.at(1200);f.done(a,{status:'action_accepted',observation:screen('a')});
 f.at(1300);let b={id:'b',action:'inspect_ui'};f.m.created(b,{taskStartedAt:1});f.at(1500);f.done(b,screen('b'));assert.equal(f.rows.at(-1).possibleRedundantInspection,true);assert.equal(f.rows.at(-1).betweenCommandsMs,100);
 f.at(1600);let c={id:'c',action:'inspect_ui'};f.m.created(c,{taskStartedAt:1});f.done(c,screen('c'));assert.equal(f.rows.at(-1).possibleRedundantInspection,false);
});
test('changed state, stale snapshots, different task and incomplete trees are not redundant candidates',()=>{
 for(const kind of ['changed','stale','task','truncated','missing']){
  const f=fixture();const a={id:'a',action:'tap_label'};f.m.created(a,{taskStartedAt:1});f.at(1200);const first=screen();if(kind==='truncated')first.truncated=true;if(kind==='missing')delete first.validForMs;f.done(a,{status:'action_accepted',observation:first});
  f.at(kind==='stale'?40000:1400);const b={id:'b',action:'inspect_ui'};f.m.created(b,{taskStartedAt:kind==='task'?2:1});f.at(kind==='stale'?41000:1500);f.done(b,screen('b',kind==='changed'?'Different label':'Secret label'));
  assert.equal(f.rows.at(-1).possibleRedundantInspection,false,kind);
 }
});
test('intervening action and overlapping commands prevent false adjacency',()=>{
 const f=fixture();const a={id:'a',action:'tap_label'},b={id:'b',action:'scroll'},c={id:'c',action:'inspect_ui'};
 f.m.created(a);f.m.created(b);f.done(a,{status:'ok',observation:screen()});f.m.created(c);f.done(c,screen());assert.equal(f.rows.at(-1).possibleRedundantInspection,false);
});
test('logging failure cannot throw into command processing',()=>{
 const f=fixture(()=>{throw Error('disk full')});const c={id:'a',action:'inspect_ui'};
 assert.doesNotThrow(()=>{f.m.created(c);f.m.offered(c);f.done(c,screen());f.m.collected(c)});
});
test('hash retains meaningful control state while ignoring snapshot bookkeeping',()=>{
 assert.equal(observationSummary(screen('a')).screenHash,observationSummary(screen('b')).screenHash);
 const changed=screen();changed.nodes[0].focused=true;assert.notEqual(observationSummary(screen()).screenHash,observationSummary(changed).screenHash);
});
test('rejection diagnostics preserve cause metadata and exclude all content',()=>{
 const f=fixture(),c={id:'reject',action:'set_text',text:'Private clipboard contents'};f.m.created(c);f.done(c,{status:'stale_screen',screenRejection:{reason:'full_snapshot_changed',snapshotAgeMs:2100,targetNodeId:3,targetUnchanged:true,targetChangedFields:['focused','Private clipboard contents'],changedFieldCounts:{focused:1,text:2,'Private clipboard contents':99},text:'Private clipboard contents',nodes:[{text:'Private clipboard contents'}]}});
 const r=f.rows.at(-1).screenRejection;assert.equal(r.targetUnchanged,true);assert.equal(r.snapshotAgeMs,2100);assert.deepEqual(r.targetChangedFields,['focused']);assert.deepEqual(r.changedFieldCounts,{text:2,focused:1});assert.ok(!JSON.stringify(f.rows).includes('Private clipboard contents'));
});
test('malformed rejection details cannot break command completion',()=>{
 const f=fixture(),c={id:'reject',action:'set_text'};f.m.created(c);assert.doesNotThrow(()=>f.done(c,{status:'stale_screen',screenRejection:{reason:'made up',changedFieldCounts:'bad'}}));assert.equal(f.rows.at(-1).screenRejection,null);
});
