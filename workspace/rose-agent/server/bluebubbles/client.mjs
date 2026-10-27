import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import https from 'node:https';
import tls from 'node:tls';
import {execFileSync} from 'node:child_process';

// Reuse the installed official server and its existing credential. The password
// never enters a model prompt, copied configuration, command argument, or log.
export function createBlueBubblesClient({configFile=path.join(os.homedir(),'.config/bluebubbles-integration/config.json')}={}){
 async function request(route,{body,timeoutMs=15000,binary=false}={}){
  const cfg=JSON.parse(fs.readFileSync(configFile,'utf8'));
  let password=execFileSync('/usr/bin/sqlite3',['-readonly',cfg.databasePath,"SELECT value FROM config WHERE name='password'"],{encoding:'utf8'}).trim();
  try{password=JSON.parse(password);}catch{}
  if(typeof password!=='string'||!password)throw Error('BlueBubbles server credential is unavailable');
  if(!route.startsWith('/api/v1/'))throw Error('Invalid BlueBubbles route');
  const url=new URL('https://localhost'+route);url.searchParams.set('password',password);
  return new Promise((resolve,reject)=>{
   const req=https.request({hostname:'127.0.0.1',port:cfg.internalPort,path:url.pathname+url.search,method:body===undefined?'GET':'POST',checkServerIdentity:(_host,cert)=>tls.checkServerIdentity(cfg.publicAddress,cert),headers:{'content-type':'application/json'}},res=>{
    const chunks=[];let bytes=0;res.on('data',chunk=>{bytes+=chunk.length;if(bytes>(binary?25:8)*1024*1024)req.destroy(Error('BlueBubbles response exceeds size limit'));else chunks.push(chunk);});
    res.on('end',()=>{if(res.statusCode<200||res.statusCode>=300)return reject(Error('BlueBubbles API returned HTTP '+res.statusCode));const data=Buffer.concat(chunks);if(binary)return resolve({data,mimeType:String(res.headers['content-type']||'application/octet-stream').split(';')[0]});try{const value=JSON.parse(data.toString('utf8'));if(value.status!==200)throw Error('BlueBubbles did not confirm the request');resolve(value);}catch{reject(Error('BlueBubbles returned an invalid response'));}});
   });
   req.on('error',()=>reject(Error('BlueBubbles connection failed; operation was not confirmed')));
   req.setTimeout(timeoutMs,()=>req.destroy());req.end(body===undefined?undefined:JSON.stringify(body));
  });
 }
 return {
  contacts:()=>request('/api/v1/contact'),
  // Names/participants are enough to resolve a chat. Loading the last message
  // for every historical chat makes a simple recipient lookup needlessly slow.
  chats:(offset=0,limit=1000)=>request('/api/v1/chat/query',{body:{offset,limit,with:['participants']}}),
  recentMessages:(limit=200,offset=0)=>request('/api/v1/message/query',{body:{limit,offset,sort:'DESC',with:['chats','chat.participants','attributedbody']}}),
  chat:guid=>request('/api/v1/chat/'+encodeURIComponent(guid)+'?with=participants,lastmessage'),
  messages:(guid,limit=10,before)=>request('/api/v1/chat/'+encodeURIComponent(guid)+'/message?'+new URLSearchParams({limit:String(limit),sort:'DESC',with:'attachments,message.attributedBody',...(before?{before:String(before)}:{})})),
  message:guid=>request('/api/v1/message/'+encodeURIComponent(guid)+'?with=chats,attributedBody'),
  downloadAttachment:(guid,{image=false}={})=>request('/api/v1/attachment/'+encodeURIComponent(guid)+'/download?force=false&original='+(!image)+(image?'&width=1600&quality=better':''),{binary:true,timeoutMs:25000}),
  send:(chatGuid,text,tempGuid)=>request('/api/v1/message/text',{body:{chatGuid,message:text,tempGuid,method:'apple-script'},timeoutMs:65000})
 };
}
