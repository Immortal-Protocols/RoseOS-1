import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import dns from 'node:dns/promises';
import net from 'node:net';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {toFile} from 'openai';
const run=promisify(execFile);
const STEP=30000,WINDOW=120000;
export function chunkStarts(position,duration=Infinity){
 const from=Math.max(0,position-WINDOW),end=Math.min(duration,position+WINDOW),out=[];
 for(let at=Math.floor(from/STEP)*STEP;at<end;at+=STEP)out.push(at);
 return out.sort((a,b)=>(a<=position&&a+STEP>position?-1:b<=position&&b+STEP>position?1:Math.abs(a+STEP/2-position)-Math.abs(b+STEP/2-position)));
}
function publicAddress(address){
 if(net.isIPv4(address)){const p=address.split('.').map(Number);return !(p[0]===0||p[0]===10||p[0]===127||p[0]>=224||p[0]===169&&p[1]===254||p[0]===172&&p[1]>=16&&p[1]<=31||p[0]===192&&p[1]===168||p[0]===100&&p[1]>=64&&p[1]<=127);}
 return net.isIPv6(address)&&!address.startsWith('::')&&!/^(fc|fd|fe8|fe9|fea|feb)/i.test(address);
}
async function publicUrl(raw){
 const u=new URL(raw);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.port&&!['80','443'].includes(u.port))throw Error('Unsupported podcast audio URL');
 const ips=await dns.lookup(u.hostname,{all:true});if(!ips.length||ips.some(x=>!publicAddress(x.address)))throw Error('Podcast audio must use a public host');return u;
}
async function download(raw,file,signal){
 let url=await publicUrl(raw),response;
 for(let n=0;n<8;n++){
  response=await fetch(url,{redirect:'manual',signal,headers:{'User-Agent':'PixelPodcasts/1.0'}});
  if([301,302,303,307,308].includes(response.status)){const next=new URL(response.headers.get('location'),url);await response.body?.cancel();url=await publicUrl(next.href);continue;}
  break;
 }
 if(!response?.ok)throw Error('Podcast audio download failed');
 const tmp=file+'.part';let size=0;const fd=fs.openSync(tmp,'w',0o600);
 try{for await(const chunk of response.body){size+=chunk.length;if(size>300000000)throw Error('Podcast audio is too large');fs.writeSync(fd,chunk);}fs.closeSync(fd);fs.renameSync(tmp,file);}catch(e){fs.closeSync(fd);fs.rmSync(tmp,{force:true});throw e;}
}
export function podcastTranscription(dir,client,options={}){
 fs.mkdirSync(dir,{recursive:true,mode:0o700});
 const entries=new Map();let active=null,running=false,controller=null,observed={};
 const now=options.now||Date.now;
 const ffmpeg=process.env.PIXEL_FFMPEG_BIN||(process.platform==='linux'?'ffmpeg':path.resolve(import.meta.dirname,'../../tools/podcast-audio/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1'));
 function entry(data){
  if(typeof data.audioUrl!=='string'||data.audioUrl.length>5000)throw Error('Missing podcast audio URL');
  const u=new URL(data.audioUrl);if(!['http:','https:'].includes(u.protocol))throw Error('Unsupported audio URL');
  const key=crypto.createHash('sha256').update(data.audioUrl+'|'+String(data.audioVersion||'remote')).digest('hex');
  if(!entries.has(key)){
   let chunks={};try{const saved=JSON.parse(fs.readFileSync(path.join(dir,key+'.json'),'utf8'));if(['gpt-4o-mini-transcribe','whisper-1'].includes(saved.model))chunks=saved.chunks;}catch{}
   entries.set(key,{key,url:data.audioUrl,local:!!data.localAudio,chunks,error:null,retryAt:0,count:0});
  }
  return entries.get(key);
 }
 const eligible=()=>active&&active.playing&&active.foreground&&now()-active.updatedAt<12000;
 const watchdog=setInterval(()=>{if(!eligible())controller?.abort();},1000);watchdog.unref();
 async function work(){
  if(running)return;running=true;
  try{while(eligible()){
   const job=active,e=job.entry;if(now()<e.retryAt)break;
   const start=chunkStarts(job.position,job.duration).find(at=>!e.chunks[at]||e.chunks[at].timing!=='word-aligned');if(start===undefined)break;
   controller=new AbortController();const signal=controller.signal;
   try{
    let bytes;
    const end=Math.min(start+STEP,job.duration),seconds=(end-start)/1000;
    if(options.extract)bytes=await options.extract(e.url,start,seconds,signal);
    else{
     const audio=path.join(dir,e.key+'.audio');
     if(!fs.existsSync(audio)){if(e.local)break;await download(e.url,audio,AbortSignal.any([signal,AbortSignal.timeout(180000)]));}
     const out=path.join(dir,e.key+'-'+start+'.wav');
     try{await run(ffmpeg,['-hide_banner','-loglevel','error','-nostdin','-y','-protocol_whitelist','file,pipe','-ss',String(start/1000),'-i',audio,'-t',String(seconds),'-vn','-ac','1','-ar','16000',out],{signal,timeout:30000,maxBuffer:100000});bytes=fs.readFileSync(out);}finally{fs.rmSync(out,{force:true});}
    }
    if(!eligible()||active.entry!==e||!chunkStarts(active.position,active.duration).includes(start))continue;
    const began=now();
    const result=options.transcribe?await options.transcribe(bytes):await client.audio.transcriptions.create({model:'whisper-1',file:await toFile(bytes,'podcast.wav',{type:'audio/wav'}),response_format:'verbose_json',timestamp_granularities:['word']},{timeout:45000,maxRetries:0});
    const text=String(result.text||'').trim().slice(0,8000);
    const words=(result.words||[]).filter(w=>typeof w.word==='string'&&Number.isFinite(w.start)&&Number.isFinite(w.end)&&w.start>=0&&w.end>=w.start).map(w=>({text:w.word.trim(),startMs:Math.min(end,start+Math.round(w.start*1000)),endMs:Math.min(end,start+Math.round(w.end*1000))}));
    e.chunks[start]={startMs:start,endMs:end,text,speaker:'',timing:'word-aligned',words};e.error=null;e.count++;
    fs.writeFileSync(path.join(dir,e.key+'.json'),JSON.stringify({model:'whisper-1',chunks:e.chunks}),{mode:0o600});
    console.log('PODCAST_TRANSCRIBED',JSON.stringify({startMs:start,endMs:end,elapsedMs:now()-began,characters:text.length}));
   }catch(error){
    if(signal.aborted)continue;
    e.error=error.status===401||error.status===403?'Transcription API access unavailable':'Could not transcribe this audio chunk';e.retryAt=now()+60000;
    console.error('PODCAST_TRANSCRIPTION_FAILED',error.status||'',String(error.message).replace(/sk[-_][\w-]+/g,'[redacted]').slice(0,250));break;
   }finally{controller=null;}
  }}finally{running=false;}
 }
 function snapshot(e,position,duration){
  const wanted=chunkStarts(position,duration),segments=wanted.filter(at=>e.chunks[at]).map(at=>e.chunks[at]).sort((a,b)=>a.startMs-b.startMs);
  return {status:e.error?'error':wanted.every(at=>e.chunks[at]?.timing==='word-aligned')?'ready':segments.length?'partial':'loading',source:'generated',timing:segments.every(s=>s.timing==='word-aligned')?'Word timestamps from speech recognition; approximate alignment.':'Some cached passages are still located within 30-second audio chunks.',segments,error:e.error,cachedChunks:Object.keys(e.chunks).length,generatedChunks:e.count};
 }
 return {
  async upload(req,data){
   const e=entry({...data,localAudio:true});const audio=path.join(dir,e.key+'.audio');
   if(fs.existsSync(audio)){req.resume();return {cached:true};}
   const tmp=audio+'.upload';let size=0;const fd=fs.openSync(tmp,'w',0o600);
   try{for await(const chunk of req){size+=chunk.length;if(size>300000000)throw Error('Podcast audio is too large');fs.writeSync(fd,chunk);}fs.closeSync(fd);fs.renameSync(tmp,audio);}catch(error){fs.closeSync(fd);fs.rmSync(tmp,{force:true});throw error;}
   return {cached:false,bytes:size};
  },
  sync(data){
   if(data.active===false){active=null;controller?.abort();return {status:'stopped',segments:[]};}
   if(!Number.isFinite(data.positionMs)||data.positionMs<0||data.positionMs>86400000)throw Error('Invalid podcast position');
   const e=entry(data),duration=Number.isFinite(data.durationMs)&&data.durationMs>0?data.durationMs:86400000;
   if(!data.lookupOnly){
    observed={receivedChunks:Number(data.receivedChunks)||0,sentChunks:Number(data.sentChunks)||0};
    const changed=active?.entry!==e;active={entry:e,position:data.positionMs,duration,playing:data.playing===true,foreground:data.foreground===true,updatedAt:now()};
    if(changed||!eligible())controller?.abort();
    if(eligible())void work();
   }
   return snapshot(e,data.positionMs,duration);
  },
  state(){return {running,eligible:!!eligible(),positionMs:active?.position,playing:active?.playing,foreground:active?.foreground,cachedChunks:active?Object.keys(active.entry.chunks).length:0,generatedChunks:active?.entry.count||0,...observed};}
 };
}
