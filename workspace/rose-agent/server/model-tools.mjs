import {modelLabel,modelRoute} from './model-switch.mjs';
import {BASETEN_MODEL} from './baseten/gateway.mjs';

export const modelToolPolicy='Use get_task_model to inspect the actual selected and running task models. For a user request to change the task model, call set_task_model directly with structured arguments; never claim a switch from intent alone, edit files, use shell commands, or operate the phone model label. Interpret natural language yourself; if the target model is unspecified, ask which model. Omit effort and speed unless the user requests them: the controller supplies model defaults. A selection applies to subsequent backend turns; the acknowledgement may finish on the previous model. Preserve the returned distinction between selected and currently running. Bare Sol means GPT-6.1 Sol; explicit GPT-6 Sol means GPT-6 Sol. These tools do not change the speaking voice.';
export const modelToolDefinitions=[
 {type:'function',name:'get_task_model',description:'Read the selected task model, any next-task override, and the model actually running the current backend turn. Also lists supported models and their defaults.',inputSchema:{type:'object',properties:{},additionalProperties:false}},
 {type:'function',name:'set_task_model',description:'Select the task model requested by the user. This applies to subsequent backend turns, not the already-running acknowledgement. Do not choose an arbitrary different model if the user did not identify one. Default scope is session. Defaults: Luna medium/fast; GPT-6.1 Sol low/ultrafast; GPT-6 Sol low/fast; Astra medium/fast; DeepSeek Flash Fast medium with its built-in Fast endpoint (omit speed). Returns verified controller state. Does not change the voice model.',inputSchema:{type:'object',properties:{model:{type:'string',enum:['gpt-6-luna','gpt-6.1-sol','gpt-6-sol','gpt-6-astra',BASETEN_MODEL]},effort:{type:'string',description:'Omit unless the user requested a reasoning level.',enum:['low','medium','high','xhigh','max','ultra']},speed:{type:'string',description:'Omit unless the user requested a speed; always omit for DeepSeek Fast.',enum:['standard','fast','ultrafast']},scope:{type:'string',enum:['session','default','next_task']}},required:['model'],additionalProperties:false}}
];

export function createModelTools({selection,config,running=()=>null,changed=()=>{},log=()=>{},available=()=>true}){
 const calls=new Map();
 function state(){
  const selected=selection.preferred(config())||modelRoute('gpt-6-luna');
  return {status:'ok',selected:{...selected,label:modelLabel(selected)},nextTask:selection.next?{...selection.next,label:modelLabel(selection.next)}:null,running:running(),supported:modelToolDefinitions[1].inputSchema.properties.model.enum.map(model=>modelRoute(model))};
 }
 async function execute(request){
  const args=request.arguments;
  if(!args||typeof args!=='object'||Array.isArray(args))throw Error('Arguments must be an object');
  if(request.tool==='get_task_model'){
   if(Object.keys(args).length)throw Error('get_task_model takes no arguments');
   return state();
  }
  if(request.tool!=='set_task_model')throw Error('Unknown model tool');
  for(const k of Object.keys(args))if(!['model','effort','speed','scope'].includes(k))throw Error('Unknown model setting: '+k);
  const scope=args.scope??'session';
  if(!['session','default','next_task'].includes(scope))throw Error('Unsupported model scope');
  if(args.speed!==undefined&&!['standard','fast','ultrafast'].includes(args.speed))throw Error('Unsupported model speed');
  const route=modelRoute(args.model,args.effort,args.speed===undefined?undefined:({standard:'default',fast:'priority',ultrafast:'ultrafast'}[args.speed]));
  if(!available(route.model))throw Error('That model connection is not configured. Select Luna to continue.');
  selection.set({route,scope:scope==='next_task'?'next':scope,request:''},false);
  const result={...state(),status:'applied',scope,appliesTo:'subsequent_backend_turns'};
  log({stage:'model_switch',...route,scope,source:'registered_tool'});changed(result);
  return result;
 }
 function handleToolCall(request){
  const key=request.callId?request.threadId+':'+request.callId:null;
  if(key&&calls.has(key))return calls.get(key);
  const work=Promise.resolve().then(()=>execute(request)).then(result=>({success:true,contentItems:[{type:'inputText',text:JSON.stringify(result)}]})).catch(error=>({success:false,contentItems:[{type:'inputText',text:JSON.stringify({status:'not_applied',reason:error.message})}]}));
  if(key)calls.set(key,work);
  return work;
 }
 return {definitions:modelToolDefinitions,handleToolCall,state};
}
