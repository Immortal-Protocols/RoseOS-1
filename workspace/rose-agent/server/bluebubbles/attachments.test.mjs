import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAttachmentReader} from './attachments.mjs';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=','base64');
function setup(t,response){const directory=fs.mkdtempSync(path.join(os.tmpdir(),'message-files-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));let calls=0;const reader=createAttachmentReader({downloadAttachment:async()=>{calls++;return response;}},{directory});return {reader,directory,calls:()=>calls};}
const message=(type='image/png',name='photo.png')=>[{messageGuid:'message-one',sender:'Example',text:'Look at https://example.com/photo',attachments:[{attachmentGuid:'attachment-one',name,mimeType:type,totalBytes:100}]}];
test('real image bytes become a private inspectable file, and repeated reads reuse the cache',async t=>{
 const f=setup(t,{data:png,mimeType:'image/png'}),a=await f.reader.read(message());
 assert.equal(a.files[0].kind,'image');assert.equal(a.files[0].status,'available');assert.deepEqual(fs.readFileSync(a.files[0].path),png);assert.equal(a.links[0].url,'https://example.com/photo');
 assert.equal(fs.statSync(a.files[0].path).mode&0o777,0o600);await f.reader.read(message());assert.equal(f.calls(),1);
});
test('JSON attachments remain their actual bytes rather than being overwritten by cache metadata',async t=>{
 const data=Buffer.from('{"project":"example"}'),f=setup(t,{data,mimeType:'application/json'}),r=await f.reader.read(message('application/json','../../outside.json'));
 assert.equal(path.dirname(r.files[0].path),f.directory);assert.deepEqual(fs.readFileSync(r.files[0].path),data);assert.equal(r.files[0].kind,'document');
});
test('invalid image bytes and oversized attachments are unavailable without inventing their contents',async t=>{
 const f=setup(t,{data:Buffer.from('not an image'),mimeType:'image/png'});assert.equal((await f.reader.read(message())).files[0].status,'unavailable');
 const m=message();m[0].attachments[0].totalBytes=30*1024*1024;const r=await f.reader.read(m);assert.equal(r.files[0].status,'unavailable');assert.equal(f.calls(),1);
});
test('attachment inspection is bounded and reports remaining files',async t=>{
 const f=setup(t,{data:png,mimeType:'image/png'}),m=message();m[0].attachments=Array.from({length:4},(_,i)=>({...m[0].attachments[0],attachmentGuid:'a-'+i}));
 const r=await f.reader.read(m);assert.equal(r.files.length,3);assert.equal(r.moreAttachmentsAvailable,true);assert.equal(f.calls(),3);assert.equal(r.nextAttachmentOffset,3);const next=await f.reader.read(m,{offset:3});assert.equal(next.files.length,1);assert.equal(next.files[0].attachmentGuid,'a-3');assert.equal(next.moreAttachmentsAvailable,false);
});
