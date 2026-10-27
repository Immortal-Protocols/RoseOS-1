import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
const [file,label,deviceId]=process.argv.slice(2);
if(!file||!label||deviceId&&!/^[a-f0-9]{64}$/.test(deviceId))throw Error('Usage: node rose-agent/delivery/publish.mjs APK LABEL [PAIRED_DEVICE_ID]');
const root=path.resolve(import.meta.dirname,'../..');
execFileSync(root+'/tools/android-sdk/android-15/apksigner',['verify',path.resolve(file)],{env:{...process.env,JAVA_HOME:root+'/tools/jdk/Contents/Home'}});
const badging=execFileSync(root+'/tools/android-sdk/android-15/aapt',['dump','badging',path.resolve(file)],{encoding:'utf8'});
const packageName=badging.match(/package: name='([^']+)'/)?.[1];
if(!packageName)throw Error('Invalid APK');
const bytes=fs.readFileSync(file);if(bytes.length>150*1024*1024)throw Error('APK too large');
const id=crypto.createHash('sha256').update(bytes).digest('hex');
const dir=os.homedir()+'/.config/pixel-voice/delivery';fs.mkdirSync(dir,{recursive:true,mode:0o700});
fs.writeFileSync(dir+'/'+id+'.apk',bytes,{mode:0o600});
const versionCode=Number(badging.match(/versionCode='(\d+)'/)?.[1]||0);
const entry={id,label,packageName,versionCode,size:bytes.length,sha256:id,created:Date.now()};
if(deviceId)entry.deviceId=deviceId;
if(fs.existsSync(file+'.idsig')){
 const sidecar=fs.readFileSync(file+'.idsig');
 if(sidecar.length>1024*1024)throw Error('Signature sidecar too large');
 entry.v4Signature={size:sidecar.length,sha256:crypto.createHash('sha256').update(sidecar).digest('hex')};
 fs.writeFileSync(dir+'/'+id+'.apk.idsig',sidecar,{mode:0o600});
}
fs.writeFileSync(dir+'/'+id+'.json.tmp',JSON.stringify(entry),{mode:0o600});fs.renameSync(dir+'/'+id+'.json.tmp',dir+'/'+id+'.json');
console.log(JSON.stringify(entry));
