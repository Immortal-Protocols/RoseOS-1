// Opt-in evaluation transport. Inactive without the private evaluation config.
// Checks the real initial wire payload before forwarding any paid request.
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {LithosEvaluationGateway} from './lithos-evaluation.mjs';
import {LocalEvaluationGateway} from './local-evaluation.mjs';

export const EVALUATION_PROVIDER='pixel_evaluation';
const hash=v=>crypto.createHash('sha256').update(typeof v==='string'?v:JSON.stringify(v)).digest('hex');
const stable=v=>Array.isArray(v)?v.map(stable):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
export function initialSignature(source,request){
 if(source.previous_response_id)throw Error('Initial request references previous response');
 if(!Array.isArray(source.input))throw Error('Expected full initial input');
 let requestCount=0,images=0;
 const visit=v=>{
  if(typeof v==='string'){if(v.includes(request)){requestCount++;return v.replaceAll(request,'<CURRENT_EVALUATION_REQUEST>');}return v;}
  if(Array.isArray(v))return v.map(visit);
  if(v&&typeof v==='object'){
   if(['input_image','image_url'].includes(v.type))images++;
   return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,visit(x)]));
  }
  return v;
 };
 for(const item of source.input){
  if(item.role==='assistant'||['function_call','function_call_output','reasoning','custom_tool_call','custom_tool_call_output'].includes(item.type))throw Error('Prior model/tool history in initial input');
 }
 // Message IDs identify this new session, not model-visible content. Retain
 // every role and content byte while omitting only that top-level identity.
 const input=source.input.map(({id,...item})=>item);
 const canonical=stable({instructions:source.instructions||'',input:visit(input),tools:source.tools||[],reasoning:source.reasoning||{},max_output_tokens:source.max_output_tokens,tool_choice:source.tool_choice,parallel_tool_calls:source.parallel_tool_calls});
 if(images!==0||requestCount!==1)throw Error(`Initial input has ${images} images and ${requestCount} copies of the request`);
 return {signatureVersion:2,canonical,sha256:hash(canonical),instructionsHash:hash(canonical.instructions),inputHash:hash(canonical.input),toolsHash:hash(canonical.tools),initialImages:images,requestCount,roles:source.input.map(x=>({type:x.type,role:x.role})),bytes:Buffer.byteLength(JSON.stringify(canonical))};
}

export class EvaluationGateway{
 constructor({config,openaiKeyFile,baseten}){Object.assign(this,{config,openaiKeyFile,baseten});this.token=crypto.randomBytes(32).toString('hex');this.count=0;this.requests=new Set();fs.mkdirSync(config.directory,{recursive:true,mode:0o700});}
 write(name,data){fs.writeFileSync(path.join(this.config.directory,name),JSON.stringify(data,null,2)+'\n',{mode:0o600});}
 async listen(){
  if(this.config.provider==='local'){
   let event=0;
   this.local=await new LocalEvaluationGateway({config:this.config,record:data=>this.write('local-provider-'+(++event)+'.json',data)}).listen();
  }
  if(this.config.provider==='lithos'){
   let event=0;
   this.lithos=await new LithosEvaluationGateway({model:this.config.model,keyFile:this.config.keyFile,openaiKeyFile:this.openaiKeyFile,budgetFile:this.config.budgetFile,dailyLimitUsd:5,record:data=>this.write('provider-'+(++event)+'.json',data)}).listen();
  }
  this.server=http.createServer((req,res)=>this.handle(req,res).catch(e=>{if(!res.headersSent){res.writeHead(409,{'content-type':'application/json'});res.end(JSON.stringify({error:{message:e.message}}));}else res.end();this.write('audit-error.json',{error:e.message});}));await new Promise(r=>this.server.listen(0,'127.0.0.1',r));this.url='http://127.0.0.1:'+this.server.address().port;return this;
 }
 async handle(req,res){
  if(req.method!=='POST'||req.url!=='/responses')throw Error('Unexpected evaluation endpoint '+req.url);
  if(req.headers.authorization!=='Bearer '+this.token)throw Error('Evaluation authentication failed');
  const chunks=[];let bytes=0;for await(const chunk of req){bytes+=chunk.length;if(bytes>32*1024**2)throw Error('Evaluation request too large');chunks.push(chunk);}
  const body=JSON.parse(Buffer.concat(chunks));if(body.model!==this.config.model)throw Error('Unexpected evaluation model');
  body.store=false;body.max_output_tokens=16384;
  const call=++this.count;
  if(call===1){
   const proof=initialSignature(body,this.config.request);this.write('initial-payload.json',body);this.write('initial-audit.json',{...proof,canonical:undefined,model:body.model,phase:this.config.phase});
   this.write('canonical-initial.json',proof.canonical);
   if(this.config.expectedHash&&proof.sha256!==this.config.expectedHash)throw Error('Initial payload does not match the approved comparison template');
  }
  if(this.config.phase==='preflight'){res.writeHead(409,{'content-type':'application/json'});res.end(JSON.stringify({error:{message:'Evaluation preflight captured locally; no paid request sent'}}));return;}
  const deepseek=['lithos','local'].includes(this.config.provider)||this.config.model.startsWith('deepseek-');
  if(deepseek)delete body.service_tier;else body.service_tier=this.config.serviceTier??'priority';
  const wire=Buffer.from(JSON.stringify(body));const started=Date.now();let firstTokenMs=null,usage=null,reportedServiceTier=null,buffer='';
  this.write('upstream-meta-'+call+'.json',{model:body.model,serviceTier:body.service_tier,reasoning:body.reasoning});
  const provider=this.local||this.lithos||this.baseten;
  const url=deepseek?provider.url+'/responses':'https://api.openai.com/v1/responses';
  const request=deepseek?http.request:https.request;
  const auth=deepseek?provider.token:fs.readFileSync(this.openaiKeyFile,'utf8').trim();
  const upstream=request(url,{method:'POST',headers:{Authorization:'Bearer '+auth,'Content-Type':'application/json','Content-Length':wire.length}},response=>{
   res.writeHead(response.statusCode,{'content-type':response.headers['content-type']||'text/event-stream','cache-control':'no-store'});
   response.on('data',chunk=>{
    buffer+=chunk.toString();let n;
    while((n=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,n);buffer=buffer.slice(n+1);if(!line.startsWith('data: '))continue;try{const e=JSON.parse(line.slice(6));if(/delta$/.test(e.type||''))firstTokenMs??=Date.now()-started;if(e.response?.usage)usage=e.response.usage;if(e.response?.service_tier)reportedServiceTier=e.response.service_tier;}catch{}}
   });
   response.on('end',()=>{this.requests.delete(upstream);this.write('call-'+call+'.json',{call,model:body.model,status:response.statusCode,serviceTier:body.service_tier,reportedServiceTier,elapsedMs:Date.now()-started,firstTokenMs,usage});});response.pipe(res);
  });
  this.requests.add(upstream);upstream.setTimeout(90000,()=>upstream.destroy(Error('Evaluation provider timeout')));upstream.on('error',e=>{this.requests.delete(upstream);if(!res.headersSent){res.writeHead(502);res.end(JSON.stringify({error:{message:e.message}}));}else res.end();});res.on('close',()=>{if(!res.writableEnded)upstream.destroy();});upstream.end(wire);
 }
 close(){for(const r of this.requests)r.destroy();this.local?.close();this.lithos?.close();this.server?.closeAllConnections();this.server?.close();}
}
