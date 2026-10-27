import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import https from 'node:https';
import {execFileSync} from 'node:child_process';
import {DeviceAuth,readState,savePrivate,digest,peerIdentity} from './device-auth.mjs';

test('real TLS pairing, proof of possession, replay, revocation and legacy retirement',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-pair-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 for(const name of ['server','pixel','attacker'])execFileSync('openssl',['req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-keyout',dir+'/'+name+'.key','-out',dir+'/'+name+'.crt','-days','1','-subj','/CN='+name],{stdio:'ignore'});
 const stateFile=dir+'/devices.json',tokenFile=dir+'/token';fs.writeFileSync(tokenFile,'old-shared');
 const auth=new DeviceAuth(stateFile);
 const server=https.createServer({key:fs.readFileSync(dir+'/server.key'),cert:fs.readFileSync(dir+'/server.crt'),requestCert:true,rejectUnauthorized:false},async(req,res)=>{
  let value;const send=(status,data)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(data));};
  if(req.url==='/enroll'){for await(const c of req)value=(value||'')+c;try{return send(200,auth.enroll(req,JSON.parse(value).code,'Pixel'));}catch{return send(403,{});}}
  // Simulate public ingress, irrespective of the test's loopback TCP socket.
  if(req.url==='/remote')return send(auth.paired(req)||(auth.legacyAllowed()&&req.headers.authorization==='Bearer old-shared')?200:401,{});
  send(auth.authorize(req,tokenFile)?200:401,{id:peerIdentity(req)?.id});
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>{server.closeAllConnections();server.close();});
 async function call(route,name,body,bearer){return new Promise((resolve,reject)=>{
  const req=https.request({host:'127.0.0.1',port:server.address().port,path:route,method:body?'POST':'GET',ca:fs.readFileSync(dir+'/server.crt'),checkServerIdentity:()=>undefined,agent:false,...(name?{key:fs.readFileSync(dir+'/'+name+'.key'),cert:fs.readFileSync(dir+'/'+name+'.crt')}:{}),headers:bearer?{authorization:'Bearer '+bearer}:{}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);req.end(body?JSON.stringify(body):undefined);
 });}
 assert.equal(await call('/remote'),401);assert.equal(await call('/remote','attacker'),401);
 savePrivate(stateFile,{devices:[],legacyUntil:Date.now()+100000,invite:{hash:digest('ABCDEF234567'),expiresAt:Date.now()+100000,attempts:0}});
 assert.equal(await call('/remote',null,null,'old-shared'),200);
 assert.equal(await call('/enroll',null,{code:'ABCDEF234567'}),403);
 assert.equal(await call('/enroll','pixel',{code:'WRONG'}),403);
 const pending=readState(stateFile);pending.invite.expiresAt=0;savePrivate(stateFile,pending);
 assert.equal(await call('/enroll','pixel',{code:'ABCDEF234567'}),403);
 pending.invite.expiresAt=Date.now()+100000;pending.invite.attempts=10;savePrivate(stateFile,pending);
 assert.equal(await call('/enroll','pixel',{code:'ABCDEF234567'}),403);
 pending.invite.attempts=0;savePrivate(stateFile,pending);
 assert.equal(await call('/enroll','pixel',{code:'ABCD-EF23-4567'}),200);
 assert.equal(await call('/remote','pixel'),200);
 assert.equal(await call('/remote','attacker'),401);
 assert.equal(await call('/enroll','attacker',{code:'ABCDEF234567'}),403);
 const state=readState(stateFile);delete state.legacyUntil;savePrivate(stateFile,state);fs.writeFileSync(tokenFile,'new-host-only');
 assert.equal(await call('/remote',null,null,'old-shared'),401);
 assert.equal(await call('/',null,null,'old-shared'),401);
 assert.equal(await call('/',null,null,'new-host-only'),200);
 assert.equal(await call('/remote',null,null,'new-host-only'),401);
 assert.equal(await call('/remote','pixel'),200);
 state.devices[0].revokedAt=Date.now();savePrivate(stateFile,state);
 assert.equal(await call('/remote','pixel'),401);
});

test('expired and exhausted pairing codes cannot enroll a key',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-pair-expiry-'));
 try{const file=dir+'/state';savePrivate(file,{devices:[],invite:{hash:digest('ABC'),expiresAt:0,attempts:0}});const auth=new DeviceAuth(file);assert.throws(()=>auth.enroll({socket:{}},'ABC','phone'));assert.deepEqual(readState(file).devices,[]);}finally{fs.rmSync(dir,{recursive:true,force:true});}
});
