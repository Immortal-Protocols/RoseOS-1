import fs from 'node:fs';
import crypto from 'node:crypto';

const IMAGE_LIMIT=2000000, READ_LIMIT=8*1024*1024;
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
function clean(value){
 return String(value??'')
  .replace(/\bBearer\s+[^\s"'<>]+/gi,'Bearer [redacted]')
  .replace(/\bsk-[A-Za-z0-9_-]{12,}/g,'[redacted key]')
  .replace(/([?&](?:token|access_token|api_key|key|secret|signature)=)[^&\s"'<>]+/gi,'$1[redacted]')
  .replace(/((?:api[_-]?key|access[_-]?token|password|secret)\s*["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi,'$1[redacted]');
}
// Source labels are display metadata, not the evidence body. Never shorten the
// actual result: the decisive field may be anywhere in it.
function bounded(value,limit){
 const text=clean(value);return text.length<=limit?text:text.slice(0,Math.floor(limit*.75))+'\n[Evidence truncated]\n'+text.slice(-Math.floor(limit*.25));
}
function urls(text){return [...new Set((text.match(/https?:\/\/[^\s<>"'`\\)]+/g)||[]).map(clean))].slice(0,12);}

/** Keeps actual tool observations for ONE user task, including its review retries.
 * Never collects assistant prose as proof. The event stream exposes commands and
 * MCP results, but native web events can omit their bodies. In that case the
 * same tool's persisted response is read from this thread's append-only history;
 * no second fetch, model summary, or unrelated conversation is substituted.
 */
export class TaskEvidence {
 constructor({now=Date.now,fastTools=[]}={}){this.now=now;this.fastTools=new Set(fastTools);this.reset();}
 reset(transcriptPath=null,{threadId=null,since=this.now(),fromStart=false}={}){
  this.generation=(this.generation||0)+1;this.threadId=threadId;this.startedAt=since;this.fromStart=fromStart;this.children=new Map();
  this.records=[];this.images=[];this.calls=new Map();this.seen=new Set();this.requiresReview=false;this.sequence=0;this.unavailable=[];this.omissions=[];
  this.transcriptPath=transcriptPath;this.partial='';
  try{this.offset=transcriptPath&&!fromStart?fs.statSync(transcriptPath).size:0;}catch{this.offset=0;}
 }
 addText({id,source,text,status='completed',review=true}){
  this.requiresReview ||= review;
  if(typeof text!=='string'||!text.trim())return;
  const content=clean(text),key='text:'+id+':'+hash(content);
  if(this.seen.has(key))return;this.seen.add(key);
  this.records.push({id:String(id),sequence:++this.sequence,observedAt:this.now(),source:bounded(source,1800),status,urls:urls(content),text:content});
 }
 omit(id,source,details){
  const item={id:String(id),source:bounded(source,1800),...details};
  const key='omission:'+JSON.stringify(item);if(this.seen.has(key))return;
  this.seen.add(key);this.omissions.push(item);
 }
 addImage(id,source,url){
  if(typeof url!=='string'||!/^data:image\/(?:png|jpeg|webp);base64,/.test(url))return;
  const bytes=Buffer.from(url.slice(url.indexOf(',')+1),'base64');
  if(!bytes.length||bytes.length>IMAGE_LIMIT){this.unavailable.push('A tool image exceeded the evidence size limit');return;}
  const key='image:'+hash(bytes);if(this.seen.has(key))return;this.seen.add(key);
  this.images.push({id:String(id),sequence:++this.sequence,observedAt:this.now(),source:bounded(source,1800),imageUrl:url});
  if(this.images.length>4){const omitted=this.images.shift();this.omit(omitted.id,omitted.source,{reason:'image_limit',limit:4,count:1,observedAt:omitted.observedAt});}
 }
 output(id,source,value,{review=true,status='completed'}={}){
  const texts=[];
  const visit=(v,depth=0,location='$')=>{
   if(v==null)return;
   if(depth>8){this.omit(id,source,{reason:'nesting_limit',limit:8,location});return;}
   if(typeof v==='string'){
    if(/^data:image\//.test(v)){this.addImage(id,source,v);return;}
    // Shell/MCP adapters sometimes wrap their result in a JSON string.
    if(v.length<READ_LIMIT&&/^[\s]*[\[{]/.test(v)){try{visit(JSON.parse(v),depth+1,location+'#json');return;}catch{}}
    texts.push(v);return;
   }
   if(Array.isArray(v)){
    for(let i=0;i<Math.min(v.length,200);i++)visit(v[i],depth+1,location+'['+i+']');
    if(v.length>200)this.omit(id,source,{reason:'entry_limit',limit:200,firstOmittedEntry:201,count:v.length-200,location});
    return;
   }
   if(typeof v!=='object'){texts.push(String(v));return;}
   if(v.success===false||v.isError===true||['failed','rejected','error'].includes(v.status))texts.push('[Tool reported failure]');
   if(typeof v.exit_code==='number'&&v.exit_code!==0)texts.push('[Command failed: exit code '+v.exit_code+']');
   if(['image','input_image','inputImage'].includes(v.type)){
    this.addImage(id,source,v.image_url||v.imageUrl||(v.data&&v.mimeType?'data:'+v.mimeType+';base64,'+v.data:''));return;
   }
   if(typeof v.text==='string'){texts.push(v.text);return;}
   if(v.type==='resource'&&v.resource?.text){texts.push(v.resource.text);return;}
   if(v.content||v.contentItems){visit(v.content||v.contentItems,depth+1,location+(v.content?'.content':'.contentItems'));return;}
   if(v.output!==undefined){visit(v.output,depth+1,location+'.output');return;}
   if(v.value!==undefined){visit(v.value,depth+1,location+'.value');return;}
   // Structured observations (including app state) remain actual tool output.
   const safe=JSON.stringify(v,(key,val)=>/imageBase64|image_url|imageUrl|encrypted|audio|base64/i.test(key)?undefined:val);
   if(safe)texts.push(safe);
  };
  visit(value);
  this.addText({id,source,text:texts.join('\n'),review,status});
 }
 observe(item){
  if(!item)return;
  const id=item.id||item.call_id||'observation';
  if(['subAgentActivity','SubAgentActivity'].includes(item.type)){
   const childId=item.agentThreadId||item.agent_thread_id;
   if(typeof childId==='string'&&childId!==this.threadId){
    this.requiresReview=true;
    if(this.children.has(childId))return;
    if(this.children.size>=16){this.unavailable.push('Delegated evidence exceeded the history count limit');return;}
    this.children.set(childId,{id:childId,collector:null});
   }
   return;
  }
  if(['function_call','custom_tool_call'].includes(item.type)){
   this.calls.set(item.call_id,{name:item.name,source:[item.namespace,item.name].filter(Boolean).join('.')+' '+(item.arguments||item.input||'')});return;
  }
  if(['function_call_output','custom_tool_call_output'].includes(item.type)){
   const call=this.calls.get(item.call_id),source=call?.source||item.name||'Recorded tool result';
   // Native events determine review activation for code-mode wrappers. Their
   // recorded output supplements evidence without penalising fast app calls.
   this.output(id,source,item.output,{review:!!call&&call.name!=='exec'&&!this.fastTools.has(call.name)});return;
  }
  if(item.type==='commandExecution'){
   this.addText({id,source:'Mac command: '+item.command,text:item.aggregatedOutput,status:item.status,review:true});return;
  }
  if(item.type==='mcpToolCall'){
   const source=[item.server,item.tool].filter(Boolean).join('.')+' '+JSON.stringify(item.arguments??{});
   this.output(id,source,item.error||item.result,{status:item.error||item.result?.isError?'failed':item.status,review:!this.fastTools.has(item.tool)});return;
  }
  if(item.type==='dynamicToolCall'){
   this.output(id,item.tool,item.contentItems,{status:item.success===false?'failed':item.status,review:!this.fastTools.has(item.tool)});return;
  }
  if(item.type==='functionCallOutput'){this.output(id,item.name,item.output,{review:!this.fastTools.has(item.name)});return;}
  if(item.type==='webSearch'){
   this.requiresReview=true;
   this.addText({id,source:'Web: '+(item.action?.url||item.query),text:JSON.stringify({action:item.action,results:item.results,bodyAvailable:!!item.results}),review:true});return;
  }
  if(item.type==='imageView'&&typeof item.path==='string'){
   try{
    const stat=fs.statSync(item.path);if(stat.size>IMAGE_LIMIT)return;
    const data=fs.readFileSync(item.path);
    const mime=data[0]===255&&data[1]===216?'jpeg':data.subarray(1,4).toString()==='PNG'?'png':data.subarray(8,12).toString()==='WEBP'?'webp':null;
    if(mime)this.addImage(id,'Viewed image: '+item.path,'data:image/'+mime+';base64,'+data.toString('base64'));
   }catch{this.unavailable.push('A viewed image was unavailable');}
  }
 }
 collect(){
  if(!this.transcriptPath)return;
  try{
   const size=fs.statSync(this.transcriptPath).size;
   if(size<this.offset){this.unavailable.push('Tool history was rotated during this task');return;}
   if(size-this.offset>READ_LIMIT){this.unavailable.push('Tool history exceeded the task evidence limit');return;}
   if(size===this.offset)return;
   const data=Buffer.alloc(size-this.offset),fd=fs.openSync(this.transcriptPath,'r');
   try{fs.readSync(fd,data,0,data.length,this.offset);}finally{fs.closeSync(fd);}
   this.offset=size;const lines=(this.partial+data.toString('utf8')).split('\n');this.partial=lines.pop();
   for(const line of lines){try{
    const record=JSON.parse(line);
    // A reused child may have older work. Never import that as evidence for
    // this task, nor accept undated history when reading a child from the start.
    if(this.fromStart&&!(Date.parse(record.timestamp)>=this.startedAt))continue;
    if(record.type==='response_item')this.observe(record.payload);
    if(record.type==='event_msg'&&record.payload?.type==='item_completed')this.observe(record.payload.item);
   }catch{}}
  }catch{this.unavailable.push('Recorded tool history was unavailable');}
 }
 async collectDelegated(resolveThread,{visited=new Set(),budget={bytes:READ_LIMIT,threads:16}}={}){
  this.collect();const generation=this.generation;
  if(this.threadId)visited.add(this.threadId);
  for(const child of this.children.values()){
   if(visited.has(child.id))continue;
   visited.add(child.id);
   if(budget.threads--<=0){this.unavailable.push('Delegated evidence exceeded the history count limit');break;}
   try{
    if(!child.collector){
     const response=await resolveThread(child.id);
     if(generation!==this.generation)return;
     const thread=response?.thread;
     if(thread?.id!==child.id||typeof thread.path!=='string'||!this.threadId)throw Error('Unresolved delegated history');
     // Resolve only a child named by this task's native activity, then verify
     // its recorded parent. Do not scan the user's other sessions for evidence.
     const fd=fs.openSync(thread.path,'r'),head=Buffer.alloc(65536);let length;
     try{length=fs.readSync(fd,head,0,head.length,0);}finally{fs.closeSync(fd);}
     const meta=JSON.parse(head.subarray(0,length).toString('utf8').split('\n')[0]);
     if(meta.type!=='session_meta'||meta.payload?.id!==child.id||meta.payload?.source?.subagent?.thread_spawn?.parent_thread_id!==this.threadId)throw Error('Delegated parent mismatch');
     child.collector=new TaskEvidence({now:this.now,fastTools:[...this.fastTools]});
     child.collector.reset(thread.path,{threadId:child.id,since:this.startedAt,fromStart:true});
    }
    const collector=child.collector,bytes=fs.statSync(collector.transcriptPath).size-collector.offset;
    if(bytes<0||bytes>budget.bytes)throw Error('Delegated history size limit');
    budget.bytes-=bytes;
    await collector.collectDelegated(resolveThread,{visited,budget});
    if(generation!==this.generation)return;
    const evidence=collector.snapshot(),prefix='Subagent '+child.id+' / ';
    for(const record of evidence.records)this.addText({...record,id:child.id+':'+record.id,source:prefix+record.source});
    for(const image of evidence.images)this.addImage(child.id+':'+image.id,prefix+image.source,image.imageUrl);
    for(const omission of evidence.omissions||[]){const {id,source,...details}=omission;this.omit(child.id+':'+id,prefix+source,details);}
    for(const reason of evidence.unavailable)this.unavailable.push(prefix+reason);
   }catch{
    if(generation!==this.generation)return;
    this.unavailable.push('Tool evidence unavailable for subagent '+child.id);
   }
  }
 }
 snapshot(){
  this.collect();
  // Preserve every current-task result, including early results in long tasks.
  // The reviewer must report an actual context-capacity error rather than try
  // to verify the answer from an arbitrarily shortened evidence packet.
  return {records:this.records.map(record=>({...record})),images:[...this.images],unavailable:[...new Set(this.unavailable)],omissions:this.omissions.map(item=>({...item})),truncated:this.omissions.length>0};
 }
}
