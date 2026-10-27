// Explicit developer test: synthetic speech -> normal Live delegation -> real Pixel worker.
// Does not forward synthetic speech to personal history or memory.
import fs from 'node:fs';import https from 'node:https';import vm from 'node:vm';import {createRequire} from 'node:module';
import OpenAI from '../server/node_modules/openai/index.mjs';import {LiveWS} from '../server/node_modules/openai/resources/live/ws.mjs';
import {openaiVoiceSession} from '../voice-providers/openai-session.mjs';
import {appWorkerPolicy} from '../voice-providers/app-worker-policy.mjs';
import {voiceInstructions,providerBehavior,liveHandoffRules} from '../server/voice-instructions.mjs';
const require=createRequire(import.meta.url),create=require('./media-tool.js');
const dir=process.env.HOME+'/.config/pixel-voice',token=fs.readFileSync(dir+'/pair-token','utf8').trim(),ca=fs.readFileSync(dir+'/server-cert.pem');
const api=(method,path,body)=>new Promise((resolve,reject)=>{const q=https.request('https://127.0.0.1:9443'+path,{method,ca,checkServerIdentity:()=>undefined,headers:{authorization:'Bearer '+token,'content-type':'application/json'}},r=>{let b='';r.on('data',x=>b+=x);r.on('end',()=>{try{const d=JSON.parse(b);r.statusCode<300?resolve(d):reject(Error(d.error));}catch(e){reject(e);}});});q.on('error',reject);q.end(body?JSON.stringify(body):undefined);});
const [file,expected]=process.argv.slice(2);if(!file||!['get_state','pause','resume'].includes(expected))throw Error('Pass a 24 kHz mono PCM WAV and expected media operation');
const b=fs.readFileSync(file);let pcm;for(let p=12;p+8<=b.length;){const n=b.readUInt32LE(p+4);if(b.toString('ascii',p,p+4)==='data'){pcm=b.subarray(p+8,p+8+n);break;}p+=8+n+n%2;}if(!pcm)throw Error('PCM WAV required');
const ws=new LiveWS(new OpenAI({apiKey:fs.readFileSync(dir+'/openai-api-key','utf8').trim(),maxRetries:0}));
let startedResolve,startedReject,pc,done,frames,position=0,calls=0,general=0,result,operation;
const started=new Promise((r,j)=>{startedResolve=r;startedReject=j;}),finished=new Promise(r=>done=r);
const stream={getTracks:()=>[],getAudioTracks:()=>[]};
class Peer{constructor(){pc=this;this.iceGatheringState='complete';}createDataChannel(){return this.channel={readyState:'open',send:s=>ws.send(JSON.parse(s)),close(){}};}addTrack(){}async createOffer(){return {sdp:'isolated-test'};}async setLocalDescription(x){this.localDescription=x;}async setRemoteDescription(){}close(){}}
class Audio{play(){return Promise.resolve();}pause(){}}
const context={console,setTimeout,clearTimeout,setInterval,clearInterval,Float32Array,Map,Set,Promise,Date,JSON,RTCPeerConnection:Peer,Audio,MediaStream:class{},navigator:{mediaDevices:{getUserMedia:async()=>stream}}};context.window=context;vm.createContext(context);
for(const file of ['voice-commands.js','voice-providers.js'])vm.runInContext(fs.readFileSync(new URL('../voice-providers/'+file,import.meta.url),'utf8'),context);
ws.socket.on('open',()=>{const session=openaiVoiceSession(voiceInstructions+'\n'+providerBehavior+'\n'+liveHandoffRules+'\n'+appWorkerPolicy('/tools/control.py',{media:true}),[]);session.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session});});
ws.on('event',e=>{if(e.type==='session.started'){startedResolve(e.session.id);frames=setInterval(()=>{const frame=Buffer.alloc(960);if(position<pcm.length){pcm.copy(frame,0,position,position+960);position+=960;}ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);}pc?.channel?.onmessage?.({data:JSON.stringify(e)});if(e.type==='error'){console.log(JSON.stringify({error:e.error?.message}));done();}});
ws.on('error',e=>{console.log(JSON.stringify({error:e.message}));startedReject(e);done();});
let handle;const deadline=setTimeout(()=>{startedReject(Error('Live test deadline'));done();},25000);
try{
 handle=await context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:await started},transport:{sdp:'test-answer'}}),toolContext:()=>({media:true}),tools:{media_control:async p=>{calls++;operation=p.operation;result=JSON.parse(await create({api})(p));done();return JSON.stringify(result);},delegate_task:async()=>{general++;done();return '{}';}},onMessage(){},onError:()=>done()});
 await finished;console.log(JSON.stringify({operation,calls,general,result}));
 if(calls!==1||general!==0||operation!==expected||!['verified_state','verified_playing','verified_paused','already_playing','already_paused'].includes(result?.status))process.exitCode=1;
}finally{clearInterval(frames);clearTimeout(deadline);await handle?.close();ws.close();}
