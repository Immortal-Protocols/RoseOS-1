import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {PersonalMemoryTrial,openAIExtractor,atomicJSON,renderReview} from './memory.mjs';
import {reviewControl} from './review-state.mjs';

const priv=path.join(os.homedir(),'.config/pixel-voice');
const dir=process.env.PIXEL_MEMORY_DIR||path.join(priv,'personal-memory-trial');
const command=process.argv[2]||'status';
fs.mkdirSync(dir,{recursive:true,mode:0o700});
const controlFile=path.join(dir,'control.json');
if(['pause','resume','use-on','use-off','reject'].includes(command)){
 let old={};try{old=JSON.parse(fs.readFileSync(controlFile,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 const changes={changedAt:Date.now()};
 if(command==='pause'||command==='resume')changes.collectionEnabled=command==='resume';
 if(command==='resume')changes.resumeAt=Date.now();
 if(command==='use-on'||command==='use-off')changes.useEnabled=command==='use-on';
 if(command==='reject'){
  const id=process.argv[3];const state=JSON.parse(fs.readFileSync(path.join(dir,'state.json'),'utf8'));
  if(!state.candidates.some(c=>c.id===id))throw Error('Unknown candidate ID');
  changes.rejectedIds=[...new Set([...(old.rejectedIds||[]),id])];
 }
 const next={...old,...changes};atomicJSON(controlFile,next);
 console.log(JSON.stringify({collectionEnabled:next.collectionEnabled===true,injectionEnabled:next.useEnabled===true}));
 process.exit(0);
}
const lock=path.join(dir,'worker.lock');let ownsLock=false;
if(['run','process-once','collect-once'].includes(command)){
 try {const fd=fs.openSync(lock,'wx',0o600);fs.writeFileSync(fd,String(process.pid));fs.closeSync(fd);ownsLock=true;}
 catch(e){
  if(e.code!=='EEXIST')throw e;
  const pid=Number(fs.readFileSync(lock,'utf8'));let running=false;
  if(Number.isInteger(pid)&&pid>0)try{process.kill(pid,0);running=true;}catch(e){if(e.code!=='ESRCH')running=true;}
  if(running)throw Error('Personal-memory worker is already running');
  fs.unlinkSync(lock);const fd=fs.openSync(lock,'wx',0o600);fs.writeFileSync(fd,String(process.pid));fs.closeSync(fd);ownsLock=true;
 }
 process.on('exit',()=>{if(ownsLock)try{if(fs.readFileSync(lock,'utf8')===String(process.pid))fs.unlinkSync(lock);}catch{}});
}
const trial=new PersonalMemoryTrial({dir,repairJournal:ownsLock,timelineFile:process.env.PIXEL_MEMORY_TIMELINE||path.join(priv,'conversation-timeline.json'),extract:openAIExtractor({keyFile:process.env.PIXEL_MEMORY_KEY_FILE||path.join(priv,'openai-api-key'),model:process.env.PIXEL_MEMORY_MODEL||'gpt-6-luna',reasoningEffort:'high',serviceTier:'default',maxOutputTokens:8192})});
function review(){const file=path.join(dir,'review.html');const tmp=file+'.'+process.pid+'.tmp';fs.writeFileSync(tmp,renderReview(trial),{mode:0o600});fs.renameSync(tmp,file);return file;}
if(command==='status')console.log(JSON.stringify(trial.status(),null,2));
else if(command==='review')console.log(review());
else if(command==='collect-once'){trial.capture();review();console.log(JSON.stringify(trial.status(),null,2));}
else if(command==='process-once'){trial.capture();await trial.process({force:true});review();console.log(JSON.stringify(trial.status(),null,2));}
else if(command==='run'){
 let stopping=false;let lastError=null;let lastControl='';
 const tick=()=>{
  if(stopping)return;
  try {
   const changed=trial.capture();
   const control=JSON.stringify(reviewControl(dir,trial.control()));if(changed||control!==lastControl){review();lastControl=control;}
   if(!trial.inFlight){const previousBatch=trial.state.batches.length;void trial.process().then(done=>{if(done||trial.state.batches.length!==previousBatch)review();}).catch(e=>console.error('Memory processing error:',e.name));}
   lastError=null;
  }catch(e){if(lastError!==e.message){console.error('Memory capture error:',e.name);lastError=e.message;}}
 };
 review();console.log(JSON.stringify({event:'personal-memory-started',...trial.status()}));tick();
 const timer=setInterval(tick,2000);
 for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{stopping=true;clearInterval(timer);console.log('Personal-memory stopping; Rose Agent is unaffected.');process.exit(0);});
}else throw Error('Commands: status, review, run, pause, resume, use-on, use-off, reject ID, collect-once, process-once');
