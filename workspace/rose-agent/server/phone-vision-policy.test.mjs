import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {phoneVisionPolicy,phoneVisionPolicyFor} from './phone-vision-policy.mjs';
import {DecisionsConfig} from '../decisions/config.mjs';
test('default visual behavior remains available for rollback',()=>{
 assert.equal(phoneVisionPolicyFor('adaptive'),phoneVisionPolicy);
 assert.match(phoneVisionPolicyFor('navigation'),/capture and VIEW a current screenshot at task start/);
 assert.match(phoneVisionPolicyFor('navigation'),/after navigation or a meaningful change/);
});
test('screenshot cadence and Sol selection persist independently',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'vision-mode-'));try{
  const c=new DecisionsConfig(path.join(dir,'c'));c.set({enabled:true,taskAgent:'sol-low-ultrafast',phoneVisionMode:'navigation'});
  assert.equal(c.set({enabled:true}).phoneVisionMode,'navigation');
  assert.equal(c.set({enabled:true,phoneVisionMode:'adaptive'}).taskAgent,'sol-low-ultrafast');
  assert.throws(()=>c.set({enabled:true,phoneVisionMode:'invalid'}));
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
