// Isolated voice-only integration server. No production sessions, tasks, mail or installs.
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import OpenAI from '../server/node_modules/openai/index.mjs';
const dir=process.env.PIXEL_EMULATOR_DIR;
if(!dir)throw new Error('PIXEL_EMULATOR_DIR required');
const prod=path.join(process.env.HOME,'.config/pixel-voice');
const token=fs.readFileSync(path.join(dir,'pair-token'),'utf8').trim();
const client=new OpenAI({apiKey:fs.readFileSync(path.join(prod,'openai-api-key'),'utf8').trim(),maxRetries:0,timeout:25000});
let activeSession=null,lastClient=null,starting=false;
const epoch=crypto.randomUUID();
function answer(res,status,obj){res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(obj));}
https.createServer({key:fs.readFileSync(path.join(prod,'server-key.pem')),cert:fs.readFileSync(path.join(prod,'server-cert.pem'))},async(req,res)=>{
 try{
  const auth=req.headers.authorization||'',expected='Bearer '+token;
  if(auth.length!==expected.length||!crypto.timingSafeEqual(Buffer.from(auth),Buffer.from(expected)))return answer(res,401,{error:'Pairing required'});
  const url=new URL(req.url,'https://localhost');
  if(req.method==='GET'){
   if(url.pathname==='/health')return answer(res,200,{ready:true,busy:false,isolated:true,active:!!activeSession,lastClient});
   if(url.pathname==='/events')return answer(res,200,{events:[],ready:true,busy:false,epoch});
   if(url.pathname==='/apps')return answer(res,200,{apps:[]});
   if(url.pathname==='/phone/commands')return answer(res,200,{commands:[]});
   return answer(res,404,{error:'Not part of emulator test'});
  }
  let raw='',size=0;for await(const part of req){size+=part.length;if(size>180000)throw Error('Too large');raw+=part;}
  const data=JSON.parse(raw||'{}');
  if(req.method==='POST'&&url.pathname==='/session'){
   if(activeSession||starting)return answer(res,409,{error:'End the active voice session first'});
   if(typeof data.sdp!=='string'||data.sdp.length<30)return answer(res,400,{error:'Missing SDP'});
   starting=true;
   try{
    const result=await client.live.create({session:{model:'gpt-live-1',instructions:'You are an isolated emulator voice test. Reply briefly to greetings. You cannot access email, files, apps or tools. Never claim to have performed actions.',delegation:{type:'client'},audio:{output:{voice:'marin'}}},transport:{type:'webrtc',sdp:data.sdp}});
    activeSession=result.session.id;return answer(res,201,result);
   }finally{starting=false;}
  }
  if(req.method==='POST'&&url.pathname==='/voice-ended'){activeSession=null;return answer(res,200,{ok:true});}
  if(req.method==='POST'&&url.pathname==='/event'){
   if(data.type==='client.diagnostic')lastClient={at:Date.now(),ready:!!data.ready,muted:!!data.muted,prepared:!!data.prepared,connection:data.connection,channel:data.channel,tracks:data.tracks};
   return answer(res,200,{ok:true});
  }
  return answer(res,403,{error:'Disabled in isolated emulator test'});
 }catch(e){answer(res,500,{error:'Emulator test request failed'});console.error('EMULATOR_REQUEST_FAILED',e.status||'local');}
}).listen(19444,'127.0.0.1',()=>console.log('EMULATOR_VOICE_READY 19444'));
