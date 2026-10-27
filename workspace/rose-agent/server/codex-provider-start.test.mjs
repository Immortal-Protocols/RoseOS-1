import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Codex} from './codex.mjs';
import {BASETEN_MODEL,BASETEN_PROVIDER} from './baseten/gateway.mjs';

async function check(run){
 const dir=mkdtempSync(join(tmpdir(),'pixel-provider-'));
 try{
  const tools=[{name:'phone_test',description:'test',inputSchema:{type:'object'}}];
  const worker=new Codex(dir,join(dir,'state.json'),null,{dynamicTools:tools});
  worker.thread='original';worker.baseten={available:()=>true};
  const calls=[];worker.rpc=async(method,params)=>{
   calls.push({method,params});
   if(method==='turn/start')return {turn:{id:'turn'}};
   return {thread:{id:'provider-thread',path:join(dir,'rollout.jsonl')}};
  };
  await run(worker,calls,tools);
 }finally{rmSync(dir,{recursive:true,force:true});}
}
const route={model:BASETEN_MODEL,effort:'low',serviceTier:'default'};
test('first provider turn starts directly with tools instead of forking an unpersisted thread',async()=>check(async(worker,calls,tools)=>{
 worker.unstartedThread=true;
 await worker.submit('Open the browser',route);
 assert.deepEqual(calls.map(c=>c.method),['thread/start','turn/start']);
 assert.equal(calls[0].params.modelProvider,BASETEN_PROVIDER);
 assert.deepEqual(calls[0].params.dynamicTools,tools);
 assert.equal(calls[0].params.cwd,worker.root);
 assert.equal(calls[0].params.approvalPolicy,'never');
 assert.equal(calls[0].params.sandbox,'danger-full-access');
 assert.equal('threadId' in calls[0].params,false);
 assert.equal(worker.unstartedThread,false);
}));
test('existing source conversation still uses the established provider fork path',async()=>check(async(worker,calls)=>{
 await worker.prepareRoute(route);
 assert.equal(calls[0].method,'thread/fork');
 assert.equal(calls[0].params.threadId,'original');
 assert.equal(calls[0].params.excludeTurns,true);
 assert.equal(calls[0].params.cwd,worker.root);
 assert.equal(calls[0].params.approvalPolicy,'never');
 assert.equal(calls[0].params.sandbox,'danger-full-access');
}));
test('missing saved rollout starts the requested provider with tools and retains recent conversation',async()=>check(async(worker,calls,tools)=>{
 worker.provider=BASETEN_PROVIDER;
 worker.providerThreads.openai='never-persisted';
 const rpc=worker.rpc;
 worker.rpc=async(method,params)=>{
  if(method==='thread/read')return {thread:{turns:[{items:[{type:'userMessage',content:[{type:'text',text:'Continue the app installs'}]}]}]}};
  if(method==='thread/resume'){calls.push({method,params});throw Error('no rollout found for thread id never-persisted');}
  return rpc(method,params);
 };
 await worker.prepareRoute({model:'gpt-6-luna',effort:'medium',serviceTier:'default'});
 assert.deepEqual(calls.map(c=>c.method),['thread/resume','thread/start','thread/inject_items']);
 assert.deepEqual(calls[1].params.dynamicTools,tools);
 assert.equal(calls[1].params.model,'gpt-6-luna');
 assert.equal(calls[1].params.cwd,worker.root);
 assert.equal(calls[1].params.approvalPolicy,'never');
 assert.equal(calls[1].params.sandbox,'danger-full-access');
 assert.equal(worker.provider,'openai');
 assert.equal(worker.providerThreads[BASETEN_PROVIDER],'original');
}));
