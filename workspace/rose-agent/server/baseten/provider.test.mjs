import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {Codex} from '../codex.mjs';
import {modelRoute,modelLabel} from '../model-switch.mjs';
import {BASETEN_MODEL,BASETEN_PROVIDER,checkerRoute,BasetenGateway,requestFootprint} from './gateway.mjs';
import {createVisualReviewer} from '../visual-verification.mjs';

test('DeepSeek has its own route while the checker stays on Luna',async()=>{
 const route=modelRoute(BASETEN_MODEL);assert.equal(route.serviceTier,'default');assert.match(modelLabel(route),/DeepSeek.*Fast/);
 assert.throws(()=>modelRoute(BASETEN_MODEL,'ultra'));assert.throws(()=>modelRoute(BASETEN_MODEL,'medium','priority'));
 assert.deepEqual(checkerRoute(route),modelRoute('gpt-6-luna'));
 let sent;
 const review=createVisualReviewer({responses:{create:async request=>{sent=request;return {status:'completed',output_text:JSON.stringify({status:'complete',summary:'ok',evidence:[],nextSteps:[]})};}}});
 await review({request:'test',context:[],candidate:'done',corrections:[],history:[],evidence:{images:[]},route});
 assert.equal(sent.model,'gpt-6-luna');assert.equal(sent.reasoning.effort,'medium');assert.equal(sent.service_tier,'priority');
});
test('provider switching preserves history via fork and returns to the normal OpenAI connection',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-provider-'));
 try{
  const c=new Codex(dir,path.join(dir,'state.json'));c.thread='original';c.baseten={available:()=>true};const calls=[];
  c.rpc=async(method,params)=>{calls.push({method,params});return {thread:{id:'fork-'+calls.length,path:path.join(dir,'history-'+calls.length)}};};
  await c.prepareRoute(modelRoute(BASETEN_MODEL));assert.equal(c.provider,BASETEN_PROVIDER);
  assert.equal(calls[0].params.threadId,'original');assert.equal(calls[0].params.modelProvider,BASETEN_PROVIDER);
  assert.equal(JSON.parse(fs.readFileSync(c.state)).provider,BASETEN_PROVIDER);
  await c.prepareRoute(modelRoute(BASETEN_MODEL));assert.equal(calls.length,1);
  c.active='running';await assert.rejects(c.prepareRoute(modelRoute('gpt-6-luna')),/must finish/);c.active=null;
  await c.prepareRoute(modelRoute('gpt-6-luna'));assert.equal(c.provider,'openai');assert.equal(calls[1].method,'thread/read');
  assert.equal(calls[2].method,'thread/resume');assert.equal(calls[2].params.threadId,'original');
  assert.equal(calls[2].params.modelProvider,'openai');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('missing credentials and exhausted trial budget fail before changing provider',async()=>{
 const c=new Codex('/tmp','/tmp/unused');c.thread='original';c.baseten={available:()=>false};
 c.rpc=async()=>{throw Error('must not call')};
 await assert.rejects(c.prepareRoute(modelRoute(BASETEN_MODEL)),/unavailable/);
 assert.equal(c.thread,'original');assert.equal(c.provider,'openai');
});

test('rollback resumes clean OpenAI history and carries visible messages only',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-context-'));
 try{
  const c=new Codex(dir,path.join(dir,'state.json'));c.provider=BASETEN_PROVIDER;c.thread='deepseek';c.providerThreads={openai:'clean-openai'};const calls=[];
  c.rpc=async(method,params)=>{calls.push({method,params});if(method==='thread/read')return {thread:{turns:[{items:[{type:'userMessage',content:[{type:'text',text:'Previous request'}]},{type:'reasoning',text:'PRIVATE REASONING'},{type:'agentMessage',phase:'final_answer',text:'Previous answer'},{type:'commandExecution',aggregatedOutput:'PRIVATE TOOL OUTPUT'}]}]}};return {thread:{id:'clean-openai',path:path.join(dir,'history')}};};
  await c.prepareRoute(modelRoute('gpt-6-luna'));
  assert.equal(calls[1].method,'thread/resume');assert.equal(calls[1].params.threadId,'clean-openai');
  const context=JSON.stringify(calls[2].params);assert.match(context,/Previous request/);assert.match(context,/Previous answer/);assert.doesNotMatch(context,/PRIVATE/);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('gateway rejects unauthenticated requests without touching provider or budget',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-gateway-'));let calls=0;
 const gateway=await new BasetenGateway({keyFile:path.join(dir,'key'),budgetFile:path.join(dir,'budget'),request:()=>{calls++;throw Error('must not call');}}).listen();
 try{const r=await fetch(gateway.url+'/responses',{method:'POST',body:JSON.stringify({model:BASETEN_MODEL})});assert.equal(r.status,401);assert.equal(calls,0);assert.equal(gateway.budget().spentUsd,0);}
 finally{gateway.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('explicit OpenAI child models use OpenAI without exposing the Baseten credential',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-child-'));let seen;
 fs.writeFileSync(path.join(dir,'openai'),'test-openai');fs.writeFileSync(path.join(dir,'baseten'),'test-baseten');
 const fake=http.createServer(async(req,res)=>{let body='';for await(const b of req)body+=b;seen={authorization:req.headers.authorization,body:JSON.parse(body)};res.writeHead(200,{'content-type':'text/event-stream'});res.end('event: response.completed\ndata: {}\n\n');});
 await new Promise(resolve=>fake.listen(0,'127.0.0.1',resolve));let url;
 const gateway=await new BasetenGateway({keyFile:path.join(dir,'baseten'),openaiKeyFile:path.join(dir,'openai'),budgetFile:path.join(dir,'budget'),request:(target,options,callback)=>{url=target;return http.request('http://127.0.0.1:'+fake.address().port,options,callback);}}).listen();
 try{
  const result=await fetch(gateway.url+'/responses',{method:'POST',headers:{authorization:'Bearer '+gateway.token},body:JSON.stringify({model:'gpt-6.1-sol',input:'child task',store:true})});await result.text();
  assert.equal(url,'https://api.openai.com/v1/responses');assert.equal(seen.authorization,'Bearer test-openai');assert.equal(seen.body.store,false);assert.equal(gateway.budget().spentUsd,0);
 }finally{gateway.close();fake.closeAllConnections();fake.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('budget reserves visual tokens instead of charging base64 as language tokens',()=>{
 const body={messages:[{role:'user',content:[{type:'text',text:'Inspect the screen'},{type:'image_url',image_url:{url:'data:image/jpeg;base64,'+'a'.repeat(1000000),detail:'original'}}]}],max_tokens:4000};
 const large=requestFootprint(body);body.messages[0].content[1].image_url.url='data:image/jpeg;base64,abc';
 const small=requestFootprint(body);
 assert.equal(large.imageCount,1);assert.deepEqual(large,small);assert.ok(large.reserveUsd<.02);
 assert.equal(body.messages[0].content[1].image_url.detail,'original');
});
