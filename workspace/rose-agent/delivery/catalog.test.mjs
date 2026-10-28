// delivery/catalog.mjs with synthetic artifacts, read back through the real server/delivery.mjs. No device, network,
// signing key or live catalog is used; the APK bytes are fixtures, not installable packages.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {Writable} from 'node:stream';
import {once} from 'node:events';
import {execFileSync, spawnSync} from 'node:child_process';
import {publish} from './catalog.mjs';
import {delivery} from '../server/delivery.mjs';

const PIN='ab'.repeat(32),DEVICE='d'.repeat(64);
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const CATALOG=path.join(import.meta.dirname,'catalog.mjs');

function setup(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'rose-catalog-'));
 t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const catalog=path.join(root,'delivery');fs.mkdirSync(catalog);
 // The retained 302 record, as the existing service holds it.
 const old=Buffer.from('signed rose agent 302');
 const prior={id:digest(old),label:'Rose Agent 302',packageName:'dev.pixel.voice',versionCode:302,size:old.length,sha256:digest(old),created:2000};
 fs.writeFileSync(path.join(catalog,prior.id+'.apk'),old);fs.writeFileSync(path.join(catalog,prior.id+'.json'),JSON.stringify(prior));
 const calc=Buffer.from('calculator 4');
 const other={id:digest(calc),label:'Calculator',packageName:'dev.pixel.calculator.agent',versionCode:4,size:calc.length,sha256:digest(calc),created:1500};
 fs.writeFileSync(path.join(catalog,other.id+'.apk'),calc);fs.writeFileSync(path.join(catalog,other.id+'.json'),JSON.stringify(other));
 return {root,catalog,prior,other};
}
function artifact(root,{name='a',version=303,bytes=Buffer.from(`signed rose agent ${version}`),signer=PIN,edit=r=>r,extra=null}={}){
 const dir=path.join(root,name);fs.mkdirSync(dir);
 const apk=`dev.pixel.voice-${version}.apk`;fs.writeFileSync(path.join(dir,apk),bytes);
 const receipt=edit({receiptType:'app-release',simulated:false,candidate:'f'.repeat(40),packageName:'dev.pixel.voice',versionCode:version,
  versionName:`${version}-app-beta`,apk,size:bytes.length,sha256:digest(bytes),signerSha256:signer,minimumTestedOs:{build:'2026102804',controller:28},checks:[]});
 fs.writeFileSync(path.join(dir,'app-release-receipt.json'),JSON.stringify(receipt,null,2)+'\n');
 const names=[apk,'app-release-receipt.json',...(extra?[extra]:[])];
 if(extra)fs.writeFileSync(path.join(dir,extra),'extra');
 fs.writeFileSync(path.join(dir,'SHA256SUMS'),names.sort().map(n=>`${digest(fs.readFileSync(path.join(dir,n)))}  ${n}\n`).join(''));
 return dir;
}
const snapshot=dir=>Object.fromEntries(fs.readdirSync(dir).sort().map(f=>[f,digest(fs.readFileSync(path.join(dir,f)))]));
async function request(dir,url,deviceId=null){
 const chunks=[];let status;
 const res=new Writable({write(chunk,encoding,callback){chunks.push(Buffer.from(chunk));callback();}});
 res.writeHead=code=>{status=code;};
 const completed=once(res,'finish');
 assert.equal(delivery({method:'GET',socket:{remoteAddress:'10.0.0.2'}},res,new URL(url,'http://test'),dir,deviceId),true);
 await completed;return {status,body:Buffer.concat(chunks)};
}

test('a verified 303 becomes the Rose Agent offered by /apps while 302 and other records stay served',async t=>{
 const {root,catalog,prior,other}=setup(t);
 const dir=artifact(root);
 const receipt=publish({artifactDir:dir,catalogDir:catalog,receiptPath:path.join(root,'publish.json'),expectedSigner:PIN,now:()=>1000});
 const bytes=fs.readFileSync(path.join(dir,'dev.pixel.voice-303.apk'));
 assert.equal(receipt.id,digest(bytes));
 assert.equal(receipt.created,2001,'sorts after the retained 302 even with an earlier clock');
 assert.deepEqual(receipt.retainedRecords,[prior.id,other.id].sort());
 const apps=JSON.parse((await request(catalog,'/apps')).body).apps;
 assert.deepEqual(apps.map(a=>[a.packageName,a.versionCode]),[['dev.pixel.voice',303],['dev.pixel.calculator.agent',4]]);
 assert.deepEqual(JSON.parse((await request(catalog,`/apps/${receipt.id}/manifest`)).body),
  {id:receipt.id,label:'Rose Agent 303-app-beta',packageName:'dev.pixel.voice',versionCode:303,size:bytes.length,sha256:receipt.id,created:2001});
 assert.ok((await request(catalog,`/apps/${receipt.id}/apk`)).body.equals(bytes));
 assert.equal((await request(catalog,`/apps/${receipt.id}/idsig`)).status,404,'no sidecar unless one was verified present');
 assert.equal((await request(catalog,`/apps/${prior.id}/apk`)).body.toString(),'signed rose agent 302','302 bytes retained');
 assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root,'publish.json'))),receipt);
 assert.equal(fs.statSync(path.join(catalog,receipt.id+'.apk')).mode&0o077,0);
});

test('rerunning the same artifact changes nothing; a different record under the same id is refused',t=>{
 const {root,catalog}=setup(t);
 const dir=artifact(root),receiptPath=path.join(root,'publish.json');
 const first=publish({artifactDir:dir,catalogDir:catalog,receiptPath,expectedSigner:PIN,now:()=>5000});
 const before=snapshot(catalog),receiptBytes=fs.readFileSync(receiptPath);
 assert.deepEqual(publish({artifactDir:dir,catalogDir:catalog,receiptPath,expectedSigner:PIN,now:()=>9000}),first);
 assert.deepEqual(snapshot(catalog),before);
 assert.ok(fs.readFileSync(receiptPath).equals(receiptBytes));
 assert.throws(()=>publish({artifactDir:dir,catalogDir:catalog,receiptPath:path.join(root,'other.json'),label:'Renamed',expectedSigner:PIN}),
  /a different catalog record already exists/);
 assert.throws(()=>publish({artifactDir:dir,catalogDir:catalog,receiptPath,deviceId:DEVICE,expectedSigner:PIN}),/a different catalog record/);
 assert.deepEqual(snapshot(catalog),before);
 fs.writeFileSync(path.join(catalog,first.id+'.apk'),'altered');
 assert.throws(()=>publish({artifactDir:dir,catalogDir:catalog,receiptPath,expectedSigner:PIN}),/missing or altered/);
});

test('every precondition fails before the first catalog write',t=>{
 const {root,catalog}=setup(t);
 const before=snapshot(catalog);
 const cases=[
  [{version:302,bytes:Buffer.from('another 302 build')},/versionCode 302 must exceed catalog 302/],
  [{version:301},/versionCode 301 must exceed catalog 302/],
  [{signer:'cd'.repeat(32)},/not the pinned app signer/],
  [{edit:r=>({...r,size:r.size+1})},/size or sha256 differs/],
  [{edit:r=>({...r,sha256:'0'.repeat(64)})},/size or sha256 differs/],
  [{edit:r=>({...r,simulated:true})},/nonsimulated app-release/],
  [{edit:r=>({...r,receiptType:'os-release'})},/nonsimulated app-release/],
  [{edit:r=>({...r,versionCode:'303'})},/lacks packageName, versionCode or apk/],
  [{extra:'config.json'},/exactly the APK and the receipt/],
 ];
 cases.forEach(([options,message],i)=>{
  const dir=artifact(root,{name:'case'+i,...options});
  assert.throws(()=>publish({artifactDir:dir,catalogDir:catalog,receiptPath:path.join(root,`r${i}.json`),expectedSigner:PIN}),message);
 });
 const tampered=artifact(root,{name:'tampered'});
 fs.appendFileSync(path.join(tampered,'dev.pixel.voice-303.apk'),'x');
 assert.throws(()=>publish({artifactDir:tampered,catalogDir:catalog,receiptPath:path.join(root,'t.json'),expectedSigner:PIN}),/does not match SHA256SUMS/);
 assert.throws(()=>publish({artifactDir:artifact(root,{name:'dev'}),catalogDir:catalog,receiptPath:path.join(root,'d.json'),deviceId:'phone',expectedSigner:PIN}),/64 lowercase hex/);
 assert.throws(()=>publish({artifactDir:artifact(root,{name:'nopin'}),catalogDir:catalog,receiptPath:path.join(root,'n.json'),expectedSigner:undefined}),/pinned app signer/);
 assert.deepEqual(snapshot(catalog),before);
 assert.deepEqual(fs.readdirSync(root).filter(f=>f.endsWith('.json')),[],'no publish receipt for a refused artifact');
});

test('optional apksigner re-check and verified idsig sidecar',async t=>{
 const {root,catalog}=setup(t);
 const fake=path.join(root,'apksigner');
 const signer=path.join(root,'signer');
 fs.writeFileSync(fake,`#!/bin/sh\necho "Signer #1 certificate SHA-256 digest: $(cat ${signer})"\n`,{mode:0o755});
 fs.writeFileSync(signer,'cd'.repeat(32));
 const dir=artifact(root);
 assert.throws(()=>publish({artifactDir:dir,catalogDir:catalog,receiptPath:path.join(root,'p.json'),expectedSigner:PIN,apksigner:fake}),
  /not signed by exactly the pinned app signer/);
 const big=path.join(root,'big.idsig');fs.writeFileSync(big,Buffer.alloc(1024*1024+1));
 assert.throws(()=>publish({artifactDir:dir,catalogDir:catalog,receiptPath:path.join(root,'p.json'),expectedSigner:PIN,idsig:big}),/too large/);
 fs.writeFileSync(signer,PIN);
 const idsig=path.join(root,'v4.idsig');fs.writeFileSync(idsig,'v4 signature');
 const receipt=publish({artifactDir:dir,catalogDir:catalog,receiptPath:path.join(root,'p.json'),expectedSigner:PIN,apksigner:fake,idsig});
 assert.deepEqual(receipt.v4Signature,{size:12,sha256:digest(Buffer.from('v4 signature'))});
 assert.equal((await request(catalog,`/apps/${receipt.id}/idsig`)).body.toString(),'v4 signature');
});

test('device scoping is kept in the catalog record but never in the receipt or output',async t=>{
 const {root,catalog,prior}=setup(t);
 const dir=artifact(root),idFile=path.join(root,'device-id'),receiptPath=path.join(root,'publish.json');
 fs.writeFileSync(idFile,DEVICE+'\n',{mode:0o600});
 const result=spawnSync(process.execPath,[CATALOG,'--artifact-dir',dir,'--catalog-dir',catalog,'--receipt',receiptPath,
  '--device-id-file',idFile,'--expected-signer',PIN],{encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);
 const receipt=JSON.parse(result.stdout);
 assert.equal(receipt.deviceScoped,true);
 for(const text of [result.stdout,result.stderr,fs.readFileSync(receiptPath,'utf8')])assert.ok(!text.includes(DEVICE));
 assert.equal(JSON.parse(fs.readFileSync(path.join(catalog,receipt.id+'.json'))).deviceId,DEVICE);
 assert.equal(JSON.parse((await request(catalog,'/apps',DEVICE)).body).apps.find(a=>a.packageName==='dev.pixel.voice').versionCode,303);
 assert.equal(JSON.parse((await request(catalog,'/apps','e'.repeat(64))).body).apps.find(a=>a.packageName==='dev.pixel.voice').id,prior.id,
  'other phones keep the retained 302');
 const refused=spawnSync(process.execPath,[CATALOG,'--artifact-dir',artifact(root,{name:'b',version:302,bytes:Buffer.from('x')}),
  '--catalog-dir',catalog,'--receipt',path.join(root,'r2.json'),'--device-id-file',idFile,'--expected-signer',PIN],{encoding:'utf8'});
 assert.equal(refused.status,1);
 assert.equal(JSON.parse(refused.stderr).verdict,'FAIL');
 assert.ok(!refused.stderr.includes(DEVICE));
});

test('the CLI defaults the signer pin to the checked-in app signer identity',t=>{
 const config=JSON.parse(fs.readFileSync(path.join(import.meta.dirname,'../../../os/release-pipeline/frankel.json'),'utf8'));
 const {root,catalog}=setup(t);
 const dir=artifact(root,{signer:config.identities.appSignerSha256});
 const out=execFileSync(process.execPath,[CATALOG,'--artifact-dir',dir,'--catalog-dir',catalog,'--receipt',path.join(root,'p.json')],{encoding:'utf8'});
 assert.equal(JSON.parse(out).signerSha256,config.identities.appSignerSha256);
 const usage=spawnSync(process.execPath,[CATALOG,'--artifact-dir',dir],{encoding:'utf8'});
 assert.equal(usage.status,1);
 assert.match(JSON.parse(usage.stderr).error,/^usage:/);
});
