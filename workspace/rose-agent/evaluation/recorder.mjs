import {Worker} from 'node:worker_threads';
import {statSync} from 'node:fs';
import crypto from 'node:crypto';

// Observation only. No awaited writes, model calls, phone commands, or changes
// to tool results. A bounded queue fails open and reports lost evidence.
export class EvidenceRecorder {
 constructor({directory,versionFiles=[],maxBytes=2*1024**3,maxPending=256}={}) {
  this.pending=0;this.dropped=0;this.maxPending=maxPending;this.closed=false;
  this.state={enabled:true,ready:false,dropped:0};
  this.worker=new Worker(new URL('./recorder-worker.mjs',import.meta.url),{workerData:{directory,versionFiles,maxBytes}});
  this.worker.on('message',message=>{if(message.type==='ack')this.pending=Math.max(0,this.pending-1);else if(message.type==='status')this.state={...this.state,...message.status};});
  this.worker.on('error',()=>{this.closed=true;this.state={...this.state,ready:false,error:'Evidence recorder stopped'};});
  this.worker.unref();
 }
 record(kind,data,context={}) {
  if(this.closed||this.pending>=this.maxPending){this.dropped++;return false;}
  try{this.worker.postMessage({type:'event',event:{schemaVersion:1,kind,at:new Date().toISOString(),...context,data},dropped:this.dropped});this.pending++;return true;}
  catch{this.dropped++;return false;}
 }
 attachTranscript(file) {
  try{this.worker.postMessage({type:'transcript',file,offset:statSync(file).size});}catch{}
 }
 status(){return {...this.state,pending:this.pending,dropped:this.dropped};}
 async flush(){
  if(this.closed)throw Error('Evidence recorder unavailable');
  const id=crypto.randomUUID();
  return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.worker.off('message',done);reject(Error('Recorder flush timed out'));},10000);
   const done=m=>{if(m.type==='flushed'&&m.id===id){clearTimeout(timer);this.worker.off('message',done);resolve(m.status);}};
   this.worker.on('message',done);this.worker.postMessage({type:'flush',id});
  });
 }
 async close(){try{await this.flush();}finally{this.closed=true;await this.worker.terminate();}}
}
