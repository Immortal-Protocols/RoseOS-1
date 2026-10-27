import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import http from 'node:http';import crypto from 'node:crypto';
import {migrationBackup} from './migration-backup.mjs';
test('operator window, exact bytes, checksum, no replacement and capacity failure',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-backup-'));
 const windowFile=path.join(dir,'window.json'),root=path.join(dir,'received');
 const handler=migrationBackup({windowFile,root,maxBytes:10});
 const server=http.createServer(async(req,res)=>{if(!await handler(req,res,new URL(req.url,'http://localhost'))){res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url=`http://127.0.0.1:${server.address().port}/migration-backup/upload`;
 try {
  assert.equal((await fetch(url,{method:'POST',body:'hello'})).status,403);
  const id=crypto.randomUUID();fs.writeFileSync(windowFile,JSON.stringify({id,expiresAt:Date.now()+10000}));
  const result=await fetch(url,{method:'POST',body:'hello'});assert.equal(result.status,200);
  const received=await result.json();assert.equal(received.sha256,crypto.createHash('sha256').update('hello').digest('hex'));
  assert.equal(fs.readFileSync(path.join(root,id+'.zip'),'utf8'),'hello');
  assert.equal((await fetch(url,{method:'POST',body:'other'})).status,409);
  const other=crypto.randomUUID();fs.writeFileSync(windowFile,JSON.stringify({id:other,expiresAt:Date.now()+10000}));
  await fetch(url,{method:'POST',body:'larger than ten bytes'}).catch(()=>{});
  assert.equal(fs.existsSync(path.join(root,other+'.zip')),false);assert.equal(fs.existsSync(path.join(root,other+'.zip.part')),false);
  fs.writeFileSync(windowFile,JSON.stringify({id:crypto.randomUUID(),expiresAt:Date.now()-1}));
  assert.equal((await fetch(url,{method:'POST',body:'hello'})).status,403);
 }finally{await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});}
});
