import test from 'node:test';
import assert from 'node:assert/strict';
import {makePlan,usageCost} from './compare.mjs';
test('usage is priced for the selected comparison model',()=>{
 assert.equal(usageCost({input:1000000,cached:0,output:1000000},'gpt-6.1-sol'),12);
 assert.equal(usageCost({input:1000000,cached:0,output:1000000},'gpt-6-luna'),.6);
 assert.throws(()=>usageCost({input:1,cached:0,output:1},'unknown'));
});
test('matched replay excludes future proposals and retains original context; backlog is separate',()=>{
 const events=Array.from({length:6},(_,i)=>({id:String(i),text:'message '+i,role:'user'}));
 const before={id:'before',createdAt:5,batchId:'old'},after={id:'after',createdAt:15,batchId:'b'};
 const plan=makePlan({batches:[{id:'b',at:10,status:'completed',eventIds:['2','3']}],candidates:[before,after],processedCount:4},events);
 assert.deepEqual(plan[0].existing,[before]);assert.deepEqual(plan[0].luna,[after]);
 assert.deepEqual(plan[0].events.map(e=>[e.id,e.isNew]),[['0',false],['1',false],['2',true],['3',true]]);
 assert.equal(plan[1].kind,'backlog');assert.deepEqual(plan[1].events.filter(e=>e.isNew).map(e=>e.id),['4','5']);
});
test('missing historical evidence prevents a misleading partial replay',()=>{
 assert.throws(()=>makePlan({batches:[{id:'b',at:1,status:'completed',eventIds:['gone']}],candidates:[],processedCount:0},[]),/Missing original/);
});
