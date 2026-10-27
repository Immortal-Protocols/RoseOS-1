import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
const exec=promisify(execFile), source=fileURLToPath(new URL('./native/youtube-menu-ocr.swift',import.meta.url));
let binaryPromise;
async function binary(){
 return binaryPromise??=(async()=>{
  const dir=path.join(os.homedir(),'.config/pixel-voice/native');fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const digest=crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex').slice(0,16), target=path.join(dir,'youtube-menu-ocr-'+digest);
  if(!fs.existsSync(target)){await exec('/usr/bin/swiftc',['-O',source,'-o',target],{timeout:60000,maxBuffer:100000});fs.chmodSync(target,0o700);}
  return target;
 })();
}
export async function readMenuText(imagePath){
 const {stdout}=await exec(await binary(),[imagePath],{timeout:5000,maxBuffer:100000});return JSON.parse(stdout);
}
export function speedMenuTarget(screen,shot,rows){
 if(screen.packageName!=='com.google.android.youtube'||shot.packageName!==screen.packageName||screen.truncated)throw Error('YouTube menu identity changed');
 if(!screen.nodes.some(n=>n.text==='More'&&n.clickTarget!==undefined))throw Error('Expected YouTube playback settings menu');
 const matches=rows.filter(r=>r.confidence>=0.8&&/^Playback speed(?:\s+[·•]?\s*(?:\d+(?:\.\d+)?)?\s*x)?$/i.test(r.text.trim()));
 if(matches.length!==1)throw Error('Playback speed label was not unambiguously recognized');
 const r=matches[0],x=Math.round((r.x+r.width/2)*shot.displayWidth),y=Math.round((r.y+r.height/2)*shot.displayHeight);
 const nodes=screen.nodes.filter(n=>n.clickable&&n.enabled&&n.bounds&&x>n.bounds[0]&&x<n.bounds[2]&&y>n.bounds[1]&&y<n.bounds[3]);
 nodes.sort((a,b)=>(a.bounds[2]-a.bounds[0])*(a.bounds[3]-a.bounds[1])-(b.bounds[2]-b.bounds[0])*(b.bounds[3]-b.bounds[1]));
 if(!nodes.length||nodes[0].password)throw Error('No safe current menu row for Playback speed');
 return {x,y,nodeId:nodes[0].id};
}
// A single local OCR-assisted menu selection. The model never invents coordinates.
export async function finishYouTubeVisual({phone,result,args,ocr=readMenuText}){
 if(result.status!=='visual_target_required')return result;
 if(result.target!=='Playback speed'||result.continuation!=='youtube_speed_apply')throw Error('Unsupported visual continuation');
 const before=await phone({action:'inspect_ui'});
 if(before.windowId!==result.windowId)return {status:'needs_assistance',reason:'YouTube menu changed before visual selection'};
 const shot=await phone({action:'screenshot'});if(shot.status!=='ok'||(!shot.imagePath&&!shot.imageBase64))return {status:'needs_assistance',reason:'Cannot inspect the playback speed menu'};
 let imagePath=shot.imagePath,tempDir;
 let rows;
 try{
  if(!imagePath){
   const bytes=Buffer.from(shot.imageBase64,'base64');if(bytes.length>800000||shot.mimeType!=='image/jpeg')throw Error('Invalid menu screenshot');
   tempDir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-youtube-'));imagePath=path.join(tempDir,'menu.jpg');fs.writeFileSync(imagePath,bytes,{mode:0o600});
  }
  rows=await ocr(imagePath);
 }finally{if(tempDir)fs.rmSync(tempDir,{recursive:true,force:true});}
 const now=await phone({action:'inspect_ui'});
 if(now.windowId!==before.windowId)return {status:'needs_assistance',reason:'YouTube menu changed during visual selection'};
 let target;try{target=speedMenuTarget(now,shot,rows);}catch(e){return {status:'needs_assistance',reason:e.message};}
 const tap=await phone({action:'touch_point',snapshotId:now.snapshotId,...target});
 if(!['gesture_dispatched','action_accepted'].includes(tap.status))return {status:'needs_assistance',reason:'Playback speed menu selection was rejected'};
 return applyYouTubeSpeed({phone,args});
}

const pause=ms=>new Promise(r=>setTimeout(r,ms));
const named=(s,name)=>s.nodes?.find(n=>n.enabled&&n.clickTarget!==undefined&&(n.description===name||n.text===name));
export function pickerRate(s){
 const values=[...new Set((s.nodes||[]).filter(n=>n.class!=='android.widget.SeekBar'&&!/slider_seek_bar$/.test(n.viewId||'')).map(n=>n.description||'').filter(t=>/^\d+(?:\.\d+)?x$/.test(t)).map(t=>Number(t.slice(0,-1))))];
 if(values.length!==1)throw Error('Current playback speed is not unambiguous');return values[0];
}
async function picker(phone){
 const end=Date.now()+2500;do{const s=await phone({action:'inspect_ui'});if(s.packageName==='com.google.android.youtube'&&named(s,'Increase playback speed'))return s;await pause(100);}while(Date.now()<end);throw Error('Playback speed picker did not appear');
}
async function physicalLabel(phone,s,n){
 const r=await phone({action:'touch_label',snapshotId:s.snapshotId,nodeId:n.clickTarget,label:n.description||n.text});
 if(!['gesture_dispatched','action_accepted'].includes(r.status))throw Error('Speed control touch was not accepted');
}
export async function applyYouTubeSpeed({phone,args}){
 let touched=false;
 try{
  let s=await picker(phone),current=pickerRate(s);const rate=args.rate;
  const before=await phone({action:'app_command',packageName:'dev.pixel.voice',operation:'youtube_state'});
  if(current!==rate||before.reportedPlaybackSpeed!==rate){
   const presets=s.nodes.filter(n=>n.enabled&&n.clickTarget!==undefined&&/^\d+(?:\.\d+)?,/.test(n.description||''))
    .map(n=>({n,value:Number(n.description.split(',')[0])})).sort((a,b)=>Math.abs(a.value-rate)-Math.abs(b.value-rate));
   if(presets.length){touched=true;await physicalLabel(phone,s,presets[0].n);await pause(180);s=await picker(phone);current=pickerRate(s);}
   for(let i=0;i<60&&Math.abs(current-rate)>.001;i++){
    s=await picker(phone);current=pickerRate(s);if(Math.abs(current-rate)<.001)break;
    const old=current,n=named(s,current<rate?'Increase playback speed':'Decrease playback speed');if(!n)throw Error('Speed adjustment control missing');
    touched=true;await physicalLabel(phone,s,n);await pause(120);s=await picker(phone);current=pickerRate(s);
    if(Math.abs(current-old)<.001||Math.abs(current-rate)>Math.abs(old-rate)+.001)throw Error('Speed adjustment did not converge');
   }
  }
  s=await picker(phone);if(Math.abs(pickerRate(s)-rate)>.001)throw Error('Picker did not reach requested speed');
  let actual;const end=Date.now()+2200;
  do{actual=await phone({action:'app_command',packageName:'dev.pixel.voice',operation:'youtube_state'});if(actual.description===before.description&&actual.reportedPlaybackSpeed===rate)break;await pause(100);}while(Date.now()<end);
  if(actual.description!==before.description||actual.reportedPlaybackSpeed!==rate)throw Error('YouTube media session did not confirm the requested speed');
  await phone({action:'back'});await pause(150);
  actual=await phone({action:'app_command',packageName:'dev.pixel.voice',operation:'youtube_state'});
  if(actual.description!==before.description||actual.reportedPlaybackSpeed!==rate)throw Error('Playback speed did not persist after closing the picker');
  return {status:'verified',speed:rate,alreadyInRequestedState:!touched,verification:'YouTube media-session speed confirmed before and after closing the picker'};
 }catch(e){return {status:touched?'uncertain':'needs_assistance',reason:e.message,playbackMayHaveChanged:touched};}
}

export async function runYouTubeSpeed({phone,args}){
 const s=await phone({action:'inspect_ui'});
 if(s.packageName==='com.google.android.youtube'&&named(s,'Increase playback speed'))return applyYouTubeSpeed({phone,args});
 const result=await phone({action:'app_command',packageName:'dev.pixel.voice',operation:'youtube_speed',params:args});
 return finishYouTubeVisual({phone,result,args});
}
