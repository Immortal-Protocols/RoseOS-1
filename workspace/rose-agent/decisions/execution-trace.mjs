import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

// Private, bounded diagnostic evidence. No headers/credentials and no additional
// model requests or phone commands. Images are deduplicated and stored locally.
export class ExecutionTrace {
 constructor(root,metadata,{now=Date.now,maxAgeMs=86400000,maxRuns=20,maxBytes=256*1024*1024,maxRunBytes=48*1024*1024}={}){
  this.now=now;this.seq=0;this.bytes=0;this.maxRunBytes=maxRunBytes;
  try{
   fs.mkdirSync(root,{recursive:true,mode:0o700});
   const runs=fs.readdirSync(root).filter(n=>/^run-[\d]+-[a-f0-9-]+$/.test(n)).map(n=>{
    const dir=path.join(root,n),files=fs.readdirSync(dir);return {dir,time:Number(n.split('-')[1]),bytes:files.reduce((s,f)=>s+fs.statSync(path.join(dir,f)).size,0)};
   }).sort((a,b)=>b.time-a.time);
   let kept=0,bytes=0;
   for(const run of runs){bytes+=run.bytes;if(now()-run.time>maxAgeMs||++kept>=maxRuns||bytes>maxBytes-maxRunBytes)fs.rmSync(run.dir,{recursive:true,force:true});}
   this.id='run-'+now()+'-'+crypto.randomUUID();this.dir=path.join(root,this.id);fs.mkdirSync(this.dir,{mode:0o700});
   this.write('run_start',metadata);
  }catch{this.disabled=true;}
 }
 image(data,mime='image/jpeg'){
  const buffer=Buffer.from(data,'base64'),file=crypto.createHash('sha256').update(buffer).digest('hex')+(mime==='image/png'?'.png':'.jpg');
  const target=path.join(this.dir,file);
  if(!fs.existsSync(target)){this.reserve(buffer.length);fs.writeFileSync(target,buffer,{mode:0o600});}
  return {$traceImage:file,mimeType:mime};
 }
 reserve(bytes){if(this.bytes+bytes>this.maxRunBytes)throw Error('trace size limit');this.bytes+=bytes;}
 encode(value,key=''){
  if(typeof value==='string'){
   const match=value.match(/^data:(image\/(?:jpeg|png|webp));base64,(.+)$/s);
   if(match)return {...this.image(match[2],match[1]),encoding:'data_url'};
   if(key==='imageBase64')return {...this.image(value),encoding:'base64'};
   return value;
  }
  if(Array.isArray(value))return value.map(v=>this.encode(v));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,this.encode(v,k)]));
  return value;
 }
 write(kind,data){
  if(this.disabled)return;
  try{const line=JSON.stringify({seq:++this.seq,at:this.now(),kind,data:this.encode(data)})+'\n';this.reserve(Buffer.byteLength(line));fs.appendFileSync(path.join(this.dir,'events.jsonl'),line,{mode:0o600});}
  catch{this.disabled=true;try{fs.appendFileSync(path.join(this.dir,'events.jsonl'),JSON.stringify({kind:'trace_incomplete',at:this.now()})+'\n',{mode:0o600});}catch{}}
 }
 client(client){
  const wrap=name=>({create:async(body,options)=>{
   const callId=crypto.randomUUID();this.write('model_request',{callId,api:name,body});
   try{const result=await client[name].create(body,options);this.write('model_response',{callId,api:name,result:name==='responses'?{id:result.id,status:result.status,output_text:result.output_text,usage:result.usage}:result});return result;}
   catch(error){this.write('model_error',{callId,api:name,message:error.message});throw error;}
  }});
  return {decisions:wrap('decisions'),responses:wrap('responses')};
 }
 phone(phone){return async command=>{
  const callId=crypto.randomUUID();this.write('phone_request',{callId,command});
  try{const result=await phone(command);this.write('phone_result',{callId,result});return result;}
  catch(error){this.write('phone_error',{callId,message:error.message});throw error;}
 };}
}

export function hydrateTrace(value,dir){
 if(Array.isArray(value))return value.map(v=>hydrateTrace(v,dir));
 if(value&&typeof value==='object'){
  if(value.$traceImage){if(!/^[a-f0-9]{64}\.(jpg|png)$/.test(value.$traceImage))throw Error('Invalid trace image');const data=fs.readFileSync(path.join(dir,value.$traceImage)).toString('base64');return value.encoding==='data_url'?'data:'+value.mimeType+';base64,'+data:data;}
  return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,hydrateTrace(v,dir)]));
 }
 return value;
}
