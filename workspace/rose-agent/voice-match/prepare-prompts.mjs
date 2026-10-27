import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import OpenAI from 'openai';import {prompts} from './http.mjs';
const root=path.join(os.homedir(),'.config/pixel-voice'),dir=path.join(root,'voice-match/prompts');fs.mkdirSync(dir,{recursive:true,mode:0o700});
const client=new OpenAI({apiKey:fs.readFileSync(root+'/openai-api-key','utf8').trim()});
for(const [name,input] of Object.entries(prompts)){const file=path.join(dir,name+'.mp3');if(fs.existsSync(file))continue;const r=await client.audio.speech.create({model:'gpt-4o-mini-tts',voice:'marin',input,response_format:'mp3'});fs.writeFileSync(file,Buffer.from(await r.arrayBuffer()),{mode:0o600});console.log(name+' ready');}
