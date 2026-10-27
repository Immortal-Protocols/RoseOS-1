import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {Transform} from 'node:stream';
import {pipeline} from 'node:stream/promises';

// Temporary, operator-opened receive window. Caller authenticates with the
// existing phone pairing token before reaching this handler. No model sees data.
export function migrationBackup({windowFile,root,maxBytes=64*1024**3}) {
 let uploading=false;
 return async function(req,res,url) {
  if(url.pathname!=='/migration-backup/upload')return false;
  const reply=(status,data)=>{res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(data));};
  if(req.method!=='POST'){reply(405,{error:'POST required'});return true;}
  let window;
  try{window=JSON.parse(fs.readFileSync(windowFile,'utf8'));}catch{}
  if(!window||!/^[-a-f0-9]{36}$/.test(window.id)||window.expiresAt<Date.now()){reply(403,{error:'Backup receive window is closed'});return true;}
  fs.mkdirSync(root,{recursive:true,mode:0o700});
  const dest=path.join(root,window.id+'.zip'),part=dest+'.part';
  if(uploading||fs.existsSync(dest)){reply(409,{error:'Backup already received or in progress'});return true;}
  uploading=true;let bytes=0;
  const hash=crypto.createHash('sha256');
  try {
   const guard=new Transform({transform(chunk,encoding,done){bytes+=chunk.length;if(bytes>maxBytes)return done(new Error('Backup exceeds receiver capacity'));hash.update(chunk);done(null,chunk);}});
   await pipeline(req,guard,fs.createWriteStream(part,{flags:'wx',mode:0o600}));
   const sha256=hash.digest('hex');
   const handle=await fs.promises.open(part,'r');try{await handle.sync();}finally{await handle.close();}
   fs.renameSync(part,dest);
   fs.writeFileSync(dest+'.json',JSON.stringify({id:window.id,receivedAt:new Date().toISOString(),bytes,sha256,scope:'Shared storage and Rose Agent private data; see in-archive manifest'},null,2),{mode:0o600});
   reply(200,{id:window.id,bytes,sha256});
  }catch(e){try{fs.unlinkSync(part);}catch{}if(!res.destroyed)reply(400,{error:e.message});}
  finally{uploading=false;}
  return true;
 };
}
