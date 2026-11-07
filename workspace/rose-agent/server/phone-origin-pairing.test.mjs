import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {AsyncLocalStorage} from 'node:async_hooks';
import {TaskStop} from './task-stop.mjs';
import {LauncherTask} from './launcher-task.mjs';
import {DeviceAuth,peerIdentity,savePrivate,readState} from '../security/device-auth.mjs';

// POST /phone/commands trusts "the paired phone" with mutations and runs its work as NON_TASK (outside the Stop gate).
// That must mean a client key DeviceAuth enrolled and has not revoked, never any TLS key: the server asks for client
// certificates with rejectUnauthorized:false, so a local caller holding the host token could add a self-signed key.
// These run the actual server code (the authorize gate, the route, the origin rules and /task/stop, sliced from
// server.mjs) with the real DeviceAuth and peerIdentity over real P-256 certificates. Nothing listens or connects.
const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
const between=(from,to)=>{const start=server.indexOf(from),end=server.indexOf(to,start);assert.ok(start>0&&end>start,from);return server.slice(start,end);};
const line=from=>{const start=server.indexOf(from);assert.ok(start>0,from);return server.slice(start,server.indexOf('\n',start));};
const originBlock=(()=>{const start=server.indexOf('// Origin of phone work,'),end=server.indexOf('\n',server.indexOf('const readOnlyPhoneActions=',start));assert.ok(start>0&&end>start);return server.slice(start,end+1);})();
const code=originBlock+line('function authorized(req)')+'\n'+line('function targetPhone(req)')+'\n'+between('function phoneCommandRejection(','function checkPausedApp(')
 +between('function legacyReadPhone(','async function rawFastPhone(');
const route=name=>line(" if(url.pathname==='"+name+"'){");
const completionRoute=line(" if(url.pathname.startsWith('/phone/commands/')){");
const MUTATION={action:'media_control',operation:'pause'},READ={action:'screenshot'};

const dir=fs.mkdtempSync(path.join(os.tmpdir(),'phone-origin-pairing-'));
const der={};
for(const name of ['pixel','stranger']){
 execFileSync('openssl',['req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-keyout',dir+'/'+name+'.key',
  '-out',dir+'/'+name+'.crt','-days','1','-subj','/CN='+name],{stdio:'ignore'});
 der[name]=new crypto.X509Certificate(fs.readFileSync(dir+'/'+name+'.crt')).raw;
}
process.on('exit',()=>fs.rmSync(dir,{recursive:true,force:true}));
const keyId=name=>crypto.createHash('sha256').update(new crypto.X509Certificate(der[name]).publicKey.export({type:'spki',format:'der'})).digest('hex');
// A request as node's TLS server presents it: an optional client certificate, a token header, a peer address.
const request=({cert=null,token=null,address='127.0.0.1'}={})=>({headers:token?{authorization:'Bearer '+token}:{},
 socket:{encrypted:true,remoteAddress:address,getPeerCertificate:()=>cert?{raw:der[cert]}:{}}});

function fixture({backgroundEnabled,legacyUntil=null,revoked=false,deviceId=null}){
 const priv=fs.mkdtempSync(path.join(dir,'priv-'));fs.writeFileSync(priv+'/pair-token','host-token');
 savePrivate(priv+'/devices.json',{devices:[{id:keyId('pixel'),label:'Pixel',pairedAt:1,...(revoked?{revokedAt:2}:{})}],...(legacyUntil?{legacyUntil}:{})});
 const deviceAuth=new DeviceAuth(priv+'/devices.json');
 const launcher=new LauncherTask(path.join(priv,'task.json'));
 const executed=[],responses=[],completed=[];
 const taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>{},abortReview:()=>{},closeWorkspace:async()=>{}});
 let context;
 const origin=()=>{const s=context.phoneOrigin.getStore();return s===context.NON_TASK?'non_task':s===context.LEGACY_READ?'legacy_read':s?.taskId?'task':'none';};
 context=vm.createContext({taskStop,backgroundEnabled,deviceAuth,priv,crypto,setImmediate,Date,Error,Object,Set,AsyncLocalStorage,
  peerIdentity,checkPausedApp:()=>{},autoGenerator:{active:false},visualTrial:false,busy:true,taskStarted:0,lastRoute:null,
  backgroundPhone:{execute:async c=>{executed.push(c.action+':'+origin());return {status:'ok'};}},
  rawFastPhone:async c=>{executed.push(c.action+':'+origin());return {status:'ok'};},
  phone:{create:c=>({id:'c'+responses.length,action:c.action,status:'pending'}),complete:(id,result)=>{completed.push(result);return {id,status:'completed'};}},
  backgroundConfig:{deviceId:deviceId??keyId('pixel')},
  measurePhone:()=>{},phonePush:{publish:c=>executed.push('pushed:'+c.action)},rawVisualCaptures:new Map(),
  decisionsConfig:{read:()=>({enabled:false})},genericUiActions:new Set(),visualGuard:{},
  answer:(res,status,body)=>responses.push({status,body}),
 });
 vm.runInContext(code+';globalThis.targetPhone=targetPhone;globalThis.phoneOrigin=phoneOrigin;globalThis.NON_TASK=NON_TASK;globalThis.LEGACY_READ=LEGACY_READ;',context);
 // The request handler's outer boundary, as in server.mjs: every route below the pairing routes needs authorized(req).
 vm.runInContext("globalThis.post=async(url,data,res,req)=>{if(!authorized(req))return answer(res,401,{error:'Pairing required'});"+route('/phone/commands')+'};',context);
 vm.runInContext("globalThis.stop=async(url,data,res,req)=>{"+route('/task/stop')+'};',context);
 vm.runInContext("globalThis.complete=async(url,data,res,req)=>{if(!authorized(req))return answer(res,401,{error:'Pairing required'});"+completionRoute+'};',context);
 const flush=()=>new Promise(r=>setImmediate(r));
 // Answers only (no flush): a background command is queued here and dispatched on the next turn of the loop.
 const send=async(data,req)=>{await context.post({pathname:'/phone/commands'},{...data},{},req);return responses.at(-1);};
 const call=async(data,req)=>{const r=await send(data,req);await flush();return r;};
 const newTask=()=>{launcher.start('Calculate a tip');taskStop.begin(launcher.current.id);return launcher.current.id;};
 const stopTask=async id=>context.stop({pathname:'/task/stop'},{taskId:id,source:'tray'},{},request({token:'host-token'}));
 const target=req=>context.targetPhone(req);
 const finish=async(id,result,req)=>{await context.complete({pathname:'/phone/commands/'+id},{...result},{},req);return responses.at(-1);};
 return {executed,responses,completed,send,call,flush,newTask,stopTask,deviceAuth,priv,target,finish};
}

const refused=r=>r.body?.status==='completed'&&r.body.result?.actionExecuted===false;

for(const backgroundEnabled of [true,false]){
 test('the paired phone keeps its direct tools and runs them as non-task work (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});
  const phone=request({cert:'pixel'});
  assert.equal(f.deviceAuth.paired(phone),true);
  const r=await f.call(MUTATION,phone);
  assert.equal(r.status,201);assert.ok(!refused(r),JSON.stringify(r));
  assert.deepEqual(f.executed,backgroundEnabled?['media_control:non_task']:['pushed:media_control']);
 });

 test('an unpaired key with the host token is a local caller: no mutation, reads as legacy reads (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});
  const stranger=request({cert:'stranger',token:'host-token'});
  assert.equal(f.deviceAuth.paired(stranger),false);
  const m=await f.call(MUTATION,stranger);
  assert.ok(refused(m),'mutation refused: '+JSON.stringify(m));
  assert.match(m.body.result.reason,/cannot prove which task it belongs to/);
  const r=await f.call(READ,stranger);
  assert.equal(r.status,201);
  assert.deepEqual(f.executed,backgroundEnabled?['screenshot:legacy_read']:['pushed:screenshot'],'never NON_TASK at dispatch');
 });

 test('a revoked phone key with the host token is treated the same as an unpaired one (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled,revoked:true});
  const revoked=request({cert:'pixel',token:'host-token'});
  assert.equal(f.deviceAuth.paired(revoked),false);
  assert.ok(refused(await f.call(MUTATION,revoked)));
  await f.call(READ,revoked);
  assert.deepEqual(f.executed,backgroundEnabled?['screenshot:legacy_read']:['pushed:screenshot']);
  assert.equal((await f.call(READ,request({cert:'pixel'}))).status,401,'without the token a revoked key is refused at the boundary');
 });

 test('the token alone keeps read and Stop-gated behaviour (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});
  const shell=request({token:'host-token'});
  assert.ok(refused(await f.call(MUTATION,shell)));
  await f.call(READ,shell);
  assert.deepEqual(f.executed,backgroundEnabled?['screenshot:legacy_read']:['pushed:screenshot']);
 });

 test('missing or invalid credentials stop at the authorize boundary, with nothing queued (background '+backgroundEnabled+')',async()=>{
  const f=fixture({backgroundEnabled});
  for(const req of [request(),request({token:'wrong'}),request({cert:'stranger'}),request({cert:'stranger',token:'wrong'}),
   request({token:'host-token',address:'203.0.113.9'})])
   for(const data of [MUTATION,READ])assert.equal((await f.call(data,req)).status,401,JSON.stringify(req.headers)+' '+req.socket.remoteAddress);
  assert.deepEqual(f.executed,[]);assert.deepEqual(f.completed,[]);
 });
}

test('a Stop between queueing and dispatch reaches an unpaired key\'s read but not the paired phone\'s direct tool',async()=>{
 const f=fixture({backgroundEnabled:true});
 const task=f.newTask();
 // Both are queued (201) before the owner's Stop; the dispatch runs after it.
 assert.equal((await f.send(READ,request({cert:'stranger',token:'host-token'}))).status,201);
 assert.equal((await f.send(MUTATION,request({cert:'pixel'}))).status,201);
 await f.stopTask(task);
 await f.flush();await f.flush();
 assert.deepEqual(f.executed,['media_control:non_task'],'the unpaired read never ran: '+JSON.stringify(f.executed));
 assert.equal(f.completed[0]?.status,'task_stopped',JSON.stringify(f.completed));
 // After Stop, a fresh unpaired-key read is refused before it is queued, as a token-only read is.
 const late=await f.call(READ,request({cert:'stranger',token:'host-token'}));
 assert.equal(late.body.result?.status,'task_stopped',JSON.stringify(late));
});

test('the explicit, expiring legacy path still authenticates, never as the paired phone, and is not widened',async()=>{
 const open=fixture({backgroundEnabled:true,legacyUntil:Date.now()+60000});
 const remote=request({token:'host-token',address:'203.0.113.9'});
 assert.ok(refused(await open.call(MUTATION,remote)),'a legacy caller gets no mutations');
 await open.call(READ,remote);
 assert.deepEqual(open.executed,['screenshot:legacy_read']);
 // The case the fix changes: during the window, an unpaired key sent with the legacy token is still only a legacy caller.
 const stranger=request({cert:'stranger',token:'host-token',address:'203.0.113.9'});
 assert.ok(refused(await open.call(MUTATION,stranger)),'an unpaired key adds no mutations in the legacy window');
 await open.call(READ,stranger);
 assert.deepEqual(open.executed,['screenshot:legacy_read','screenshot:legacy_read'],'never NON_TASK');
 const expired=fixture({backgroundEnabled:true,legacyUntil:Date.now()-1});
 assert.equal((await expired.call(READ,remote)).status,401);
 assert.equal((await expired.call(READ,request({cert:'stranger',token:'host-token',address:'203.0.113.9'}))).status,401,'an unpaired key adds nothing remotely');
 assert.deepEqual(readState(expired.priv+'/devices.json').devices.map(d=>d.id),[keyId('pixel')],'nothing was enrolled');
});

test('only the enrolled, unrevoked configured phone is the target phone that receives and completes commands',async()=>{
 const live=fixture({backgroundEnabled:true});
 assert.equal(live.target(request({cert:'pixel'})),true,'the paired configured phone');
 assert.equal(live.target(request({cert:'stranger',token:'host-token'})),false,'another key');
 assert.equal(live.target(request({token:'host-token'})),false,'the token alone');
 assert.equal((await live.finish('c1',{status:'ok'},request({cert:'pixel'}))).status,200);
 const revoked=fixture({backgroundEnabled:true,revoked:true});
 const key=request({cert:'pixel',token:'host-token'});
 assert.equal(revoked.target(key),false,'a revoked phone key, even with the token, is no longer the target');
 const r=await revoked.finish('c1',{status:'ok',forged:true},key);
 assert.equal(r.status,403,JSON.stringify(r));
 assert.deepEqual(revoked.completed,[],'its result was not accepted');
 // Background control off: any authorized caller is the target, as before (the existing foreground design).
 const off=fixture({backgroundEnabled:false,revoked:true});
 assert.equal(off.target(request({token:'host-token'})),true);
});

test('server.mjs classifies /phone/commands once, by pairing, behind the authorize boundary',()=>{
 const handler=server.slice(server.indexOf('const server=https.createServer('));
 const boundary=handler.indexOf("if(!authorized(req))return answer(res,401,{error:'Pairing required'});");
 assert.ok(boundary>0&&boundary<handler.indexOf("if(url.pathname==='/phone/commands'){"),'the route sits behind the outer boundary');
 assert.match(server,/function pairedPhone\(req\)\{return deviceAuth\.paired\(req\)\?peerIdentity\(req\):null;\}/);
 assert.match(server,/function phoneCommandRejection\(paired,data\)\{\n if\(paired\)return null;/);
 const commands=route('/phone/commands');
 assert.match(commands,/const paired=!!pairedPhone\(req\);const stopped=phoneCommandRejection\(paired,data\);/);
 assert.match(commands,/await \(paired\?nonTaskPhone\(data,true\):legacyReadPhone\(data,true\)\)/);
 assert.doesNotMatch(commands,/peerIdentity\(req\)/,'no second, weaker classification in the route');
 assert.equal([...server.matchAll(/phoneCommandRejection\(/g)].length,2,'one definition, one call');
 assert.match(server,/function targetPhone\(req\)\{return !backgroundEnabled\|\|pairedPhone\(req\)\?\.id===backgroundConfig\.deviceId;\}/);
});
