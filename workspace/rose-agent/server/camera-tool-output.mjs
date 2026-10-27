import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

export const cameraTools=new Set(['ai_camera_inspect','ai_camera_prepare','ai_camera_capture','ai_camera_recording','ai_camera_anime_status']);
const defaultDirectory=path.join(os.homedir(),'.config/pixel-voice/camera-previews');

// Dynamic image outputs are flattened to text by the installed code-mode runtime.
// Return the same private image-file contract already used by phone screenshots;
// never make the worker parse a base64 URL out of a text transcript.
export function cameraToolOutput(result,{directory=defaultDirectory}={}){
 const {previewJpegBase64,...facts}=result;
 if(previewJpegBase64!==undefined){
  let bytes;
  if(typeof previewJpegBase64==='string'&&previewJpegBase64.length<=800000&&/^[A-Za-z0-9+/]*={0,2}$/.test(previewJpegBase64)){
   const candidate=Buffer.from(previewJpegBase64,'base64');
   if(candidate.length>=4&&candidate[0]===255&&candidate[1]===216&&candidate.at(-2)===255&&candidate.at(-1)===217)bytes=candidate;
  }
  facts.previewAvailable=!!bytes;
  if(bytes){
   try{
    fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const target=path.join(directory,crypto.randomUUID()+'.jpg');
    fs.writeFileSync(target,bytes,{mode:0o600,flag:'wx'});
    facts.previewImagePath=target;
    facts.previewInstruction='Use the built-in view_image tool on previewImagePath to see this already captured preview. Do not re-inspect the camera merely to recover image bytes. Metadata does not establish scene appearance.';
   }catch{
    facts.previewAvailable=false;
    facts.previewError='The preview could not be stored for viewing. The camera action status and any saved media remain authoritative; do not repeat a capture to recover the preview.';
   }
   // Retention failure is not a failed camera action. Never turn an already
   // executed capture into a retryable error because cleanup failed.
   try{
    const files=fs.readdirSync(directory).filter(n=>/^[0-9a-f-]+\.jpg$/.test(n)).map(n=>({n,time:fs.statSync(path.join(directory,n)).mtimeMs})).sort((a,b)=>b.time-a.time);
    for(const file of files.slice(20))fs.unlinkSync(path.join(directory,file.n));
   }catch{}
  }else facts.previewError='Camera image was unavailable or invalid; metadata does not establish scene appearance.';
 }
 return {success:['ok','verified','submitted','complete'].includes(result.status),contentItems:[{type:'inputText',text:JSON.stringify(facts)}]};
}

// Feed actual tool observations and exactly these preview pixels to the existing
// completion checker. This does not obtain another frame or activate UI tapping.
export function recordCameraEvidence(evidence,request,result,{directory=defaultDirectory}={}){
 if(!cameraTools.has(request.tool))return false;
 evidence.output(request.callId||request.tool,request.tool,result,{review:true});
 const facts=JSON.parse(result.contentItems[0].text),target=facts.previewImagePath;
 try{
  if(target&&path.dirname(target)===directory&&/^[0-9a-f-]+\.jpg$/.test(path.basename(target))){
   const stat=fs.lstatSync(target);
   if(!stat.isFile()||stat.isSymbolicLink()||stat.size>600000)throw Error('Invalid preview file');
   evidence.addImage(request.callId||request.tool,request.tool+' viewfinder observedAt='+facts.previewObservedAt,'data:image/jpeg;base64,'+fs.readFileSync(target).toString('base64'));
  }
 }catch{
  // A missing review image must not turn an already executed capture into a
  // tool failure that might cause the worker to repeat the physical action.
  evidence.unavailable.push('The Camera preview file was unavailable to the completion checker. Do not repeat a capture to recover it.');
 }
 return true;
}
