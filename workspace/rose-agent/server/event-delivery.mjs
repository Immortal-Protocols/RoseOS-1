import fs from 'node:fs';

// Record only delivery metadata, never transcript text. An interrupted dispatch
// is deliberately not replayed: the action may already have reached Codex.
export class EventDelivery {
 constructor(file){
  this.file=file;this.seen=new Map();this.inflight=new Map();this.serial=Promise.resolve();
  if(file){try{for(const line of fs.readFileSync(file,'utf8').trim().split('\n').filter(Boolean)){
   const row=JSON.parse(line);if(row.key)this.seen.set(row.key,row.result||{ok:false,error:'Backend restarted during handoff; action status must be checked before retrying.'});
  }}catch(e){if(e.code!=='ENOENT')throw e;}}
 }
 record(row){if(this.file)fs.appendFileSync(this.file,JSON.stringify({...row,at:new Date().toISOString()})+'\n',{mode:0o600});}
 receive(e,session,handle){
  const handoff=e.type==='session.delegation.created';
  const key=handoff&&e.delegation?.id?'delegation:'+e.delegation.id:e.event_id?'event:'+e.event_id:null;
  if(key&&this.inflight.has(key))return this.inflight.get(key);
  if(key&&this.seen.has(key))return Promise.resolve({...this.seen.get(key),duplicate:true});
  const run=this.serial.then(async()=>{
   if(e.clientSessionId&&e.clientSessionId!==session())return {ok:false,error:'This voice session has ended. Please repeat the request in the current conversation.'};
   const meta={key,type:e.type,session:e.clientSessionId||null,start:e.start_ms,end:e.end_ms,offset:e.offset_ms};
   if(handoff)this.record({...meta,stage:'received'});
   else if(e.type==='session.input_transcript.delta'||e.type==='session.output_transcript.delta'){
    this.record({...meta,key:null,stage:'transcript_received'});
   }
   let result;
   try{result={ok:true,...await handle(e)};}catch(error){result={ok:false,error:error.message||'Handoff failed'};}
   if(key)this.seen.set(key,result);
   if(handoff)this.record({...meta,stage:result.ok?'accepted':'failed',result});
   // Keep the in-memory transcript/diagnostic dedup window bounded.
   if(this.seen.size>10000)for(const k of this.seen.keys()){if(!k.startsWith('delegation:'))this.seen.delete(k);if(this.seen.size<8000)break;}
   return result;
  });
  this.serial=run.catch(()=>{});
  if(key){this.inflight.set(key,run);run.finally(()=>this.inflight.delete(key)).catch(()=>{});}
  return run;
 }
}
