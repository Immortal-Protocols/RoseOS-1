// Creates one reusable candidate. This does not replace the active voice.
import fs from 'node:fs';import path from 'node:path';
import OpenAI from '../server/node_modules/openai/index.mjs';
const dir=process.env.PIXEL_VOICE_CONFIG_DIR||path.join(process.env.HOME,'.config/pixel-voice');
const file=path.join(dir,'voice-candidates/chicago-baritone.json');
if(fs.existsSync(file)){console.log(JSON.stringify({status:'saved_candidate',file}));process.exit(0);}
const prompt='An original adult masculine voice with a somewhat deep baritone pitch, warm and resonant timbre, clear American English and a subtle Chicago regional accent. Use a measured, assured, conversational cadence with thoughtful pauses and restrained emphasis. Sound approachable and composed, without sounding theatrical.';
const client=new OpenAI({apiKey:fs.readFileSync(path.join(dir,'openai-api-key'),'utf8').trim(),maxRetries:0});
const voice=await client.post('/audio/voices',{body:{type:'prompt',name:'Chicago Baritone',prompt,model:'auto'}});
if(!/^voice_[A-Za-z0-9_-]+$/.test(voice.id))throw Error('Voice creation did not return a valid ID');
fs.mkdirSync(path.dirname(file),{recursive:true,mode:0o700});
fs.writeFileSync(file,JSON.stringify({id:voice.id,name:'Chicago Baritone',prompt,languageStyle:'Speak with a measured, conversational cadence and subtle Chicago pronunciation.',createdAt:new Date().toISOString(),auditioned:false},null,2),{mode:0o600,flag:'wx'});
console.log(JSON.stringify({status:'created_candidate',file,activeVoiceChanged:false}));
