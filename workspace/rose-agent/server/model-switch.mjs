import fs from 'node:fs';
import {BASETEN_MODEL} from './baseten/gateway.mjs';
import {CEREBRAS_MODEL} from './cerebras/gateway.mjs';

const definitions={
 'gpt-6-luna':{effort:'medium',serviceTier:'priority'},
 'gpt-6.1-sol':{effort:'low',serviceTier:'ultrafast'},
 'gpt-6-sol':{effort:'low',serviceTier:'priority'},
 'gpt-6-astra':{effort:'medium',serviceTier:'priority'},
 [BASETEN_MODEL]:{effort:'medium',serviceTier:'default'},
 [CEREBRAS_MODEL]:{effort:'low',serviceTier:'default'},
};
export function modelLabel(route){
 if(!route)return 'Model unavailable';
 const names={'gpt-6-luna':'GPT-6 Luna','gpt-6.1-sol':'GPT-6.1 Sol','gpt-6-sol':'GPT-6 Sol','gpt-6-astra':'GPT-6 Astra'};
 if(route.route==='fast')return 'Decisions';
 const effort=String(route.effort||'medium');
 if(route.model===BASETEN_MODEL)return `DeepSeek Flash · ${effort[0].toUpperCase()+effort.slice(1)} · Fast`;
 if(route.model===CEREBRAS_MODEL)return `Qwen 3.8 · ${effort[0].toUpperCase()+effort.slice(1)} · Cerebras`;
 return `${names[route.model]||route.model} · ${effort[0].toUpperCase()+effort.slice(1)} · ${{priority:'Fast',ultrafast:'Ultrafast',default:'Standard'}[route.serviceTier]||route.serviceTier}`;
}
export function validateModelRoute(route){
 if(!definitions[route?.model])throw Error('Unsupported task model.');
 if(route.model===CEREBRAS_MODEL){if(!['none','low','medium','high'].includes(route.effort)||route.serviceTier!=='default')throw Error('Qwen on Cerebras supports none, low, medium, or high reasoning and the standard endpoint.');return {model:route.model,effort:route.effort,serviceTier:route.serviceTier};}
 if(route.model===BASETEN_MODEL){if(!['none','low','medium','high','max'].includes(route.effort)||route.serviceTier!=='default')throw Error('DeepSeek Fast supports none, low, medium, high, or max reasoning; its Fast service is built into this model.');return {model:route.model,effort:route.effort,serviceTier:route.serviceTier};}
 if(!['low','medium','high','xhigh','max','ultra'].includes(route.effort)&&!(route.model==='gpt-6-luna'&&route.effort==='none')||route.model==='gpt-6-luna'&&route.effort==='ultra')throw Error('That reasoning level is not supported by this model.');
 if(!['default','priority','ultrafast'].includes(route.serviceTier)||route.serviceTier==='ultrafast'&&!['gpt-6.1-sol','gpt-6-astra'].includes(route.model))throw Error('Ultrafast is available here only for GPT-6.1 Sol and GPT-6 Astra.');
 return {model:route.model,effort:route.effort,serviceTier:route.serviceTier};
}
export function modelRoute(model,effort,serviceTier){
 const defaults=definitions[model];
 if(!defaults)throw Error('Supported task models are GPT-6 Luna, GPT-6.1 Sol, GPT-6 Sol, and GPT-6 Astra.');
 return validateModelRoute({model,effort:effort??defaults.effort,serviceTier:serviceTier??defaults.serviceTier});
}
export class ModelSelection {
 constructor(file){this.file=file;this.session=null;this.next=null;this.saved=null;try{this.saved=validateModelRoute(JSON.parse(fs.readFileSync(file,'utf8')));}catch{}}
 fallback(config){return this.saved|| (config.taskAgent==='sol-low-ultrafast'?{model:'gpt-6.1-sol',...definitions['gpt-6.1-sol']}:null);}
 preferred(config){return this.session||this.fallback(config);}
 set(command,active){
  const route=validateModelRoute(command.route);
  if(command.scope==='default'){fs.writeFileSync(this.file+'.tmp',JSON.stringify(route),{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);this.saved=route;this.session=null;this.next=null;}
  else if(command.scope==='session'){this.session=route;this.next=null;}
  else if(command.scope==='next'||!active&&!command.request)this.next=route;
  return route;
 }
 takeNext(){const route=this.next;this.next=null;return route;}
}
