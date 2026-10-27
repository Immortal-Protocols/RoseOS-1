// Independent QA full-process startup check (Chief assignment CHIEF-STAGING-001-QA).
// Runs the REAL workspace/rose-agent/server/server.mjs as a child process with the registered
// app-tool catalog enabled for Calculator, in an isolated temporary config directory:
//  - dummy non-secret key files, a throwaway self-signed TLS cert (generated locally by openssl);
//  - app-tools-trial.json built from Calculator's own src/main/assets/agent-tools.json;
//  - PIXEL_CODEX_BIN -> fake-codex.mjs (local stdio JSON-RPC stand-in, no model/network);
//  - net-guard.cjs preload refusing and logging every non-loopback connection or DNS lookup.
// Asserts: PIXEL_VOICE_READY; Calculator tools registered with the worker (thread/start
// dynamicTools); TLS /health on loopback answers 401 (auth gate); zero non-loopback attempts;
// SIGTERM shutdown. Limits: no real Codex, provider, phone, Android or Pixel 10 deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import https from 'node:https';
import net from 'node:net';
import crypto from 'node:crypto';
import {spawn,execFileSync} from 'node:child_process';

const here=path.dirname(new URL(import.meta.url).pathname);
const root=path.resolve(here,'../../..');
const server=path.join(root,'workspace/rose-agent/server/server.mjs');
const calcTools=path.join(root,'workspace/pixel-calculator-agent/src/main/assets/agent-tools.json');
const freePort=()=>new Promise(r=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});

async function fixture(){
 const cfg=fs.mkdtempSync(path.join(os.tmpdir(),'qa-startup-cfg-')),home=fs.mkdtempSync(path.join(os.tmpdir(),'qa-startup-home-'));
 for(const f of ['openai-api-key','openai-podcast-api-key','podcast-recommendation-token'])fs.writeFileSync(path.join(cfg,f),'qa-dummy-not-a-secret\n',{mode:0o600});
 execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',path.join(cfg,'server-key.pem'),'-out',path.join(cfg,'server-cert.pem'),'-days','1','-subj','/CN=localhost'],{stdio:'ignore'});
 // Throwaway local pairing token (random, never a real credential); the auth gate reads it per request.
 fs.writeFileSync(path.join(cfg,'pair-token'),crypto.randomBytes(24).toString('hex')+'\n',{mode:0o600});
 fs.writeFileSync(path.join(cfg,'guardian-voice.json'),JSON.stringify({port:await freePort()}));
 const tools=JSON.parse(fs.readFileSync(calcTools,'utf8'));
 fs.writeFileSync(path.join(cfg,'app-tools-trial.json'),JSON.stringify({enabled:true,packages:['dev.pixel.calculator.agent'],catalogs:{'dev.pixel.calculator.agent':{status:'ok',tools}}}));
 return {cfg,home,tools};
}

function start(f,port){
 const env={PATH:process.env.PATH,HOME:f.home,PIXEL_VOICE_CONFIG_DIR:f.cfg,PIXEL_LISTEN_HOST:'127.0.0.1',PIXEL_LISTEN_PORT:String(port),
  PIXEL_CODEX_BIN:path.join(here,'fake-codex.mjs'),QA_NET_LOG:path.join(f.cfg,'qa-net.log'),QA_CODEX_LOG:path.join(f.cfg,'qa-codex.log')};
 const child=spawn(process.execPath,['--require',path.join(here,'net-guard.cjs'),server],{cwd:root,env,stdio:['ignore','pipe','pipe']});
 let out='';child.stdout.on('data',d=>out+=d);child.stderr.on('data',d=>out+=d);
 const ready=new Promise((resolve,reject)=>{
  const t=setTimeout(()=>reject(Error('no PIXEL_VOICE_READY within 30 s:\n'+out.slice(-2000))),30000);
  child.stdout.on('data',()=>{if(out.includes('PIXEL_VOICE_READY '+port)){clearTimeout(t);resolve();}});
  child.on('exit',code=>{clearTimeout(t);reject(Error('server exited '+code+' before ready:\n'+out.slice(-2000)));});
 });
 return {child,ready,output:()=>out};
}

const health=port=>new Promise((resolve,reject)=>{https.get({host:'127.0.0.1',port,path:'/health',rejectUnauthorized:false},r=>{r.resume();resolve(r.statusCode);}).on('error',reject);});

test('full server start with Calculator app tools enabled reaches READY, registers Calculator tools, gates /health, makes no external connections, and shuts down',async()=>{
 const f=await fixture();const port=await freePort();const s=start(f,port);
 try{
  await s.ready;
  const lines=fs.readFileSync(path.join(f.cfg,'qa-codex.log'),'utf8').trim().split('\n').map(l=>JSON.parse(l));
  const threadStart=lines.find(l=>l.method==='thread/start');
  assert.ok(threadStart,'worker thread/start was not sent');
  const names=(threadStart.params.dynamicTools||[]).map(t=>t.name);
  for(const t of f.tools)assert.ok(names.includes(t.name),'Calculator tool not registered: '+t.name);
  assert.equal(await health(port),401,'unauthenticated /health must be refused');
  const netLog=path.join(f.cfg,'qa-net.log');
  assert.equal(fs.existsSync(netLog)?fs.readFileSync(netLog,'utf8').trim():'','','non-loopback connection attempted');
 }finally{
  const exited=s.child.exitCode!==null||s.child.signalCode!==null?Promise.resolve({code:s.child.exitCode,signal:s.child.signalCode}):new Promise(r=>s.child.once('exit',(code,signal)=>r({code,signal})));
  s.child.kill('SIGTERM');
  const result=await Promise.race([exited,new Promise(r=>setTimeout(()=>r(null),8000))]);
  if(!result)s.child.kill('SIGKILL');
  assert.ok(result,'server did not exit within 8 s of SIGTERM');
  fs.rmSync(f.cfg,{recursive:true,force:true});fs.rmSync(f.home,{recursive:true,force:true});
 }
});
