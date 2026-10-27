import {parentPort,workerData} from 'node:worker_threads';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const {directory,versionFiles=[],maxBytes}=workerData;
for(const name of ['', 'streams','assets','cases'])fs.mkdirSync(path.join(directory,name),{recursive:true,mode:0o700});
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const status={enabled:true,ready:true,events:0,bytes:0,dropped:0,startedAt:new Date().toISOString(),error:null};
function size(dir){return fs.readdirSync(dir,{withFileTypes:true}).reduce((n,e)=>n+(e.isDirectory()?size(path.join(dir,e.name)):fs.statSync(path.join(dir,e.name)).size),0);}
status.bytes=size(directory);
let chain=Promise.resolve(),transcript=null,currentCase=null,seq=0;
const secretKey=/^(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret|pair[_-]?token)$/i;
function clean(text){return text.replace(/\bBearer\s+[^\s"'<>]+/gi,'Bearer [redacted]').replace(/\bsk-[A-Za-z0-9_-]{12,}/g,'[redacted key]').replace(/([?&](?:token|access_token|api_key|secret|signature)=)[^&\s"'<>]+/gi,'$1[redacted]');}
function reserve(n){if(status.bytes+n>maxBytes){status.error='Storage limit reached; collection incomplete';throw Error(status.error);}status.bytes+=n;}
function asset(bytes,mime,source){
 if(bytes.length>4*1024**2)return {unavailable:'Image exceeds capture limit'};
 const sha256=hash(bytes),extension=mime==='image/png'?'png':mime==='image/webp'?'webp':'jpg';
 const relative=`assets/${sha256}.${extension}`,file=path.join(directory,relative);
 if(!fs.existsSync(file)){reserve(bytes.length);fs.writeFileSync(file,bytes,{mode:0o600,flag:'wx'});}
 return {artifact:relative,sha256,mimeType:mime,bytes:bytes.length,...(source?{sourcePath:source}:{})};
}
function encode(value,key='',depth=0){
 if(depth>40)return {unavailable:'Evidence nesting limit'};
 if(secretKey.test(key))return '[redacted]';
 if(typeof value==='string'){
  const m=value.match(/^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)$/);
  if(m)return asset(Buffer.from(m[2],'base64'),m[1]);
  if(key==='imageBase64')return asset(Buffer.from(value,'base64'),'image/jpeg');
  if((key==='imagePath'||key==='path')&&/\.(jpg|jpeg|png|webp)$/.test(value)&&fs.existsSync(value)){
   try{return asset(fs.readFileSync(value),value.endsWith('.png')?'image/png':value.endsWith('.webp')?'image/webp':'image/jpeg',value);}catch{return {unavailable:'Referenced image unavailable',sourcePath:value};}
  }
  return clean(value);
 }
 if(Array.isArray(value))return value.map(v=>encode(v,'',depth+1));
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,encode(v,k,depth+1)]));
 return value;
}
function write(event){
 const row={schemaVersion:1,recordId:crypto.randomUUID(),sequence:++seq,...event,data:encode(event.data)};
 const text=JSON.stringify(row)+'\n';reserve(Buffer.byteLength(text));
 fs.appendFileSync(path.join(directory,'streams',row.at.slice(0,10)+'.jsonl'),text,{mode:0o600});status.events++;
 if(row.kind==='request.started'&&row.caseId){
  currentCase=row.caseId;
  const manifest={schemaVersion:1,caseId:row.caseId,startedAt:row.at,request:row.data.request,source:row.data.source,sessionId:row.sessionId||null,state:'collecting',outcome:'unassessed',...(row.data.observationBatching?{observationBatching:row.data.observationBatching}:{}),evidence:{stream:'streams/'+row.at.slice(0,10)+'.jsonl',caseId:row.caseId},limitations:['No resettable device snapshot','Only observations made by the normal task flow','No raw voice audio','Total billed cost has not been reconstructed']};
  fs.writeFileSync(path.join(directory,'cases',row.caseId+'.json'),JSON.stringify(manifest,null,2),{mode:0o600});
 }
 if(row.kind==='request.finished'&&row.caseId){
  const file=path.join(directory,'cases',row.caseId+'.json');
  if(fs.existsSync(file)){const m=JSON.parse(fs.readFileSync(file));Object.assign(m,{endedAt:row.at,state:'recorded',workerStatus:row.data.status});fs.writeFileSync(file,JSON.stringify(m,null,2),{mode:0o600});}
 }
}
function publish(){
 status.updatedAt=new Date().toISOString();
 const temp=path.join(directory,'status.json.tmp');fs.writeFileSync(temp,JSON.stringify(status,null,2),{mode:0o600});fs.renameSync(temp,path.join(directory,'status.json'));parentPort.postMessage({type:'status',status});
}
function safeWrite(event){try{write(event);}catch{status.dropped++;status.error||='An evidence write failed';}}
function tail(){
 if(!transcript)return;
 try{
  const stat=fs.statSync(transcript.file);
  if(stat.size<transcript.offset){safeWrite({at:new Date().toISOString(),kind:'capture.gap',data:{reason:'Worker transcript truncated'}});transcript.offset=0;transcript.partial='';}
  const length=Math.min(stat.size-transcript.offset,8*1024**2);if(length<=0)return;
  const buffer=Buffer.alloc(length),fd=fs.openSync(transcript.file,'r');try{fs.readSync(fd,buffer,0,length,transcript.offset);}finally{fs.closeSync(fd);}
  transcript.offset+=length;const lines=(transcript.partial+buffer.toString()).split('\n');transcript.partial=lines.pop();
  for(const line of lines){
   let r;try{r=JSON.parse(line);}catch{continue;}
   const p=r.payload||{};
   if(r.type==='session_meta'||r.type==='turn_context'||r.type==='response_item'&&p.type!=='reasoning'||r.type==='event_msg'&&['task_started','task_complete','token_count','turn_aborted'].includes(p.type)){
    safeWrite({at:r.timestamp||new Date().toISOString(),kind:'worker.'+r.type,caseId:currentCase,data:r,source:{file:transcript.file}});
   }
  }
 }catch{status.error='Worker transcript temporarily unavailable';}
}
parentPort.on('message',message=>{
 chain=chain.then(()=>{
  if(message.type==='event'){
   if(message.dropped>status.dropped){const count=message.dropped-status.dropped;status.dropped=message.dropped;safeWrite({at:new Date().toISOString(),kind:'capture.gap',data:{reason:'Capture queue overflow',count}});}
   // Drain preceding transcript lines before changing the current case.
   if(message.event.kind==='request.started')tail();
   safeWrite(message.event);parentPort.postMessage({type:'ack'});
  }else if(message.type==='transcript')transcript={file:message.file,offset:message.offset,partial:''};
  else if(message.type==='flush'){tail();publish();parentPort.postMessage({type:'flushed',id:message.id,status});}
 }).catch(()=>{status.error='Capture processing error';});
});
const versions=versionFiles.map(file=>{try{const b=fs.readFileSync(file);return {file,sha256:hash(b),bytes:b.length};}catch{return {file,unavailable:true};}});
safeWrite({at:new Date().toISOString(),kind:'capture.started',data:{versionFiles:versions,versionMeaning:'Files on collector startup, not historical versions',maxBytes}});
setInterval(()=>{chain=chain.then(()=>{tail();publish();}).catch(()=>{});},1000);
publish();
