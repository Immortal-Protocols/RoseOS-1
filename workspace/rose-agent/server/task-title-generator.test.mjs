import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {LauncherTask} from './launcher-task.mjs';
import {TaskTitleGenerator,titleRequest} from './task-title-generator.mjs';

function setup(t,create){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'parallel-title-'));
 const state=new LauncherTask(path.join(dir,'state.json'));
 const records=[],calls=[];
 const generator=new TaskTitleGenerator({state,client:{responses:{create:(...args)=>{calls.push(args);return create(...args);}}},record:(data,context)=>records.push({data,context})});
 t.after(async()=>{await state.flush();fs.rmSync(dir,{recursive:true,force:true});});
 state.start('Find flights arriving Wednesday afternoon');
 return {state,generator,records,calls};
}
const response=title=>({status:'completed',output_text:JSON.stringify({title}),model:'gpt-6-luna',service_tier:'fast',usage:{input_tokens:60,output_tokens:12}});

test('slow naming never holds task progress or completion; same finished card can receive its title',async t=>{
 let resolve;const {state,generator,records,calls}=setup(t,()=>new Promise(r=>resolve=r));
 const id=state.current.id,pending=generator.start(id,state.current.request,{caseId:'case-one'});
 state.progress('Checking flight times');state.finish('completed','Found two flights');
 const finished=state.current.finishedAt;
 assert.equal(state.current.title,'Working on your request');
 assert.equal(state.current.state,'completed');
 await Promise.resolve();resolve(response('Find Wednesday flight options'));await pending;
 assert.equal(state.current.title,'Find Wednesday flight options');
 assert.equal(state.current.state,'completed');assert.equal(state.current.result,'Found two flights');
 assert.equal(state.current.finishedAt,finished);assert.equal(calls.length,1);
 assert.equal(records[0].context.caseId,'case-one');
 assert.equal(calls[0][0].reasoning.effort,'none');assert.equal(calls[0][0].service_tier,'priority');
 assert.equal(calls[0][0].store,false);assert.deepEqual(calls[0][1],{timeout:3000,maxRetries:0});
});

test('late title cannot rename a newer task',async t=>{
 let resolve;const {state,generator,records}=setup(t,()=>new Promise(r=>resolve=r));
 const pending=generator.start(state.current.id,state.current.request);
 state.start('Show current battery level');const current={...state.current};
 await Promise.resolve();resolve(response('Find Wednesday flight options'));await pending;
 assert.deepEqual(state.current,current);assert.equal(records[0].data.status,'not_applied');
});

test('failure and malformed output do not retry or alter the task',async t=>{
 for(const result of [()=>Promise.reject(Error('unavailable')),()=>response('One'),()=>response('This title has far too many words'),()=>({status:'incomplete'}),()=>({status:'completed',output_text:'not JSON'})]){
  const {state,generator,calls,records}=setup(t,result);const original={...state.current};
  assert.equal((await generator.start(state.current.id,state.current.request)).status,'unavailable');
  assert.deepEqual(state.current,original);assert.equal(calls.length,1);assert.equal(records.length,1);
 }
});

test('naming request contains only the current request and a constrained title response',()=>{
 const r=titleRequest('Read the battery level');assert.equal(r.input,'Read the battery level');
 assert.equal(r.model,'gpt-6-luna');assert.equal(r.tools,undefined);assert.equal(r.text.format.strict,true);
 const pattern=new RegExp(r.text.format.schema.properties.title.pattern);
 assert.equal(pattern.test('Read current battery level'),true);assert.equal(pattern.test('Read battery'),false);
 assert.equal(pattern.test('Read the current phone battery level'),false);
});

test('old clipped titles migrate without changing saved requests or results',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'title-migration-')),file=path.join(dir,'state.json');
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 fs.writeFileSync(file,JSON.stringify({version:1,current:{id:'old',state:'completed',title:'This is the beginning of a long request that was never summarized',request:'Full request',result:'Full result'}}));
 const state=new LauncherTask(file);assert.equal(state.current.title,'Working on your request');
 assert.equal(state.current.request,'Full request');assert.equal(state.current.result,'Full result');
});
