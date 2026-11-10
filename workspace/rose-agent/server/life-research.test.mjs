import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {EventEmitter} from 'node:events';
import {Codex} from './codex.mjs';
import {publicPageReceipt,lifeResearchPolicy,researchConfig,verifyResearchMcp,supportsResearchVersion} from './life-research.mjs';

const opened=url=>({type:'webSearch',action:{type:'openPage',url},status:'completed'});
test('unknown or older native contracts fail closed before research process startup',()=>{
 for(const version of ['codex-cli 0.163.0','codex-cli 0.166.9','unknown','0.167.0'])assert.equal(supportsResearchVersion(version),false);
 for(const version of ['codex-cli 0.167.0-alpha.4\n','codex-cli 0.167.0','codex-cli 1.0.0'])assert.equal(supportsResearchVersion(version),true);
 const source=readFileSync(new URL('./codex.mjs',import.meta.url),'utf8');
 assert.ok(source.indexOf('supportsResearchVersion(version.stdout')<source.indexOf('this.child=spawn('));
});
test('only observed completed public page operations produce receipts',()=>{
 assert.deepEqual(publicPageReceipt(opened('https://example.com/source')),
  {url:'https://example.com/source',title:'example.com',evidence:'completed_public_page_open'});
 for(const item of [{type:'agentMessage',text:'https://example.com/source'},
   {...opened('https://example.com'),type:'dynamicToolCall'},
   {...opened('https://example.com'),status:'failed'},
   {...opened('https://example.com'),error:'failed'},
   {type:'webSearch',action:{type:'search',query:'topic'}}]) assert.equal(publicPageReceipt(item),null);
});
test('unsafe or token-bearing URLs are not retained',()=>{
 for(const url of ['http://example.com','https://user:pass@example.com','file:///tmp/test',
  'https://localhost/x','https://intranet','https://test.local','https://127.0.0.1',
  'https://[::1]/x','https://example.com/?token=secret','https://example.com/?api_key=secret'])
  assert.equal(publicPageReceipt(opened(url)),null,url);
});
test('research keeps existing account/model and restricts thread to public web tools',async()=>{
 for(const publicResearch of [false,true]){
  const c=new Codex('/empty','',null,{externalPhone:true,publicResearch,model:'gpt-6.1-terra'}),calls=[];
  c.rpc=async(method,params)=>{calls.push({method,params});
   if(method==='account/read')return {account:{type:'chatgpt'}};
   if(method==='model/list')return {data:[{id:'gpt-6.1-terra',serviceTiers:[{id:'fast'}]}]};
   if(method==='config/read')return {config:{mcp_servers:{private_notes:{enabled:false}}}};
   if(method==='mcpServerStatus/list')return {data:[{runtimeStatus:'disabled',tools:{},resources:[],resourceTemplates:[]}]};
   return {thread:{id:'research'},model:'gpt-6.1-terra',serviceTier:'fast'};
  };
  await c.initVoiceThread({dynamicTools:[]});
  const p=calls.find(c=>c.method==='thread/start').params;
  assert.equal(p.ephemeral,true);assert.equal(p.sandbox,'read-only');
  assert.equal(p.config.web_search,publicResearch?'live':'disabled');
  for(const key of ['features.shell_tool','features.apps','features.multi_agent'])assert.equal(p.config[key],false);
  assert.deepEqual(p.dynamicTools,[]);
  if(publicResearch){
   assert.deepEqual(p.environments,[]);
   assert.equal(p.config['mcp_servers.private_notes.enabled'],false);
   assert.equal(calls.at(-1).method,'mcpServerStatus/list');
  }
 }
});
test('ambient private MCP config is disabled and non-disabled or unknown tools fail closed',async()=>{
 const config=researchConfig({mcp_servers:{private_notes:{enabled:false},calendar:{enabled:false}}});
 assert.equal(config['mcp_servers.private_notes.enabled'],false);
 assert.equal(config['mcp_servers.calendar.enabled'],false);
 for(const key of ['features.memories','features.hooks','features.view_image'])assert.equal(config[key],false);
 for(const server of [{runtimeStatus:'connected',tools:{}},{runtimeStatus:'starting',tools:{}},
   {runtimeStatus:'disabled',tools:{read_private_note:{}}},{tools:{}}])
  await assert.rejects(verifyResearchMcp(async()=>({data:[server]}),'thread',['private_notes']),/isolation/);
 await assert.rejects(verifyResearchMcp(async()=>({data:[],nextCursor:'same'}),'thread',['private_notes']),/isolation/);
 await assert.rejects(verifyResearchMcp(async()=>({}),'thread',['private_notes']),/isolation/);
 assert.throws(()=>researchConfig({mcp_servers:{'bad.path':{}}}),/isolation/);
 assert.throws(()=>researchConfig({mcp_servers:{private_notes:{enabled:true}}}),/isolation/);
 assert.throws(()=>researchConfig({mcp_servers:{private_notes:{}}}),/isolation/);
 assert.throws(()=>researchConfig({instructions:'Private owner context'}),/isolation/);
 assert.throws(()=>researchConfig({model_instructions_file:'/private/context'}),/isolation/);
 assert.equal(config.project_doc_max_bytes,0);
 const requests=[];
 await verifyResearchMcp(async(method,params)=>{requests.push({method,params});return {data:[]};},'thread',['private_notes']);
 assert.equal(requests[0].params.serverName,'private_notes');
 await verifyResearchMcp(()=>{throw Error('must not discover');},'thread',[]);
});
test('enabled MCP refuses before thread and changed config refuses before any topic',async()=>{
 for(const changeAfterStart of [false,true]){
  const c=new Codex('/empty','',null,{externalPhone:true,publicResearch:true,model:'gpt-6.1-terra'}),calls=[];
  let reads=0;
  c.rpc=async(method,params)=>{
   calls.push(method);
   if(method==='account/read')return {account:{type:'chatgpt'}};
   if(method==='model/list')return {data:[{id:'gpt-6.1-terra',serviceTiers:[{id:'fast'}]}]};
   if(method==='config/read')return {config:{mcp_servers:{private_notes:{enabled:!changeAfterStart||++reads>1}}}};
   if(method==='thread/start')return {thread:{id:'research'},model:'gpt-6.1-terra',serviceTier:'fast'};
   throw Error('Unexpected RPC');
  };
  await assert.rejects(c.initVoiceThread({}),{code:'research_isolation_unavailable'});
  assert.equal(calls.includes('thread/start'),changeAfterStart);
  assert.equal(calls.includes('mcpServerStatus/list'),false);
  assert.equal(calls.includes('turn/start'),false);
 }
});
test('bridge fixes research instructions and rejects phone tools',()=>{
 const source=readFileSync(new URL('./codex-voice-bridge.mjs',import.meta.url),'utf8');
 assert.match(source,/research && \(!Array\.isArray\(m.tools\)\|\|m.tools.length\)/);
 assert.match(source,/developerInstructions:research\?lifeResearchPolicy:m.instructions/);
 assert.match(source,/c.on\('toolEvidence',item=>/);
 assert.match(lifeResearchPolicy,/untrusted reference data/);
 assert.match(lifeResearchPolicy,/Do not claim a note was saved/);
});
test('research close waits for child exit and escalates only its child',async()=>{
 for(const ignoreTerm of [false,true]){
  const c=new Codex('/empty','',null,{}),child=new EventEmitter(),signals=[];
  child.exitCode=null;
  child.kill=signal=>{signals.push(signal||'SIGTERM');
   if(!ignoreTerm||signal==='SIGKILL')setTimeout(()=>{child.signalCode=signal||'SIGTERM';child.emit('exit');},1);
  };
  c.child=child;
  assert.equal(await c.closeConfirmed(10),true);
  assert.deepEqual(signals,ignoreTerm?['SIGTERM','SIGKILL']:['SIGTERM']);
 }
});
test('unacknowledged research process stop remains uncertain',async()=>{
 const c=new Codex('/empty','',null,{}),child=new EventEmitter();
 child.exitCode=null;child.kill=()=>{};c.child=child;
 assert.equal(await c.closeConfirmed(2),false);
 assert.equal(child.listenerCount('exit'),0);
});
