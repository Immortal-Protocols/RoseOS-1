import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {initialSignature,EvaluationGateway} from './evaluation-gateway.mjs';
const request='Open the requested route';
const payload=()=>({model:'gpt-6-luna',instructions:'shared policy',input:[{type:'message',role:'user',content:[{type:'input_text',text:'Current request: '+request}]}],tools:[{type:'function',name:'phone',parameters:{type:'object'}}],reasoning:{effort:'medium'},max_output_tokens:16384});
test('initial audit compares full context and tools while allowing another current task',()=>{
 const a=payload(),b=payload();b.model='deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast';
 a.input[0].id='session-a';b.input[0].id='session-b';
 assert.equal(initialSignature(a,request).sha256,initialSignature(b,request).sha256);
 b.input[0].content[0].text='Current request: Another route';
 assert.equal(initialSignature(a,request).sha256,initialSignature(b,'Another route').sha256);
 b.tools[0].parameters={type:'string'};
 assert.notEqual(initialSignature(a,request).sha256,initialSignature(b,'Another route').sha256);
});
test('prior answers, tools, screenshots and duplicate request contexts fail before inference',()=>{
 for(const history of [{type:'message',role:'assistant',content:[{type:'output_text',text:'old answer'}]},{type:'function_call',name:'old_tool'},{type:'reasoning',summary:[]}]){
  const p=payload();p.input.unshift(history);assert.throws(()=>initialSignature(p,request),/history/);
 }
 const p=payload();p.input[0].content.push({type:'input_image',image_url:'data:image/jpeg;base64,a'});assert.throws(()=>initialSignature(p,request),/images/);
 const q=payload();q.input.push(q.input[0]);assert.throws(()=>initialSignature(q,request),/copies/);
});
test('reasoning is the only signature difference for an effort comparison',()=>{
 const a=payload(),b=payload();b.reasoning={effort:'none'};
 const pa=initialSignature(a,request),pb=initialSignature(b,request);
 assert.equal(pa.inputHash,pb.inputHash);assert.equal(pa.toolsHash,pb.toolsHash);
 assert.notEqual(pa.sha256,pb.sha256);
});
test('preflight stays local on every request and preserves a reviewable initial payload',async()=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-eval-audit-'));
 const g=await new EvaluationGateway({config:{directory,model:'gpt-6-luna',request,phase:'preflight'},openaiKeyFile:'/not-a-key',baseten:null}).listen();
 try{
  for(let i=0;i<2;i++){
   const response=await fetch(g.url+'/responses',{method:'POST',headers:{Authorization:'Bearer '+g.token},body:JSON.stringify(payload())});assert.equal(response.status,409);
  }
  const a=JSON.parse(fs.readFileSync(path.join(directory,'initial-audit.json')));assert.equal(a.initialImages,0);assert.equal(a.requestCount,1);
  assert.equal(fs.existsSync(path.join(directory,'call-1.json')),false);
 }finally{g.close();fs.rmSync(directory,{recursive:true,force:true});}
});
