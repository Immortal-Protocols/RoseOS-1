import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {Codex} from '../codex.mjs';
import {modelRoute,modelLabel} from '../model-switch.mjs';
import {checkerRoute} from '../baseten/gateway.mjs';
import {CEREBRAS_MODEL,CEREBRAS_PROVIDER,CerebrasGateway,requestFootprint} from './gateway.mjs';

test('Qwen low uses Cerebras and keeps completion review on Luna',()=>{
 const r=modelRoute(CEREBRAS_MODEL);assert.equal(r.effort,'low');assert.match(modelLabel(r),/Qwen.*Low.*Cerebras/);
 assert.deepEqual(checkerRoute(r),modelRoute('gpt-6-luna'));
 assert.throws(()=>modelRoute(CEREBRAS_MODEL,'ultra'));
 assert.throws(()=>modelRoute(CEREBRAS_MODEL,'low','priority'));
});
test('Qwen provider can return to the untouched OpenAI thread',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'qwen-route-'));
 try{
  const c=new Codex(dir,path.join(dir,'state'));c.thread='original';c.cerebras={available:()=>true};const calls=[];
  c.rpc=async(method,params)=>{calls.push({method,params});if(method==='thread/read')return {thread:{turns:[]}};return {thread:{id:params.threadId==='original'&&method==='thread/resume'?'original':'qwen-thread'}};};
  await c.prepareRoute(modelRoute(CEREBRAS_MODEL));assert.equal(c.provider,CEREBRAS_PROVIDER);
  assert.equal(calls[0].params.config.web_search,'disabled');
  await c.prepareRoute(modelRoute('gpt-6-luna'));assert.equal(c.provider,'openai');assert.equal(c.thread,'original');
  assert.equal(calls[2].params.config.web_search,'live');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('Cerebras request preserves screenshots, low effort and one leading system block',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'qwen-wire-'));let seen;
 fs.writeFileSync(path.join(dir,'key'),'test-key');
 const fake=http.createServer(async(req,res)=>{
  let text='';for await(const b of req)text+=b;seen=JSON.parse(text);
  res.writeHead(200,{'content-type':'text/event-stream'});
  res.end('data: '+JSON.stringify({choices:[{index:0,delta:{content:'Ready'},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:1,total_tokens:11}})+'\n\ndata: [DONE]\n\n');
 });
 await new Promise(resolve=>fake.listen(0,'127.0.0.1',resolve));
 const g=await new CerebrasGateway({keyFile:path.join(dir,'key'),budgetFile:path.join(dir,'budget'),request:(_url,opts,cb)=>http.request('http://127.0.0.1:'+fake.address().port,opts,cb)}).listen();
 try{
  const r=await fetch(g.url+'/responses',{method:'POST',headers:{authorization:'Bearer '+g.token},body:JSON.stringify({model:CEREBRAS_MODEL,reasoning:{effort:'low'},max_output_tokens:100,input:[{role:'developer',content:'system one'},{role:'user',content:[{type:'input_image',image_url:'data:image/jpeg;base64,YQ==',detail:'original'}]},{role:'developer',content:'system two'}]})});
  assert.equal(r.status,200);assert.match(await r.text(),/response.completed/);
  assert.equal(seen.reasoning_effort,'low');assert.equal(seen.reasoning_format,'parsed');assert.equal(seen.max_completion_tokens,100);
  assert.equal(seen.messages[0].role,'system');assert.equal(seen.messages.filter(x=>x.role==='system').length,1);
  assert.equal(seen.messages[1].content[0].image_url.url,'data:image/jpeg;base64,YQ==');assert.equal(seen.messages[1].content[0].image_url.detail,undefined);
 }finally{g.close();fake.closeAllConnections();fake.close();fs.rmSync(dir,{recursive:true,force:true});}
});
test('base64 size never enters Cerebras token reservation',()=>{
 const a={messages:[{role:'user',content:[{type:'image_url',image_url:{url:'data:image/jpeg;base64,abc'}}]}],max_completion_tokens:500};
 const b=structuredClone(a);b.messages[0].content[0].image_url.url+='a'.repeat(1000000);
 assert.deepEqual(requestFootprint(a),requestFootprint(b));assert.equal(requestFootprint(a).imageCount,1);
});
