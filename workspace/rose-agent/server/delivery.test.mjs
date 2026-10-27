import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Writable} from 'node:stream';
import {once} from 'node:events';
import {delivery} from './delivery.mjs';

function fixture(t){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-delivery-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const global={id:'a'.repeat(64),label:'Existing',packageName:'dev.test',versionCode:1,created:1};
 const scoped={id:'b'.repeat(64),label:'Updated',packageName:'dev.test',versionCode:2,created:2,deviceId:'old-pixel',v4Signature:{sha256:'c'.repeat(64),size:7}};
 for(const entry of [global,scoped]){fs.writeFileSync(path.join(dir,entry.id+'.json'),JSON.stringify(entry));fs.writeFileSync(path.join(dir,entry.id+'.apk'),'apk');}
 fs.writeFileSync(path.join(dir,scoped.id+'.apk.idsig'),'sidecar');
 return {dir,global,scoped};
}
async function request(dir,url,deviceId,local=false){
 const chunks=[];let status;
 const res=new Writable({write(chunk,encoding,callback){chunks.push(Buffer.from(chunk));callback();}});
 res.writeHead=code=>{status=code;};
 const completed=once(res,'finish');
 assert.equal(delivery({method:'GET',socket:{remoteAddress:local?'127.0.0.1':'10.0.0.2'}},res,new URL(url,'http://test'),dir,deviceId),true);
 await completed;return {status,body:Buffer.concat(chunks).toString()};
}
test('targeted update is offered only to that phone, without hiding the other phone’s previous version',async t=>{
 const {dir,global,scoped}=fixture(t);
 assert.deepEqual(JSON.parse((await request(dir,'/apps','old-pixel')).body).apps,[scoped]);
 assert.deepEqual(JSON.parse((await request(dir,'/apps','fold')).body).apps,[global]);
});
test('matching metadata, APK and detached signature are available to the selected phone',async t=>{
 const {dir,scoped}=fixture(t);
 const base='/apps/'+scoped.id;
 assert.deepEqual(JSON.parse((await request(dir,base+'/manifest','old-pixel')).body),scoped);
 assert.equal((await request(dir,base+'/apk','old-pixel')).body,'apk');
 assert.equal((await request(dir,base+'/idsig','old-pixel')).body,'sidecar');
 for(const route of ['manifest','apk','idsig'])assert.equal((await request(dir,base+'/'+route,'fold')).status,404);
});
test('ordinary APK delivery stays supported; the authenticated local publisher can inspect a scoped package',async t=>{
 const {dir,global,scoped}=fixture(t);
 assert.equal((await request(dir,'/apps/'+global.id+'/apk','fold')).body,'apk');
 assert.equal((await request(dir,'/apps/'+global.id+'/idsig','fold')).status,404);
 assert.equal((await request(dir,'/apps/'+scoped.id+'/manifest',null,true)).status,200);
});
