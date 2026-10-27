import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';

const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const redact=s=>s.replace(/\bBearer\s+[^\s"'<>]+/gi,'Bearer [redacted]').replace(/\bsk-[A-Za-z0-9_-]{12,}/g,'[redacted key]');
const secret=/^(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret|pair[_-]?token)$/i;
const iso=value=>typeof value==='number'?new Date(value).toISOString():value;

// Pure offline export. Reads supplied logs, never calls the phone or a model.
export function exportCase(spec,output){
 if(!/^[a-zA-Z0-9_-]{1,100}$/.test(spec.caseId))throw Error('Invalid case name');
 const start=Date.parse(spec.start),end=Date.parse(spec.end);
 if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)throw Error('Invalid capture window');
 if(fs.existsSync(output))throw Error('Case destination already exists');
 fs.mkdirSync(output,{recursive:true,mode:0o700});fs.mkdirSync(path.join(output,'assets'),{mode:0o700});
 const artifacts=new Map(),references=new Set(),provenance=[],events=[],usage=[];
 let order=0;
 const addAsset=(bytes,mime,source)=>{
  const hash=sha(bytes),ext=mime==='image/png'?'png':mime==='image/webp'?'webp':'jpg';
  const relative=`assets/${hash}.${ext}`;
  if(!artifacts.has(hash)){fs.writeFileSync(path.join(output,relative),bytes,{mode:0o600,flag:'wx'});artifacts.set(hash,{path:relative,sha256:hash,bytes:bytes.length,mimeType:mime,sources:[]});}
  if(source&&!artifacts.get(hash).sources.includes(source))artifacts.get(hash).sources.push(source);
  return {artifact:relative,sha256:hash,mimeType:mime};
 };
 const encode=(v,key='')=>{
  if(secret.test(key))return '[redacted]';
  if(typeof v==='string'){
   const m=v.match(/^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\r\n]+)$/);
   if(m)return addAsset(Buffer.from(m[2],'base64'),m[1],'embedded worker image');
   if(key==='imageBase64')return addAsset(Buffer.from(v,'base64'),'image/jpeg','embedded phone result');
   for(const m of v.matchAll(/\/[^\s"'<>\\]+\.(?:jpg|jpeg|png|webp)/g))references.add(m[0]);
   return redact(v);
  }
  if(Array.isArray(v))return v.map(x=>encode(x));
  if(v&&typeof v==='object')return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,encode(x,k)]));
  return v;
 };
 function add(source,at,data){
  const t=Date.parse(iso(at));if(!Number.isFinite(t)||t<start||t>=end)return;
  events.push({schemaVersion:1,caseId:spec.caseId,at:new Date(t).toISOString(),source,order:order++,data:encode(data)});
 }
 for(const source of spec.sources){
  const bytes=fs.readFileSync(source.file);provenance.push({kind:source.kind,file:source.file,sha256:sha(bytes),bytes:bytes.length});
  if(source.kind==='voice'){
   for(const session of JSON.parse(bytes))for(const m of session.messages||[])add('voice',m.at,{sessionId:session.id,...m});
   continue;
  }
  let lineNumber=0;
  for(const line of bytes.toString().split('\n')){
   lineNumber++;if(!line.trim())continue;let r;try{r=JSON.parse(line);}catch{continue;}
   if(source.kind==='codex'){
    if(!(r.type==='turn_context'||r.type==='response_item'&&r.payload?.type!=='reasoning'||r.type==='event_msg'&&['task_started','task_complete','token_count','turn_aborted'].includes(r.payload?.type)))continue;
    if(r.payload?.type==='token_count'&&Date.parse(r.timestamp)>=start&&Date.parse(r.timestamp)<end)usage.push({at:r.timestamp,sourceLine:lineNumber,info:r.payload.info});
   }
   add(source.kind,r.timestamp??r.at??r.wallMs??r.createdAt,{sourceLine:lineNumber,...r});
  }
 }
 const unavailable=[];
 for(const filename of references){
  if(!(spec.imageRoots||[]).some(root=>path.resolve(filename).startsWith(path.resolve(root)+path.sep)))continue;
  try{addAsset(fs.readFileSync(filename),filename.endsWith('.png')?'image/png':filename.endsWith('.webp')?'image/webp':'image/jpeg',filename);}catch{unavailable.push(filename);}
 }
 events.sort((a,b)=>a.at.localeCompare(b.at)||a.order-b.order);
 const eventText=events.map(e=>JSON.stringify(e)).join('\n')+'\n';
 fs.writeFileSync(path.join(output,'events.jsonl'),eventText,{mode:0o600});
 const unique=new Map();
 for(const u of usage){const total=u.info?.total_token_usage;if(total)unique.set(JSON.stringify(total),u);}
 const tokenTotals={};
 for(const u of unique.values())for(const [k,v] of Object.entries(u.info?.last_token_usage||{}))if(Number.isFinite(v))tokenTotals[k]=(tokenTotals[k]||0)+v;
 const manifest={schemaVersion:1,caseId:spec.caseId,request:spec.request,window:{start:spec.start,end:spec.end},exportedAt:new Date().toISOString(),
  outcome:{status:'unassessed',reason:'Evidence bundle only; no investigator conclusion or proposed fix supplied'},
  replay:{ready:false,reason:'No restorable initial device/app snapshot was recorded'},
  evidence:{events:'events.jsonl',eventCount:events.length,sha256:sha(eventText),artifacts:[...artifacts.values()],unavailableReferencedImages:unavailable,provenance},
  usage:{scope:'Worker token events only; not total billed interaction cost',uniqueUpdates:unique.size,reportedLastUsageSum:tokenTotals,costUsd:null,notes:['Cached input is part of input, not additional input.','Reasoning output is part of output, not additional output.','Voice and other model calls may be absent; no complete cost inferred.']},
  gaps:spec.gaps||[],versionSnapshots:(spec.versionFiles||[]).map(file=>{const b=fs.readFileSync(file);return {file,sha256:sha(b),observedAt:new Date().toISOString(),historicalMatchVerified:false};})};
 fs.writeFileSync(path.join(output,'case.json'),JSON.stringify(manifest,null,2)+'\n',{mode:0o600});
 const imageLines=manifest.evidence.artifacts.map((a,i)=>`- [Recorded image ${i+1}](${a.path})`).join('\n');
 fs.writeFileSync(path.join(output,'README.md'),`# ${spec.caseId}\n\nRequest: ${spec.request}\n\nThis is an evidence-only case. No diagnosis, fix, or success grade has been supplied.\n\n- [Case manifest](case.json)\n- [Original event sequence](events.jsonl)\n${imageLines}\n\n## Limits\n\n${manifest.gaps.map(g=>'- '+g).join('\n')}\n\nA later investigator should form its own explanation from the evidence. This package does not contain the subsequent design discussion.\n`,{mode:0o600});
 return manifest;
}
export function validateCase(output){
 const m=JSON.parse(fs.readFileSync(path.join(output,'case.json'))),events=fs.readFileSync(path.join(output,m.evidence.events));
 if(sha(events)!==m.evidence.sha256)throw Error('Event integrity mismatch');
 const rows=events.toString().trim().split('\n').filter(Boolean).map(JSON.parse);
 if(rows.length!==m.evidence.eventCount)throw Error('Event count mismatch');
 for(const a of m.evidence.artifacts)if(sha(fs.readFileSync(path.join(output,a.path)))!==a.sha256)throw Error('Image integrity mismatch');
 if(m.outcome.status!=='unassessed')throw Error('Source case must not contain an inferred outcome');
 return {events:rows.length,images:m.evidence.artifacts.length};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){
 const [specFile,output]=process.argv.slice(2);if(!specFile||!output)throw Error('Usage: node export-case.mjs SPEC_JSON NEW_OUTPUT_DIRECTORY');
 exportCase(JSON.parse(fs.readFileSync(specFile)),output);console.log(JSON.stringify(validateCase(output)));
}
