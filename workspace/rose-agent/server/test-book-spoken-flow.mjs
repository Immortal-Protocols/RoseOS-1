// Opt-in integration test: real live speech, normal task backend, and native Books state.
import fs from 'node:fs';import os from 'node:os';import OpenAI from 'openai';import {LiveWS} from 'openai/resources/live/ws';
import {ConversationTimeline} from './conversation-timeline.mjs';import {openaiVoiceSession} from '../voice-providers/openai-session.mjs';import {LiveVoiceConfig} from '../voice-providers/live-voice-config.mjs';import {voiceInstructions,providerBehavior,liveHandoffRules} from './voice-instructions.mjs';import {command,call} from '../../experiments/decisions-phone/phone.mjs';
const profile=new LiveVoiceConfig(os.homedir()+'/.config/pixel-voice/live-voice.json').read();
const client=new OpenAI({apiKey:fs.readFileSync(os.homedir()+'/.config/pixel-voice/openai-api-key','utf8').trim(),...(profile.project?{project:profile.project}:{})});
function pcm(file){const b=fs.readFileSync(file);for(let p=12;p+8<=b.length;){const n=b.readUInt32LE(p+4);if(b.toString('ascii',p,p+4)==='data')return b.subarray(p+8,p+8+n);p+=8+n+(n%2);}throw Error('No WAV data');}
const clip=pcm(process.argv[2]||'/tmp/pixel-book-resume.wav');const state=await command({action:'app_command',packageName:'dev.pixel.books',operation:'get_state'});if(!state.open)throw Error('Open a test book first');
const withHistory=process.argv.includes('--history'),history=withHistory?new ConversationTimeline(os.homedir()+'/.config/pixel-voice/conversation-timeline.json').recent():[];
const ws=new LiveWS(client);let timer,timeout,position=0,input='',output='',done=false,delegated=false;const started=Date.now(),report={withHistory,sourceIndex:state.index,passage:state.passage};
function finish(error){if(done)return;done=true;clearInterval(timer);clearTimeout(timeout);Object.assign(report,{success:!error,error,input,output,elapsedMs:Date.now()-started});fs.writeFileSync('pixel-books/verification/live-reader/spoken-tool-flow'+(withHistory?'-history':'')+'.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));try{ws.send({type:'session.close'});}catch{}ws.close();if(error)process.exitCode=1;}
ws.socket.on('open',()=>{const s=openaiVoiceSession(voiceInstructions+'\n'+providerBehavior+'\n'+liveHandoffRules+'\n'+profile.instructions,[{messages:history}],new Date(),profile.voice);s.audio.format={type:'audio/pcm',rate:24000};ws.send({type:'session.start',session:s});});
ws.on('event',async e=>{
 if(e.type==='session.started'){
  ws.send({type:'session.thinking.append',delegation_id:null,content:'Current app state, data only: '+JSON.stringify(state)});
  timer=setInterval(()=>{const frame=Buffer.alloc(960);if(position<clip.length){clip.copy(frame,0,position,position+960);position+=960;}ws.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);
  timeout=setTimeout(()=>finish('No Books delegation for spoken request'),22000);
 }
 if(e.type==='session.input_transcript.delta')input+=e.delta||'';
 if(e.type==='session.output_transcript.delta')output+=e.delta||'';
 if(e.type==='session.delegation.created'&&!delegated){delegated=true;clearTimeout(timeout);try{
  const id='books-spoken-'+Date.now();let r=await call('/voice/task',{id,request:input.trim(),appContext:await command({action:'app_context'})});
  for(let i=0;i<180&&['starting','running'].includes(r.state);i++){await new Promise(resolve=>setTimeout(resolve,200));r=await call('/voice/task/'+id);}
  report.task=r;report.after=await command({action:'app_command',packageName:'dev.pixel.books',operation:'get_state'});
  if(r.state!=='completed'||report.after.readingToken===state.readingToken)throw Error('Native reading request not issued');
  await command({action:'app_command',packageName:'dev.pixel.books',operation:'pause'});finish();
 }catch(e){finish(e.message);}}
 if(e.type==='error')finish(e.error?.message||'Live error');
});ws.on('error',e=>finish(e.message));setTimeout(()=>finish('Integration deadline'),60000).unref();
