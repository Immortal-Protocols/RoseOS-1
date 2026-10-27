import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {responsesToChat,chatStreamToResponses} from './baseten/protocol.mjs';
import {prepareRequest,streamAdapter} from './baseten/tools.mjs';
// Evaluation-only provider. No production model selection or credentials change.
export const LITHOS_MODELS={
 'deepseek-ai/DeepSeek-V4.1-Flash':{input:.15,cached:.003,output:.60},
 'deepseek-ai/DeepSeek-V4.1-Flash-ultra':{input:.35,cached:.007,output:1.40},
 'zai-org/GLM-5.3-Flash-ultra':{input:.30,cached:.06,output:1.00},
 'zai-org/GLM-5.3-Flash-ultra-chat':{input:.30,cached:.06,output:1.00},
};
export function requestFootprint(body,rates={input:.60,output:2.40}){
 let imageCount=0;
 const text=JSON.stringify(body,(key,value)=>{
  if(key==='image_url'&&value?.url){imageCount++;return {...value,url:''};}
  return value;
 });
 // Images are visual tokens, not the bytes of their base64 transport. DeepSeek
 // documents <=1024 tokens/image; reserve 4x that plus UTF-8 text bytes and
 // envelope overhead. Do not reduce actual image detail or assume cache hits.
 // https://api-docs.deepseek.com/guides/vision/#token-usage
 const inputUpperBound=Buffer.byteLength(text)+imageCount*4096+4096;
 return {imageCount,inputUpperBound,reserveUsd:(inputUpperBound*rates.input+(body.max_tokens||16384)*rates.output)/1e6};
}

// Local authenticated wire adapter; never exposes the provider credential to
// Codex, the phone, logs, or a public listener. Failed actions aren't replayed.
export class LithosEvaluationGateway {
 constructor({model,keyFile,openaiKeyFile,budgetFile,record=()=>{},dailyLimitUsd=5,request=https.request}){
  if(!LITHOS_MODELS[model])throw Error("Unapproved Lithos evaluation model");
  this.model=model;this.rates=LITHOS_MODELS[model];
  Object.assign(this,{keyFile,openaiKeyFile,budgetFile,record,dailyLimitUsd,request});
  this.token=crypto.randomBytes(32).toString('hex');this.controllers=new Set();this.reserved=0;
 }
 budget(){
  const day=new Date().toISOString().slice(0,10);let saved;
  try{saved=JSON.parse(fs.readFileSync(this.budgetFile,'utf8'));}catch{}
  return saved?.day===day?saved:{day,spentUsd:0};
 }
 charge(value){const b=this.budget();b.spentUsd+=value;fs.writeFileSync(this.budgetFile+'.tmp',JSON.stringify(b),{mode:0o600});fs.renameSync(this.budgetFile+'.tmp',this.budgetFile);}
 available(){return fs.existsSync(this.keyFile)&&this.budget().spentUsd<this.dailyLimitUsd;}
 async listen(){
  this.server=http.createServer((req,res)=>this.handle(req,res).catch(()=>{if(!res.headersSent)this.fail(res,502,'Provider connection failed');else res.end();}));
  await new Promise((resolve,reject)=>{this.server.once('error',reject);this.server.listen(0,'127.0.0.1',resolve);});
  this.url='http://127.0.0.1:'+this.server.address().port;return this;
 }
 fail(res,status,message){res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify({error:{message}}));}
 async handle(req,res){
  if(req.method!=='POST'||req.url!=='/responses')return this.fail(res,404,'Unsupported Lithos evaluation endpoint');
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
  if(source.model!==this.model)return this.fail(res,400,'The trial credential supports only the selected Lithos model');
  if(!fs.existsSync(this.keyFile))return this.fail(res,503,'Lithos evaluation credential is unavailable. Select Luna to continue.');
  source.store=false;source.max_output_tokens=Math.min(source.max_output_tokens||16384,16384);
  let body,names,aliases;try{const p=prepareRequest(source);names=p.names;aliases=p.aliases;body=responsesToChat(p.body);body.stream_options={include_usage:true};
   for(const m of body.messages)if(Array.isArray(m.content))for(const p of m.content)if(p.type==='image_url'&&p.image_url.detail==='original')p.image_url.detail='high';}catch(error){this.record({status:'unsupported_request',reason:error.message,tools:source.tools?.map(t=>({type:t.type,name:t.name,tools:t.tools?.map(x=>({type:x.type,name:x.name}))}))});return this.fail(res,400,error.message);}
  const wire=Buffer.from(JSON.stringify(body));
  const footprint=requestFootprint(body,this.rates),reserve=footprint.reserveUsd;
  if(this.budget().spentUsd+this.reserved+reserve>this.dailyLimitUsd){this.record({status:'local_budget_rejected',spentUsd:this.budget().spentUsd,...footprint});return this.fail(res,402,'Lithos evaluation budget cannot cover this request. Select Luna to continue.');}
  this.record({status:'request',model:this.model,reasoningEffort:body.reasoning_effort, ...footprint});
  this.reserved+=reserve;
  const key=fs.readFileSync(this.keyFile,'utf8').trim(),started=Date.now(),session=String(req.headers.session_id||req.headers['x-session-affinity']||'pixel-voice').slice(0,150);
  const chat=chatStreamToResponses(this.model),convert=streamAdapter(names,aliases);
  let terminal=false,finished=false,buffer='',usage=null,httpStatus=0,firstTokenMs=null,reason=null;
  const finish=()=>{
   if(finished)return;finished=true;this.reserved-=reserve;this.controllers.delete(upstream);
   const cost=usage?(Math.max(0,usage.input_tokens-(usage.input_tokens_details?.cached_tokens||0))*this.rates.input+(usage.input_tokens_details?.cached_tokens||0)*this.rates.cached+usage.output_tokens*this.rates.output)/1e6:reserve;
   this.charge(cost);
   try{this.record({model:this.model,status:terminal?'completed':'failed',httpStatus,elapsedMs:Date.now()-started,firstTokenMs,usage,costUsd:cost,costEstimated:!usage,imageCount:footprint.imageCount,reason});}catch{}
   res.end();
  };
  const send=event=>{
   if(event.type==='response.output_text.delta'||event.type==='response.function_call_arguments.delta')firstTokenMs??=Date.now()-started;
   if(['response.completed','response.incomplete','response.failed'].includes(event.type)){terminal=event.type==='response.completed';usage=event.response.usage;reason=terminal?null:event.type;}
   for(const e of convert(event))res.write('event: '+e.type+'\ndata: '+JSON.stringify(e)+'\n\n');
  };
  const upstream=this.request('https://api.lithosai.cloud/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json','Content-Length':wire.length,'x-session-affinity':session}},up=>{
   httpStatus=up.statusCode;
   if(up.statusCode>=400){
    reason='upstream_'+up.statusCode;let detail='';up.on('data',b=>{if(detail.length<4096)detail+=b.toString();});up.on('end',()=>this.record({status:'provider_error',httpStatus:up.statusCode,detail}));
    this.fail(res,up.statusCode,'Lithos provider rejected the request ('+up.statusCode+'). Select Luna to continue; completed actions will not be retried.');
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
  upstream.on('error',()=>{reason??='connection_error';if(!res.headersSent)this.fail(res,502,'Lithos connection failed. Select Luna to continue.');finish();});
  res.on('close',()=>{if(!res.writableEnded){reason='cancelled';upstream.destroy();finish();}});
  upstream.end(wire);
 }
 close(){for(const request of this.controllers)request.destroy();this.server?.closeAllConnections();this.server?.close();}
}
