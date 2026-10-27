import {test} from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {EventEmitter} from 'node:events';
import {PhonePush} from './push.mjs';
class Response extends EventEmitter {writableLength=0;chunks=[];writeHead(status){this.status=status;}flushHeaders(){}write(x){this.chunks.push(x);}end(x){if(x)this.chunks.push(x);this.writableEnded=true;this.emit('close');}destroy(){this.destroyed=true;this.emit('close');}}
test('off by default, reconnect reoffers pending IDs, disable closes streams',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'phone-push-'));const offered=[];
 try{
  const p=new PhonePush({file:path.join(dir,'config.json'),epoch:'epoch',pending:()=>[{id:'existing'}],offered:c=>offered.push(c.id)});
  const off=new Response();p.connect({},off);assert.equal(off.status,503);
  p.set(true);const first=new Response();p.connect({},first);p.publish({id:'new'});
  assert.deepEqual(offered,['existing','new']);assert.match(first.chunks.join(''),/"epoch":"epoch"/);
  first.end();const again=new Response();p.connect({},again);assert.equal(offered.at(-1),'existing');
  assert.equal(p.status().connections,1);p.set(false);assert.equal(again.writableEnded,true);assert.equal(p.status().connections,0);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('slow connections are dropped rather than buffering more commands',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'phone-push-'));
 try{const p=new PhonePush({file:path.join(dir,'config.json'),epoch:'x',pending:()=>[]});p.set(true);const r=new Response();p.connect({},r);r.writableLength=300000;p.publish({id:'a'});assert.equal(r.destroyed,true);assert.equal(p.status().connections,0);}finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('task snapshots use the existing stream, reconnect with latest state, and never enqueue commands',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-push-'));let current={state:'running'};const offered=[];
 try{
  const p=new PhonePush({file:path.join(dir,'config.json'),epoch:'x',pending:()=>[],taskStatus:()=>current,offered:x=>offered.push(x)});p.set(true);
  const first=new Response();p.connect({},first);assert.match(first.chunks.join(''),/"taskStatus":\{"state":"running"\}/);
  current={state:'completed'};p.publishTaskStatus(current);assert.match(first.chunks.join(''),/"type":"task_status","taskStatus":\{"state":"completed"\}/);
  first.end();const next=new Response();p.connect({},next);assert.match(next.chunks.join(''),/"taskStatus":\{"state":"completed"\}/);assert.deepEqual(offered,[]);
  p.close();
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('observer phones receive task results but never pending or newly published commands',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'observer-push-'));const offered=[];
 const p=new PhonePush({file:path.join(dir,'config.json'),epoch:'x',pending:()=>[{id:'pending'}],taskStatus:()=>({current:{state:'completed',result:'224'}}),offered:c=>offered.push(c.id)});
 try{
  p.set(true);const worker=new Response(),observer=new Response();
  p.connect({},worker,{receiveCommands:true,peerId:'fold'});p.connect({},observer,{receiveCommands:false,peerId:'regular'});
  p.publish({id:'next'});p.publishTaskStatus({current:{state:'completed',result:'224'}});
  assert.match(worker.chunks.join(''),/"id":"pending"/);assert.match(worker.chunks.join(''),/"id":"next"/);
  const output=observer.chunks.join('');assert.match(output,/"result":"224"/);assert.doesNotMatch(output,/"type":"commands"/);
  assert.doesNotMatch(output,/"id":"pending"|"id":"next"/);assert.deepEqual(offered,['pending','next']);
  assert.equal(p.status().commandConnections,1);assert.equal(p.status().observerConnections,1);
 }finally{p.close();fs.rmSync(dir,{recursive:true,force:true});}
});
test('a phone reconnect replaces only its own stream, preserving the executor and its routing',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'reconnect-push-'));
 const p=new PhonePush({file:path.join(dir,'config.json'),epoch:'x',pending:()=>[]});
 try{
  p.set(true);const worker=new Response(),oldObserver=new Response(),newObserver=new Response();
  p.connect({},worker,{receiveCommands:true,peerId:'fold'});p.connect({},oldObserver,{receiveCommands:false,peerId:'regular'});
  p.connect({},newObserver,{receiveCommands:false,peerId:'regular'});
  assert.equal(oldObserver.writableEnded,true);assert.notEqual(worker.writableEnded,true);assert.equal(p.status().connections,2);
  p.publish({id:'after-reconnect'});assert.match(worker.chunks.join(''),/after-reconnect/);assert.doesNotMatch(newObserver.chunks.join(''),/after-reconnect/);
 }finally{p.close();fs.rmSync(dir,{recursive:true,force:true});}
});
