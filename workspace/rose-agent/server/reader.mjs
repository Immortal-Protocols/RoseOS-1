import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {voiceId} from './elevenlabs.mjs';

export function validateNarration(data){
 if(!data||typeof data.text!=='string'||!data.text.trim()||data.text.length>5000)throw new Error('Narration needs a passage of at most 5000 characters');
 return data.text.trim();
}
export async function readerNarration(data,root=((process.env.PIXEL_VOICE_CONFIG_DIR||(process.env.HOME+"/.config/pixel-voice"))+"/reader-audio")){
 const text=validateNarration(data);
 const hash=crypto.createHash('sha256').update(voiceId+'|eleven_turbo_v2_5|'+text).digest('hex');
 await fs.mkdir(root,{recursive:true,mode:0o700});const file=path.join(root,hash+'.mp3');
 try{return {audio:(await fs.readFile(file)).toString('base64'),format:'mp3',cached:true};}catch{}
 const key=(await fs.readFile(((process.env.PIXEL_VOICE_CONFIG_DIR||(process.env.HOME+"/.config/pixel-voice"))+"/elevenlabs-api-key"),'utf8')).trim();
 const r=await fetch('https://api.elevenlabs.io/v1/text-to-speech/'+voiceId+'?output_format=mp3_44100_128',{
  method:'POST',headers:{'xi-api-key':key,'content-type':'application/json'},signal:AbortSignal.timeout(30000),
  body:JSON.stringify({text,model_id:'eleven_turbo_v2_5',voice_settings:{stability:.6,similarity_boost:.93,use_speaker_boost:true}})
 });
 if(!r.ok){const err=await r.json().catch(()=>({}));throw new Error(err.detail?.status==='missing_permissions'?'ElevenLabs Text to Speech permission is required for book narration.':`Book narration is unavailable (${r.status}).`);}
 const audio=Buffer.from(await r.arrayBuffer());if(audio.length>8_000_000)throw new Error('Narration segment is too large');
 await fs.writeFile(file,audio,{mode:0o600});const files=await fs.readdir(root);if(files.length>128){const oldest=await Promise.all(files.map(async f=>({f,t:(await fs.stat(path.join(root,f))).mtimeMs})));for(const {f} of oldest.sort((a,b)=>a.t-b.t).slice(0,files.length-128))await fs.unlink(path.join(root,f));}
 return {audio:audio.toString('base64'),format:'mp3',cached:false};
}
