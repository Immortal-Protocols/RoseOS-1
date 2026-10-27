import {Codex} from './codex.mjs';
const c=new Codex(process.cwd(),'/tmp/pixel-tier-inspect.json',(process.env.HOME + "/.config/pixel-voice/openai-api-key"));
await c.init();try{const x=await c.rpc('model/list',{includeHidden:true});console.log(JSON.stringify(x.data.filter(m=>m.model==='gpt-6-astra')));}finally{c.child.kill();}
