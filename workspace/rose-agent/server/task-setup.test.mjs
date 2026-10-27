import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Codex} from './codex.mjs';
import {taskSetupPolicy} from './task-setup.mjs';
import {taskHtmlPolicy} from './task-html.mjs';
import {phoneVisionPolicyFor} from './phone-vision-policy.mjs';
import {PersonalMemoryContext} from '../personal-memory/context.mjs';
const settings={controlPath:'/installed/phone/control.py',visualPath:'/installed/visual',appToolsEnabled:true,presentation:'html',phoneVisionMode:'navigation'};

test('moving guidance preserves visual verification, tools, source rules and selected card mode',()=>{
 const setup=taskSetupPolicy(settings);
 assert.ok(setup.includes(phoneVisionPolicyFor('navigation')));
 assert.ok(setup.includes(taskHtmlPolicy('html')));
 assert.match(setup,/Never guess or remember node IDs/);
 assert.match(setup,/Never silently substitute another account/);
 assert.match(setup,/Send mail or mutate events only when requested/);
 assert.match(setup,/installed\/phone\/control.py/);
 assert.ok(taskSetupPolicy({...settings,presentation:'native'}).includes(taskHtmlPolicy('native')));
 assert.match(taskSetupPolicy({...settings,general:true}),/General phone routing is enabled/);
});

test('ten successive requests do not accumulate setup; active corrections still steer',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-setup-'));
 try{
  let setup=taskSetupPolicy(settings);const c=new Codex(dir,path.join(dir,'state.json'),null,{taskSetup:()=>setup});
  c.thread='t';c.instructions='Base rules';c.policyHashes.t=c.policyHash();const sent=[];
  c.rpc=async(method,params)=>{sent.push({method,params});return {turn:{id:'turn-'+sent.length}};};
  for(let i=0;i<10;i++){c.active=null;await c.submit('Request '+i);}
  assert.equal(sent.filter(x=>x.method==='thread/inject_items').length,0);
  assert.equal(sent.length,10);
  for(const call of sent)assert.equal(call.params.input[0].text.startsWith('Request '),true);
  await c.submit('Actually use the previous result');
  assert.equal(sent.at(-1).method,'turn/steer');
  assert.equal(sent.at(-1).params.input[0].text,'Actually use the previous result');
  setup=taskSetupPolicy({...settings,presentation:'native'});c.active=null;
  await c.submit('Mode changed');c.active=null;await c.submit('Next request');
  assert.equal(sent.filter(x=>x.method==='thread/inject_items').length,1);
  assert.match(JSON.stringify(sent.find(x=>x.method==='thread/inject_items')),/Result card mode: native/);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test('unchanged personal memory is not resent; new revision remains available',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-memory-revision-'));
 try{
  const c=new Codex(dir,path.join(dir,'state.json'));c.thread='a';const memory=new PersonalMemoryContext(dir);
  const first=memory.forConsumer('codex','one',c.memoryRevision());assert.ok(first.text);
  c.rememberMemoryRevision(first.revision);
  const same=memory.forConsumer('codex','two',c.memoryRevision());assert.equal(same.unchanged,true);assert.equal(same.text,undefined);
  assert.equal(JSON.parse(fs.readFileSync(c.state)).memoryRevisions.a,first.revision);
  c.thread='b';assert.equal(c.memoryRevision(),null);assert.ok(memory.forConsumer('codex','three',c.memoryRevision()).text);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
