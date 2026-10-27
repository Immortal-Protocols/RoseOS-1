import {Codex} from './codex.mjs';import {once} from 'node:events';
const c=new Codex(process.cwd(),'/tmp/pixel-routing-model-check.json',(process.env.HOME + "/.config/pixel-voice/openai-api-key"));
await c.init();const catalog=await c.rpc('model/list',{includeHidden:true});console.log(JSON.stringify(catalog.data.filter(m=>['gpt-6-luna','gpt-6-astra'].includes(m.model)).map(m=>({model:m.model,efforts:m.supportedReasoningEfforts}))));
for(const route of [{model:'gpt-6-luna',effort:'low'},{model:'gpt-6-astra',effort:'medium'}]){const start=Date.now();const done=once(c,'done');await c.submit('Validation only. Do not use tools or change any files. Reply with exactly MODEL_OK.',route);const [result]=await done;console.log(JSON.stringify({...route,status:result.status,ms:Date.now()-start,error:result.error}));}
c.child.kill();
