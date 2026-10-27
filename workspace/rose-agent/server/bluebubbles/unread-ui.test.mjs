import test from 'node:test';
import assert from 'node:assert/strict';
import {createUnreadUI,unreadRows} from './unread-ui.mjs';

function fixture({glitchLabel,initialChat=false}={}){
 const entries=Array.from({length:5},(_,i)=>({title:['Ross','Christine','Nathan','Other person','Last person'][i],preview:'Test message '+i,unread:i!==1}));
 let mode=initialChat?'chat':'inbox',filter=false,page=0,seq=0,peek=null,glitched=false;const actions=[];
 const node=(id,description,bounds,extra={})=>({id,description,bounds,enabled:true,visible:true,clickable:true,clickTarget:id,...extra});
 function frame(){
  let nodes=[];
  if(mode==='inbox'){
   const rows=entries.filter(e=>!filter||e.unread).slice(page*2,page*2+2);
   nodes=[node(1,'Messages',[10,10,600,100],{clickable:false}),node(2,'',[620,20,680,80]),node(3,'',[700,20,760,80]),
    ...rows.map((e,i)=>node(10+i,e.title+'\n3:00 PM\n'+e.preview,[0,120+i*150,800,260+i*150],{longClickable:true})),node(99,'',[0,100,800,900],{clickable:false,scrollable:true})];
  }else if(mode==='chat')nodes=[node(1,'Existing conversation',[0,0,100,100]),node(2,'Message',[0,100,100,200],{editable:true})];
  else if(mode==='menu')nodes=[node(1,'Filter Chats',[0,0,100,100])];
  else if(mode==='sheet')nodes=[node(1,'Chat Filters',[0,0,100,100],{clickable:false}),node(2,'All Messages',[0,100,100,200],{selected:!filter}),node(3,'Unread Messages',[100,100,200,200],{selected:filter}),node(4,'All',[0,300,100,400],{selected:true}),node(5,'Dismiss',[0,500,100,600])];
  else if(mode==='peek')nodes=[node(1,peek.unread?'Mark Read':'Mark Unread',[0,0,100,100])];
  return {status:'ok',packageName:'com.bluebubbles.messaging',snapshotId:'snapshot-'+(++seq),truncated:false,nodes};
 }
 async function phone(c){
  actions.push(c);
  if(c.action==='open_app')return {status:'opened',foregroundVerified:true};
  if(c.action==='inspect_ui')return frame();
  if(c.action==='back'){mode='inbox';return {status:'action_accepted'};}
  if(c.action==='scroll'){const max=Math.ceil(entries.filter(e=>!filter||e.unread).length/2)-1;const next=Math.max(0,Math.min(max,page+(c.direction==='forward'?1:-1)));if(next===page)return {status:'action_failed'};page=next;return {status:'action_accepted'};}
  if(c.action==='tap'){assert.equal(c.nodeId,3);mode='menu';return {status:'action_accepted'};}
  if(c.action==='tap_label'){
   if(c.label===glitchLabel&&!glitched){glitched=true;return {status:'label_target_mismatch'};}
   if(c.label==='Filter Chats')mode='sheet';
   else if(c.label==='Unread Messages'){filter=true;page=0;}
   else if(c.label==='All Messages'){filter=false;page=0;}
   else if(c.label==='Dismiss')mode='inbox';
   else if(c.label==='Mark Read'){peek.unread=false;mode='inbox';page=0;}
   else if(c.label==='Mark Unread'){peek.unread=true;mode='inbox';page=0;}
   else assert.fail(c.label);
   return {status:'action_accepted'};
  }
  if(c.action==='long_press'){peek=entries.filter(e=>!filter||e.unread)[page*2+c.nodeId-10];mode='peek';return {status:'action_accepted'};}
  assert.fail(c.action);
 }
 return {ui:createUnreadUI(phone,{sleep:async()=>{}}),entries,actions,frame};
}
test('unread filter walks pages without opening a conversation and can report exact total',async()=>{
 const f=fixture();const r=await f.ui.scan(20);assert.equal(r.rows.length,4);assert.equal(r.total,4);assert.equal(r.moreAvailable,false);assert.ok(!f.actions.some(a=>a.action==='bluebubbles_open_chat'||a.action==='long_press'));assert.ok(!f.entries[1].unread);
});
test('limits are explicit rather than pretending to be the full unread count',async()=>{
 const f=fixture();const r=await f.ui.scan(2);assert.equal(r.rows.length,2);assert.equal(r.moreAvailable,true);assert.equal(r.total,null);
});
test('mark read and restore unread use the exact row and verify through the filter',async()=>{
 const f=fixture();const r=await f.ui.scan(20),entry=r.rows[0];assert.equal((await f.ui.setRead(entry,true)).markedRead,true);assert.equal(f.entries[0].unread,false);assert.equal((await f.ui.setRead(entry,false)).markedUnread,true);assert.equal(f.entries[0].unread,true);
});
test('changed preview blocks a stale acknowledgement instead of reading new messages',async()=>{
 const f=fixture();const entry=(await f.ui.scan(20)).rows[0];f.entries[0].preview='A newly arrived message';await assert.rejects(f.ui.setRead(entry,true),/changed/);assert.equal(f.entries[0].unread,true);
});
test('row parsing preserves literal text and does not treat arbitrary controls as conversations',()=>{
 const f=fixture();const rows=unreadRows(f.frame());assert.equal(rows[0].title,'Ross');assert.equal(rows[0].preview,'Test message 0');assert.equal(rows.length,2);
});
test('24-hour timestamps are accepted and indistinguishable visible rows fail closed',()=>{
 const f=fixture(),frame=f.frame();frame.nodes[3].description='Ross\n15:42\nA literal message';
 assert.equal(unreadRows(frame)[0].displayTime,'15:42');
 frame.nodes.push({...frame.nodes[3],id:50});assert.throws(()=>unreadRows(frame),/indistinguishable/);
});
test('an animated-menu target rejection is re-observed before one bounded retry',async()=>{
 const f=fixture({glitchLabel:'Mark Unread'}),entry=(await f.ui.scan(20,false)).rows.find(e=>e.title==='Christine');
 assert.equal((await f.ui.setRead(entry,false)).markedUnread,true);
 const attempts=f.actions.filter(a=>a.action==='tap_label'&&a.label==='Mark Unread');assert.equal(attempts.length,2);assert.notEqual(attempts[0].snapshotId,attempts[1].snapshotId);
});
test('resuming an existing chat is disclosed rather than claiming read state was preserved',async()=>{
 const f=fixture({initialChat:true});const r=await f.ui.scan(20);assert.equal(r.readStateMayHaveChanged,true);assert.equal(r.total,4);
 assert.ok(f.actions.some(c=>c.action==='back'));assert.ok(!f.actions.some(c=>c.action==='bluebubbles_open_chat'));
 const normal=fixture();assert.equal((await normal.ui.scan(20)).readStateMayHaveChanged,false);
});
