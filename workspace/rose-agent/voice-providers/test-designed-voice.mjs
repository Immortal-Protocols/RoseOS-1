import fs from 'node:fs';
import OpenAI from '../server/node_modules/openai/index.mjs';
import {LiveWS} from '../server/node_modules/openai/resources/live/ws.mjs';
import {openaiVoiceSession} from './openai-session.mjs';
import {LiveVoiceConfig} from './live-voice-config.mjs';

const candidate=process.argv[2],output=process.argv[3];
if(!candidate||!output)throw Error('Pass a candidate JSON and private output PCM path');
const profile=new LiveVoiceConfig(candidate).read();
const client=new OpenAI({apiKey:fs.readFileSync(process.env.HOME+'/.config/pixel-voice/openai-api-key','utf8').trim(),maxRetries:0});
const speech=await client.audio.speech.create({model:'gpt-4o-mini-tts',voice:'alloy',input:'Hello. Please tell me that the voice test is working.',response_format:'pcm'});
const input=Buffer.from(await speech.arrayBuffer());
const live=new LiveWS(client);
let position=0,frames,settle,finish,spoken='',heard='',failure='',audio=[],events=new Set();
const done=new Promise(resolve=>finish=resolve);
const timeout=setTimeout(()=>{failure='No verified voice response within 45 seconds';finish();},45000);
live.socket.on('open',()=>{const session=openaiVoiceSession('This is a synthetic voice test. Answer the spoken greeting with one short sentence. '+profile.instructions,[],new Date(),profile.voice);session.audio.format={type:'audio/pcm',rate:24000};live.send({type:'session.start',session});});
live.on('event',event=>{
 events.add(event.type);
 if(event.type==='session.started')frames=setInterval(()=>{const frame=Buffer.alloc(960);if(position<input.length){input.copy(frame,0,position,position+960);position+=960;}live.send({type:'session.input_audio.append',audio:frame.toString('base64')});},20);
 if(event.type==='session.input_transcript.delta')heard+=event.delta||'';
 if(event.type==='session.output_transcript.delta'){spoken+=event.delta||'';clearTimeout(settle);settle=setTimeout(()=>{if(audio.length)finish();},3500);}
 if(event.type==='session.output_audio.delta')audio.push(Buffer.from(event.delta||event.audio||'','base64'));
 if(event.type==='session.output_audio.done'&&audio.length)finish();
 if(event.type==='session.delegation.created')live.send({type:'session.commentary.append',delegation_id:event.delegation.id,content:'The synthetic voice test is working. Say that in one sentence.'});
 if(event.type==='error'){failure=event.error?.message||'Live error';finish();}
});
live.on('error',error=>{failure=error.message;finish();});
try{await done;const bytes=Buffer.concat(audio);if(bytes.length)fs.writeFileSync(output,bytes,{mode:0o600});console.log(JSON.stringify({verified:!failure&&bytes.length>0,audioBytes:bytes.length,heard,spoken,events:[...events],failure}));if(failure||!bytes.length)process.exitCode=1;}finally{clearInterval(frames);clearTimeout(settle);clearTimeout(timeout);live.close();}
