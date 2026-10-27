import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import OpenAI from 'openai';
import {LiveWS} from 'openai/resources/live/ws';
import {voiceSession} from './reconnect.mjs';
const client=new OpenAI({apiKey:fs.readFileSync(path.join(os.homedir(),'.config/pixel-voice/openai-api-key'),'utf8').trim()});
const ws=new LiveWS(client);let audioTimer,timeout,text='',done=false;
const finish=(ok)=>{if(done)return;done=true;clearInterval(audioTimer);clearTimeout(timeout);console.log(JSON.stringify({success:ok,reply:text}));process.exitCode=ok?0:1;try{ws.send({type:'session.close'});}catch{}setTimeout(()=>ws.close(),500);};
ws.socket.on('open',()=>{
 const session=voiceSession('You are an isolated test assistant. No tools are available.',[{messages:[{role:'user',text:'We are designing a garden planner named Marigold.'},{role:'assistant',text:'Understood. The garden planner is named Marigold.'}]}]);
 session.audio.format={type:'audio/pcm',rate:24000};
 ws.send({type:'session.start',event_id:'reconnect-test-start',session});
 timeout=setTimeout(()=>finish(false),25000);
});
ws.on('event',e=>{
 if(e.type==='session.started'){
  console.log('Live history accepted');
  audioTimer=setInterval(()=>ws.send({type:'session.input_audio.append',audio:Buffer.alloc(960).toString('base64')}),20);
  ws.send({type:'session.instructions.append',event_id:'test-recall',delegation_id:null,content:'For this isolated recall test, speak one short sentence now identifying the app we were designing and its name from the prior conversation. Then remain silent.'});
 }
 if(e.type==='session.output_transcript.delta'){
  text+=e.delta;
  if(/Marigold/i.test(text))setTimeout(()=>finish(true),800);
 }
 if(e.type==='error'){console.log('Live test error',JSON.stringify(e));finish(false);}
});
ws.on('error',e=>{console.log('Live transport error',e.message);finish(false);});
