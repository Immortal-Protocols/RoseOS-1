import {execFile} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export function messagesBridge(privateDir, executable, testRunner=null){
 const journal=path.join(privateDir,'messages-send-journal.json');
 let sent={};try{sent=JSON.parse(fs.readFileSync(journal,'utf8'));}catch{}
 function persist(){fs.writeFileSync(journal,JSON.stringify(sent),{mode:0o600});}
 async function run(input){
  const exchange=path.join(privateDir,'messages-bridge');fs.mkdirSync(exchange,{recursive:true,mode:0o700});
  const id=crypto.randomUUID();const base=path.join(exchange,id);
  fs.writeFileSync(base+'.in',JSON.stringify(input),{mode:0o600});
  try{
   await new Promise((resolve,reject)=>execFile('/usr/bin/open',['-n','-g','-a',path.resolve(executable,'../../..'),'--args','--request-file',id],error=>error?reject(error):resolve()));
   const deadline=Date.now()+(input.action==='updates'?26000:20000);
   while(Date.now()<deadline){
    if(fs.existsSync(base+'.out'))return JSON.parse(fs.readFileSync(base+'.out','utf8'));
    await new Promise(resolve=>setTimeout(resolve,100));
   }
   throw new Error('Messages bridge timed out. Check the Mac for a permission prompt.');
  }finally{for(const suffix of ['.in','.out'])try{fs.unlinkSync(base+suffix);}catch{}}
 }
 function reply(res,code,data){res.writeHead(code,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(data));}
 return {
  async send(input){
   // Reuse the same validated, idempotent send boundary as the phone UI.
   return new Promise((resolve,reject)=>this.post(null,{writeHead(code){this.code=code;},end(raw){try{resolve(JSON.parse(raw));}catch(e){reject(e);}}},new URL('https://localhost/messages/send'),input).catch(reject));
  },
  async read(input){
   if(!input||!['status','chats','messages'].includes(input.action))throw Error('Read-only Messages action required');
   if(input.action==='messages'&&(!Number.isSafeInteger(input.chatId)||input.chatId<1))throw Error('A valid chat ID is required');
   return (testRunner||run)(input);
  },
  async get(req,res,url){
   if(!url.pathname.startsWith('/messages/'))return false;
   try{
    let input;
    if(url.pathname==='/messages/status')input={action:'status'};
    else if(url.pathname==='/messages/updates'){
     const raw=url.searchParams.get('cursor');
     if(raw!==null&&(!/^\d+$/.test(raw)||!Number.isSafeInteger(Number(raw)))){reply(res,400,{error:'Invalid message cursor'});return true;}
     input={action:'updates',...(raw===null?{}:{cursor:Number(raw)})};
    }
    else if(url.pathname==='/messages/chats')input={action:'chats'};
    else if(/^\/messages\/chats\/\d+$/.test(url.pathname))input={action:'messages',chatId:Number(url.pathname.split('/').pop())};
    else{reply(res,404,{error:'Unknown Messages route'});return true;}
    const result=await (testRunner||run)(input);reply(res,result.status==='error'?503:200,result);
   }catch(e){reply(res,503,{status:'error',error:e.message});}return true;
  },
  async post(req,res,url,data){
   if(url.pathname!=='/messages/send')return false;
   if(!Number.isSafeInteger(data.chatId)||data.chatId<1||typeof data.text!=='string'||!data.text.trim()||Buffer.byteLength(data.text)>16000||!/^[-a-f0-9]{36}$/.test(data.requestId||'')){reply(res,400,{error:'Choose a conversation and enter a message.'});return true;}
   const hash=crypto.createHash('sha256').update(JSON.stringify([data.chatId,data.text])).digest('hex');
   const prior=sent[data.requestId];
   if(prior){reply(res,prior.hash===hash?200:409,prior.hash===hash?prior.result:{error:'Request ID already used'});return true;}
   // Persist before the external send. Uncertain outcomes must never auto-resend.
   sent[data.requestId]={hash,result:{status:'uncertain',error:'This send was already attempted. Check Messages before retrying.'}};persist();
   try{const result=await (testRunner||run)({action:'send',chatId:data.chatId,text:data.text});sent[data.requestId]={hash,result};persist();reply(res,result.status==='error'?503:200,result);}
   catch(e){reply(res,503,{status:'uncertain',error:e.message+' Check the conversation before sending again.'});}
   return true;
  }
 };
}
