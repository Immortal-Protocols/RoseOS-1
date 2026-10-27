import test from 'node:test';import assert from 'node:assert/strict';import {createRequire} from 'node:module';
import {MessagesWorker,resolveChat} from '../worker.mjs';
const require=createRequire(import.meta.url),route=require('../../voice-providers/voice-commands.js');
const chats=[{id:1,displayName:'Jack Smith',participants:'a',dateMs:300},{id:2,displayName:'Jack Smith, Terri Jones',participants:'a, b',dateMs:200},{id:3,displayName:'Molly Green',participants:'c',dateMs:100}];
const inbox={1:[{id:11,dateMs:100,fromMe:0,text:'Earlier message'},{id:12,dateMs:200,fromMe:1,text:'A question'},{id:13,dateMs:300,fromMe:0,text:'A reply'}],2:[{id:20,dateMs:200,fromMe:0,text:'Group message'}],3:[{id:30,dateMs:100,fromMe:0,text:'Other message'}]};
function fixture(){const calls=[];return {calls,worker:new MessagesWorker({enabled:()=>true,read:async input=>{calls.push(input);if(input.action==='chats')return {status:'ok',chats};assert.equal(input.action,'messages');return {status:'ok',chat:chats.find(c=>c.id===input.chatId),messages:inbox[input.chatId]};}})};}
test('unique person versus group, ambiguous names, no body matching',()=>{
 assert.equal(resolveChat(chats,'Jack').chat.id,1);
 assert.equal(resolveChat(chats,'Jack and Terri').chat.id,2);
 assert.equal(resolveChat([...chats,{id:4,displayName:'Jack Jones',participants:'d'}],'Jack').status,'needs_clarification');
 assert.equal(resolveChat(chats,'A reply').status,'not_found');
 assert.equal(resolveChat(chats,'Molly Green').chat.id,3);
});
test('disabled and send-shaped requests do no work',async()=>{
 const worker=new MessagesWorker({enabled:()=>false,read:()=>assert.fail('Off switch must bypass reads')});
 assert.equal((await worker.execute({operation:'latest'})).status,'disabled');
 const f=fixture();assert.equal((await f.worker.execute({operation:'send',chatId:1,text:'x'})).status,'invalid_request');
 assert.equal((await f.worker.execute({operation:'read',chatId:1,text:'x'})).status,'invalid_request');assert.equal(f.calls.length,0);
});
test('read preserves sender direction and establishes only a session-scoped checkpoint',async()=>{
 const f=fixture();let result=await f.worker.execute({operation:'check_reply',query:'Jack'},'owner-session');
 assert.equal(result.status,'verified_reply_state');assert.equal(result.replyAfterLastOutgoing,true);assert.equal(result.newSincePreviousWorkerRead,null);
 result=await f.worker.execute({operation:'check_reply',query:'Jack'},'owner-session');assert.equal(result.newSincePreviousWorkerRead,false);
 result=await f.worker.execute({operation:'check_reply',query:'Jack'},'different-session');assert.equal(result.newSincePreviousWorkerRead,null);
 assert.ok(f.calls.every(c=>['chats','messages'].includes(c.action)));
});
test('latest uses incoming direction and temporal coverage; never claims unread-only',async()=>{
 const f=fixture(),r=await f.worker.execute({operation:'latest',limit:2});assert.equal(r.status,'verified_messages');
 assert.deepEqual(r.messages.map(m=>m.id),[13,20]);assert.equal(r.incomingOnly,true);assert.equal(r.unreadOnly,false);
});
test('bounded latest scan reports partial coverage instead of guessing',async()=>{
 let reads=0;const many=Array.from({length:20},(_,i)=>({id:i+1,displayName:'Chat '+i,participants:'id'+i,dateMs:100-i}));
 const worker=new MessagesWorker({enabled:()=>true,read:async input=>input.action==='chats'?{status:'ok',chats:many}:(reads++,{status:'ok',chat:{id:input.chatId},messages:[]})});
 const result=await worker.execute({operation:'latest'});assert.equal(result.status,'partial');assert.equal(reads,12);
});
test('mismatched conversation identity fails without returning another chat',async()=>{
 const worker=new MessagesWorker({enabled:()=>true,read:async input=>input.action==='chats'?{status:'ok',chats}:{status:'ok',chat:{id:999},messages:inbox[1]}});
 const result=await worker.execute({operation:'read',query:'Jack'});assert.equal(result.status,'unavailable');assert.equal(result.messages,undefined);
});
test('direct phrases remain narrow, gated, and do not intercept sends or pronouns',()=>{
 assert.equal(route('Read my latest messages',{messages:true}).params.operation,'latest');
 assert.equal(route('Any reply from Jack?',{messages:true}).params.operation,'check_reply');
 assert.equal(route("Read Jack's last message",{messages:true}).params.query,'Jack');
 assert.equal(route('Find my recent chat with Molly',{messages:true}).params.operation,'find');
 for(const text of ['Any reply from him?','Tell Jack I am coming','Someone said read my latest messages'])assert.equal(route(text,{messages:true}),null);
 assert.equal(route('Read my latest messages',{messages:false}),null);
});

test('multiple messages from one chat never move the checkpoint backwards',async()=>{
 const f=fixture();await f.worker.execute({operation:'latest',limit:4},'s');
 const r=await f.worker.execute({operation:'check_reply',query:'Jack'},'s');
 assert.equal(r.newSincePreviousWorkerRead,false);
});

test('reading messages from a contact never attributes outgoing text to them',async()=>{
 const f=fixture(),r=await f.worker.execute({operation:'read',query:'Jack',limit:2});
 assert.deepEqual(r.messages.map(m=>m.id),[11,13]);assert.equal(r.incomingOnly,true);
});
