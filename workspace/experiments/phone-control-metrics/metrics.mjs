import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {gestureSummary} from './gesture.mjs';
const ignored=new Set(['snapshotId','validForMs','capturedAt','observedAt','timestamp','gestureDiagnostics']);
const actions=new Set(['tap','tap_label','touch_label','touch_point','set_text','ime_enter','scroll','swipe','drag','open_app','home','back']);
function stable(value){
 if(Array.isArray(value))return value.map(stable);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().filter(k=>!ignored.has(k)).map(k=>[k,stable(value[k])]));
 return value;
}
export function observationSummary(result){
 const o=result?.observation||(Array.isArray(result?.nodes)?result:null);
 if(!o||o.status!=='ok'||o.truncated||!Array.isArray(o.nodes))return null;
 return {screenHash:crypto.createHash('sha256').update(JSON.stringify(stable(o))).digest('hex'),nodeCount:o.nodes.length,snapshotId:typeof o.snapshotId==='string'?o.snapshotId:null,packageName:typeof o.packageName==='string'?o.packageName:null,freshForMs:typeof o.snapshotId==='string'&&Number.isFinite(o.validForMs)?Math.min(30000,Math.max(0,o.validForMs)):0};
}
// Receives existing command results. Never changes commands, drives the phone,
// requests screenshots, or stores screen text, labels, field contents, or images. Snapshot IDs and
// parameter shapes permit joining existing action logs for offline review.
const rejectionFields=new Set(['parentId','class','viewId','text','visible','description','password','clickable','editable','scrollable','focused','imeEnter','enabled','checked','selected','clickTarget']);
export function rejectionSummary(value){
 if(!value||typeof value!=='object'||!['snapshot_missing','snapshot_replaced','snapshot_expired','target_guard_rejected','full_snapshot_changed'].includes(value.reason))return null;
 const out={reason:value.reason};
 for(const key of ['snapshotAgeMs','targetNodeId','oldNodeCount','newNodeCount','addedNodeCount','removedNodeCount','changedNodeCount','boundsChangedNodeCount'])if(Number.isInteger(value[key])&&value[key]>=-1&&value[key]<=3600000)out[key]=value[key];
 for(const key of ['windowChanged','truncatedBefore','truncatedAfter','targetPresentBefore','targetPresentAfter','targetBoundsChanged','targetUnchanged'])if(typeof value[key]==='boolean')out[key]=value[key];
 if(Array.isArray(value.targetChangedFields))out.targetChangedFields=[...new Set(value.targetChangedFields.filter(x=>rejectionFields.has(x)))];
 if(value.changedFieldCounts&&typeof value.changedFieldCounts==='object'){out.changedFieldCounts={};for(const key of rejectionFields){const n=value.changedFieldCounts[key];if(Number.isInteger(n)&&n>=0&&n<=300)out.changedFieldCounts[key]=n;}}
 return out;
}
export class PhoneMetrics {
 constructor({write,now=Date.now,limit=1000}){this.write=write;this.now=now;this.limit=limit;this.rows=new Map();this.previousId=null;this.gestureRevisions=new Map();}
 emit(row){try{this.write(row);}catch{/* Telemetry must never fail a command. */}}
 created(c,context={}){
  if(this.rows.has(c.id))return;
  const row={id:c.id,action:c.action,createdAt:this.now(),previousId:this.previousId,taskStartedAt:context.taskStartedAt??null,model:context.model??null,effort:context.effort??null,inputSnapshotId:c.snapshotId??null,parameterShape:Object.keys(c).filter(k=>c[k]!==undefined&&!['id','created','expires','status'].includes(k)).sort(),textLength:typeof c.text==='string'?c.text.length:null};
  this.rows.set(c.id,row);this.previousId=c.id;
  while(this.rows.size>this.limit)this.rows.delete(this.rows.keys().next().value);
  this.emit({kind:'created',...row});
 }
 offered(c,channel='poll'){
  const row=this.rows.get(c.id);if(!row||row.offeredAt!==undefined)return;
  row.offeredAt=this.now();row.deliveryChannel=channel;this.emit({kind:'offered',id:c.id,at:row.offeredAt,channel,queueWaitMs:row.offeredAt-row.createdAt});
 }
 executed(id,delivery){
  const row=this.rows.get(id);if(!row||row.execution||!delivery||!['push','poll'].includes(delivery.channel))return;
  const valid=v=>Number.isFinite(v)&&v>=0&&v<600000;
  if(!valid(delivery.executionMs)||!valid(delivery.receivedToStartMs))return;
  row.execution={channel:delivery.channel,executionMs:delivery.executionMs,receivedToStartMs:delivery.receivedToStartMs};
  this.emit({kind:'execution',id,at:this.now(),...row.execution});
 }
 completed(c){
  const row=this.rows.get(c.id);if(!row||row.completedAt!==undefined||c.status!=='completed')return;
  const gestures=c.result?.gestureDiagnostics||c.result?.observation?.gestureDiagnostics;
  if(Array.isArray(gestures))for(const value of gestures.slice(-24)){
   const diagnostic=gestureSummary(value);if(!diagnostic)continue;
   if((this.gestureRevisions.get(diagnostic.gestureId)??-1)>=diagnostic.revision)continue;
   this.gestureRevisions.set(diagnostic.gestureId,diagnostic.revision);
   while(this.gestureRevisions.size>this.limit)this.gestureRevisions.delete(this.gestureRevisions.keys().next().value);
   this.emit({...diagnostic,collectedBy:c.id,at:this.now()});
  }
  row.completedAt=this.now();row.screen=observationSummary(c.result);
  const previous=this.rows.get(row.previousId);
  const sequential=previous?.completedAt!==undefined&&previous.completedAt<=row.createdAt;
  const sameTask=previous&&previous.taskStartedAt===row.taskStartedAt;
  const comparable=!!(sequential&&sameTask&&previous.screen&&row.screen);
  const afterAction=!!(comparable&&row.createdAt-previous.completedAt<previous.screen.freshForMs&&row.action==='inspect_ui'&&actions.has(previous.action));
  this.emit({kind:'completed',id:c.id,action:row.action,at:row.completedAt,taskStartedAt:row.taskStartedAt,model:row.model,effort:row.effort,resultStatus:c.result?.status??null,
   screenRejection:c.result?.status==='stale_screen'?rejectionSummary(c.result.screenRejection):null,
   totalMs:row.completedAt-row.createdAt,queueWaitMs:row.offeredAt===undefined?null:row.offeredAt-row.createdAt,
   dispatchToResultMs:row.offeredAt===undefined?null:row.completedAt-row.offeredAt,
   previousId:row.previousId,previousAction:previous?.action??null,
   betweenCommandsMs:sequential&&sameTask?row.createdAt-previous.completedAt:null,
   screenHash:row.screen?.screenHash??null,nodeCount:row.screen?.nodeCount??null,
   inputSnapshotId:row.inputSnapshotId,outputSnapshotId:row.screen?.snapshotId??null,packageName:row.screen?.packageName??null,
   deliveryChannel:row.execution?.channel??row.deliveryChannel??null,phoneExecutionMs:row.execution?.executionMs??null,phoneQueueMs:row.execution?.receivedToStartMs??null,
   usedPreviousObservation:!!(sequential&&sameTask&&row.inputSnapshotId&&row.inputSnapshotId===previous?.screen?.snapshotId),
   inspectionAfterActionWithObservation:afterAction,
   sameScreenAsPrevious:comparable?row.screen.screenHash===previous.screen.screenHash:null,
   possibleRedundantInspection:afterAction&&row.screen.screenHash===previous.screen.screenHash});
 }
 collected(c){
  const row=this.rows.get(c?.id);if(!row||row.completedAt===undefined||row.collectedAt!==undefined)return;
  row.collectedAt=this.now();this.emit({kind:'collected',id:c.id,at:row.collectedAt,resultPickupMs:row.collectedAt-row.completedAt,createToPickupMs:row.collectedAt-row.createdAt});
 }
}
export function boundedWriter(file,{maxBytes=5*1024*1024}={}){
 fs.mkdirSync(path.dirname(file),{recursive:true,mode:0o700});
 let bytes=fs.existsSync(file)?fs.statSync(file).size:0,queued=0,chain=Promise.resolve();
 return row=>{
  if(queued>=2000)return; // Drop telemetry rather than block phone work.
  const text=JSON.stringify(row)+'\n';queued++;
  chain=chain.then(async()=>{
   if(bytes+Buffer.byteLength(text)>maxBytes){
    for(let i=2;i>=0;i--){const from=i?file+'.'+i:file;try{await fs.promises.rename(from,file+'.'+(i+1));}catch(e){if(e.code!=='ENOENT')throw e;}}
    bytes=0;
   }
   await fs.promises.appendFile(file,text,{mode:0o600});bytes+=Buffer.byteLength(text);
  }).catch(()=>{}).finally(()=>queued--);
 };
}
