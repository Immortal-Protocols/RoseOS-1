import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {messagesBridge} from '../server/messages.mjs';
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-messages-test-'));
try {
 let calls=0;
 const bridge=messagesBridge(dir,'unused',async input=>{calls++;return {status:'send_requested'};});
 const request={chatId:12,text:'Test',requestId:'01234567-89ab-cdef-0123-456789abcdef'};
 const res=()=>({writeHead(code){this.code=code;},end(body){this.data=JSON.parse(body);}});
 let r=res();await bridge.post({},r,new URL('https://localhost/messages/send'),request);assert.equal(r.data.status,'send_requested');assert.equal(calls,1);
 r=res();await bridge.post({},r,new URL('https://localhost/messages/send'),request);assert.equal(calls,1);assert.equal(r.data.status,'send_requested');
 r=res();await bridge.post({},r,new URL('https://localhost/messages/send'),{...request,text:'Changed'});assert.equal(r.code,409);assert.equal(calls,1);
 r=res();await bridge.post({},r,new URL('https://localhost/messages/send'),{...request,chatId:-1});assert.equal(r.code,400);assert.equal(calls,1);
 const failed=messagesBridge(dir,'unused',async()=>{calls++;throw Error('Lost reply');});
 const other={...request,requestId:'11234567-89ab-cdef-0123-456789abcdef'};
 r=res();await failed.post({},r,new URL('https://localhost/messages/send'),other);assert.equal(r.data.status,'uncertain');assert.equal(calls,2);
 const restarted=messagesBridge(dir,'unused',async()=>{calls++;return {status:'send_requested'};});
 r=res();await restarted.post({},r,new URL('https://localhost/messages/send'),other);assert.equal(r.data.status,'uncertain');assert.equal(calls,2);
 let updateInput;
 const updates=messagesBridge(dir,'unused',async input=>{updateInput=input;return {status:'ok',cursor:123,messages:[]};});
 r=res();await updates.get({},r,new URL('https://localhost/messages/updates'));assert.deepEqual(updateInput,{action:'updates'});assert.equal(r.data.cursor,123);
 r=res();await updates.get({},r,new URL('https://localhost/messages/updates?cursor=123'));assert.deepEqual(updateInput,{action:'updates',cursor:123});
 for(const bad of ['-1','nope','9007199254740993']){r=res();await updates.get({},r,new URL('https://localhost/messages/updates?cursor='+bad));assert.equal(r.code,400);}
 console.log('PASS: send validation, replay deduplication, payload mismatch and uncertain-send protection across restart. No messages sent.');
} finally{fs.rmSync(dir,{recursive:true,force:true});}
