import test from 'node:test';
import assert from 'node:assert/strict';
import {Codex} from './codex.mjs';
import {voiceSessionProfile} from './session-permissions.mjs';

function fixture({auth='chatgpt',model='gpt-6.1-terra',tiers=['fast'],selected='gpt-6.1-terra',tier='fast',
 active={id:'rose_voice'},roots=[],config={}}={}) {
 const c=new Codex('/tmp','/tmp/unused',null,{externalPhone:true,model:'gpt-6.1-terra'}),calls=[];
 c.rpc=async(method,params)=>{calls.push({method,params});
  if(method==='account/read')return {account:{type:auth}};
  if(method==='model/list')return {data:[{id:model,model,serviceTiers:tiers.map(id=>({id}))}]};
  if(method==='config/read')return {config:{default_permissions:'rose_voice',permissions:{rose_voice:voiceSessionProfile},...config}};
  if(method==='permissionProfile/list')return {data:[{id:'rose_voice',allowed:true}]};
  if(method==='mcpServerStatus/list')return {data:[{runtimeStatus:'disabled',tools:{},resources:[],resourceTemplates:[]}]};
  if(method==='thread/start')return {thread:{id:'thread'},model:selected,serviceTier:tier,
   activePermissionProfile:active,runtimeWorkspaceRoots:roots};
  if(method==='turn/start')return {turn:{id:'turn'}};
 };
 return {c,calls};
}
test('Terra Fast explicitly uses managed ChatGPT refresh, ephemeral thread and registered tools',async()=>{
 const {c,calls}=fixture();const tools=[{name:'observe'}];
 await c.initVoiceThread({dynamicTools:tools});
 assert.deepEqual(calls[0],{method:'account/read',params:{refreshToken:true}});
 const p=calls.find(x=>x.method==='thread/start').params;
 assert.equal(p.model,'gpt-6.1-terra');assert.equal(p.serviceTier,'fast');
 assert.equal(p.modelProvider,'openai');assert.equal(p.ephemeral,true);
 assert.equal(p.permissions,'rose_voice');assert.equal(p.sandbox,undefined);
 assert.deepEqual(p.runtimeWorkspaceRoots,[]);assert.deepEqual(p.environments,[]);
 assert.deepEqual(p.dynamicTools,tools);
 await c.submit('Hello',{model:'gpt-6.1-terra',effort:'medium',serviceTier:'fast'});
 assert.equal(calls.at(-1).params.serviceTierForTurn,'fast');
 assert.equal(calls.at(-1).params.permissions,'rose_voice');
 assert.deepEqual(calls.at(-1).params.environments,[]);
 assert.equal(calls.at(-2).method,'config/read');
});
test('refuses voice startup when Codex exposes an ambient tool or reports a wider sandbox',async()=>{
 for(const options of [{config:{mcp_servers:{local_files:{enabled:true}}}},
  {config:{sandbox_mode:'read-only'}},{active:{id:':read-only'}},{roots:['/tmp']}]){
  const {c,calls}=fixture(options);
  await assert.rejects(c.initVoiceThread({}),{code:'voice_isolation_unavailable'});
  assert.equal(c.thread,null);
  assert.equal(calls.some(x=>x.method==='turn/start'),false);
 }
});
test('a widened profile blocks the next voice turn before it sends user context',async()=>{
 const {c,calls}=fixture();await c.initVoiceThread({});
 const rpc=c.rpc;
 c.rpc=async(method,params)=>method==='config/read'?{config:{default_permissions:':workspace'}}:rpc(method,params);
 await assert.rejects(c.submit('private context',{model:c.selectedModel,serviceTier:'fast'}),{code:'voice_isolation_unavailable'});
 assert.equal(calls.some(x=>x.method==='turn/start'),false);
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
 const {params}=calls.find(x=>x.method==='thread/start');
 assert.equal(params.serviceTier,'priority');
 assert.equal(params.config['features.fast_mode'],true);
});
test('completion preceding turn/start response never resurrects the turn',async()=>{
 const {c}=fixture();c.runtime={};c.rpc=async()=>{c.endedTurns.add('turn');return {turn:{id:'turn'}};};
 await c.submit('Hello');assert.equal(c.active,null);
});
test('character life permits dynamic app/memory tools with no native search or ambient MCP',async()=>{
 const {c,calls}=fixture({config:{mcp_servers:{node_repl:{enabled:false}}}});c.runtime.characterLife=true;
 const tools=[{name:'memory'},{name:'app_work'}];
 await c.initVoiceThread({dynamicTools:tools});
 const p=calls.find(x=>x.method==='thread/start').params;
 assert.equal(p.config.web_search,'disabled');
 assert.equal(p.config['features.shell_tool'],false);
 assert.equal(p.config['features.view_image'],false);
 assert.equal(p.config['mcp_servers.node_repl.enabled'],false);
 assert.deepEqual(p.dynamicTools,tools);assert.deepEqual(p.environments,[]);
});
test('phone screenshots use same-turn image input with exact turn binding',async()=>{
 const {c,calls}=fixture();c.thread='thread';
 const image={type:'inputImage',imageUrl:'data:image/png;base64,AQ=='};
 const text={type:'inputText',text:'{"ok":true,"frame_id":"frame-1"}'};
 const result=await c.forwardToolImages({turnId:'turn',tool:'observe'},{success:true,contentItems:[text,image]});
 assert.deepEqual(result.contentItems,[text]);assert.equal(calls.length,1);
 assert.equal(calls[0].method,'turn/steer');
 assert.equal(calls[0].params.expectedTurnId,'turn');
 assert.equal(calls[0].params.threadId,'thread');
 assert.match(calls[0].params.input[0].text,/frame-1/);
 assert.deepEqual(calls[0].params.input[1],{type:'image',url:image.imageUrl});
 await assert.rejects(c.forwardToolImages({turnId:'turn',tool:'observe'},
  {success:true,contentItems:[{type:'inputImage',imageUrl:'https://untrusted.example/image'}]}),/Unsupported/);
});
