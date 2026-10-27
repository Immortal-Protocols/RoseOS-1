// Used only by the explicitly enabled, fresh-thread evaluation harness.
import http from 'node:http';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {responsesToChat,chatStreamToResponses} from './baseten/protocol.mjs';
import {prepareRequest,streamAdapter} from './baseten/tools.mjs';

export const LOCAL_MODELS={
 'local/qwen38-27b-splash':{label:'Qwen 3.8 27B · Splash · Mac'},
 'local/qwen38-27b-nvfp4':{label:'Qwen 3.8 27B · Alienware'},
 'local/qwen38-flash-next-oq4e':{label:'Qwen 3.8 Flash Next · Mac'},
};

export function localChatRequest(source,config){
 if(!LOCAL_MODELS[config.model]||source.model!==config.model)throw Error('Unapproved local evaluation model');
 const prepared=prepareRequest({...source,store:false});
 const body=responsesToChat(prepared.body);
 body.model=config.upstreamModel;
 // Qwen's chat template accepts one opening system message. Preserve text
 // and order by joining only adjacent opening instruction messages.
 const opening=[];
 while(body.messages[0]?.role==='system')opening.push(body.messages.shift().content);
 if(opening.length)body.messages.unshift({role:'system',content:opening.join('\n\n')});
 if(config.model==='local/qwen38-27b-nvfp4')for(const message of body.messages){
  if(message.reasoning_content!==undefined){message.reasoning=message.reasoning_content;delete message.reasoning_content;}
 }
 body.stream_options={include_usage:true};
 body.reasoning_effort=source.reasoning?.effort||'low';
 // Splash supports constrained decoding. Keep the exact custom-tool envelope
 // rather than accepting or rewriting a model's different argument field.
 if(config.model==='local/qwen38-27b-splash')for(const tool of body.tools||[]){
  if(prepared.names.has(tool.function?.name))tool.function.strict=true;
 }
 body.temperature=1.0;body.top_p=.95;body.top_k=20;body.min_p=0;
 body.chat_template_kwargs={enable_thinking:true,preserve_thinking:true,reasoning_effort:source.reasoning?.effort||'low'};
 // These engines consume the supplied pixels directly; the Codex-only detail
 // enum isn't understood by all OpenAI-compatible image parsers.
 let imageCount=0;const imageHashes=[];
 for(const message of body.messages)if(Array.isArray(message.content))for(const part of message.content){
  if(part.type==='image_url'){
   imageCount++;imageHashes.push(crypto.createHash('sha256').update(part.image_url.url).digest('hex'));
   delete part.image_url.detail;
  }
 }
 return {body,names:prepared.names,aliases:prepared.aliases,imageCount,imageHashes};
}

export function localChatChunk(chunk){
 // vLLM names this field reasoning; oMLX names it reasoning_content.
 // Preserve either spelling for the existing Responses transcript adapter.
 return {...chunk,choices:(chunk.choices||[]).map(choice=>({
  ...choice,delta:{...choice.delta,...(choice.delta?.reasoning!==undefined&&choice.delta?.reasoning_content===undefined?{reasoning_content:choice.delta.reasoning}:{})}
 }))};
}

export class LocalEvaluationGateway{
 constructor({config,record=()=>{}}){
  if(!LOCAL_MODELS[config.model])throw Error('Unapproved local model');
  const url=new URL(config.upstreamUrl);
  if(url.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||url.pathname!=='/v1/chat/completions')throw Error('Local evaluation requires a loopback model endpoint');
  this.config=config;this.record=record;this.upstream=url;this.token=crypto.randomBytes(32).toString('hex');this.requests=new Set();
 }
 async listen(){
  this.server=http.createServer((req,res)=>this.handle(req,res).catch(error=>this.fail(res,502,error.message)));
  await new Promise(resolve=>this.server.listen(0,'127.0.0.1',resolve));
  this.url='http://127.0.0.1:'+this.server.address().port;return this;
 }
 fail(res,status,message){if(res.headersSent){res.end();return;}res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify({error:{message}}));}
 async handle(req,res){
  if(req.method!=='POST'||req.url!=='/responses')return this.fail(res,404,'Unexpected local evaluation endpoint');
  if(req.headers.authorization!=='Bearer '+this.token)return this.fail(res,401,'Local evaluation authentication required');
  let bytes=0;const chunks=[];for await(const chunk of req){bytes+=chunk.length;if(bytes>32*1024**2)return this.fail(res,413,'Evaluation request too large');chunks.push(chunk);}
  const source=JSON.parse(Buffer.concat(chunks));
  const {body,names,aliases,imageCount,imageHashes}=localChatRequest(source,this.config);
  const wire=Buffer.from(JSON.stringify(body));const started=Date.now();let firstTokenMs=null,usage=null,done=false,reason=null,status=0,buffer='';const rawToolDeltas=[];
  const chat=chatStreamToResponses(this.config.model),convert=streamAdapter(names,aliases);
  const finish=()=>{
   if(done)return;done=true;this.requests.delete(upstream);
   this.record({model:this.config.model,upstreamModel:body.model,status,elapsedMs:Date.now()-started,firstTokenMs,usage,costUsd:0,imageCount,imageHashes,reason,reasoningEffort:body.reasoning_effort,rawToolDeltas});res.end();
  };
  const send=event=>{
   if(/delta$/.test(event.type))firstTokenMs??=Date.now()-started;
   if(event.response?.usage)usage=event.response.usage;
   for(const translated of convert(event))res.write('event: '+translated.type+'\ndata: '+JSON.stringify(translated)+'\n\n');
  };
  const headers={'Content-Type':'application/json','Content-Length':wire.length};
  if(this.config.upstreamKeyFile)headers.Authorization='Bearer '+fs.readFileSync(this.config.upstreamKeyFile,'utf8').trim();
  const upstream=http.request(this.upstream,{method:'POST',headers},response=>{
   status=response.statusCode;
   if(status>=400){let detail='';response.on('data',chunk=>{if(detail.length<8192)detail+=chunk;});response.on('end',()=>{reason=detail;this.fail(res,status,'Local model rejected request: '+detail);finish();});return;}
   res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-store'});
   response.on('data',chunk=>{
    buffer+=chunk;let end;
    while((end=buffer.indexOf('\n'))>=0){
     const line=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!line.startsWith('data:'))continue;
     const data=line.slice(5).trim();if(!data||data==='[DONE]')continue;
     try{const chunk=JSON.parse(data);for(const choice of chunk.choices||[])if(choice.delta?.tool_calls)rawToolDeltas.push(choice.delta.tool_calls);for(const event of chat.push(localChatChunk(chunk)))send(event);}catch(error){reason='Invalid local model stream: '+error.message;upstream.destroy(error);}
    }
   });
   response.on('end',()=>{try{for(const event of chat.end())send(event);}catch(error){reason=error.message;}finish();});
  });
  this.requests.add(upstream);upstream.setTimeout(90000,()=>upstream.destroy(Error('Local model response timeout')));
  upstream.on('error',error=>{reason=error.message;this.fail(res,502,error.message);finish();});
  res.on('close',()=>{if(!res.writableEnded)upstream.destroy(Error('Evaluation cancelled'));});upstream.end(wire);
 }
 close(){for(const request of this.requests)request.destroy();this.server?.closeAllConnections();this.server?.close();}
}
