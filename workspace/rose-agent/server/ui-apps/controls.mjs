// Shared, on-demand UI primitives. No app-specific coordinates or old app integrations.
export function createAppControls({phone,packageName,timeoutMs=18000}){
 let deadline=0,mutated=false;
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 function check(){if(Date.now()>deadline)throw Error('App operation timed out');}
 async function screen(){
  check();let s=await phone({action:'inspect_ui'});
  if(s.status==='no_window'){await sleep(120);s=await phone({action:'inspect_ui'});}
  if(s.status!=='ok'||s.packageName!==packageName)throw Error('Target app is no longer in the active control workspace');
  if(s.truncated)throw Error('App controls are incomplete');
  if(s.nodes.some(n=>n.password))throw Error('Sign in directly on the phone to continue');return s;
 }
 function one(s,predicate){
  const found=new Map();for(const n of s.nodes)if(n.enabled&&n.clickTarget!==undefined&&predicate(n))found.set(n.clickTarget,n);
  if(found.size>1)throw Error('Requested app control is ambiguous');return found.values().next().value;
 }
 const label=(s,value)=>one(s,n=>n.description===value||n.text===value);
 const id=(s,value)=>one(s,n=>n.viewId===packageName+':id/'+value);
 async function tap(s,n,{physical=true,changesContent=false}={}){
  if(!n)throw Error('Requested app control is unavailable');check();const text=n.description||n.text;if(!text)throw Error('App control has no readable label');
  const action=physical?'touch_label':'tap_label';
  const wasMutated=mutated;
  if(changesContent)mutated=true;
  let r=await phone({action,snapshotId:s.snapshotId,nodeId:n.clickTarget,label:text});
  // These responses explicitly mean no action was dispatched. Resolve the
  // same semantic control from a new snapshot; never retry an unknown result.
  if(['label_target_mismatch','stale_screen'].includes(r.status)){
   mutated=wasMutated;
   const fresh=await screen();
   const target=one(fresh,x=>(!n.viewId||x.viewId===n.viewId)&&(x.description||x.text)===text);
   if(!target)throw Error('Requested app control changed before the action');
   if(changesContent)mutated=true;
   r=await phone({action,snapshotId:fresh.snapshotId,nodeId:target.clickTarget,label:text});
  }
  if(!['action_accepted',...(physical?['gesture_dispatched']:[])].includes(r.status))throw Error('App rejected the requested action: '+r.status+' ('+text+')');
  if(changesContent)mutated=true;return r;
 }
 async function wait(predicate,ms=3500){const end=Date.now()+ms;do{const s=await screen();if(predicate(s))return s;await sleep(100);}while(Date.now()<end);throw Error('Expected app state did not appear');}
 async function run(fn){deadline=Date.now()+timeoutMs;mutated=false;try{return await fn();}catch(e){return {status:mutated?'uncertain':'needs_assistance',reason:e.message,actionMayHaveChangedContent:mutated};}}
 return {screen,one,label,id,tap,wait,run,phone,sleep,markMutation:()=>{mutated=true;}};
}
