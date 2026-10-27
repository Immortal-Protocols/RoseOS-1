import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ModelSelection} from './model-switch.mjs';
import {createModelTools} from './model-tools.mjs';

function fixture(){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-model-tools-'));
 const selection=new ModelSelection(path.join(dir,'selection.json')),logs=[];
 const running={model:'gpt-6-luna',effort:'medium',serviceTier:'priority'};
 const tools=createModelTools({selection,config:()=>({}),running:()=>running,log:r=>logs.push(r)});
 return {selection,logs,tools,dir,async call(tool,args,id='call-'+Math.random()){const r=await tools.handleToolCall({threadId:'test',callId:id,tool,arguments:args});return {success:r.success,...JSON.parse(r.contentItems[0].text)};}};
}
test('tool applies selected model without pretending the acknowledgement moved models',async()=>{
 const f=fixture();try{
 const r=await f.call('set_task_model',{model:'gpt-6-sol'});
 assert.equal(r.status,'applied');assert.equal(r.selected.model,'gpt-6-sol');assert.equal(r.running.model,'gpt-6-luna');
 assert.equal(r.appliesTo,'subsequent_backend_turns');assert.equal(f.logs[0].source,'registered_tool');
 assert.equal(fs.existsSync(path.join(f.dir,'selection.json')),false);
 }finally{fs.rmSync(f.dir,{recursive:true,force:true});}
});
test('invalid parameters never change selection or report success',async()=>{
 const f=fixture();try{
 for(const args of [{model:'unknown'},{model:'gpt-6-sol',speed:'ultrafast'},{model:'gpt-6-luna',effort:'ultra'},{model:'gpt-6-sol',scope:'all'},{model:'gpt-6-sol',typo:true}]){
 const r=await f.call('set_task_model',args);assert.equal(r.success,false);assert.equal(r.status,'not_applied');assert.equal(f.selection.session,null);
 }assert.equal(f.logs.length,0);
 }finally{fs.rmSync(f.dir,{recursive:true,force:true});}
});
test('default persists, next task is consumed once, and a duplicated call cannot apply twice',async()=>{
 const f=fixture();try{
 await f.call('set_task_model',{model:'gpt-6-sol',scope:'default'});
 assert.equal(new ModelSelection(path.join(f.dir,'selection.json')).saved.model,'gpt-6-sol');
 await f.call('set_task_model',{model:'gpt-6-astra',scope:'next_task'},'same');
 assert.equal(f.selection.takeNext().model,'gpt-6-astra');
 await f.call('set_task_model',{model:'gpt-6-astra',scope:'next_task'},'same');
 assert.equal(f.selection.takeNext(),null);assert.equal(f.logs.length,2);
 const r=await f.call('get_task_model',{});assert.equal(r.selected.model,'gpt-6-sol');
 }finally{fs.rmSync(f.dir,{recursive:true,force:true});}
});
