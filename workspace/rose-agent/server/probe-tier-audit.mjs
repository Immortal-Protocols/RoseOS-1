import fs from 'node:fs';import OpenAI from 'openai';import {Codex} from './codex-audit.mjs';import {once} from 'node:events';
const file=(process.env.HOME + "/.config/pixel-voice/openai-api-key");
const api=new OpenAI({apiKey:fs.readFileSync(file,'utf8').trim(),maxRetries:0});
try{const r=await api.responses.create({model:'gpt-6-astra',service_tier:'ultrafast',store:false,input:'Reply OK.',max_output_tokens:64});console.log(JSON.stringify({apiTier:r.service_tier,status:r.status}));}catch(e){console.log(JSON.stringify({status:e.status,error:e.message}));process.exit(1);}
const c=new Codex(process.cwd(),'/tmp/pixel-ultrafast-check.json',file);await c.init();
try{const done=once(c,'done');const r=await c.rpc('turn/start',{threadId:c.thread,input:[{type:'text',text:'Reply exactly ULTRAFAST_OK. Do not use tools.'}],model:'gpt-6-astra',effort:'medium',serviceTierForTurn:'ultrafast'});console.log(JSON.stringify({thread:c.thread,turn:r.turn.id}));const [x]=await done;console.log(JSON.stringify({status:x.status,error:x.error}));await new Promise(r=>setTimeout(r,1500));}finally{c.child.kill();}
