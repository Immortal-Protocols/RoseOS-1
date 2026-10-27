// Explicit opt-in. Real Live speech -> actual browser request assembly -> /voice/task.
import fs from 'node:fs';
import vm from 'node:vm';
import OpenAI from '../server/node_modules/openai/index.mjs';
import {LiveWS} from '../server/node_modules/openai/resources/live/ws.mjs';
import {openaiVoiceSession} from './openai-session.mjs';
import {voiceInstructions,providerBehavior} from '../server/voice-instructions.mjs';
import {call} from '../../experiments/decisions-phone/phone.mjs';
const file=process.argv[2];if(!file)throw Error('Pass a mono PCM16 24 kHz WAV with an authorized model command.');
const wav=fs.readFileSync(file);let pcm;
for(let p=12;p+8<=wav.length;){const n=wav.readUInt32LE(p+4);if(wav.toString('ascii',p,p+4)==='data'){pcm=wav.subarray(p+8,p+8+n);break;}p+=8+n+n%2;}
if(!pcm)throw Error('No PCM data');
let startedResolve,startedReject,peer,frames,position=0,handle,finish,transcript='',requests=[],matched=false,inputRms=0;
const started=new Promise((r,j)=>{startedResolve=r;startedReject=j;}),finished=new Promise(r=>finish=r);
const ws=new LiveWS(new OpenAI({apiKey:fs.readFileSync(process.env.HOME+'/.config/pixel-voice/openai-api-key','utf8').trim(),maxRetries:0}));
class Peer{constructor(){peer=this;this.iceGatheringState='complete';}createDataChannel(){return this.channel={readyState:'open',send:s=>ws.send(JSON.parse(s)),close(){}};}addTrack(){}async createOffer(){return{sdp:'isolated'};}async setLocalDescription(x){this.localDescription=x;}async setRemoteDescription(){}close(){}}
class InputAudioContext{resume(){return Promise.resolve();}createAnalyser(){return {fftSize:256,getFloatTimeDomainData:a=>a.fill(inputRms),connect(){}};}createGain(){return {gain:{value:0},connect(){}};}createMediaStreamSource(){return {connect(){}};}close(){return Promise.resolve();}}
const context={AudioContext:InputAudioContext,console,setTimeout,clearTimeout,setInterval,clearInterval,Float32Array,Map,Set,Promise,Date,JSON,RTCPeerConnection:Peer,Audio:class{play(){return Promise.resolve();}pause(){}},MediaStream:class{},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[],getAudioTracks:()=>[]})}}};context.window=context;vm.createContext(context);
for(const f of ['voice-commands.js','voice-providers.js'])vm.runInContext(fs.readFileSync(new URL(f,import.meta.url),'utf8'),context);
ws.socket.on('open',()=>{const session=openaiVoiceSession(voiceInstructions+'\n'+providerBehavior,[]);session.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session});});
ws.on('event',e=>{
 if(e.type==='session.started'){startedResolve(e.session.id);frames=setInterval(()=>{const b=Buffer.alloc(960);if(position<pcm.length){pcm.copy(b,0,position,position+960);position+=960;}let sum=0;for(let i=0;i<b.length;i+=2){const v=b.readInt16LE(i)/32768;sum+=v*v;}inputRms=Math.sqrt(sum/(b.length/2));ws.send({type:'session.input_audio.append',audio:b.toString('base64')});},20);}
 if(e.type==='session.input_transcript.delta'){transcript+=e.delta;console.log(JSON.stringify({delta:e.delta,start:e.start_ms,end:e.end_ms}));}
 peer?.channel?.onmessage?.({data:JSON.stringify(e)});
 if(e.type==='error'){process.exitCode=1;console.log(JSON.stringify({error:e.error?.message}));finish();}
});ws.on('error',e=>{console.log(JSON.stringify({error:e.message}));finish();});
const timeout=setTimeout(()=>{startedReject(Error('Speech test timed out'));finish();},35000);
try{
 handle=await context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:await started},transport:{sdp:'answer'}}),toolContext:()=>({appRoutinesEnabled:false}),tools:{delegate_task:async p=>{
  requests.push(p.request);let result=await call('/voice/task',{id:'model-speech-fixed-'+Date.now(),request:p.request});
  for(let n=0;n<50&&['starting','running'].includes(result.state);n++){await new Promise(r=>setTimeout(r,100));result=await call('/voice/task/'+result.id);}
  const model=(await call('/health')).taskModel;
  console.log(JSON.stringify({request:p.request,result,model}));
  if(model.model==='gpt-6.1-sol'&&model.effort==='medium'&&model.serviceTier==='ultrafast'&&result.state==='completed'&&/medium.*ultra[ -]?fast/i.test(p.request)){matched=true;setTimeout(finish,700);}
  return JSON.stringify(result);
 }},onMessage(){},onError:()=>finish()});
 await finished;
 console.log(JSON.stringify({transcript,requests}));if(!matched||requests.length<1||requests.length>3||new Set(requests).size!==requests.length)process.exitCode=1;
}finally{clearTimeout(timeout);clearInterval(frames);await handle?.close();ws.close();}
