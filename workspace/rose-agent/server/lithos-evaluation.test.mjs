import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {LithosEvaluationGateway,LITHOS_MODELS} from './lithos-evaluation.mjs';

test('Lithos adapter preserves low effort, tool programs and pixels and meters actual usage',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lithos-eval-'));
 fs.writeFileSync(path.join(dir,'key'),'private-test-key');
 let seen,target;
 const server=http.createServer(async(req,res)=>{
  let text='';for await(const b of req)text+=b;seen=JSON.parse(text);
  res.writeHead(200,{'content-type':'text/event-stream'});
  for(const c of [{choices:[{index:0,delta:{content:'Confirmed'},finish_reason:null}]},{choices:[{index:0,delta:{},finish_reason:'stop'}]},{choices:[],usage:{prompt_tokens:1000,completion_tokens:100,prompt_tokens_details:{cached_tokens:800},completion_tokens_details:{reasoning_tokens:50}}}])res.write('data: '+JSON.stringify(c)+'\n\n');
  res.end('data: [DONE]\n\n');
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const records=[];const model=Object.keys(LITHOS_MODELS)[0];
 const gateway=await new LithosEvaluationGateway({model,keyFile:path.join(dir,'key'),budgetFile:path.join(dir,'budget'),record:e=>records.push(e),request:(url,opts,cb)=>{target=url;return http.request('http://127.0.0.1:'+server.address().port,opts,cb);}}).listen();
 try{
  const payload={model,reasoning:{effort:'low'},max_output_tokens:16384,input:[{role:'user',content:[{type:'input_text',text:'Inspect this'},{type:'input_image',image_url:'data:image/jpeg;base64,AAAA',detail:'original'}]}],tools:[{type:'custom',name:'exec',description:'JavaScript',format:{type:'text'}}]};
  const response=await fetch(gateway.url+'/responses',{method:'POST',headers:{Authorization:'Bearer '+gateway.token},body:JSON.stringify(payload)});
  const result=await response.text();assert.equal(response.status,200);assert.match(result,/response.completed/);
  assert.equal(target,'https://api.lithosai.cloud/v1/chat/completions');assert.equal(seen.model,model);assert.equal(seen.reasoning_effort,'low');
  assert.equal(seen.messages[0].content[1].image_url.url,'data:image/jpeg;base64,AAAA');
  assert.equal(seen.messages[0].content[1].image_url.detail,'high');
  assert.equal(seen.tools[0].function.parameters.properties.input.type,'string');
  assert.ok(Math.abs(gateway.budget().spentUsd-.0000924)<1e-10);
  assert.equal(records.at(-1).usage.output_tokens_details.reasoning_tokens,50);
  assert.doesNotMatch(JSON.stringify(records),/private-test-key/);
  const bad=await fetch(gateway.url+'/responses',{method:'POST',headers:{Authorization:'Bearer '+gateway.token},body:JSON.stringify({...payload,model:'unapproved'})});assert.equal(bad.status,400);
 }finally{gateway.close();server.closeAllConnections();server.close();fs.rmSync(dir,{recursive:true,force:true});}
});
