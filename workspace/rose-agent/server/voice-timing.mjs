import fs from 'node:fs';
const stages=new Set(['session_started','session_ended','mic_activity_start','mic_activity_end','output_audio_activity_start','output_audio_activity_end','user_transcript_arrived','reply_text_arrived','reply_mode_speaking','reply_mode_listening','network_ping','task_started','task_result_received','task_failed']);
// Only allow numeric measurements, never transcript text, audio, or credentials.
const numeric=['seq','turn','monoMs','wallMs','detectedMonoMs','quietWindowMs','threshold','characters','pingMs'];
export class VoiceTimingLog {
 constructor(file){this.file=file;this.seen=new Set();}
 accept(session,events){
  if(typeof session!=='string'||!Array.isArray(events))return 0;
  const lines=[];
  for(const e of events.slice(-64)){
   if(!e||!stages.has(e.stage)||!Number.isSafeInteger(e.seq)||!Number.isFinite(e.monoMs)||!Number.isFinite(e.wallMs))continue;
   const key=session+':'+e.seq;if(this.seen.has(key))continue;
   const item={receivedAt:new Date().toISOString(),sessionId:session,stage:e.stage};
   for(const k of numeric)if(Number.isFinite(e[k])&&Math.abs(e[k])<1e14)item[k]=e[k];
   if(typeof e.muted==='boolean')item.muted=e.muted;
   this.seen.add(key);lines.push(JSON.stringify(item));
  }
  if(this.seen.size>8192)this.seen=new Set([...this.seen].slice(-4096));
  if(lines.length)try{
   if(fs.existsSync(this.file)&&fs.statSync(this.file).size>2_000_000)fs.renameSync(this.file,this.file+'.previous');
   fs.appendFileSync(this.file,lines.join('\n')+'\n',{mode:0o600});
  }catch{} // Diagnostics must never break voice.
  return lines.length;
 }
}
