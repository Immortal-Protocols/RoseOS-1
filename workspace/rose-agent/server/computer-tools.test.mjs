// Computer Link tool contract (docs/COMPUTER-LINK.md), against a fake phone. No device,
// dongle or provider is involved.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createComputerTools,computerToolDefinitions,computerKeys} from './computer-tools.mjs';

const JPEG=Buffer.from([0xff,0xd8,0xff,0xe0,1,2,3,0xff,0xd9]).toString('base64');
function fixture(reply=()=>({status:'action_accepted'})){
 const sent=[],directory=fs.mkdtempSync(path.join(os.tmpdir(),'computer-frames-'));
 const tools=createComputerTools({phone:async data=>{sent.push(data);return reply(data);},directory});
 const call=(tool,args)=>tools.handleToolCall({tool,arguments:args});
 return {sent,directory,tools,call,facts:async(tool,args)=>JSON.parse((await call(tool,args)).contentItems[0].text)};
}

test('every tool has a closed schema and a computer_ action',()=>{
 const definitions=computerToolDefinitions();
 assert.deepEqual(definitions.map(t=>t.name),['computer_status','computer_open','computer_screenshot','computer_move','computer_click','computer_drag','computer_scroll','computer_type','computer_key']);
 for(const t of definitions){
  assert.equal(t.inputSchema.additionalProperties,false);
  for(const key of t.inputSchema.required)assert.ok(t.inputSchema.properties[key],t.name+'.'+key);
  // Every input is tied to the frame it was chosen from.
  if(!['computer_status','computer_open','computer_screenshot'].includes(t.name))assert.ok(t.inputSchema.required.includes('frameId'),t.name);
 }
});

test('a click is forwarded with its frame and reported as accepted, not verified',async()=>{
 const f=fixture();
 const out=await f.call('computer_click',{frameId:'f1',x:640,y:360,button:'right',count:2});
 assert.deepEqual(f.sent,[{frameId:'f1',x:640,y:360,button:'right',count:2,action:'computer_click'}]);
 assert.equal(out.success,true);
 assert.equal(JSON.parse(out.contentItems[0].text).status,'action_accepted');
});

test('a refused or stale action is not a success',async()=>{
 for(const status of ['stale_frame','not_open','not_allowed','not_calibrated','no_connection','locked','out_of_frame','unsupported_character','send_failed','task_stopped','uncertain','error']){
  const f=fixture(()=>({status}));
  assert.equal((await f.call('computer_type',{frameId:'f1',text:'hello'})).success,false,status);
 }
 const f=fixture(()=>({status:'action_accepted',actionExecuted:false}));
 assert.equal((await f.call('computer_key',{frameId:'f1',keys:['enter']})).success,false);
});

test('invalid calls never reach the phone',async()=>{
 const f=fixture();
 const bad=[
  ['computer_shell',{}],
  ['computer_click',{x:1,y:2}],
  ['computer_click',{frameId:'f1',x:-1,y:2}],
  ['computer_click',{frameId:'f1',x:1.5,y:2}],
  ['computer_click',{frameId:'f1',x:1,y:2,button:'fourth'}],
  ['computer_click',{frameId:'f1',x:1,y:2,command:'rm'}],
  ['computer_drag',{frameId:'f1',points:[1,2,3]}],
  ['computer_scroll',{frameId:'f1',x:1,y:2,direction:'sideways'}],
  ['computer_type',{frameId:'f1',text:'x'.repeat(501)}],
  ['computer_key',{frameId:'f1',keys:[]}],
  ['computer_key',{frameId:'f1',keys:['cmd','power']}],
  ['computer_key',{frameId:'f1',keys:'enter'}],
  ['computer_type',[]],
 ];
 for(const [tool,args] of bad)await assert.rejects(f.call(tool,args),undefined,tool+' '+JSON.stringify(args));
 assert.deepEqual(f.sent,[]);
});

test('shortcut keys are a fixed list',()=>{
 for(const key of ['cmd','ctrl','alt','shift','enter','escape','tab','space','a','z','0','9','f1','f12'])assert.ok(computerKeys.includes(key),key);
 assert.equal(new Set(computerKeys).size,computerKeys.length);
});

test('a screenshot becomes a private image file, never transcript base64',async()=>{
 const f=fixture(()=>({status:'ok',frameId:'f7',width:1920,height:1080,imageBase64:JPEG}));
 const out=await f.call('computer_screenshot',{});
 const facts=JSON.parse(out.contentItems[0].text);
 assert.equal(out.success,true);
 assert.equal(facts.frameId,'f7');
 assert.equal(facts.imageBase64,undefined);
 assert.ok(!out.contentItems[0].text.includes(JPEG));
 assert.equal(path.dirname(facts.imagePath),f.directory);
 assert.deepEqual(fs.readFileSync(facts.imagePath),Buffer.from(JPEG,'base64'));
 assert.equal(fs.statSync(facts.imagePath).mode&0o777,0o600);
});

test('an invalid frame image is an error, not a view of the screen',async()=>{
 for(const imageBase64 of ['not base64!',Buffer.from('plain text').toString('base64'),42]){
  const f=fixture(()=>({status:'ok',frameId:'f7',imageBase64}));
  const out=await f.call('computer_screenshot',{});
  const facts=JSON.parse(out.contentItems[0].text);
  assert.equal(out.success,false);
  assert.equal(facts.status,'error');
  assert.equal(facts.imagePath,undefined);
  assert.deepEqual(fs.readdirSync(f.directory),[]);
 }
});

test('only the most recent frames are retained',async()=>{
 const f=fixture(()=>({status:'ok',frameId:'f',imageBase64:JPEG}));
 for(let i=0;i<11;i++)await f.call('computer_screenshot',{});
 assert.equal(fs.readdirSync(f.directory).length,8);
});

test('a refused screenshot passes the phone\'s status through',async()=>{
 const f=fixture(()=>({status:'not_calibrated',actionExecuted:false}));
 const out=await f.call('computer_screenshot',{});
 assert.equal(out.success,false);
 assert.deepEqual(JSON.parse(out.contentItems[0].text),{status:'not_calibrated',actionExecuted:false});
});

test('opening the screen is reported as a launch request, never as control',async()=>{
 const f=fixture(()=>({status:'launch_requested',note:'The owner must tap Allow Rose on the phone before any input is sent.'}));
 const out=await f.call('computer_open',{});
 assert.deepEqual(f.sent,[{action:'computer_open'}]);
 assert.equal(out.success,true);
 assert.match(JSON.parse(out.contentItems[0].text).note,/Allow Rose/);
});

// server.mjs's own Computer Link wiring, sliced from the source and run with fake Stop, origin and phone.
test('server wiring: opt-in, task-bound, honours Stop, and never routes to a background display',async()=>{
 const vm=await import('node:vm');
 const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 const start=server.indexOf('let computerLinkEnabled=false;'),end=server.indexOf('const htmlTools=createTaskHtmlTools(');
 assert.ok(start>0&&end>start);
 assert.match(server,/\.\.\.\(computerLinkEnabled\?computerTools\.definitions\(\):\[\]\)/,'tools offered only when enabled');
 assert.match(server,/if\(computerLinkEnabled&&computerTools\.names\.has\(request\.tool\)\)return computerTools\.handleToolCall\(request\);/);
 const run=({enabled,artemis=false,origin={taskId:'t1'},stopped=false})=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'computer-link-'));
  if(enabled!==undefined)fs.writeFileSync(path.join(dir,'computer-link.json'),JSON.stringify({enabled}));
  const sent=[];
  const ctx={fs,priv:dir,artemisEnabled:artemis,createComputerTools,Error,JSON,
   phoneOrigin:{getStore:()=>origin},
   taskStop:{authorize:({taskId})=>taskId==='t1'?null:{status:'stale_task'},guard:()=>stopped?{status:'task_stopped',actionExecuted:false}:null},
   rawFastPhone:async data=>{sent.push(data);return {status:'action_accepted'};},
   backgroundPhone:{execute:()=>{throw Error('must not route to a background display');}}};
  vm.runInNewContext(server.slice(start,end)+';globalThis.out={computerLinkEnabled,computerTools};',ctx);
  return {...ctx.out,sent};
 };
 assert.equal(run({}).computerLinkEnabled,false,'off without the file');
 assert.equal(run({enabled:false}).computerLinkEnabled,false);
 assert.equal(run({enabled:true,artemis:true}).computerLinkEnabled,false,'never with the ARTEMIS trial');
 const on=run({enabled:true});
 assert.equal(on.computerLinkEnabled,true);
 const click={tool:'computer_click',arguments:{frameId:'f1',x:1,y:2}};
 assert.equal((await on.computerTools.handleToolCall(click)).success,true);
 assert.deepEqual(JSON.parse(JSON.stringify(on.sent)),[{frameId:'f1',x:1,y:2,action:'computer_click'}]);
 for(const origin of [{nonTask:true},{legacyRead:true},{taskId:'other'}]){
  const r=run({enabled:true,origin});
  assert.equal((await r.computerTools.handleToolCall(click)).success,false,JSON.stringify(origin));
  assert.deepEqual(r.sent,[]);
 }
 const stopped=run({enabled:true,stopped:true});
 const out=await stopped.computerTools.handleToolCall(click);
 assert.equal(out.success,false);
 assert.equal(JSON.parse(out.contentItems[0].text).status,'task_stopped');
 assert.deepEqual(stopped.sent,[]);
});
