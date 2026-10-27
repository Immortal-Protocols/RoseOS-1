// Opt-in sequential Live regression: synthetic requests, simulated tool results, no phone actions.
import fs from 'node:fs';import https from 'node:https';import vm from 'node:vm';import {createRequire} from 'node:module';
import OpenAI from '../server/node_modules/openai/index.mjs';import {LiveWS} from '../server/node_modules/openai/resources/live/ws.mjs';
import {voiceSession} from '../server/reconnect.mjs';import {openaiVoiceSession} from './openai-session.mjs';import {voiceInstructions,providerBehavior,liveHandoffRules} from '../server/voice-instructions.mjs';
const dir=process.env.HOME+'/.config/pixel-voice',require=createRequire(import.meta.url),create=require('../app/assets/apple-music-voice.js');
const token=fs.readFileSync(dir+'/pair-token','utf8').trim(),ca=fs.readFileSync(dir+'/server-cert.pem');
const api=(method,path,body)=>new Promise((resolve,reject)=>{const q=https.request('https://127.0.0.1:9443'+path,{method,ca,checkServerIdentity:()=>undefined,headers:{authorization:'Bearer '+token,'content-type':'application/json'}},r=>{let b='';r.on('data',x=>b+=x);r.on('end',()=>{try{const d=JSON.parse(b);r.statusCode<300?resolve(d):reject(Error(d.error));}catch(e){reject(e);}});});q.on('error',reject);q.end(body?JSON.stringify(body):undefined);});
function readPcm(file){const b=fs.readFileSync(file);for(let p=12;p+8<=b.length;){const n=b.readUInt32LE(p+4);if(b.toString('ascii',p,p+4)==='data')return b.subarray(p+8,p+8+n);p+=8+n+n%2;}throw Error('PCM WAV required');}
const clips=process.argv.slice(2).map(readPcm);if(clips.length<2)throw Error('Provide at least two 24 kHz mono PCM WAV requests');let clip=0,pcm=clips[0],nextClipTimer;const results=[];
const ws=new LiveWS(new OpenAI({apiKey:fs.readFileSync(dir+'/openai-api-key','utf8').trim(),maxRetries:0}));
let resolveStarted,rejectStarted,pc,result,done,frames,position=0,generalCalls=0,musicCalls=0,transcript='';const started=new Promise((r,j)=>{resolveStarted=r;rejectStarted=j;}),finished=new Promise(r=>done=r);
const stream={getTracks:()=>[],getAudioTracks:()=>[]};
class Peer{constructor(){pc=this;this.iceGatheringState='complete';}createDataChannel(){return this.channel={readyState:'open',send:s=>ws.send(JSON.parse(s)),close(){}};}addTrack(){}async createOffer(){return {sdp:'isolated-test'};}async setLocalDescription(x){this.localDescription=x;}async setRemoteDescription(){}close(){}}
class Audio{play(){return Promise.resolve();}pause(){}}
const context={console,setTimeout,clearTimeout,setInterval,clearInterval,Float32Array,Map,Set,Promise,Date,JSON,RTCPeerConnection:Peer,Audio,MediaStream:class{},navigator:{mediaDevices:{getUserMedia:async()=>stream}}};context.window=context;vm.createContext(context);
for(const file of ['voice-commands.js','voice-providers.js']){
 const source=fs.readFileSync(new URL(file,import.meta.url),'utf8');
 vm.runInContext(source,context);
}
ws.socket.on('open',()=>{let history=[];if(process.env.VOICE_TEST_HISTORY==='1'){const saved=JSON.parse(fs.readFileSync(dir+'/conversation-timeline.json','utf8'));history=[{messages:(Array.isArray(saved)?saved:saved.entries||saved.messages||[]).slice(-40)}];}const asContext=process.env.VOICE_TEST_CONTEXT_HISTORY==='1';const session=(asContext?openaiVoiceSession:voiceSession)(voiceInstructions+'\n'+providerBehavior+'\n'+liveHandoffRules,history);session.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session});});
ws.on('event',e=>{if(e.type==='session.started'){resolveStarted(e.session.id);frames=setInterval(()=>{const frame=Buffer.alloc(960);if(position<pcm.length){pcm.copy(frame,0,position,position+960);position+=960;}ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);}if(e.type==='session.input_transcript.delta')transcript+=e.delta;if(!(process.env.VOICE_TEST_DROP_DELEGATIONS==='1'&&e.type==='session.delegation.created'))pc?.channel?.onmessage?.({data:JSON.stringify(e)});if(e.type==='error'){console.log(JSON.stringify({error:e.error?.message}));done();}});ws.on('error',e=>{console.log(JSON.stringify({error:e.message}));done();});
let handle;const deadline=setTimeout(()=>{rejectStarted(Error('Live sequence deadline'));done();},55000);
try{
 handle=await context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:await started},transport:{sdp:'test-answer'}}),tools:{apple_music_play:async p=>{musicCalls++;result={status:'verified_playing',title:p.title,artist:p.artist,testOnly:true};results.push(result);if(musicCalls>=clips.length)nextClipTimer=setTimeout(done,1200);else nextClipTimer=setTimeout(()=>{pcm=clips[++clip];position=0;console.log(JSON.stringify({nextRequest:clip+1}));},2200);return JSON.stringify(result);},delegate_task:async()=>{generalCalls++;done();return '{}';}},onMessage(){},onError:()=>done()});
 await finished;
 console.log(JSON.stringify({transcript,musicCalls,generalCalls,results}));
 if(musicCalls!==clips.length||generalCalls!==0||result?.status!=='verified_playing')process.exitCode=1;
}finally{clearInterval(frames);clearTimeout(deadline);clearTimeout(nextClipTimer);await handle?.close();ws.close();}
