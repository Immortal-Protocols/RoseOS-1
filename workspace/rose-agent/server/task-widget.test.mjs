import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {LauncherTask} from './launcher-task.mjs';
import {TaskWidgetGenerator,validateTaskWidget,widgetRequest} from './task-widget.mjs';
const widget={type:'Card',children:[{type:'Text',value:'Result with caveat'},{type:'Markdown',value:'[Source](https://example.com/result)'}]};
const source='Result with caveat. [Source](https://example.com/result)';
const response={status:'completed',output_text:JSON.stringify({widget})};
function setup(t,create){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-widget-'));const state=new LauncherTask(path.join(dir,'task.json'));
 const configFile=path.join(dir,'config.json');fs.writeFileSync(configFile,JSON.stringify({enabled:true}));
 const calls=[],records=[],generator=new TaskWidgetGenerator({state,configFile,client:{responses:{create:(...args)=>{calls.push(args);return create(...args);}}},record:r=>records.push(r)});
 t.after(async()=>{await state.flush();fs.rmSync(dir,{recursive:true,force:true});});
 state.start('Read source');state.finish('completed',source);return {state,configFile,calls,records,generator};
}
test('late formatting never changes completion, answer, title, or a newer task',async t=>{
 let resolve;const {state,generator}=setup(t,()=>new Promise(r=>resolve=r));const task={...state.current},pending=generator.start(task);
 assert.equal(state.current.state,'completed');assert.equal(state.current.result,source);
 state.start('Next task');const newer={...state.current};resolve(response);await pending;assert.deepEqual(state.current,newer);
});
test('successful formatting attaches only a widget to the original completed answer',async t=>{
 const {state,generator,calls}=setup(t,()=>response);const before={...state.current};await generator.start({...state.current});
 assert.deepEqual(state.current.widget,widget);assert.equal(state.current.result,before.result);assert.equal(state.current.finishedAt,before.finishedAt);assert.equal(state.current.state,'completed');
 assert.equal(calls.length,1);assert.equal(calls[0][0].store,false);assert.equal(calls[0][1].maxRetries,0);
});
test('errors, incomplete results, unsupported actions, and missing links preserve original text without retry',async t=>{
 for(const output of [()=>Promise.reject(Error('Offline')),()=>({status:'incomplete'}),()=>({status:'completed',output_text:JSON.stringify({widget:{type:'Card',children:[{type:'Text',value:'Missing source'}]}})}),()=>({status:'completed',output_text:JSON.stringify({widget:{type:'Card',onClickAction:{type:'send'},children:[]}})})]){
  const {state,generator,calls}=setup(t,output),before={...state.current};await generator.start({...state.current});
  assert.equal(state.current.widgetStatus,'unavailable');assert.equal(state.current.result,before.result);assert.equal(state.current.state,'completed');assert.equal(state.current.finishedAt,before.finishedAt);assert.equal(state.current.widget,undefined);assert.equal(calls.length,1);
 }
});
test('formatting exposes a preparing state without withholding the completed answer or speech',async t=>{
 let resolve;const {state,generator}=setup(t,()=>new Promise(r=>resolve=r));const task={...state.current};
 const pending=generator.start(task);assert.equal(state.current.widgetStatus,'preparing');assert.equal(state.current.state,'completed');assert.equal(state.current.result,source);
 resolve(response);await pending;assert.equal(state.current.widgetStatus,'ready');
});
test('disabled rollout makes no calls and progress cannot start formatting',async t=>{
 const {state,generator,calls,configFile}=setup(t,()=>response);fs.writeFileSync(configFile,'{"enabled":false}');generator.observe(state.snapshot());assert.equal(calls.length,0);
 fs.writeFileSync(configFile,'{"enabled":true}');state.start('New request');generator.observe(state.snapshot());assert.equal(calls.length,0);
});
test('each completion generates once even when status is delivered repeatedly',async t=>{
 const {state,generator,calls}=setup(t,()=>response);generator.observe(state.snapshot());generator.observe(state.snapshot());await new Promise(r=>setImmediate(r));assert.equal(calls.length,1);
});
test('supported card layouts retain exact source links',()=>{
 const layouts=[
  {type:'Text',value:'Result with caveat'},
  {type:'Title',value:'Result',size:'lg'},
  {type:'Badge',label:'Verified',color:'success'},
  {type:'Row',children:[{type:'Caption',value:'Summary'}]},
  {type:'Col',children:[{type:'Box',children:[{type:'Text',value:'Details'}]},{type:'Divider'}]},
 ];
 for(const layout of layouts){
  const card={type:'Card',children:[layout,{type:'Markdown',value:'[Source](https://example.com/result)'}]};
  assert.equal(validateTaskWidget(card,source).type,'Card');
  assert.throws(()=>validateTaskWidget(card,'[Different source](https://example.com/other)'));
 }
 assert.equal(widgetRequest(source).input.includes(source),true);
});
