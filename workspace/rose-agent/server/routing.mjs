import {select,routeInstructions,numberedRouteInstructions} from '../decisions/model.mjs';
const LUNA=Object.freeze({model:'gpt-6-luna',effort:'medium',serviceTier:'priority'});
export const ROUTES=Object.freeze({quick:LUNA,deep:LUNA});
export const SOL_TRIAL=Object.freeze({model:'gpt-6.1-sol',effort:'low',serviceTier:'ultrafast'});
export async function chooseRoute(client,request,config={enabled:false},context=[],signal){
 if(config.taskAgent==='luna-medium-fast')return {route:'deep',...LUNA,source:'direct-luna-trial',routingMs:0};
 if(config.taskAgent==='sol-low-ultrafast')return {route:'deep',...SOL_TRIAL,source:'direct-sol-trial',routingMs:0};
 if(!config.enabled)return {route:'quick',...LUNA,source:'fixed-luna',routingMs:0};
 try{
  const numbered=config.executor==='numbered';
  const r=await select(client,{input:{request,context:context.slice(-4)},instructions:numbered?numberedRouteInstructions:routeInstructions,choices:[{value:'fast',description:numbered?'Numbered screenshot phone executor: operate Android apps for the requested action, including authorized shopping/cart changes and supplied messages.':'Generic Decisions phone executor: observed taps, explicit text, search, scroll, open apps.'},{value:'luna',description:numbered?'Luna for coding, Mac tasks, research, generating new prose, and explanations.':'Luna reasoning with full tools for planning, ambiguous requests, research, coding, Mac tasks, protected or final actions, and recovery.'}],decider:config.decider,signal});
  return {route:r.selected==='fast'?'fast':'deep',...LUNA,...(r.selected==='fast'?{effort:null,serviceTier:'decisions'}:{}),source:config.decider==='luna'?'luna-decider':'decisions-decider',routingMs:r.elapsedMs};
 }catch(error){if(signal?.aborted)throw error;return {route:'deep',...LUNA,source:'decider-error-fallback',routingMs:0};}
}
