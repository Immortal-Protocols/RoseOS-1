// Diagnostic metadata only. Never persist labels, field text, or screen contents.
const uuid=/^[a-f0-9-]{36}$/i,hash=/^[a-f0-9]{64}$/i;
const number=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isFinite(v)&&v>=min&&v<=max;
function screen(value){
 if(!value||typeof value!=='object')return;
 const out={};
 if(typeof value.packageName==='string'&&/^[\w.]{1,200}$/.test(value.packageName))out.packageName=value.packageName;
 if(typeof value.screenHash==='string'&&hash.test(value.screenHash))out.screenHash=value.screenHash;
 if(typeof value.focusedViewId==='string'&&/^[\w.$:/-]{0,240}$/.test(value.focusedViewId))out.focusedViewId=value.focusedViewId;
 for(const key of ['windowId','nodeCount','afterMs','lastWindowChangeAgoMs'])if(number(value[key],key==='windowId'?-1:0))out[key]=value[key];
 return out;
}
export function gestureSummary(value){
 if(!value||!uuid.test(value.id)||!Number.isInteger(value.revision)||value.revision<0)return null;
 if(!['dispatching','queued','completed','cancelled','rejected','dispatch_error'].includes(value.phase))return null;
 if(!['touch_label','touch_point','swipe','drag','screen_gesture'].includes(value.action))return null;
 const out={kind:'gesture_diagnostic',gestureId:value.id,revision:value.revision,action:value.action,phase:value.phase};
 if(typeof value.commandId==='string'&&uuid.test(value.commandId))out.commandId=value.commandId;
 for(const key of ['startedAt','startedElapsedMs','elapsedMs','durationMs','snapshotAgeMs'])if(number(value[key],key==='snapshotAgeMs'?-1:0))out[key]=value[key];
 for(const key of ['screenChangeRejectionEnabled','screenChangedBeforeDispatch'])if(typeof value[key]==='boolean')out[key]=value[key];
 for(const key of ['before','callbackWindow']){const s=screen(value[key]);if(s)out[key]=s;}
 if(Array.isArray(value.points)&&[2,4].includes(value.points.length)&&value.points.every(v=>number(v,0,20000)))out.points=value.points;
 if(value.target&&typeof value.target==='object'){
  const t={};
  for(const key of ['viewId','className'])if(typeof value.target[key]==='string'&&/^[\w.$:/-]{0,240}$/.test(value.target[key]))t[key]=value.target[key];
  for(const key of ['focused','editable','enabled'])if(typeof value.target[key]==='boolean')t[key]=value.target[key];
  if(Array.isArray(value.target.bounds)&&value.target.bounds.length===4&&value.target.bounds.every(v=>number(v,0,20000)))t.bounds=value.target.bounds;
  out.target=t;
 }
 if(Array.isArray(value.observations))out.observations=value.observations.slice(0,8).map(screen).filter(Boolean);
 return out;
}
