import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {PersonalMemoryTrial,atomicJSON,renderReview,validateCandidates,openAIExtractor} from './memory.mjs';

const event=(id,text,at=1001,role='user',source='voice')=>({id,text,at,role,source});
const candidate=(id='one',text='Prefers light blue.',quote='I prefer light blue.')=>({text,category:'preference',scope:'durable',reason:'Explicit preference; speaker identity unverified.',evidence:[{event_id:id,quote}],related_candidate_ids:[]});
function fixture(t,extract){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-memory-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));let now=1000;const timeline=path.join(dir,'timeline.json');atomicJSON(timeline,{messages:[]});const options={dir:path.join(dir,'memory'),timelineFile:timeline,now:()=>now,extract:extract||(async()=>({result:{candidates:[candidate()]},usage:{input_tokens:5,output_tokens:3},model:'test'}))};const trial=new PersonalMemoryTrial(options);return {trial,options,dir,timeline,setTime:n=>now=n,write:messages=>atomicJSON(timeline,{messages})};}

test('only new messages, duplicates ignored, evidence survives original timeline pruning and restart',async t=>{
 const f=fixture(t);f.write([event('old','Old preference',999),event('one','I prefer light blue.')]);assert.equal(f.trial.capture(),1);assert.equal(f.trial.capture(),0);
 assert.equal(await f.trial.process({force:true}),true);f.write([]);const recovered=new PersonalMemoryTrial(f.options);assert.equal(recovered.events.length,1);assert.equal(recovered.state.candidates.length,1);assert.equal(recovered.status().injectionEnabled,false);assert.equal(recovered.state.candidates[0].attribution,'voice-speaker-unverified');assert.equal(recovered.state.processedCount,1);assert.equal(fs.statSync(recovered.evidenceFile).mode&0o777,0o600);assert.equal(fs.statSync(recovered.stateFile).mode&0o777,0o600);
});
test('forged quotes, assistant evidence, old context evidence, invalid links and secrets fail closed',()=>{
 const good=event('one','I prefer light blue.');
 assert.throws(()=>validateCandidates({candidates:[candidate('one','Blue','not spoken')]},[good]));
 assert.throws(()=>validateCandidates({candidates:[candidate()]},[{...good,role:'assistant'}]));
 assert.throws(()=>validateCandidates({candidates:[candidate()]},[{...good,isNew:false}]));
 assert.throws(()=>validateCandidates({candidates:[{...candidate(),related_candidate_ids:['missing']}]},[good]));
 assert.throws(()=>validateCandidates({candidates:[candidate('one','password: secret')]},[good]));
 assert.equal(validateCandidates({candidates:[]},[good]).length,0);
});
test('model failures preserve evidence and processing cursor; automatic retries are bounded',async t=>{
 let calls=0;const f=fixture(t,async()=>{calls++;throw Error('bad response');});f.write([event('one','I prefer light blue.')]);f.trial.capture();await f.trial.process({force:true});assert.equal(f.trial.state.processedCount,0);assert.equal(f.trial.events.length,1);await f.trial.process({force:true});assert.equal(calls,1);f.setTime(122000);await f.trial.process({force:true});assert.equal(calls,2);assert.equal(f.trial.state.candidates.length,0);
});
test('capture continues while extraction runs; an in-flight result does not consume later evidence',async t=>{
 let resolve;const f=fixture(t,()=>new Promise(r=>resolve=r));f.write([event('one','I prefer light blue.')]);f.trial.capture();const running=f.trial.process({force:true});f.write([event('one','I prefer light blue.'),event('two','Now I prefer orange.',1002)]);assert.equal(f.trial.capture(),1);assert.equal(await f.trial.process({force:true}),false);resolve({result:{candidates:[candidate()]}});await running;assert.equal(f.trial.state.processedCount,1);assert.equal(f.trial.events.length,2);
});
test('pause blocks new capture and discards in-flight extraction without changing any voice configuration',async t=>{
 let resolve;const f=fixture(t,()=>new Promise(r=>resolve=r));f.write([event('one','I prefer light blue.')]);f.trial.capture();const job=f.trial.process({force:true});f.trial.setCollection(false);f.write([event('two','I prefer orange.',1002)]);assert.equal(f.trial.capture(),0);resolve({result:{candidates:[candidate()]}});await job;assert.equal(f.trial.state.candidates.length,0);assert.equal(f.trial.state.processedCount,0);assert.equal(f.trial.status().injectionEnabled,false);
});
test('temporary proposals and corrections remain reviewable and never overwrite old evidence',async t=>{
 let n=0;const f=fixture(t,async({existing})=>({result:{candidates:[n++?{...candidate('two','Prefers orange.','Actually I prefer orange.'),related_candidate_ids:[existing[0].id]}:{...candidate(),scope:'temporary'}]}}));f.write([event('one','I prefer light blue.')]);f.trial.capture();await f.trial.process({force:true});f.setTime(122000);f.write([event('two','Actually I prefer orange.',120000)]);f.trial.capture();await f.trial.process({force:true});assert.equal(f.trial.state.candidates.length,2);assert.equal(f.trial.state.candidates[0].scope,'temporary');assert.equal(f.trial.state.candidates[1].relatedCandidateIds[0],f.trial.state.candidates[0].id);assert.ok(f.trial.state.candidates.every(c=>c.status==='proposed'));
});
test('review HTML escapes untrusted content and shows source attribution',t=>{
 const f=fixture(t);f.write([event('one','<script>alert(1)</script>')]);f.trial.capture();const html=renderReview(f.trial);assert.ok(!html.includes('<script>'));assert.match(html,/&lt;script&gt;/);assert.match(html,/Collect only/);assert.match(html,/never used in replies/);
});
test('model adapter sends store:false with no tools and does not emit credentials in failures',async t=>{
 const f=fixture(t);const key=path.join(f.dir,'key');fs.writeFileSync(key,'test-secret');let payload;const extract=openAIExtractor({keyFile:key,request:async(_url,opts)=>{payload=JSON.parse(opts.body);return {ok:true,json:async()=>({status:'completed',model:'test',output:[{content:[{type:'output_text',text:'{"candidates":[]}'}]}]})};}});await extract({events:[],existing:[]});assert.equal(payload.store,false);assert.equal(payload.tools,undefined);assert.equal(payload.text.format.strict,true);const fail=openAIExtractor({keyFile:key,request:async()=>({ok:false,status:403})});await assert.rejects(fail({events:[],existing:[]}),/HTTP 403/);
});
test('resuming collection does not import speech from the paused interval',t=>{
 const f=fixture(t);f.setTime(2000);f.trial.setCollection(false);f.write([event('paused','I said this while paused.',2500)]);assert.equal(f.trial.capture(),0);f.setTime(3000);f.trial.setCollection(true);f.write([event('paused','I said this while paused.',2500),event('new','A new preference.',3001)]);assert.equal(f.trial.capture(),1);assert.equal(f.trial.events[0].id,'new');
});
test('more than 12 spaced attempts per hour are allowed but failed manual attempts still respect two minutes',async t=>{
 let calls=0;const f=fixture(t,async()=>{calls++;throw Error('offline');});f.write([event('one','I prefer light blue.')]);f.trial.capture();
 for(let i=0;i<14;i++){f.setTime(1000+i*121000);await f.trial.process({force:true});}
 assert.equal(calls,14);assert.equal(f.trial.state.processedCount,0);
 const last=f.trial.state.batches.at(-1).at;
 f.setTime(last+119999);assert.equal(await f.trial.process({force:true}),false);assert.equal(calls,14);
 f.setTime(last+120000);await f.trial.process({force:true});assert.equal(calls,15);
});
test('96 earlier calls in a day do not block a new batch',async t=>{
 const f=fixture(t);f.write([event('one','I prefer light blue.')]);f.trial.capture();
 const now=86400000;f.setTime(now);
 f.trial.state.batches=Array.from({length:96},(_,i)=>({id:'previous-'+i,at:now-12*3600000+i*300000,status:'completed'}));
 assert.equal(await f.trial.process({force:true}),true);
 assert.equal(f.trial.state.processedCount,1);assert.equal(f.trial.state.candidates.length,1);
});
test('invalid generated evidence is never committed, and no untrusted fallback is used',async t=>{
 const f=fixture(t,async()=>({result:{candidates:[candidate('one','Prefers orange.','I prefer orange.')]}}));f.write([event('one','I prefer light blue.')]);f.trial.capture();await f.trial.process({force:true});assert.equal(f.trial.state.candidates.length,0);assert.equal(f.trial.state.processedCount,0);assert.match(f.trial.state.lastError,/exact new user quotation/);
});

test('high reasoning uses Standard processing and records the actual returned tier',async t=>{
 const f=fixture(t);const key=path.join(f.dir,'key');fs.writeFileSync(key,'test-secret');let payload;
 const extract=openAIExtractor({keyFile:key,reasoningEffort:'high',serviceTier:'default',maxOutputTokens:8192,request:async(_url,opts)=>{
  payload=JSON.parse(opts.body);
  return {ok:true,json:async()=>({status:'completed',model:'gpt-6-luna',service_tier:'default',output:[{content:[{type:'output_text',text:'{"candidates":[]}'}]}]})};
 }});
 const result=await extract({events:[],existing:[]});
 assert.equal(payload.reasoning.effort,'high');assert.equal(payload.service_tier,'default');assert.equal(payload.max_output_tokens,8192);
 assert.equal(result.reasoningEffort,'high');assert.equal(result.serviceTier,'default');assert.equal(payload.store,false);
});
