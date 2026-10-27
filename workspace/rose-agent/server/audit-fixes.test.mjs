import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {TaskEvidence} from './task-evidence.mjs';
import {VisualVerification,createVisualReviewer} from './visual-verification.mjs';
import {taskReviewTarget,taskReviewMatches} from './task-review.mjs';
import {initialSignature} from './evaluation-gateway.mjs';
import {BasetenGateway,BASETEN_MODEL} from './baseten/gateway.mjs';
import {CerebrasGateway,CEREBRAS_MODEL} from './cerebras/gateway.mjs';

test('200 entries survive; entry 201 is explicitly identified as omitted',()=>{
 const e=new TaskEvidence();const parts=Array.from({length:201},(_,i)=>({type:'text',text:'ENTRY_'+(i+1)+'_END'}));
 e.output('result','Source',parts);const s=e.snapshot();
 assert.match(s.records[0].text,/ENTRY_200_END/);assert.doesNotMatch(s.records[0].text,/ENTRY_201_END/);
 assert.equal(s.truncated,true);assert.deepEqual(s.omissions,[{id:'result',source:'Source',reason:'entry_limit',limit:200,firstOmittedEntry:201,count:1,location:'$'}]);
 e.reset();e.output('short','Source',parts.slice(0,200));assert.equal(e.snapshot().truncated,false);
});

test('nesting remains at eight and every skipped branch is labelled',()=>{
 const e=new TaskEvidence();let value={type:'text',text:'deep fact'};
 for(let i=0;i<10;i++)value={content:[value]};
 e.output('nested','Nested source',value);e.output('nested','Nested source',value);
 const s=e.snapshot();assert.equal(s.omissions.length,1);assert.equal(s.omissions[0].reason,'nesting_limit');assert.equal(s.omissions[0].limit,8);assert.ok(s.omissions[0].location);assert.equal(s.truncated,true);
});

test('only four tool images remain, with source metadata for evicted images',()=>{
 const e=new TaskEvidence();for(let i=0;i<5;i++)e.addImage('image-'+i,'Screen '+i,'data:image/png;base64,'+Buffer.from('image'+i).toString('base64'));
 const s=e.snapshot();assert.equal(s.images.length,4);assert.equal(s.images[0].id,'image-1');assert.equal(s.omissions.length,1);
 assert.equal(s.omissions[0].id,'image-0');assert.equal(s.omissions[0].source,'Screen 0');assert.equal(s.omissions[0].reason,'image_limit');
 assert.ok(!JSON.stringify(s.omissions).includes('base64'));e.reset();assert.deepEqual(e.snapshot().omissions,[]);
});

test('omission metadata and complete HTML reach the same checker call and are logged',async()=>{
 const e=new TaskEvidence();e.omit('old-image','Earlier screen',{reason:'image_limit',limit:4,count:1});e.addText({id:'fact',source:'Tool',text:'Correct price is $263'});
 const artifact={type:'html',html:'<p>Wrong price: $236</p>',sha256:'card-hash'};let calls=0,sent;const logs=[];
 const review=createVisualReviewer({responses:{create:async r=>{calls++;sent=r;return {status:'completed',output_text:JSON.stringify({status:'continue',summary:'Card price is wrong',evidence:[],nextSteps:['Correct the card']})};}}});
 const gate=new VisualVerification({enabled:true,evidence:e,review,phone:()=>{throw Error('No new screenshot needed');},log:v=>logs.push(v)});
 gate.reset('Show price');const r=await gate.assess('Price is $263',{model:'gpt-6-luna',effort:'medium',serviceTier:'priority'},{artifact,force:true});
 assert.equal(calls,1);assert.equal(r.status,'continue');const body=JSON.parse(sent.input[0].content[0].text);
 assert.deepEqual(body.resultArtifact,artifact);assert.equal(body.taskEvidence.omissions[0].id,'old-image');
 assert.match(sent.instructions,/BOTH/);assert.match(sent.instructions,/omission alone does not invalidate/);
 const logged=logs.find(x=>x.stage==='visual_review');assert.equal(logged.artifactHash,'card-hash');assert.equal(logged.omissionCount,1);
});

test('approval becomes stale if the task, correction, candidate or card changes',()=>{
 const task={id:'one',request:'Compare',latestInstruction:'Use USD',presentation:'html',visualization:{html:'<p>$263</p>'}};
 const target=taskReviewTarget(task,'Price is $263');assert.ok(taskReviewMatches(target,{...task,title:'New title',progress:'checking'},'Price is $263'));
 for(const changed of [{...task,id:'two'},{...task,latestInstruction:'Use EUR'},{...task,visualization:{html:'<p>$236</p>'}}])assert.equal(taskReviewMatches(target,changed,'Price is $263'),false);
 assert.equal(taskReviewMatches(target,task,'Price is $236'),false);
});

test('benchmark preserves array order but ignores object-key ordering',()=>{
 const source={input:[{role:'user',content:'Current request'}],tools:[{type:'function',name:'a',parameters:{type:'object'}},{type:'function',name:'b',parameters:{type:'object'}}]};
 const a=initialSignature(source,'Current request');const b=initialSignature({...source,tools:[...source.tools].reverse()},'Current request');
 assert.notEqual(a.sha256,b.sha256);assert.notEqual(a.toolsHash,b.toolsHash);assert.equal(a.signatureVersion,2);
 const c=initialSignature({...source,tools:source.tools.map(t=>({parameters:t.parameters,name:t.name,type:t.type}))},'Current request');assert.equal(a.sha256,c.sha256);
});

for(const [Gateway,model] of [[BasetenGateway,BASETEN_MODEL],[CerebrasGateway,CEREBRAS_MODEL]]){
 test(model+' live usage ignores test ledger while explicit tests retain the cap',async t=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'budget-scope-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const keyFile=path.join(dir,'key'),budgetFile=path.join(dir,'budget');fs.writeFileSync(keyFile,'offline-key');
  const original=JSON.stringify({day:new Date().toISOString().slice(0,10),spentUsd:1000});fs.writeFileSync(budgetFile,original);
  let upstreamCalls=0;const records=[];
  const fake=http.createServer(async(req,res)=>{for await(const chunk of req){}upstreamCalls++;res.writeHead(200,{'content-type':'text/event-stream'});res.end('data: '+JSON.stringify({choices:[{index:0,delta:{content:'OK'},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:1,total_tokens:11}})+'\n\ndata: [DONE]\n\n');});
  await new Promise(r=>fake.listen(0,'127.0.0.1',r));t.after(()=>{fake.closeAllConnections();fake.close();});
  const request=(_url,opts,cb)=>http.request('http://127.0.0.1:'+fake.address().port,opts,cb);
  const body=JSON.stringify({model,input:[{role:'user',content:'Say OK'}],max_output_tokens:32});
  const live=await new Gateway({keyFile,budgetFile,dailyLimitUsd:1,enforceBudget:false,request,record:r=>records.push(r)}).listen();t.after(()=>live.close());
  assert.equal(live.available(),true);
  const ok=await fetch(live.url+'/responses',{method:'POST',headers:{authorization:'Bearer '+live.token},body});await ok.text();assert.equal(ok.status,200);assert.equal(upstreamCalls,1);
  assert.equal(fs.readFileSync(budgetFile,'utf8'),original);assert.equal(records.at(-1).budgetEnforced,false);assert.ok(records.at(-1).usage);assert.equal(live.reserved,0);
  const trial=await new Gateway({keyFile,budgetFile,dailyLimitUsd:1,request}).listen();t.after(()=>trial.close());assert.equal(trial.available(),false);
  const blocked=await fetch(trial.url+'/responses',{method:'POST',headers:{authorization:'Bearer '+trial.token},body});await blocked.text();assert.equal(blocked.status,402);assert.equal(upstreamCalls,1);
 });
}
