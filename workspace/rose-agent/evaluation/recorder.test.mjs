import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EvidenceRecorder} from './recorder.mjs';
import {exportCase,validateCase} from './export-case.mjs';
const temp=()=>fs.mkdtempSync(path.join(os.tmpdir(),'pixel-evidence-test-'));
function rows(dir){return fs.readdirSync(path.join(dir,'streams')).flatMap(n=>fs.readFileSync(path.join(dir,'streams',n),'utf8').trim().split('\n').filter(Boolean).map(JSON.parse));}
test('capture preserves actual unknown result, redacts credentials, and pins deduplicated images',async()=>{
 const directory=temp(),r=new EvidenceRecorder({directory});
 try{
  const source={status:'verified',artist:null,playing:true,password:'private',imageBase64:Buffer.from('test-image').toString('base64')};
  r.record('request.started',{request:'Play some music',source:'test',observationBatching:{enabled:true,policyVersion:1}},{caseId:'test-case'});
  r.record('phone.completed',source,{caseId:'test-case'});r.record('phone.completed',source,{caseId:'test-case'});
  r.record('request.finished',{status:'completed'},{caseId:'test-case'});
  await r.flush();
  const events=rows(directory),result=events.find(e=>e.kind==='phone.completed').data;
  assert.equal(result.artist,null);assert.equal(result.status,'verified');assert.equal(result.password,'[redacted]');
  assert.equal(source.password,'private');assert.equal(typeof source.imageBase64,'string');
  assert.equal(fs.readdirSync(path.join(directory,'assets')).length,1);
  const m=JSON.parse(fs.readFileSync(path.join(directory,'cases/test-case.json')));
  assert.equal(m.state,'recorded');assert.equal(m.outcome,'unassessed');
  assert.deepEqual(m.observationBatching,{enabled:true,policyVersion:1});
  assert.equal(fs.statSync(path.join(directory,'streams',fs.readdirSync(path.join(directory,'streams'))[0])).mode&0o777,0o600);
 }finally{await r.close();fs.rmSync(directory,{recursive:true,force:true});}
});
test('transcript capture starts at attachment and retains usage without replaying tools',async()=>{
 const directory=temp(),source=path.join(directory,'original.jsonl');fs.writeFileSync(source,'old\n');
 const r=new EvidenceRecorder({directory:path.join(directory,'out')});
 try{
  r.attachTranscript(source);await r.flush();
  const record={timestamp:new Date().toISOString(),type:'event_msg',payload:{type:'token_count',info:{last_token_usage:{input_tokens:123}}}};
  fs.appendFileSync(source,JSON.stringify(record)+'\n');await r.flush();
  assert.equal(rows(path.join(directory,'out')).filter(e=>e.kind==='worker.event_msg').length,1);
  assert.equal(fs.readFileSync(source,'utf8'),'old\n'+JSON.stringify(record)+'\n');
 }finally{await r.close();fs.rmSync(directory,{recursive:true,force:true});}
});
test('storage exhaustion reports incompleteness without throwing into the task',async()=>{
 const directory=temp(),r=new EvidenceRecorder({directory,maxBytes:2000});
 try{assert.equal(r.record('large',{text:'a'.repeat(5000)}),true);const status=await r.flush();assert.match(status.error,/limit/);assert.ok(status.dropped>0);}
 finally{await r.close();fs.rmSync(directory,{recursive:true,force:true});}
});
test('offline export excludes later discussion, deduplicates usage, and detects image tampering',()=>{
 const dir=temp();try{
  const at='2026-10-19T21:17:32.000Z',source=path.join(dir,'source.jsonl');
  const usage={timestamp:at,type:'event_msg',payload:{type:'token_count',info:{last_token_usage:{input_tokens:100,cached_input_tokens:80,output_tokens:5},total_token_usage:{input_tokens:100,output_tokens:5}}}};
  const image={timestamp:at,type:'response_item',payload:{type:'custom_tool_call_output',output:[{type:'input_image',image_url:'data:image/jpeg;base64,'+Buffer.from('image').toString('base64')}]}};
  fs.writeFileSync(source,[usage,usage,image,{timestamp:'2026-10-19T22:00:00Z',type:'response_item',payload:{type:'message',content:[{text:'later proposed fix'}]}}].map(JSON.stringify).join('\n'));
  const spec={caseId:'fixture',request:'Play music',start:'2026-10-19T21:17:00Z',end:'2026-10-19T21:19:00Z',sources:[{kind:'codex',file:source}]};
  const out=path.join(dir,'case'),m=exportCase(spec,out);
  assert.equal(m.usage.uniqueUpdates,1);assert.equal(m.usage.reportedLastUsageSum.input_tokens,100);
  assert.equal(validateCase(out).events,3);assert.ok(!fs.readFileSync(path.join(out,'events.jsonl'),'utf8').includes('later proposed fix'));
  assert.throws(()=>exportCase(spec,out),/already exists/);
  fs.appendFileSync(path.join(out,m.evidence.artifacts[0].path),'tampered');assert.throws(()=>validateCase(out),/integrity/);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
