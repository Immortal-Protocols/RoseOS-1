import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';
import {SpeakerWorker,VoiceMatch} from './service.mjs';
export const prompts={hello:"Hey. Say a sentence so I can recognize you.",again:"I'm not sure yet. Say another sentence for me.",guest:"Hey, I don't recognize your voice. Did the owner lend you his glasses?",code:"Okay. Ask the owner for the guest passphrase, then say it to me.",wrong:"That didn't match. Try the passphrase again.",denied:"Okay. Ask the owner before using his assistant.",owner:"Hey, the owner.",welcome:"You're in. This is a guest conversation."};
export function voiceMatchHttp(root,client,pairToken){
 const worker=new SpeakerWorker(path.dirname(new URL(import.meta.url).pathname));worker.start();
 const match=new VoiceMatch(root,{worker,client});
 const expected=crypto.createHmac('sha256',pairToken).update('pixel-voice-match-owner').digest('hex');
 const owner=req=>{const h=String(req.headers['x-pixel-owner-action']||'');return h.length===expected.length&&crypto.timingSafeEqual(Buffer.from(h),Buffer.from(expected));};
 const transcription=async pcm=>{const {default:OpenAI,toFile}=await import('openai');const raw=Buffer.from(pcm,'base64');if(raw.length<4000||raw.length>512000)throw Error('Invalid recording');const b=Buffer.alloc(44+raw.length);b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(16000,24);b.writeUInt32LE(32000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(raw.length,40);raw.copy(b,44);return (await client.audio.transcriptions.create({model:'gpt-4o-mini-transcribe',file:await toFile(b,'reply.wav',{type:'audio/wav'}),language:'en'})).text;};
 async function handle(req,url,body){const route=url.pathname;if(!route.startsWith('/voice-match/'))return null;
  if(route.startsWith('/voice-match/admin/')&&!owner(req))return {status:403,body:{error:'Use the unlocked Pixel to manage voice match'}};
  if(route==='/voice-match/status')return {body:match.status()};
  if(route==='/voice-match/begin')return {body:match.begin()};
  if(route==='/voice-match/verify')return {body:await match.verify(body.attempt,body.pcm)};
  if(route==='/voice-match/confirm'){const a=match.attempt(body.attempt);if(a.confirmed!==undefined)throw Error('Confirmation already used');const text=await transcription(body.pcm);a.confirmed=/\b(yes|yeah|yep|sure|did|lent|allowed|permission)\b/i.test(text)&&! /\b(no|not|didn['’]?t)\b/i.test(text);return {body:{confirmed:a.confirmed}};}
  if(route==='/voice-match/password'){const a=match.attempt(body.attempt);if(!a.confirmed)throw Error('Ask the owner for permission first');return {body:await match.password(body.attempt,body.pcm)};}
  if(route==='/voice-match/prompt'){const name=body.name;if(!Object.hasOwn(prompts,name))throw Error('Unknown prompt');const file=path.join(root,'prompts',name+'.mp3');return {body:{audio:fs.readFileSync(file).toString('base64')}};}
  if(route==='/voice-match/admin/enroll')return {body:await match.enroll(body.pcm,body.reset===true)};
  if(route==='/voice-match/admin/password')return {body:match.setPassword(body.password)};
  if(route==='/voice-match/admin/toggle')return {body:match.enable(body.enabled===true)};
  if(route==='/voice-match/admin/skip')return {body:match.skip(body.attempt)};
  return {status:404,body:{error:'Unknown voice-match action'}};
 }
 return {match,handle,scope:token=>match.identity(token),required:()=>match.config().enabled===true,stop:()=>worker.stop()};
}
