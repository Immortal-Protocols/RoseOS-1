// Opt-in voice prompt check. Synthetic audio, simulated backend, no device actions.
import fs from 'node:fs';
import OpenAI from '../server/node_modules/openai/index.mjs';
import {LiveWS} from '../server/node_modules/openai/resources/live/ws.mjs';
import {openaiVoiceSession} from './openai-session.mjs';
import {voiceInstructions,providerBehavior,liveHandoffRules} from '../server/voice-instructions.mjs';

const file=process.argv[2];
if(!file)throw Error('Pass a synthetic 24 kHz mono PCM WAV request');
const wav=fs.readFileSync(file);let pcm;
for(let p=12;p+8<=wav.length;){
 const size=wav.readUInt32LE(p+4);
 if(wav.toString('ascii',p,p+4)==='data'){pcm=wav.subarray(p+8,p+8+size);break;}
 p+=8+size+size%2;
}
if(!pcm)throw Error('PCM WAV required');
const key=fs.readFileSync(process.env.HOME+'/.config/pixel-voice/openai-api-key','utf8').trim();
const ws=new LiveWS(new OpenAI({apiKey:key,maxRetries:0}));
let frames,settle,resultTimer,resultSent=false,position=0,delegations=0,spoken='',input='',failure;
let finish;
const done=new Promise(resolve=>finish=resolve);
const timeout=setTimeout(()=>{failure='Timed out waiting for a verified-result response';finish();},30000);
ws.socket.on('open',()=>{
 const session=openaiVoiceSession(voiceInstructions+'\n'+providerBehavior+'\n'+liveHandoffRules,
  [{messages:[{role:'assistant',text:'I will ask it to do that.'}]}]);
 session.audio.format={type:'audio/pcm',rate:24000};
 ws.send({type:'session.start',session});
});
ws.on('event',event=>{
 if(event.type==='session.started')frames=setInterval(()=>{
  const frame=Buffer.alloc(960);
  if(position<pcm.length){pcm.copy(frame,0,position,position+960);position+=960;}
  ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});
 },20);
 if(event.type==='session.input_transcript.delta')input+=event.delta||'';
 if(event.type==='session.output_transcript.delta'){
  spoken+=event.delta||'';
  if(resultSent){clearTimeout(settle);settle=setTimeout(finish,2500);}
 }
 if(event.type==='session.delegation.created'&&event.delegation?.target==='client'){
  delegations++;
  const complete=()=>{resultSent=true;ws.send({type:'session.commentary.append',delegation_id:event.delegation.id,
   content:'Simulated test result: the requested Messages app was opened and its conversation was scrolled upward successfully. No message was sent.'});};
  if(process.env.VOICE_TEST_PROGRESS==='1'){
   ws.send({type:'session.commentary.append',delegation_id:event.delegation.id,content:'Messages is open. Finding the conversation now; scrolling is not finished yet.'});
   resultTimer=setTimeout(complete,4500);
  }else complete();
 }
 if(event.type==='error'){failure=event.error?.message||'Live error';finish();}
});
ws.on('error',error=>{failure=error.message;finish();});
try{
 await done;
 const handoffWording=/\b(?:ask|tell|get|have) (?:it|the (?:helper|backend|agent))\b/i.test(spoken);
 console.log(JSON.stringify({simulated:true,input,spoken,delegations,handoffWording,failure}));
 if(failure||delegations!==1||handoffWording)process.exitCode=1;
}finally{clearInterval(frames);clearTimeout(settle);clearTimeout(resultTimer);clearTimeout(timeout);ws.close();}
