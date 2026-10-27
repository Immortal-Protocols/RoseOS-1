import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {modelRoute,ModelSelection,modelLabel} from './model-switch.mjs';
import {Codex} from './codex.mjs';

test('structured routes validate supported model, effort and speed',()=>{
 assert.deepEqual(modelRoute('gpt-6-sol'),{model:'gpt-6-sol',effort:'low',serviceTier:'priority'});
 assert.deepEqual(modelRoute('gpt-6-luna','none','priority'),{model:'gpt-6-luna',effort:'none',serviceTier:'priority'});
 assert.throws(()=>modelRoute('gpt-6-astra','none'));
 assert.throws(()=>modelRoute('other'));
 assert.throws(()=>modelRoute('gpt-6-luna','ultra'));
 assert.throws(()=>modelRoute('gpt-6-sol','low','ultrafast'));
});
test('one-task override is consumed once; default persists, session does not',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-model-'));
 try{
 const file=path.join(dir,'model.json'),selection=new ModelSelection(file);
 selection.set({route:modelRoute('gpt-6.1-sol'),scope:'next',request:''},false);
 assert.equal(selection.takeNext().model,'gpt-6.1-sol');assert.equal(selection.takeNext(),null);
 selection.set({route:modelRoute('gpt-6-luna'),scope:'default',request:''},false);
 selection.set({route:modelRoute('gpt-6-astra'),scope:'session',request:''},false);
 assert.equal(selection.preferred({}).model,'gpt-6-astra');
 assert.equal(new ModelSelection(file).preferred({}).model,'gpt-6-luna');
 assert.equal(modelLabel(selection.preferred({})),'GPT-6 Astra · Medium · Fast');
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('active model switch waits for old completion before starting new turn',async()=>{
 const c=new Codex('/tmp','/tmp/unused');c.thread='same-thread';c.active='old';let complete;
 c.rpc=async(method,params)=>{
  if(method==='turn/interrupt'){assert.equal(params.turnId,'old');return;}
  assert.equal(method,'turn/start');assert.equal(c.active,null);assert.equal(params.model,'gpt-6.1-sol');assert.equal(params.threadId,'same-thread');return {turn:{id:'new'}};
 };
 let stopped=false;const wait=c.interruptForModelSwitch().then(()=>{stopped=true;});
 await new Promise(r=>setImmediate(r));assert.equal(stopped,false);
 c.active=null;c.emit('turnEnded',{id:'old',status:'interrupted'});await wait;
 await c.submit('Continue without repeating completed actions',{model:'gpt-6.1-sol',effort:'low',serviceTier:'ultrafast'});
 assert.equal(c.active,'new');
});
