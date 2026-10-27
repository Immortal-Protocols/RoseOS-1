import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBlueBubblesAdapter,blueBubblesTools} from './adapter.mjs';
import {loadAppToolCatalog} from '../app-tool-catalog.mjs';
const guid='any;-;+15555550100',other='any;-;same@example.test';
const chat={guid,style:45,displayName:'',participants:[{address:'+15555550100'}]};
const contact={displayName:'Example Person',phoneNumbers:[{address:'(555) 555-0100'}],emails:[]};
const msg={guid:'message-one',text:'hello',isFromMe:true,error:0,dateCreated:Date.UTC(2026,9,17),isDelivered:false,chats:[{guid}]};
const visible={status:'ok',packageName:'com.bluebubbles.messaging',nodes:[{id:0,parentId:-1},{id:1,parentId:0,description:'EP\nExample Person',visible:true},{id:2,parentId:0,editable:true}]};
function fixture(t,overrides={}){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'messages-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const sent=[];const client={contacts:async()=>({data:[contact]}),chats:async()=>({data:[chat],metadata:{total:1}}),chat:async()=>({data:{...chat,lastMessage:msg}}),messages:async()=>({data:[msg],metadata:{total:1}}),send:async(g,text)=>{sent.push([g,text]);return {data:{...msg,text}};},message:async()=>({data:msg}),...overrides};
 const phone=async()=>({status:'opened',foregroundVerified:true,observation:visible});
 return {client,sent,dir,phone,adapter:createBlueBubblesAdapter(phone,{client,ledgerDirectory:dir})};
}
test('registered complete operations work through the shared catalog without a foreground pre-open',async t=>{
 const f=fixture(t);const tools=await loadAppToolCatalog({phone:()=>assert.fail('no phone for read'),packages:[],adapters:[f.adapter]});
 assert.equal(tools.definitions.length,5);assert.equal(blueBubblesTools.length,5);
 const r=await tools.handleToolCall({tool:'messages_read_recent',arguments:{query:'Example Person'},threadId:'thread',callId:'call'});
 assert.equal(r.success,true);const body=JSON.parse(r.contentItems[0].text);assert.equal(body.markedRead,false);assert.equal(body.messages[0].dateCreated,'2026-10-17T00:00:00.000Z');assert.equal(body.messages[0].direction,'outgoing');
});
test('all pages searched; ambiguous names do not send or open',async t=>{
 let opens=0;const f=fixture(t,{chats:async offset=>({data:offset===0?[chat]:[{...chat,guid:other,participants:[{address:'same@example.test'}]}],metadata:{total:2}}),contacts:async()=>({data:[{...contact,emails:[{address:'same@example.test'}]}]})});
 const a=createBlueBubblesAdapter(async()=>{opens++;},{client:f.client,ledgerDirectory:f.dir});
 const r=await a.execute('messages_send_text',{query:'Example Person',text:'hello',operationId:'ambiguous-send'});assert.equal(r.status,'needs_clarification');assert.equal(r.totalMatches,2);assert.equal(opens,0);assert.equal(f.sent.length,0);
});
test('phone/email normalization resolves one exact recipient and reads incoming direction',async t=>{
 const f=fixture(t,{messages:async()=>({data:[{...msg,isFromMe:false,handle:{address:'+15555550100'}}],metadata:{total:3}})});
 const r=await f.adapter.execute('messages_read_recent',{query:'555-555-0100',limit:1});assert.equal(r.status,'verified');assert.equal(r.messages[0].sender,'Example Person');assert.equal(r.messages[0].direction,'incoming');assert.equal(r.olderMessagesAvailable,true);
});
test('empty historical chats use the safe list endpoint rather than chat last-message expansion',async t=>{
 const f=fixture(t,{chat:async()=>assert.fail('broken server endpoint must not be used'),messages:async()=>({data:[],metadata:{total:0}})});
 const r=await f.adapter.execute('messages_find_chats',{query:'Example Person'});assert.equal(r.status,'verified');assert.equal(r.matches[0].lastMessageAtMs,null);
});
test('open verifies chat header with composer, not just app launch or list row',async t=>{
 const f=fixture(t);assert.equal((await f.adapter.execute('messages_open_chat',{query:'Example Person'})).status,'verified');
 let clock=0;const row={...visible,nodes:[{id:0,parentId:-1,scrollable:true},{id:1,parentId:0,description:'Example Person'},{id:2,parentId:0,editable:true}]};
 const a=createBlueBubblesAdapter(async c=>c.action==='inspect_ui'?row:{status:'opened',foregroundVerified:true,observation:row},{client:f.client,now:()=>clock,sleep:async ms=>{clock+=ms;}});
 assert.equal((await a.execute('messages_open_chat',{query:'Example Person'})).status,'needs_assistance');
});
test('unknown or contradictory conversation ID never sends',async t=>{
 const f=fixture(t);assert.equal((await f.adapter.execute('messages_send_text',{chatGuid:other,text:'hello',operationId:'unknown-send'})).status,'needs_assistance');assert.equal((await f.adapter.execute('messages_send_text',{chatGuid:guid,query:'Different Person',text:'hello',operationId:'mismatch-send'})).status,'needs_assistance');assert.equal(f.sent.length,0);
});
test('verified outgoing readback is sent, delivery separate; duplicate survives restart',async t=>{
 const f=fixture(t),args={query:'Example Person',text:'hello',operationId:'stable-send-id'};
 const [first,duplicate]=await Promise.all([f.adapter.execute('messages_send_text',args),f.adapter.execute('messages_send_text',args)]);assert.equal(first.status,'verified');assert.equal(duplicate.status,'verified');assert.equal(first.sent,true);assert.equal(first.delivered,false);assert.equal(f.sent.length,1);
 const restarted=createBlueBubblesAdapter(f.phone,{client:f.client,ledgerDirectory:f.dir});assert.equal((await restarted.execute('messages_send_text',args)).status,'verified');assert.equal(f.sent.length,1);
 assert.equal((await restarted.execute('messages_send_text',{...args,text:'different'})).status,'not_executed');assert.equal(f.sent.length,1);
});
test('timeout never retries a possibly successful send',async t=>{
 let sends=0;const f=fixture(t,{send:async()=>{sends++;throw Error('timeout');}}),args={query:'Example Person',text:'hello',operationId:'timeout-send-id'};
 assert.equal((await f.adapter.execute('messages_send_text',args)).status,'uncertain');assert.equal((await f.adapter.execute('messages_send_text',args)).status,'uncertain');assert.equal(sends,1);
});
test('wrong chat, failed outgoing record, and readback failure never claim success',async t=>{
 for(const overrides of [{message:async()=>({data:{...msg,chats:[{guid:other}]}})},{send:async()=>({data:{...msg,error:5}})},{message:async()=>{throw Error('offline');}}]){
  const f=fixture(t,overrides);assert.equal((await f.adapter.execute('messages_send_text',{query:'Example Person',text:'hello',operationId:'send-test'})).status,'uncertain');
 }
});
test('retired guided review is absent from the catalog and cannot execute',async t=>{
 const f=fixture(t);assert.ok(!blueBubblesTools.some(tool=>tool.name==='messages_review'));
 const r=await f.adapter.execute('messages_review',{operation:'start',operationId:crypto.randomUUID()});
 assert.equal(r.status,'needs_assistance');assert.equal(r.reason,'Unknown Messages tool');assert.equal(f.sent.length,0);
});

test('unread tool loads actual Pixel rows and recent Mac history without opening chats or downloading media',async t=>{
 const f=fixture(t,{messages:async()=>({data:[{...msg,isFromMe:false,handle:{address:'+15555550100'},attachments:[{guid:'photo',mimeType:'image/jpeg'}]}],metadata:{total:12}})});
 let scanned;
 const a=createBlueBubblesAdapter(()=>assert.fail('No conversation should open'),{client:f.client,attachmentReader:{read:()=>assert.fail('No automatic media download')},unreadUI:{scan:async limit=>{scanned=limit;return {rows:[{title:'Example Person',preview:'hello'}],source:'Pixel Messages unread filter',total:1,moreAvailable:false,observedAt:123};}}});
 const catalog=await loadAppToolCatalog({phone:()=>assert.fail('No pre-open'),packages:[],adapters:[a]});
 const r=await catalog.handleToolCall({tool:'messages_read_unread',arguments:{messagesPerChat:5},threadId:'one',callId:'unread'});
 const body=JSON.parse(r.contentItems[0].text);assert.equal(r.success,true);assert.equal(scanned,20);assert.equal(body.unreadConversationCount,1);assert.equal(body.markedRead,false);assert.equal(body.unreadGranularity,'conversation');
 assert.equal(body.conversations[0].chat.chatGuid,guid);assert.equal(body.conversations[0].messages[0].sender,'Example Person');assert.equal(body.conversations[0].messages[0].attachments[0].attachmentGuid,'photo');assert.equal(body.conversations[0].olderMessagesAvailable,true);assert.equal(body.partial,false);assert.equal(f.sent.length,0);
});

test('unread duplicate names require uniquely matching message evidence',async t=>{
 const f=fixture(t,{chats:async()=>({data:[chat,{...chat,guid:other,participants:[{address:'same@example.test'}]}],metadata:{total:2}}),contacts:async()=>({data:[{...contact,emails:[{address:'same@example.test'}]}]}),messages:async id=>({data:[{...msg,text:id===guid?'First unique preview':'Second unique preview'}],metadata:{total:1}})});
 const scan=async()=>({rows:[{title:'Example Person',preview:'Second unique preview'}],source:'Pixel',total:1,moreAvailable:false});
 const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan}});
 const r=await a.execute('messages_read_unread');assert.equal(r.partial,false);assert.equal(r.conversations[0].chat.chatGuid,other);
 const b=createBlueBubblesAdapter(f.phone,{client:{...f.client,messages:async()=>({data:[{...msg,text:'Second unique preview'}]})},unreadUI:{scan}});
 const ambiguous=await b.execute('messages_read_unread');assert.equal(ambiguous.partial,true);assert.equal(ambiguous.conversations[0].resolved,false);assert.equal(ambiguous.conversations[0].messages,undefined);
});

test('unread errors and limits never look like an empty or complete inbox',async t=>{
 const f=fixture(t),snapshot={rows:[{title:'Unknown Person',preview:'missing'}],source:'Pixel',total:null,moreAvailable:true};
 const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>snapshot}});
 const r=await a.execute('messages_read_unread');assert.equal(r.partial,true);assert.equal(r.moreAvailable,true);assert.equal(r.unreadConversationCount,null);assert.equal(r.conversations[0].resolved,false);
 const unavailable=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>{throw Error('Phone unavailable');}}});
 assert.equal((await unavailable.execute('messages_read_unread')).status,'needs_assistance');
 assert.equal((await a.execute('messages_read_unread',{limit:101})).status,'needs_assistance');
 const empty=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({...snapshot,rows:[],total:0,moreAvailable:false})}});
 const e=await empty.execute('messages_read_unread');assert.equal(e.unreadConversationCount,0);assert.equal(e.partial,false);
});

test('unread loads are bounded to four parallel requests and keep Pixel order',async t=>{
 const chats=Array.from({length:9},(_,i)=>({...chat,guid:'any;-;'+i,displayName:'Group '+i}));
 let active=0,peak=0;
 const f=fixture(t,{chats:async()=>({data:chats,metadata:{total:chats.length}}),messages:async id=>{active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,2));active--;return {data:[{...msg,guid:id}],metadata:{total:1}};}});
 const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({rows:chats.map(c=>({title:c.displayName,preview:'hello'})),total:chats.length,moreAvailable:false,source:'Pixel'})}});
 const r=await a.execute('messages_read_unread');assert.equal(peak,4);assert.deepEqual(r.conversations.map(c=>c.chat.chatGuid),chats.map(c=>c.guid));
});

test('formatted eight-digit short codes and unnamed small-group labels map to exact participants',async t=>{
 const short={...chat,guid:'any;-;93639452',participants:[{address:'93639452'}]};
 const group={...chat,guid:'any;+;group',style:43,participants:[{address:'+15555550100'},{address:'+15555550101'}]};
 const f=fixture(t,{chats:async()=>({data:[short,group],metadata:{total:2}}),contacts:async()=>({data:[contact,{displayName:'Second Person',phoneNumbers:[{address:'+15555550101'}]}]})});
 const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({rows:[{title:'+1 93639452',preview:'hello'},{title:'Example & Second',preview:'hello'}],total:2,moreAvailable:false,source:'Pixel'})}});
 const r=await a.execute('messages_read_unread');assert.equal(r.partial,false);assert.deepEqual(r.conversations.map(c=>c.chat.chatGuid),[short.guid,group.guid]);
});

test('attachment preview and truncated text disambiguate duplicate names without reading media',async t=>{
 const long='This is a long message used as a truncated on-screen preview, with enough distinctive words to verify the matching content safely. ';
 for(const preview of ['1 Photo',long]){
  const f=fixture(t,{chats:async()=>({data:[chat,{...chat,guid:other}],metadata:{total:2}}),messages:async id=>({data:id===guid?[]:[{...msg,text:long+'The rest of the message.',attachments:[{guid:'image',mimeType:'image/jpeg'}]}],metadata:{total:id===guid?0:1}})});
  const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({rows:[{title:'Example Person',preview}],total:1,moreAvailable:false,source:'Pixel'})}});
  const r=await a.execute('messages_read_unread');assert.equal(r.partial,false);assert.equal(r.conversations[0].chat.chatGuid,other);
 }
});

test('an empty matching server history is explicitly unresolved',async t=>{
 const f=fixture(t,{messages:async()=>({data:[],metadata:{total:0}})});
 const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({rows:[{title:'Example Person',preview:'Visible text'}],total:1,moreAvailable:false,source:'Pixel'})}});
 const r=await a.execute('messages_read_unread');assert.equal(r.partial,true);assert.equal(r.conversations[0].resolved,false);
});
test('the unread result propagates the app-resume read-state warning',async t=>{
 const f=fixture(t);const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({rows:[],total:0,source:'Pixel',readStateMayHaveChanged:true})}});
 const r=await a.execute('messages_read_unread');assert.equal(r.markedRead,null);assert.match(r.readStateWarning,/resumed an existing chat/);
});
test('default unread history is ten messages per conversation with more history still available',async t=>{
 let limit;const f=fixture(t,{messages:async(_id,n)=>{limit=n;return {data:[msg],metadata:{total:10}};}});
 const a=createBlueBubblesAdapter(f.phone,{client:f.client,unreadUI:{scan:async()=>({rows:[{title:'Example Person',preview:'hello'}],total:1,source:'Pixel'})}});
 const r=await a.execute('messages_read_unread');assert.equal(limit,10);assert.equal(r.conversations[0].olderMessagesAvailable,true);
});
