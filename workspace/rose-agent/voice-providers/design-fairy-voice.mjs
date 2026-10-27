// Create an original candidate without replacing the selected voice.
import fs from 'node:fs';import path from 'node:path';
import OpenAI from '../server/node_modules/openai/index.mjs';
const dir=process.env.PIXEL_VOICE_CONFIG_DIR||path.join(process.env.HOME,'.config/pixel-voice');
const file=path.join(dir,'voice-candidates/fairy-spark.json');
if(fs.existsSync(file)){console.log(JSON.stringify({status:'saved_candidate',file}));process.exit(0);}
const prompt='An original adult feminine fantasy fairy companion voice: bright, light, sparkling, and very peppy, with a clear higher register, nimble playful intonation and a warm smile in the sound. Energetic and curious, crisp intelligible American English, quick buoyant phrases with natural pauses. Gentle bell-like clarity without shrillness, squeaking, baby talk, or shouting.';
const client=new OpenAI({apiKey:fs.readFileSync(path.join(dir,'openai-api-key'),'utf8').trim(),maxRetries:0});
const voice=await client.post('/audio/voices',{body:{type:'prompt',name:'Fairy Spark',prompt,model:'auto'}});
if(!/^voice_[A-Za-z0-9_-]+$/.test(voice.id))throw Error('No valid voice ID');
fs.mkdirSync(path.dirname(file),{recursive:true,mode:0o700});
fs.writeFileSync(file,JSON.stringify({id:voice.id,name:'Fairy Spark',prompt,languageStyle:'Speak with bright, playful enthusiasm and a nimble, friendly cadence. Keep words clear and volume comfortable.',createdAt:new Date().toISOString(),auditioned:false},null,2),{mode:0o600,flag:'wx'});
console.log(JSON.stringify({status:'created_candidate',file,activeVoiceChanged:false}));
