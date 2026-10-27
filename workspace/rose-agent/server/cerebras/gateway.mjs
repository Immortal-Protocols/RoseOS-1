import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {responsesToChat,chatStreamToResponses} from './protocol.mjs';
import {prepareRequest,streamAdapter} from '../baseten/tools.mjs';

export const CEREBRAS_MODEL='qwen-3.8-27b';
export const CEREBRAS_PROVIDER='pixel_cerebras';

export function requestFootprint(body){
 let imageCount=0;
 const text=JSON.stringify(body,(key,value)=>{
  if(key==='image_url'&&typeof value==='string'&&value.startsWith('data:image/')){imageCount++;return '';}
  if(key==='image_url'&&value?.url){imageCount++;return {...value,url:''};}
  return value;
 });
 // Reserve visual tokens independently of base64 transport. No cache discount
 // is assumed for admission or cost reporting. Do not resize the actual image.
 const inputUpperBound=Buffer.byteLength(text)+imageCount*4608+4096;
 return {imageCount,inputUpperBound,reserveUsd:(inputUpperBound*.99+(body.max_completion_tokens||8192)*1.49)/1e6};
}

// Local authenticated wire adapter; never exposes the provider credential to
// Codex, the phone, logs, or a public listener. Failed actions aren't replayed.
export class CerebrasGateway {
 constructor({keyFile,openaiKeyFile,budgetFile,record=()=>{},dailyLimitUsd=1,enforceBudget=true,request=https.request}){
  Object.assign(this,{keyFile,openaiKeyFile,budgetFile,record,dailyLimitUsd,enforceBudget,request});
  this.token=crypto.randomBytes(32).toString('hex');this.controllers=new Set();this.reserved=0;
 }
 budget(){
  const day=new Date().toISOString().slice(0,10);let saved;
  try{saved=JSON.parse(fs.readFileSync(this.budgetFile,'utf8'));}catch{}
  return saved?.day===day?saved:{day,spentUsd:0};
 }
 charge(value){const b=this.budget();b.spentUsd+=value;fs.writeFileSync(this.budgetFile+'.tmp',JSON.stringify(b),{mode:0o600});fs.renameSync(this.budgetFile+'.tmp',this.budgetFile);}
 available(){return fs.existsSync(this.keyFile)&&(!this.enforceBudget||this.budget().spentUsd<this.dailyLimitUsd);}
 async listen(){
  this.server=http.createServer((req,res)=>this.handle(req,res).catch(()=>{if(!res.headersSent)this.fail(res,502,'Provider connection failed');else res.end();}));
  await new Promise((resolve,reject)=>{this.server.once('error',reject);this.server.listen(0,'127.0.0.1',resolve);});
  this.url='http://127.0.0.1:'+this.server.address().port;return this;
 }
 fail(res,status,message){res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify({error:{message}}));}
 async handle(req,res){
  if(req.method!=='POST'||req.url!=='/responses')return this.fail(res,404,'Unsupported Cerebras trial endpoint');
  const expected=Buffer.from('Bearer '+this.token),actual=Buffer.from(req.headers.authorization||'');
  if(expected.length!==actual.length||!crypto.timingSafeEqual(expected,actual))return this.fail(res,401,'Local provider authentication required');
  let size=0,chunks=[];for await(const b of req){size+=b.length;if(size>32*1024*1024)return this.fail(res,413,'Model request too large');chunks.push(b);}
  let source;try{source=JSON.parse(Buffer.concat(chunks));}catch{return this.fail(res,400,'Invalid model request');}
  if(['gpt-6-luna','gpt-6.1-sol','gpt-6-sol','gpt-6-astra'].includes(source.model)&&this.openaiKeyFile){
   // An explicitly selected OpenAI subagent keeps its original provider.
   const wire=Buffer.from(JSON.stringify({...source,store:false}));
   const q=this.request('https://api.openai.com/v1/responses',{method:'POST',headers:{Authorization:'Bearer '+fs.readFileSync(this.openaiKeyFile,'utf8').trim(),'Content-Type':'application/json','Content-Length':wire.length}},up=>{res.writeHead(up.statusCode,{'content-type':up.headers['content-type']||'text/event-stream'});up.pipe(res);});
   this.controllers.add(q);q.setTimeout(90000,()=>q.destroy(Error('OpenAI subagent timeout')));q.on('close',()=>this.controllers.delete(q));q.on('error',()=>{if(!res.headersSent)this.fail(res,502,'OpenAI subagent connection failed');else res.end();});res.on('close',()=>q.destroy());q.end(wire);return;
  }
  if(source.model!==CEREBRAS_MODEL)return this.fail(res,400,'The trial credential supports only Qwen');
  if(!fs.existsSync(this.keyFile))return this.fail(res,503,'Qwen trial credential is unavailable. Select Luna to continue.');
  source.store=false;source.max_output_tokens=Math.min(source.max_output_tokens||8192,8192);
  let body,names,aliases;try{const p=prepareRequest(source);names=p.names;aliases=p.aliases;body=responsesToChat(p.body);
   for(const m of body.messages){
    if(m.reasoning_content!==undefined){m.reasoning=m.reasoning_content;delete m.reasoning_content;}
    if(Array.isArray(m.content))for(const part of m.content)if(part.type==='image_url')delete part.image_url.detail;
   }
   body.max_completion_tokens=body.max_tokens;delete body.max_tokens;
   body.stream_options={include_usage:true};body.reasoning_format='parsed';}catch(error){this.record({status:'unsupported_request',reason:error.message,tools:source.tools?.map(t=>({type:t.type,name:t.name,tools:t.tools?.map(x=>({type:x.type,name:x.name}))}))});return this.fail(res,400,error.message);}
  const wire=Buffer.from(JSON.stringify(body));
  const footprint=requestFootprint(body),reserve=footprint.reserveUsd;
  if(this.enforceBudget&&this.budget().spentUsd+this.reserved+reserve>this.dailyLimitUsd){this.record({status:'local_budget_rejected',spentUsd:this.budget().spentUsd,...footprint});return this.fail(res,402,'Qwen trial budget cannot cover this request. Select Luna to continue.');}
  if(this.enforceBudget)this.reserved+=reserve;
  const key=fs.readFileSync(this.keyFile,'utf8').trim(),started=Date.now(),session=String(req.headers.session_id||req.headers['x-session-affinity']||'pixel-voice').slice(0,150);
  const chat=chatStreamToResponses(CEREBRAS_MODEL),convert=streamAdapter(names,aliases);
  let terminal=false,finished=false,buffer='',usage=null,httpStatus=0,firstTokenMs=null,reason=null,rateLimits={};
  const finish=()=>{
   if(finished)return;finished=true;if(this.enforceBudget)this.reserved-=reserve;this.controllers.delete(upstream);
   const cost=usage?(usage.input_tokens*.99+usage.output_tokens*1.49)/1e6:(this.enforceBudget?reserve:null);
   if(this.enforceBudget)this.charge(cost);
   try{this.record({model:CEREBRAS_MODEL,status:terminal?'completed':'failed',httpStatus,elapsedMs:Date.now()-started,firstTokenMs,usage,costUsd:cost,costEstimated:!usage,budgetEnforced:this.enforceBudget,costBasis:'Full token rates; no cache discount assumed',imageCount:footprint.imageCount,reason,rateLimits,reasoningEffort:body.reasoning_effort});}catch{}
   res.end();
  };
  const send=event=>{
   if(event.type==='response.output_text.delta'||event.type==='response.function_call_arguments.delta')firstTokenMs??=Date.now()-started;
   if(['response.completed','response.incomplete','response.failed'].includes(event.type)){terminal=event.type==='response.completed';usage=event.response.usage;reason=terminal?null:event.type;}
   for(const e of convert(event))res.write('event: '+e.type+'\ndata: '+JSON.stringify(e)+'\n\n');
  };
  const upstream=this.request('https://api.cerebras.ai/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json','Content-Length':wire.length,'x-session-affinity':session}},up=>{
   httpStatus=up.statusCode;
   rateLimits=Object.fromEntries(Object.entries(up.headers).filter(([name])=>/ratelimit|retry-after/i.test(name)));
   if(up.statusCode>=400){
    reason='upstream_'+up.statusCode;let errorBody='';up.on('data',chunk=>{if(errorBody.length<4000)errorBody+=chunk.toString();});up.on('end',()=>{try{reason=JSON.parse(errorBody).error?.message||reason;}catch{}});
    this.fail(res,up.statusCode,'Qwen provider rejected the request ('+up.statusCode+'). Select Luna to continue; completed actions will not be retried.');
    up.on('end',finish);return;
   }
   res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-store'});
   up.on('data',chunk=>{
    buffer+=chunk.toString();let n;
    while((n=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,n);buffer=buffer.slice(n+1);if(!line.startsWith('data:'))continue;
     const raw=line.slice(5).trim();if(raw==='[DONE]')continue;
     try{for(const event of chat.push(JSON.parse(raw)))send(event);}catch(error){reason='invalid_stream';upstream.destroy(error);return;}
    }
   });
   up.on('end',()=>{try{for(const event of chat.end())send(event);}catch{reason='incomplete_stream';}finish();});
  });
  this.controllers.add(upstream);
  upstream.setTimeout(90000,()=>upstream.destroy(Error('Provider response timeout')));
  upstream.on('error',()=>{reason??='connection_error';if(!res.headersSent)this.fail(res,502,'Qwen connection failed. Select Luna to continue.');finish();});
  res.on('close',()=>{if(!res.writableEnded){reason='cancelled';upstream.destroy();finish();}});
  upstream.end(wire);
 }
 close(){for(const request of this.controllers)request.destroy();this.server?.closeAllConnections();this.server?.close();}
}
