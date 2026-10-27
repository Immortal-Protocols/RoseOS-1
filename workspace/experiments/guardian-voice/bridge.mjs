import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';

export class GuardianBridge {
 constructor({token,now=Date.now}={}) {this.token=token;this.now=now;this.jobs=new Map();this.waiter=null;this.lastSeen=0;this.mode=null;this.generation=0;this.tasks=new Map();}
 status(){return {connected:this.now()-this.lastSeen<35000,mode:this.mode};}
 enqueue(action,args={}) {
  if(!this.status().connected)throw new Error('Open A-LIST Manager on the Mac before connecting to a Guardian.');
  const id=crypto.randomUUID();let resolve,reject;
  const promise=new Promise((a,b)=>{resolve=a;reject=b});
  const timer=setTimeout(()=>{this.jobs.delete(id);reject(new Error('Guardian connection timed out. Check Manager before retrying; an accepted message may still receive a reply.'));},210000);timer.unref?.();
  this.jobs.set(id,{id,action,...args,resolve,reject,timer,offered:false});this.flush();return promise;
 }
 poll(res){this.lastSeen=this.now();if(this.waiter)this.waiter.end(JSON.stringify({job:null}));this.waiter=res;res.on('close',()=>{if(this.waiter===res)this.waiter=null;});this.flush();if(this.waiter===res)setTimeout(()=>{if(this.waiter===res){this.waiter=null;res.end(JSON.stringify({job:null}));}},20000).unref();}
 flush(){if(!this.waiter)return;const job=[...this.jobs.values()].find(j=>!j.offered);if(!job)return;job.offered=true;const res=this.waiter;this.waiter=null;const {resolve,reject,timer,offered,...wire}=job;res.end(JSON.stringify({job:wire}));}
 complete(id,result){this.lastSeen=this.now();const job=this.jobs.get(id);if(!job)return false;clearTimeout(job.timer);this.jobs.delete(id);if(result.error)job.reject(new Error(result.error));else job.resolve(result);return true;}
 async select(query){const generation=++this.generation;this.mode=null;const result=await this.enqueue('select',{query});if(generation!==this.generation)throw new Error('Guardian selection changed.');if(!result.client?.id||!result.client?.name)throw new Error('Manager did not verify a Guardian.');this.mode={client:result.client,generation};return this.mode;}
 back(){this.generation++;this.mode=null;return {mode:null};}
 async ask({request,id,generation}){const mode=this.mode;if(!mode||mode.generation!==generation)throw new Error('This Guardian conversation is no longer active.');const result=await this.enqueue('ask',{client:mode.client,request,requestId:id});if(this.mode!==mode)throw new Error('Guardian reply belongs to the previous conversation.');return result;}
 startTask(input){
  if(!this.mode||this.mode.generation!==input.generation)throw new Error('Guardian conversation changed.');
  if(this.tasks.has(input.id)){const prior=this.tasks.get(input.id);if(prior.generation!==input.generation)throw new Error('Request belongs to another conversation.');return prior;}
  if(!/^[a-f0-9-]{36}$/i.test(input.id||'')||typeof input.request!=='string'||!input.request.trim()||input.request.length>12000)throw new Error('Invalid Guardian request.');
  const task={id:input.id,generation:input.generation,state:'running',progress:'Guardian is preparing a reply…'};this.tasks.set(input.id,task);
  this.ask(input).then(result=>Object.assign(task,{state:'completed',text:result.text}),error=>Object.assign(task,{state:'failed',text:error.message}));
  while(this.tasks.size>100)this.tasks.delete(this.tasks.keys().next().value);return task;
 }
 listen(port=8797){this.server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
  const expected='Bearer '+this.token,given=req.headers.authorization||'';
  if(req.headers.origin||req.headers.host!==`127.0.0.1:${port}`||given.length!==expected.length||!crypto.timingSafeEqual(Buffer.from(given),Buffer.from(expected))){res.writeHead(403);res.end('{}');return;}
  if(req.method==='GET'&&req.url==='/poll'){this.poll(res);return;}
  if(req.method==='POST'&&req.url==='/result'){try{let body='';for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>128000)throw Error();}const value=JSON.parse(body);this.complete(value.id,value.result);res.end('{"ok":true}');}catch{res.writeHead(400);res.end('{}');}return;}
  res.writeHead(404);res.end('{}');
 });this.server.listen(port,'127.0.0.1');return this.server;}
}
export function guardianVoiceInstructions(mode){return `You are the live voice interface to ${mode.client.name}'s A-LIST Manager Guardian. The human is the manager, speaking ABOUT this member, not the member. For EVERY user question or request, delegate to the client; it reaches the actual Guardian with its history and tools. Do not answer questions yourself, invent member information, or use other tools. Speak the Guardian's returned reply faithfully in first person, without saying you asked another agent. Briefly introduce the connection once when requested. The user can say Back to Pixel to leave. Acknowledge failures honestly. Do not apologize or claim feelings.`;}
