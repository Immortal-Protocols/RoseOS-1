import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import http from 'node:http';import https from 'node:https';import crypto from 'node:crypto';import {spawn} from 'node:child_process';
import {ClipboardStore} from './store.mjs';
import {DeviceAuth} from '../rose-agent/security/device-auth.mjs';
const deviceAuth=new DeviceAuth(os.homedir()+'/.config/pixel-voice/paired-devices.json');
const base=path.join(os.homedir(),'.config/pixel-clipboard');
const config=()=>JSON.parse(fs.readFileSync(base+'/config.json','utf8'));
const store=new ClipboardStore(),clients=new Set();let sendRequests=0,rejectedSendRequests=0;
const auth=(req,key)=>{const expected='Bearer '+key,given=req.headers.authorization||'';return given.length===expected.length&&crypto.timingSafeEqual(Buffer.from(given),Buffer.from(expected));};
const reply=(res,status,obj)=>{res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(obj));};
const send=(res,item)=>{if(res.destroyed||res.writableEnded)return;if(res.writableLength>131072){res.destroy();return;}res.write('data: '+JSON.stringify({type:'clipboard',...item})+'\n\n');};
store.on('clipboard',item=>{for(const res of clients)send(res,item);});
async function body(req){let text='';for await(const chunk of req){text+=chunk;if(Buffer.byteLength(text)>70000)throw Error('Request too large');}return JSON.parse(text);}
async function handler(req,res){
 try{
  const cfg=config();const route=new URL(req.url,'https://localhost').pathname;
  if(req.method==='GET'&&route==='/setup/'+cfg.setupToken){
   const file=base+'/Send to Pixel.shortcut';if(!fs.existsSync(file))return reply(res,404,{error:'Shortcut not ready'});
   res.writeHead(200,{'content-type':'application/octet-stream','content-disposition':'attachment; filename="Send to Pixel.shortcut"','cache-control':'no-store'});fs.createReadStream(file).pipe(res);return;
  }
  if(req.method==='POST'&&route==='/send'){
   sendRequests++;if(!auth(req,cfg.sendToken)){rejectedSendRequests++;return reply(res,401,{error:'Unauthorized'});}
   if(!cfg.enabled)return reply(res,503,{error:'Clipboard transfer is off'});
   const data=await body(req),transfer=store.offer(data.text),result=await transfer.completion;
   return reply(res,result.status==='copied'?200:503,{status:result.status});
  }
  if(req.method==='GET'&&route==='/receive'){
   if(!deviceAuth.authorizeSecret(req,cfg.receiveToken))return reply(res,401,{error:'Unauthorized'});
   if(!cfg.enabled)return reply(res,503,{error:'Clipboard transfer is off'});
   for(const old of clients)old.end();
   res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-store','connection':'keep-alive'});res.flushHeaders();clients.add(res);
   res.write(': ready\n\n');const item=store.pending();if(item)send(res,item);
   const interval=setInterval(()=>{if(!config().enabled){store.clear();res.end();}else if(res.writableLength>131072)res.destroy();else res.write(': keepalive\n\n');},5000);interval.unref();
   res.on('close',()=>{clearInterval(interval);clients.delete(res);});return;
  }
  if(req.method==='POST'&&route==='/receipt'){
   if(!deviceAuth.authorizeSecret(req,cfg.receiveToken))return reply(res,401,{error:'Unauthorized'});
   const data=await body(req);return reply(res,200,{accepted:store.acknowledge(data.id,data.status)});
  }
  if(req.method==='GET'&&route==='/status'){
   if(!deviceAuth.authorizeSecret(req,cfg.receiveToken))return reply(res,401,{error:'Unauthorized'});
   return reply(res,200,{enabled:cfg.enabled,receiverConnected:clients.size>0,pending:!!store.pending(),last:store.last,sendRequests,rejectedSendRequests});
  }
  reply(res,404,{error:'Not found'});
 }catch{if(!res.headersSent)reply(res,400,{error:'Invalid transfer request'});else res.end();}
}
// Only ACME token responses are served over HTTP; never clipboard or credentials.
const challenge=http.createServer((req,res)=>{
 const match=req.url?.match(/^\/\.well-known\/acme-challenge\/([A-Za-z0-9_-]+)$/);
 if(!match||req.method!=='GET'){res.writeHead(404);res.end();return;}
 const file=base+'/webroot/.well-known/acme-challenge/'+match[1];
 try{res.writeHead(200,{'content-type':'text/plain'});res.end(fs.readFileSync(file));}catch{res.writeHead(404);res.end();}
});challenge.listen(18080,'0.0.0.0');
const certDir=base+'/certificates/live/clipboard';
const tls=()=>({requestCert:true,rejectUnauthorized:false,cert:fs.readFileSync(certDir+'/fullchain.pem'),key:fs.readFileSync(certDir+'/privkey.pem'),maxVersion:'TLSv1.2'});
const server=https.createServer(tls(),handler);server.listen(config().port,'0.0.0.0',()=>console.log('Clipboard relay ready'));
let certificate=fs.readFileSync(certDir+'/fullchain.pem','utf8');
setInterval(()=>{try{const next=fs.readFileSync(certDir+'/fullchain.pem','utf8');if(next!==certificate){server.setSecureContext(tls());certificate=next;console.log('Clipboard certificate renewed');}}catch{}},60000).unref();
let renewing=false;
function renew(){
 if(renewing)return;renewing=true;
 const child=spawn(base+'/venv/bin/certbot',['renew','--quiet','--config-dir',base+'/certificates','--work-dir',base+'/work','--logs-dir',base+'/logs'],{stdio:'ignore'});
 child.on('error',()=>{renewing=false;console.log('Clipboard certificate renewal unavailable');});child.on('close',code=>{renewing=false;if(code)console.log('Clipboard certificate renewal failed');});
}
setInterval(renew,6*60*60*1000).unref();setTimeout(renew,5000).unref();
process.on('SIGTERM',()=>{store.clear();for(const res of clients)res.end();server.close();challenge.close();process.exit();});
