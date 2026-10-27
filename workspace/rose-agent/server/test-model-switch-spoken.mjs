// Explicit opt-in: feed a local test WAV through Live speech and the real /voice/task path.
import fs from 'node:fs';
import os from 'node:os';
import OpenAI from 'openai';
import {LiveWS} from 'openai/resources/live/ws';
import {voiceSession} from './reconnect.mjs';
import {voiceInstructions} from './voice-instructions.mjs';
import {call} from '../../experiments/decisions-phone/phone.mjs';
const file=process.argv[2];if(!file)throw Error('Pass a mono PCM16 24kHz WAV containing a model-switch command.');
const wav=fs.readFileSync(file);let pcm;
for(let p=12;p+8<=wav.length;){const n=wav.readUInt32LE(p+4);if(wav.toString('ascii',p,p+4)==='data'){pcm=wav.subarray(p+8,p+8+n);break;}p+=8+n+(n%2);}
if(!pcm)throw Error('No PCM data');
const client=new OpenAI({apiKey:fs.readFileSync(os.homedir()+'/.config/pixel-voice/openai-api-key','utf8').trim()});
const ws=new LiveWS(client);let timer,timeout,position=0,transcript='',done=false,delegated=false;
const finished=new Promise((resolve,reject)=>{
 function finish(error){if(done)return;done=true;clearInterval(timer);clearTimeout(timeout);try{ws.send({type:'session.close'});}catch{}ws.close();error?reject(error):resolve();}
 timeout=setTimeout(()=>finish(Error('Spoken command timed out')),22000);
 ws.socket.on('open',()=>{const session=voiceSession(voiceInstructions,[]);session.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session});});
 ws.on('event',async e=>{
  if(e.type==='session.started')timer=setInterval(()=>{const frame=Buffer.alloc(960);if(position<pcm.length){pcm.copy(frame,0,position,position+960);position+=960;}ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);
  if(e.type==='session.input_transcript.delta')transcript+=e.delta;
  if(e.type==='session.delegation.created'&&!delegated){
   delegated=true;
   try{const id='model-spoken-'+Date.now();let result=await call('/voice/task',{id,request:transcript.trim()});
    for(let n=0;n<30&&['starting','running'].includes(result.state);n++){await new Promise(r=>setTimeout(r,100));result=await call('/voice/task/'+id);}
    const state=(await call('/health')).taskModel;
    console.log(JSON.stringify({transcript,result,taskModel:state}));
    if(result.state!=='completed'||state.model!=='gpt-6-luna')throw Error('Speech did not select Luna');finish();
   }catch(error){finish(error);}
  }
  if(e.type==='error')finish(Error(e.error?.message||'Live error'));
 });ws.on('error',finish);
});
await finished;
