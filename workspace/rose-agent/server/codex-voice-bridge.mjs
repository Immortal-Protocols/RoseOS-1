// Private stdio IPC for rose.app. Codex remains the only app-server adapter.
import {createInterface} from 'node:readline';
import {Codex} from './codex.mjs';
import {lifeResearchPolicy,publicPageReceipt} from './life-research.mjs';

const sessions = new Map(), calls = new Map();
let nextCall = 0;
const send = value => process.stdout.write(JSON.stringify(value)+'\n');
const fail = () => ({success:false,contentItems:[{type:'inputText',text:'Tool unavailable or cancelled.'}]});
// The model was answered fail() for this call (interrupt or timeout): its own result will be dropped, so rose.app learns
// which calls those were (a phone action that still finished is then reported on a later turn).
const cancelled = (session,callId) => send({session,event:'tool_cancelled',callId});
async function handle(m) {
 if(m.op==='tool_result') {
  const call=calls.get(m.callId);
  if(call){calls.delete(m.callId);clearTimeout(call.timer);call.resolve(m.result);}
  return;
 }
 if(m.op==='open') {
  if(sessions.has(m.session))throw Error('Session already exists');
  const research=m.purpose==='public_research';
  if(research && (!Array.isArray(m.tools)||m.tools.length))throw Error('Research cannot have phone tools');
  const publish=event=>{if(sessions.get(m.session)===c)send({session:m.session,threadId:c.thread,...event});};
  const c=new Codex(m.cwd, '', null, {externalPhone:true,publicResearch:research,model:m.model,
   developerInstructions:research?lifeResearchPolicy:m.instructions,dynamicTools:m.tools.map(tool=>({type:'function',...tool})),
   handleToolCall:params=>new Promise(resolve=>{
    const callId=String(++nextCall);
    const timer=setTimeout(()=>{calls.delete(callId);resolve(fail());cancelled(m.session,callId);},60000);
    calls.set(callId,{resolve,timer,session:m.session});
    publish({event:'tool',callId,params});
   })});
  sessions.set(m.session,c);
  c.on('turnCreated',p=>publish({event:'started',turnId:p.turnId}));
  c.on('textDelta',p=>publish({event:'delta',...p}));
  c.on('message',(text,phase,turnId)=>publish({event:'message',text,phase,turnId}));
  c.on('done',turn=>publish({event:'done',turn:{id:turn.id,status:turn.status,
   error:turn.error?{message:'Codex turn failed'}:null}}));
  c.on('failure',()=>publish({event:'failure',message:'Codex process stopped'}));
  if(research)c.on('toolEvidence',item=>{
   const source=publicPageReceipt(item);
   if(source)publish({event:'research_source',source});
  });
  if(research){
   let webCalls=0,refused=false;
   c.on('toolActivity',(method,params)=>{
    if(refused||method!=='item/started')return;
    const type=params.item?.type;
    if(type==='webSearch'&&++webCalls<=6)return;
    if(['agentMessage','reasoning','userMessage','plan'].includes(type))return;
    refused=true;publish({event:'failure',message:'Research tool boundary or budget exceeded'});
    c.interruptForModelSwitch().catch(()=>{});
   });
  }
  try {await c.init();return {threadId:c.thread,model:c.selectedModel,serviceTier:c.actualServiceTier,authMode:c.authMode};}
  catch(error){
   if(research && !await c.closeConfirmed())
    throw Object.assign(Error('Research process stop unconfirmed'),{code:'research_stop_unconfirmed'});
   if(!research)c.close();
   sessions.delete(m.session);throw error;
  }
 }
 const c=sessions.get(m.session);
 if(!c)throw Error('Unknown session');
 if(m.op==='submit')return c.submit(m.text,{model:c.selectedModel,effort:'medium',serviceTier:c.serviceTier});
 if(m.op==='interrupt') {
  for(const [id,call] of calls)if(call.session===m.session){clearTimeout(call.timer);call.resolve(fail());calls.delete(id);cancelled(m.session,id);}
  return c.interruptForModelSwitch();
 }
 if(m.op==='close'){
  if(c.runtime.publicResearch){
   if(!await c.closeConfirmed())throw Error('Research process stop unconfirmed');
   sessions.delete(m.session);return {stopped:true};
  }
  c.close();sessions.delete(m.session);return {};
 }
 throw Error('Unknown operation');
}
createInterface({input:process.stdin}).on('line',line=>{
 let m;try{m=JSON.parse(line);}catch{return;}
 // Do not serialize input: tool responses and interrupts must pass a pending submit.
 Promise.resolve().then(()=>handle(m)).then(result=>{
  if(m.id!==undefined)send({id:m.id,result:result??{}});
 }).catch(error=>{if(m.id!==undefined)send({id:m.id,error:['chatgpt_login_required','model_unavailable','fast_unavailable','model_mode_mismatch','research_isolation_unavailable','research_stop_unconfirmed'].includes(error.code)?error.code:'codex_request_failed'});});
});
process.stdin.on('end',async()=>{
 const stopped=await Promise.all([...sessions.values()].map(c=>c.runtime.publicResearch?c.closeConfirmed():(c.close(),true)));
 process.exit(stopped.every(Boolean)?0:1);
});
