import test from 'node:test';
import assert from 'node:assert/strict';
import {Codex} from './codex.mjs';

function fixture({auth='chatgpt',model='gpt-6.1-terra',tiers=['fast'],selected='gpt-6.1-terra',tier='fast'}={}) {
 const c=new Codex('/tmp','/tmp/unused',null,{externalPhone:true,model:'gpt-6.1-terra'}),calls=[];
 c.rpc=async(method,params)=>{calls.push({method,params});
  if(method==='account/read')return {account:{type:auth}};
  if(method==='model/list')return {data:[{id:model,model,serviceTiers:tiers.map(id=>({id}))}]};
  if(method==='thread/start')return {thread:{id:'thread'},model:selected,serviceTier:tier};
  if(method==='turn/start')return {turn:{id:'turn'}};
 };
 return {c,calls};
}
test('Terra Fast explicitly uses managed ChatGPT refresh, ephemeral thread and registered tools',async()=>{
 const {c,calls}=fixture();const tools=[{name:'observe'}];
 await c.initVoiceThread({dynamicTools:tools});
 assert.deepEqual(calls[0],{method:'account/read',params:{refreshToken:true}});
 const p=calls[2].params;
 assert.equal(p.model,'gpt-6.1-terra');assert.equal(p.serviceTier,'fast');
 assert.equal(p.modelProvider,'openai');assert.equal(p.ephemeral,true);
 assert.equal(p.sandbox,'read-only');assert.deepEqual(p.dynamicTools,tools);
 await c.submit('Hello',{model:'gpt-6.1-terra',effort:'medium',serviceTier:'fast'});
 assert.equal(calls.at(-1).params.serviceTierForTurn,'fast');
 assert.equal(calls.at(-2).method,'account/read');
});
for(const [label,options,code] of [
 ['API key',{auth:'apiKey'},'chatgpt_login_required'],
 ['missing model',{model:'gpt-6-luna'},'model_unavailable'],
 ['missing Fast',{tiers:['default']},'fast_unavailable'],
 ['model downgrade',{selected:'gpt-6-luna'},'model_mode_mismatch'],
 ['mode downgrade',{tier:'default'},'model_mode_mismatch'],
])test(`refuses ${label} with no fallback`,async()=>{
 const {c}=fixture(options);await assert.rejects(c.initVoiceThread({}),{code});
});
test('expired/changed login refuses a turn before sending user context',async()=>{
 const {c}=fixture();await c.initVoiceThread({});const calls=[];
 c.rpc=async(method,params)=>{calls.push(method);return {account:null};};
 await assert.rejects(c.submit('private context',{model:c.selectedModel,serviceTier:'fast'}),{code:'chatgpt_login_required'});
 assert.deepEqual(calls,['account/read']);
});
test('voice mode rejects API key configuration before reading a key file or starting Codex',async()=>{
 const c=new Codex('/tmp','/tmp/unused','/does/not/exist',{externalPhone:true,model:'gpt-6.1-terra'});
 await assert.rejects(c.init(),{code:'chatgpt_login_required'});assert.equal(c.child,undefined);
});
test('advertised priority alias selects Fast and retains the exact Terra model',async()=>{
 const {c,calls}=fixture({tiers:['default','priority'],tier:'priority'});
 await c.initVoiceThread({});assert.equal(c.selectedModel,'gpt-6.1-terra');
 assert.equal(calls.at(-1).params.serviceTier,'priority');
 assert.equal(calls.at(-1).params.config['features.fast_mode'],true);
});
test('completion preceding turn/start response never resurrects the turn',async()=>{
 const {c}=fixture();c.runtime={};c.rpc=async()=>{c.endedTurns.add('turn');return {turn:{id:'turn'}};};
 await c.submit('Hello');assert.equal(c.active,null);
});
