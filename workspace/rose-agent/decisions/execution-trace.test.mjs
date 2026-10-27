import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ExecutionTrace,hydrateTrace} from './execution-trace.mjs';
test('trace links unchanged model input, guidance and phone action with deduplicated images',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'execution-trace-'));
 try{
  const t=new ExecutionTrace(root,{request:'Close the app'}),body={input:[{image_url:'data:image/jpeg;base64,aW1hZ2U='}]};
  const response={status:'completed',output_text:'{"nextSteps":["Go home"]}'};let seen;
  const client=t.client({responses:{create:async b=>{seen=b;return response;}}});
  assert.equal(await client.responses.create(body),response);assert.equal(seen,body);
  const result={status:'ok',imageBase64:'aW1hZ2U='};assert.equal(await t.phone(async()=>result)({action:'screenshot'}),result);
  const rows=fs.readFileSync(path.join(t.dir,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  const request=rows.find(r=>r.kind==='model_request'),reply=rows.find(r=>r.kind==='model_response');
  assert.equal(request.data.callId,reply.data.callId);assert.deepEqual(hydrateTrace(request.data.body,t.dir),body);
  assert.equal(reply.data.result.output_text,response.output_text);
  assert.equal(fs.readdirSync(t.dir).filter(n=>n.endsWith('.jpg')).length,1);
  assert.equal(fs.statSync(path.join(t.dir,'events.jsonl')).mode&0o777,0o600);
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('logging failure cannot prevent a command or change its error',async()=>{
 const t=new ExecutionTrace('/dev/null/not-a-directory',{}),result={status:'ok'};
 assert.equal(await t.phone(async()=>result)({action:'home'}),result);
 const error=new Error('model failed');await assert.rejects(t.client({decisions:{create:async()=>{throw error;}}}).decisions.create({}),e=>e===error);
});
test('old traces expire when the next run begins',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'execution-trace-'));
 try{const old=new ExecutionTrace(root,{}, {now:()=>1});new ExecutionTrace(root,{}, {now:()=>90000000});assert.equal(fs.existsSync(old.dir),false);}
 finally{fs.rmSync(root,{recursive:true,force:true});}
});
