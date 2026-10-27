import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {recoveryDownload} from './recovery-download.mjs';
test('recovery lists only its one release, expires, and never handles private routes',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-recovery-'));
 try{
  const id='a'.repeat(64),file=dir+'/recovery.json';
  fs.writeFileSync(dir+'/'+id+'.apk','signed-public-apk-fixture');
  fs.writeFileSync(file,JSON.stringify({id,packageName:'dev.pixel.voice',expiresAt:Date.now()+60000}));
  const recovery=recoveryDownload(file,dir);let response;
  const res={writeHead(code){assert.equal(code,200);},end(text){response=JSON.parse(text);}};
  assert.equal(recovery.handle({method:'GET'},res,new URL('https://localhost/apps')),true);
  assert.deepEqual(response.apps.map(a=>a.id),[id]);
  for(const pathname of ['/health','/task','/pair/devices','/apps/'+('b'.repeat(64))+'/apk'])assert.equal(recovery.handle({method:'GET'},res,new URL('https://localhost'+pathname)),false);
  assert.equal(recovery.handle({method:'POST'},res,new URL('https://localhost/apps')),false);
  fs.writeFileSync(file,JSON.stringify({id,packageName:'dev.pixel.voice',expiresAt:0}));
  assert.equal(recoveryDownload(file,dir),null);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
