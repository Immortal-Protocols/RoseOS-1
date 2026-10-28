// Atomic, idempotent writer for the existing /apps catalog served by server/delivery.mjs, fed only by a verified
// os-release app-only artifact (APK, app-release-receipt.json, SHA256SUMS). It writes the same layout the server
// reads: <sha256>.apk, optional <sha256>.apk.idsig and <sha256>.json last. It never deletes or rewrites an existing
// record, has no network transport, and never prints or records a paired-device id.
//
//   node workspace/rose-agent/delivery/catalog.mjs --artifact-dir DIR --catalog-dir DIR --receipt FILE
//     [--label TEXT] [--device-id-file FILE] [--idsig FILE] [--expected-signer SHA256] [--apksigner PATH]
//
// Preconditions, all before the first write: SHA256SUMS covers exactly the APK and the receipt and every digest
// matches; the receipt is a nonsimulated app-release whose size, sha256 and signer equal the APK bytes and the pin;
// optional apksigner re-check; the versionCode is above every catalog record of the package. An identical existing
// record is accepted unchanged (rerun); any other record under the same id, or a different receipt file, fails.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';

const MAX_APK=150*1024*1024,MAX_IDSIG=1024*1024,HEX=/^[a-f0-9]{64}$/;
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const fail=message=>{throw Object.assign(Error(message),{catalog:true});};
const records=dir=>fs.readdirSync(dir).filter(f=>/^[a-f0-9]{64}\.json$/.test(f)).sort()
 .map(f=>({file:f,bytes:fs.readFileSync(path.join(dir,f))}));
const identity=entry=>JSON.stringify(Object.fromEntries(Object.entries(entry).filter(([k])=>k!=='created').sort()));

function writeOnce(file,bytes){
 // Same name means same content: an existing file is accepted only with identical bytes, never replaced.
 if(fs.existsSync(file)){if(!fs.readFileSync(file).equals(bytes))fail(`${path.basename(file)} exists with different bytes`);return false;}
 const tmp=`${file}.tmp-${process.pid}`;
 const fd=fs.openSync(tmp,'wx',0o600);
 try{fs.writeSync(fd,bytes);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
 fs.renameSync(tmp,file);return true;
}
function syncDir(dir){const fd=fs.openSync(dir,'r');try{fs.fsyncSync(fd);}finally{fs.closeSync(fd);}}

export function verifyArtifact(artifactDir,expectedSigner,apksigner){
 const receipt=JSON.parse(fs.readFileSync(path.join(artifactDir,'app-release-receipt.json'),'utf8'));
 if(receipt.receiptType!=='app-release'||receipt.simulated!==false)fail('not a nonsimulated app-release receipt');
 if(!/^[a-z][\w.]+$/.test(receipt.packageName||'')||!Number.isInteger(receipt.versionCode)||!/^[\w.-]+\.apk$/.test(receipt.apk||''))
  fail('receipt lacks packageName, versionCode or apk');
 const sums=fs.readFileSync(path.join(artifactDir,'SHA256SUMS'),'utf8').trim().split('\n').map(l=>l.split(/ {2}/));
 const listed=sums.map(([,name])=>name).sort();
 if(JSON.stringify(listed)!==JSON.stringify([receipt.apk,'app-release-receipt.json'].sort()))fail('SHA256SUMS must cover exactly the APK and the receipt');
 for(const [sum,name] of sums)if(digest(fs.readFileSync(path.join(artifactDir,name)))!==sum)fail(`${name} does not match SHA256SUMS`);
 const apk=fs.readFileSync(path.join(artifactDir,receipt.apk));
 if(apk.length>MAX_APK)fail('APK too large');
 if(apk.length!==receipt.size||digest(apk)!==receipt.sha256)fail('APK size or sha256 differs from the receipt');
 if(!HEX.test(expectedSigner||'')||receipt.signerSha256!==expectedSigner)fail('receipt signer is not the pinned app signer');
 if(apksigner){
  const out=execFileSync(apksigner,['verify','--print-certs',path.join(artifactDir,receipt.apk)],{encoding:'utf8'});
  const signers=[...out.matchAll(/certificate SHA-256 digest: ([0-9a-f]{64})/g)].map(m=>m[1]);
  if(JSON.stringify(signers)!==JSON.stringify([expectedSigner]))fail('APK is not signed by exactly the pinned app signer');
 }
 return {receipt,apk,receiptBytes:fs.readFileSync(path.join(artifactDir,'app-release-receipt.json'))};
}

export function publish({artifactDir,catalogDir,receiptPath,label,deviceId=null,idsig=null,expectedSigner,apksigner=null,now=Date.now}){
 if(deviceId!==null&&!HEX.test(deviceId))fail('device id must be 64 lowercase hex characters');
 const {receipt,apk,receiptBytes}=verifyArtifact(artifactDir,expectedSigner,apksigner);
 const id=receipt.sha256;
 const entry={id,label:label||`Rose Agent ${receipt.versionName||receipt.versionCode}`,packageName:receipt.packageName,
  versionCode:receipt.versionCode,size:apk.length,sha256:id};
 if(deviceId)entry.deviceId=deviceId;
 let sidecar=null;
 if(idsig){
  sidecar=fs.readFileSync(idsig);
  if(!sidecar.length||sidecar.length>MAX_IDSIG)fail('signature sidecar is empty or too large');
  entry.v4Signature={size:sidecar.length,sha256:digest(sidecar)};
 }
 fs.mkdirSync(catalogDir,{recursive:true,mode:0o700});
 const before=records(catalogDir);
 const existing=before.find(r=>r.file===`${id}.json`);
 const others=before.filter(r=>r!==existing).map(r=>JSON.parse(r.bytes));
 let stored;
 if(existing){
  stored=JSON.parse(existing.bytes);
  if(identity(stored)!==identity(entry))fail(`a different catalog record already exists for ${id}`);
  if(!fs.existsSync(path.join(catalogDir,`${id}.apk`))||digest(fs.readFileSync(path.join(catalogDir,`${id}.apk`)))!==id)
   fail(`the catalog APK for ${id} is missing or altered`);
 }else{
  const same=others.filter(e=>e.packageName===entry.packageName);
  const newest=Math.max(0,...same.map(e=>e.versionCode));
  if(entry.versionCode<=newest)fail(`${entry.packageName} versionCode ${entry.versionCode} must exceed catalog ${newest}`);
  // created orders /apps; it must sort after every retained record of the package, even with a skewed clock.
  stored={...entry,created:Math.max(now(),1+Math.max(0,...same.map(e=>e.created||0)))};
 }
 const publishReceipt={receiptType:'app-catalog-publish',id,packageName:stored.packageName,versionCode:stored.versionCode,
  size:stored.size,sha256:id,signerSha256:receipt.signerSha256,label:stored.label,created:stored.created,
  v4Signature:stored.v4Signature||null,deviceScoped:Boolean(stored.deviceId),candidate:receipt.candidate||null,
  minimumTestedOs:receipt.minimumTestedOs||null,appReleaseReceiptSha256:digest(receiptBytes),
  retainedRecords:others.map(e=>e.id).sort(),transport:'none (catalog file write only)'};
 const receiptOut=Buffer.from(JSON.stringify(publishReceipt,null,2)+'\n');
 if(fs.existsSync(receiptPath)&&!fs.readFileSync(receiptPath).equals(receiptOut))fail('the publish receipt exists with different content');
 // Bytes first, metadata last: the server lists a record only once its JSON exists.
 writeOnce(path.join(catalogDir,`${id}.apk`),apk);
 if(sidecar)writeOnce(path.join(catalogDir,`${id}.apk.idsig`),sidecar);
 writeOnce(path.join(catalogDir,`${id}.json`),Buffer.from(JSON.stringify(stored)));
 syncDir(catalogDir);
 for(const r of before)if(!fs.readFileSync(path.join(catalogDir,r.file)).equals(r.bytes))fail(`retained record ${r.file} changed`);
 writeOnce(receiptPath,receiptOut);
 return publishReceipt;
}

function pinnedSigner(){
 const config=path.resolve(import.meta.dirname,'../../../os/release-pipeline/frankel.json');
 return fs.existsSync(config)?JSON.parse(fs.readFileSync(config,'utf8')).identities.appSignerSha256.toLowerCase():null;
}

if(import.meta.url===`file://${process.argv[1]}`){
 const known=['artifact-dir','catalog-dir','receipt','label','device-id-file','idsig','expected-signer','apksigner'];
 try{
  const args={};
  for(let i=2;i<process.argv.length;i+=2){
   const key=process.argv[i];
   if(!/^--[a-z-]+$/.test(key)||process.argv[i+1]===undefined)fail('bad argument');
   args[key.slice(2)]=process.argv[i+1];
  }
  const unknown=Object.keys(args).filter(k=>!known.includes(k));
  if(unknown.length||!args['artifact-dir']||!args['catalog-dir']||!args.receipt)
   fail('usage: catalog.mjs --artifact-dir DIR --catalog-dir DIR --receipt FILE [--label TEXT] [--device-id-file FILE] [--idsig FILE] [--expected-signer SHA256] [--apksigner PATH]');
  const deviceId=args['device-id-file']?fs.readFileSync(args['device-id-file'],'utf8').trim():null;
  console.log(JSON.stringify(publish({artifactDir:args['artifact-dir'],catalogDir:args['catalog-dir'],receiptPath:args.receipt,
   label:args.label,deviceId,idsig:args.idsig||null,expectedSigner:args['expected-signer']||pinnedSigner(),apksigner:args.apksigner||null}),null,2));
 }catch(error){
  // Only the catalog's own messages are printed; nothing else is echoed.
  console.error(JSON.stringify({verdict:'FAIL',error:error.catalog?error.message:error.code||error.name}));
  process.exit(1);
 }
}
