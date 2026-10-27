import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {readMapsState,createMapsAdapter} from './maps.mjs';
const fixture=name=>JSON.parse(fs.readFileSync(new URL('../../../experiments/maps-tools/fixtures/'+name+'.json',import.meta.url),'utf8'));

test('typed search text cannot count as a displayed place',()=>{
 const state=readMapsState(fixture('search-typed'));
 assert.equal(state.phase,'editing');assert.equal(state.complete,false);
});
test('a selected city needs its actual heading and matching directions control',()=>{
 const s=fixture('city-result'),state=readMapsState(s);
 assert.equal(state.phase,'place');assert.equal(state.place.name,'San Clemente');assert.equal(state.complete,true);
 s.nodes=s.nodes.filter(n=>n.text!=='San Clemente');
 assert.equal(readMapsState(s).complete,false);
});
test('unreadable business cards request assistance instead of inventing place names',()=>{
 const state=readMapsState(fixture('business-results'));
 assert.equal(state.phase,'results');assert.equal(state.complete,false);assert.equal(state.place,undefined);
});
test('accepted search is submitted once and requires a real result',async()=>{
 let entered=0,writes=0;
 const phone=async c=>{
  if(c.action==='inspect_ui')return fixture(entered?'city-result':'search-typed');
  if(c.action==='set_text'){writes++;return {status:'action_accepted'};}
  if(c.action==='ime_enter'){entered++;return {status:'action_accepted'};}
  throw Error('Unexpected command '+c.action);
 };
 const result=await createMapsAdapter(phone).execute('maps_search',{query:'San Clemente, California'});
 assert.equal(result.status,'verified');assert.equal(result.phase,'place');assert.equal(entered,1);assert.equal(writes,1);
});
test('rejected submission is not repeated or reported successful',async()=>{
 let entered=0;
 const phone=async c=>{
  if(c.action==='inspect_ui')return fixture('search-typed');
  if(c.action==='set_text')return {status:'action_accepted'};
  if(c.action==='ime_enter'){entered++;return {status:'action_failed'};}
  throw Error('Unexpected command '+c.action);
 };
 const result=await createMapsAdapter(phone).execute('maps_search',{query:'San Clemente, California'});
 assert.equal(result.status,'needs_assistance');assert.equal(entered,1);
});
test('an input node replaced during transition is freshly resolved before retry',async()=>{
 let writes=0,entered=0,inspections=0;
 const phone=async c=>{
  if(c.action==='inspect_ui'){inspections++;return fixture(entered?'city-result':'search-typed');}
  if(c.action==='set_text')return {status:++writes===1?'node_unavailable':'action_accepted'};
  if(c.action==='ime_enter'){entered++;return {status:'action_accepted'};}
  throw Error('Unexpected command '+c.action);
 };
 const result=await createMapsAdapter(phone).execute('maps_search',{query:'San Clemente, California'});
 assert.equal(result.status,'verified');assert.equal(writes,2);assert.equal(entered,1);assert.ok(inspections>=4);
});
