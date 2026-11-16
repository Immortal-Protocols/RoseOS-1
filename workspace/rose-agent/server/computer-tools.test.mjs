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
 assert.deepEqual(definitions.map(t=>t.name),['computer_status','computer_screenshot','computer_move','computer_click','computer_drag','computer_scroll','computer_type','computer_key']);
 for(const t of definitions){
  assert.equal(t.inputSchema.additionalProperties,false);
  for(const key of t.inputSchema.required)assert.ok(t.inputSchema.properties[key],t.name+'.'+key);
  // Every input is tied to the frame it was chosen from.
  if(!['computer_status','computer_screenshot'].includes(t.name))assert.ok(t.inputSchema.required.includes('frameId'),t.name);
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
 for(const status of ['stale_frame','no_session','no_signal','stopped','error']){
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
  ['computer_type',{frameId:'f1',text:'x'.repeat(4001)}],
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

test('a screenshot with no signal passes the phone\'s status through',async()=>{
 const f=fixture(()=>({status:'no_signal'}));
 const out=await f.call('computer_screenshot',{});
 assert.equal(out.success,false);
 assert.deepEqual(JSON.parse(out.contentItems[0].text),{status:'no_signal'});
});
