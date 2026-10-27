import test from 'node:test';
import assert from 'node:assert/strict';
import {BackgroundPhone} from './background-phone.mjs';

const list=id=>({id,nodeId:id,className:'androidx.recyclerview.widget.RecyclerView',viewId:'southwest:id/flights',bounds:[0,300,1080,2300],text:'',description:'',scrollable:true,enabled:true,visible:true});
function fixture(change=n=>n){
 const calls=[];let reads=0;
 const phone=new BackgroundPhone({enabled:()=>true,send:async c=>{
  calls.push(c);
  if(c.operation==='get_state')return {status:'ready',sessionId:'session',targetPackage:'southwest'};
  if(c.params?.action==='inspect_ui'){
   reads++;return {status:'ok',packageName:'southwest',snapshotId:'snapshot-'+reads,nodes:reads===1?[list(12)]:change([list(28)])};
  }
  if(c.params?.action==='screenshot')return {status:'ok',snapshotId:'image-invalidates-node-snapshot',packageName:'southwest'};
  return {status:'action_accepted'};
 }});
 return {phone,calls};
}
test('scroll refreshes and resolves the same list after a screenshot invalidates its node IDs',async()=>{
 const {phone,calls}=fixture();await phone.execute({action:'open_app',packageName:'southwest'});
 await phone.execute({action:'screenshot'});
 const r=await phone.execute({action:'scroll',snapshotId:'snapshot-1',nodeId:12,direction:'forward'});
 assert.equal(r.status,'action_accepted');
 assert.deepEqual(calls.slice(-2).map(c=>c.params.action),['inspect_ui','scroll']);
 assert.equal(calls.at(-1).params.snapshotId,'snapshot-2');assert.equal(calls.at(-1).params.nodeId,28);
 assert.equal(calls.filter(c=>c.params?.action==='scroll').length,1);
});
test('changed and ambiguous controls never execute a stale scroll',async()=>{
 for(const change of [nodes=>nodes.map(n=>({...n,bounds:[0,400,1080,2300]})),nodes=>[...nodes,{...nodes[0],id:29,nodeId:29}]]){
  const {phone,calls}=fixture(change);await phone.execute({action:'open_app',packageName:'southwest'});
  const r=await phone.execute({action:'scroll',snapshotId:'snapshot-1',nodeId:12,direction:'forward'});
  assert.equal(r.actionExecuted,false);assert.equal(r.status,'stale_screen');
  assert.equal(calls.some(c=>c.params?.action==='scroll'),false);
 }
});
test('unknown references and references from a closed session cannot select a new control',async()=>{
 const {phone,calls}=fixture();await phone.execute({action:'open_app',packageName:'southwest'});
 let r=await phone.execute({action:'scroll',snapshotId:'unknown',nodeId:12,direction:'forward'});
 assert.equal(r.actionExecuted,false);
 await phone.close();await phone.execute({action:'open_app',packageName:'southwest'});
 r=await phone.execute({action:'scroll',snapshotId:'snapshot-1',nodeId:12,direction:'forward'});
 assert.equal(r.actionExecuted,false);assert.equal(calls.some(c=>c.params?.action==='scroll'),false);
});
