// Opt-in live regression: synthesized spoken requests, no phone tools or actions.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import OpenAI from 'openai';
import {LiveWS} from 'openai/resources/live/ws';
import {voiceSession} from './reconnect.mjs';
import {voiceInstructions} from './voice-instructions.mjs';
const files=process.argv.slice(2);
if(files.length!==3)throw Error('Supply three mono 24kHz PCM16 WAV files.');
function pcm(file){const b=fs.readFileSync(file);for(let p=12;p+8<=b.length;){const n=b.readUInt32LE(p+4);if(b.toString('ascii',p,p+4)==='data')return b.subarray(p+8,p+8+n);p+=8+n+(n%2);}throw Error('Missing WAV data');}
const clips=files.map(pcm);
const client=new OpenAI({apiKey:fs.readFileSync(path.join(os.homedir(),'.config/pixel-voice/openai-api-key'),'utf8').trim()});
const ws=new LiveWS(client);
let timer,timeout,phase=-1,position=0,clip=null,delegated=false,done=false,nextAt=0;
const results=[];
function finish(ok,error){if(done)return;done=true;clearInterval(timer);clearTimeout(timeout);console.log(JSON.stringify({success:ok,error,results}));process.exitCode=ok?0:1;try{ws.send({type:'session.close'});}catch{}setTimeout(()=>{ws.close();},300);}
function next(){phase++;if(phase===clips.length)return finish(true);clip=clips[phase];position=0;delegated=false;results.push({request:phase+1,transcript:'',delegations:0});console.log('Spoken request '+(phase+1));clearTimeout(timeout);timeout=setTimeout(()=>finish(false,'No delegation for spoken request '+(phase+1)),17000);}
ws.socket.on('open',()=>{const session=voiceSession(voiceInstructions,[{messages:[{role:'user',text:'Flip the camera.'},{role:'assistant',text:'The backend tapped Flip Camera once.'}]}]);session.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session});});
ws.on('event',e=>{
 if(e.type==='session.started'){nextAt=Date.now()+800;timer=setInterval(()=>{if(done)return;if(phase<0&&Date.now()>=nextAt)next();let frame=Buffer.alloc(960);if(clip){clip.copy(frame,0,position,position+960);position+=960;if(position>=clip.length)clip=null;}ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);}
 if(e.type==='session.input_transcript.delta'&&phase>=0){results[phase].transcript+=e.delta;}
 if(e.type==='session.delegation.created'){if(delegated)return finish(false,'Duplicate delegation for a single request');delegated=true;clearTimeout(timeout);results[phase].delegations++;console.log('Delegation '+(phase+1));ws.send({type:'session.commentary.append',delegation_id:e.delegation.id,content:'Test backend result for the latest request: tapped Flip Camera once. This is a simulated success; no physical phone action was performed.'});setTimeout(next,5500);}
 if(e.type==='error')finish(false,e.error?.message||'Live error');
});
ws.on('error',e=>finish(false,e.message));
setTimeout(()=>finish(false,'Test exceeded 65 seconds'),65000).unref();
