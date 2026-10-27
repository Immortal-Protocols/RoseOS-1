// Explicit integration test: synthetic speech -> Live -> real selected Guardian -> spoken reply.
import fs from 'node:fs';import crypto from 'node:crypto';import https from 'node:https';
import OpenAI from '../../rose-agent/server/node_modules/openai/index.mjs';
import {LiveWS} from '../../rose-agent/server/node_modules/openai/resources/live/ws.mjs';
import {openaiVoiceSession} from '../../rose-agent/voice-providers/openai-session.mjs';
import {guardianVoiceInstructions} from './bridge.mjs';
const privateDir=process.env.HOME+'/.config/pixel-voice';const token=fs.readFileSync(privateDir+'/pair-token','utf8').trim();
async function call(route,data){return new Promise((resolve,reject)=>{const body=data?JSON.stringify(data):null;const r=https.request({hostname:'127.0.0.1',port:9443,path:route,rejectUnauthorized:false,method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'}},res=>{let text='';res.on('data',c=>text+=c);res.on('end',()=>{try{const value=JSON.parse(text);if(res.statusCode>=400)reject(Error(value.error));else resolve(value);}catch(e){reject(e);}});});r.on('error',reject);r.end(body);});}
const {mode}=await call('/guardian/status'),phone=await call('/glasses/status');if(!mode||!phone.ready)throw Error('Connect the Pixel Guardian session first');
const wav=fs.readFileSync('/tmp/guardian-test.wav');let pcm;for(let p=12;p+8<=wav.length;){const n=wav.readUInt32LE(p+4);if(wav.toString('ascii',p,p+4)==='data'){pcm=wav.subarray(p+8,p+8+n);break;}p+=8+n+n%2;}if(!pcm)throw Error('Missing PCM');
const ws=new LiveWS(new OpenAI({apiKey:fs.readFileSync(privateDir+'/openai-api-key','utf8').trim(),maxRetries:0}));
let interval,timer,settle,input='',output='',result='',position=0,delegated=false,audioBytes=0,failure;let finish;const done=new Promise(r=>finish=r);
timer=setTimeout(()=>{failure='Timed out';finish();},200000);
ws.socket.on('open',()=>{const session=openaiVoiceSession(guardianVoiceInstructions(mode),[]);session.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session});});
ws.on('event',async event=>{
 if(event.type==='session.started')interval=setInterval(()=>{const frame=Buffer.alloc(960);if(position<pcm.length){pcm.copy(frame,0,position,position+960);position+=960;}ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);
 if(event.type==='session.input_transcript.delta')input+=event.delta||'';
 if(event.type==='session.output_audio.delta')audioBytes+=Buffer.from(event.delta||event.audio||'','base64').length;
 if(event.type==='session.output_transcript.delta'){output+=event.delta||'';if(result){clearTimeout(settle);settle=setTimeout(finish,1800);}}
 if(event.type==='session.delegation.created'&&!delegated){delegated=true;try{
  let task=await call('/guardian/ask',{id:crypto.randomUUID(),request:input,generation:mode.generation,sessionId:phone.sessionId});
  while(task.state==='running'){await new Promise(r=>setTimeout(r,500));task=await call('/guardian/task/'+task.id);}
  if(task.state!=='completed')throw Error(task.text||'Guardian failed');result=task.text;
  ws.send({type:'session.commentary.append',delegation_id:event.delegation.id,content:result});
 }catch(e){failure=e.message;finish();}}
 if(event.type==='error'){failure=event.error?.message;finish();}
});ws.on('error',e=>{failure=e.message;finish();});
await done;clearInterval(interval);clearTimeout(timer);clearTimeout(settle);ws.close();
console.log(JSON.stringify({syntheticSpeech:true,realGuardian:true,input,result,output,audioBytes,delegated,failure}));if(failure||!result||!output.includes('Holland'))process.exitCode=1;
