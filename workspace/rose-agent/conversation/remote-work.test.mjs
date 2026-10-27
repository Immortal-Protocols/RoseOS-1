import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import WebSocket from '../server/node_modules/ws/index.js';
import {RemoteWork} from './remote-work.mjs';

test('remote handoff uses the Unix WebSocket protocol, saves the thread first, and accepts a turn receipt',async context=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-remote-'));
 const socketPath=path.join(directory,'control.sock');
 const server=http.createServer();
 const websocket=new WebSocket.Server({server});
 let saved=false,turns=0;
 websocket.on('connection',(socket,request)=>{
  assert.equal(request.headers['sec-websocket-extensions'],undefined);
  socket.on('message',raw=>{
   const message=JSON.parse(raw);
   if(!message.id)return;
   let result={};
   if(message.method==='thread/start'){assert.equal(message.params.approvalPolicy,'never');assert.equal(message.params.sandbox,'danger-full-access');assert.equal(message.params.model,undefined);result={thread:{id:'thread_verified'}};}
   if(message.method==='turn/start'){assert.equal(saved,true);turns++;result={turn:{id:'turn_verified'}};}
   socket.send(JSON.stringify({id:message.id,result}));
  });
 });
 await new Promise(resolve=>server.listen(socketPath,resolve));
 context.after(async()=>{for(const client of websocket.clients)client.terminate();websocket.close();await new Promise(resolve=>server.close(resolve));fs.rmSync(directory,{recursive:true,force:true});});
 const remote=await new RemoteWork({socketPath}).connect();
 try{const receipt=await remote.start({cwd:directory,prompt:'Authorized test',onThread:async threadId=>{assert.equal(threadId,'thread_verified');saved=true;}});assert.equal(receipt.turnId,'turn_verified');assert.equal(turns,1);}finally{remote.close();}
});
