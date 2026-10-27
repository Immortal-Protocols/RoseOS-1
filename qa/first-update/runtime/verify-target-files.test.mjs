// Self-test of verify-target-files.py on synthetic target-files zips (no build involved).
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';import {execFileSync,spawnSync} from 'node:child_process';
const tool=new URL('./verify-target-files.py',import.meta.url).pathname;
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function build({alter=false,presigned=true}){
 const d=fs.mkdtempSync(path.join(os.tmpdir(),'qa-tf-'));const apks={agent:Buffer.from('agent-apk'),voice:Buffer.from('voice-apk'),calc:Buffer.from('calc-apk')};
 const policy={agent:{packageName:'dev.pixel.backgroundcontrol',apkSha256:sha(apks.agent)},voice:{packageName:'dev.pixel.voice',apkSha256:sha(apks.voice)},targets:[{packageName:'dev.pixel.calculator.agent',apkSha256:sha(apks.calc)}]};
 fs.writeFileSync(path.join(d,'policy.json'),JSON.stringify(policy));
 const py=`import zipfile,sys
z=zipfile.ZipFile(sys.argv[1],'w')
z.writestr('SYSTEM_EXT/priv-app/PixelBackgroundControl/PixelBackgroundControl.apk',b'agent-apk')
z.writestr('PRODUCT/app/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk',b'voice-apk${alter?'-rezipped':''}')
z.writestr('PRODUCT/app/RosePreinstall_dev_pixel_calculator_agent/RosePreinstall_dev_pixel_calculator_agent.apk',b'calc-apk')
c='${presigned?'PRESIGNED':'build/make/target/product/security/platform.x509.pem'}'
z.writestr('META/apkcerts.txt','name="PixelBackgroundControl.apk" certificate="PRESIGNED" private_key=""\\nname="RosePreinstall_dev_pixel_voice.apk" certificate="'+c+'" private_key=""\\nname="RosePreinstall_dev_pixel_calculator_agent.apk" certificate="PRESIGNED" private_key=""\\n')
z.close()`;
 execFileSync('python3',['-c',py,path.join(d,'tf.zip')]);return d;
}
const run=d=>spawnSync('python3',[tool,path.join(d,'tf.zip'),path.join(d,'policy.json')],{encoding:'utf8'});
test('passes when bytes match the receipt and all are PRESIGNED',()=>{const r=run(build({}));assert.equal(r.status,0,r.stdout);assert.equal(JSON.parse(r.stdout).verdict,'PASS');});
test('fails when the build rewrote an APK',()=>{const r=run(build({alter:true}));assert.equal(r.status,1);assert.equal(JSON.parse(r.stdout).results.find(x=>x.packageName==='dev.pixel.voice').bytesMatchReceipt,false);});
test('fails when release signing would re-sign a preinstall',()=>{const r=run(build({presigned:false}));assert.equal(r.status,1);assert.equal(JSON.parse(r.stdout).results.find(x=>x.packageName==='dev.pixel.voice').presigned,false);});
