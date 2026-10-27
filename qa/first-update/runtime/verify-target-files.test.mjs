// Self-test of verify-target-files.py on synthetic target-files zips (no build involved).
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';import {execFileSync,spawnSync} from 'node:child_process';
const tool=new URL('./verify-target-files.py',import.meta.url).pathname;
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function build({alter=false,presigned=true,conn=null}){
 const d=fs.mkdtempSync(path.join(os.tmpdir(),'qa-tf-'));
 let voice=Buffer.from('voice-apk');
 if(conn){const vp=path.join(d,'voice.apk');execFileSync('python3',['-c',"import zipfile,sys\nwith zipfile.ZipFile(sys.argv[1],'w') as z: z.writestr('assets/connection.json',sys.argv[2])",vp,JSON.stringify(conn)]);voice=fs.readFileSync(vp);}
 const apks={agent:Buffer.from('agent-apk'),voice,calc:Buffer.from('calc-apk')};
 const policy={agent:{packageName:'dev.pixel.backgroundcontrol',apkSha256:sha(apks.agent)},voice:{packageName:'dev.pixel.voice',apkSha256:sha(apks.voice)},targets:[{packageName:'dev.pixel.calculator.agent',apkSha256:sha(apks.calc)}]};
 fs.writeFileSync(path.join(d,'policy.json'),JSON.stringify(policy));
 const py=`import zipfile,sys
z=zipfile.ZipFile(sys.argv[1],'w')
z.writestr('SYSTEM_EXT/priv-app/PixelBackgroundControl/PixelBackgroundControl.apk',b'agent-apk')
z.writestr('PRODUCT/app/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk',open(sys.argv[2],'rb').read()+${alter?"b'-rezipped'":"b''"})
z.writestr('PRODUCT/app/RosePreinstall_dev_pixel_calculator_agent/RosePreinstall_dev_pixel_calculator_agent.apk',b'calc-apk')
c='${presigned?'PRESIGNED':'build/make/target/product/security/platform.x509.pem'}'
z.writestr('META/apkcerts.txt','name="PixelBackgroundControl.apk" certificate="PRESIGNED" private_key=""\\nname="RosePreinstall_dev_pixel_voice.apk" certificate="'+c+'" private_key=""\\nname="RosePreinstall_dev_pixel_calculator_agent.apk" certificate="PRESIGNED" private_key=""\\n')
z.close()`;
 fs.writeFileSync(path.join(d,'voice.bin'),apks.voice);execFileSync('python3',['-c',py,path.join(d,'tf.zip'),path.join(d,'voice.bin')]);return d;
}
const run=(d,extra=[])=>spawnSync('python3',[tool,path.join(d,'tf.zip'),path.join(d,'policy.json'),...extra],{encoding:'utf8'});
const PIN='f703219f8b57699699e85b0cb839bd4ef161a1571b31569b72f1722da79075ca',EP='https://100.68.248.87:9444',want=['--expect-endpoint',EP,'--expect-pin',PIN];
const voiceRow=r=>JSON.parse(r.stdout).results.find(x=>x.packageName==='dev.pixel.voice');
test('B1: passes with the Pixel 10 endpoint and pin',()=>{const r=run(build({conn:{endpoint:EP,pin:PIN}}),want);assert.equal(r.status,0,r.stdout);});
test('B1: fails with compile-only loopback assets',()=>{const r=run(build({conn:{endpoint:'https://127.0.0.1:9443',pin:'0'.repeat(64)}}),want);assert.equal(r.status,1);assert.equal(voiceRow(r).connectionMatches,false);});
test('B1: fails if a token is bundled, without printing it',()=>{const r=run(build({conn:{endpoint:EP,pin:PIN,token:'SECRET-QA-FIXTURE'}}),want);assert.equal(r.status,1);assert.equal(voiceRow(r).connection.hasToken,true);assert.ok(!r.stdout.includes('SECRET-QA-FIXTURE'));});
test('passes when bytes match the receipt and all are PRESIGNED',()=>{const r=run(build({}));assert.equal(r.status,0,r.stdout);assert.equal(JSON.parse(r.stdout).verdict,'PASS');});
test('fails when the build rewrote an APK',()=>{const r=run(build({alter:true}));assert.equal(r.status,1);assert.equal(JSON.parse(r.stdout).results.find(x=>x.packageName==='dev.pixel.voice').bytesMatchReceipt,false);});
test('fails when release signing would re-sign a preinstall',()=>{const r=run(build({presigned:false}));assert.equal(r.status,1);assert.equal(JSON.parse(r.stdout).results.find(x=>x.packageName==='dev.pixel.voice').presigned,false);});
