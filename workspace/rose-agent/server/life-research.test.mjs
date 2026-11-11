import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {EventEmitter} from 'node:events';
import {Codex} from './codex.mjs';
import {publicPageReceipt,lifeResearchPolicy,researchConfig,verifyResearchMcp,supportsResearchVersion} from './life-research.mjs';

const opened=url=>({type:'webSearch',action:{type:'openPage',url},status:'completed'});
test('actual child launch scopes the MCP override and gates all topic submission',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'rose-research-launch-'));
 const cli=join(dir,'codex.cjs'),record=join(dir,'record.jsonl');
 const old={bin:process.env.PIXEL_CODEX_BIN,record:process.env.ROSE_TEST_RECORD,scenario:process.env.ROSE_TEST_SCENARIO};
 writeFileSync(cli,`#!${process.execPath}
 const fs=require('node:fs');
 if(process.argv.includes('--version')){console.log('codex-cli 0.167.1');process.exit(0);}
 const args=process.argv.slice(2),file=process.env.ROSE_TEST_RECORD,scenario=process.env.ROSE_TEST_SCENARIO;
 const log=x=>fs.appendFileSync(file,JSON.stringify(x)+'\\n');log({args});
 require('node:readline').createInterface({input:process.stdin}).on('line',line=>{
  const m=JSON.parse(line);if(m.id===undefined)return;log({method:m.method});let result={};
  if(m.method==='account/read')result={account:{type:'chatgpt'}};
  if(m.method==='model/list')result={data:[{id:'gpt-6.1-terra',serviceTiers:[{id:'priority'}]}]};
  if(m.method==='config/read'){
   const servers={node_repl:{enabled:!args.includes('mcp_servers.node_repl.enabled=false')}};
   if(scenario==='unknown')servers.unexpected={};
   result={config:{mcp_servers:servers}};
  }
  if(m.method==='thread/start')result={thread:{id:'research'},model:'gpt-6.1-terra',serviceTier:'priority'};
  if(m.method==='mcpServerStatus/list')result={data:[{runtimeStatus:scenario==='connected'?'connected':'disabled',tools:{},resources:[],resourceTemplates:[]}]};
  if(m.method==='turn/start')result={turn:{id:'turn'}};
  console.log(JSON.stringify({id:m.id,result}));
 });`,{mode:0o700});
 try{
  process.env.PIXEL_CODEX_BIN=cli;process.env.ROSE_TEST_RECORD=record;
  for(const [publicResearch,scenario] of [[false,'normal'],[true,'normal'],[true,'unknown'],[true,'connected']]){
   writeFileSync(record,'');process.env.ROSE_TEST_SCENARIO=scenario;
   const c=new Codex(dir,join(dir,'unused'),null,{externalPhone:true,publicResearch,model:'gpt-6.1-terra',dynamicTools:[]});
   try{
    if(scenario==='normal'){
     await c.init();await c.submit('Synthetic public topic',{model:c.selectedModel,serviceTier:c.serviceTier});
    }else await assert.rejects(c.init(),{code:'research_isolation_unavailable'});
   }finally{await c.closeConfirmed();}
   const rows=readFileSync(record,'utf8').trim().split('\n').map(JSON.parse),args=rows[0].args;
   assert.equal(args.includes('mcp_servers.node_repl.enabled=false'),publicResearch);
   const methods=rows.slice(1).map(r=>r.method);
   if(scenario==='unknown')assert.equal(methods.includes('thread/start'),false);
   if(scenario!=='normal')assert.equal(methods.includes('turn/start'),false);
   if(publicResearch&&scenario==='normal')assert.ok(methods.indexOf('mcpServerStatus/list')<methods.indexOf('turn/start'));
  }
 }finally{
  for(const [key,value] of [['PIXEL_CODEX_BIN',old.bin],['ROSE_TEST_RECORD',old.record],['ROSE_TEST_SCENARIO',old.scenario]])
   if(value===undefined)delete process.env[key];else process.env[key]=value;
  rmSync(dir,{recursive:true,force:true});
 }
});
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
