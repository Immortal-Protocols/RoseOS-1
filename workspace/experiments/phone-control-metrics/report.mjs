import {sequenceCandidates} from './analysis.mjs';
import fs from 'node:fs';import path from 'node:path';import os from 'node:os';
const file=path.join(os.homedir(),'.config/pixel-voice/phone-command-metrics.jsonl');
const since=process.argv[2]?Date.parse(process.argv[2]):0;if(!Number.isFinite(since))throw Error('Use an ISO date for the optional since argument');
let events=[];for(const f of [file+'.3',file+'.2',file+'.1',file])if(fs.existsSync(f))for(const line of fs.readFileSync(f,'utf8').trim().split('\n')){try{events.push(JSON.parse(line));}catch{}}
const completed=events.filter(e=>e.kind==='completed'&&e.at>=since);const pickups=new Map(events.filter(e=>e.kind==='collected').map(e=>[e.id,e]));
const summary=values=>{const v=values.filter(Number.isFinite).sort((a,b)=>a-b);return {samples:v.length,medianMs:v.length?(v.length%2?v[Math.floor(v.length/2)]:(v[v.length/2-1]+v[v.length/2])/2):null,p95Ms:v.length?v[Math.min(v.length-1,Math.ceil(v.length*.95)-1)]:null,totalMs:v.reduce((a,b)=>a+b,0)};};
const rejections=completed.filter(e=>e.resultStatus==='stale_screen');
const counted=values=>Object.fromEntries([...new Set(values)].map(v=>[v,values.filter(x=>x===v).length]));
const suspects=completed.filter(e=>e.possibleRedundantInspection);const byAction={};for(const e of completed)(byAction[e.action]??=[]).push(e.totalMs);
console.log(JSON.stringify({completedCommands:completed.length,screenRejections:{total:rejections.length,withDiagnostics:rejections.filter(e=>e.screenRejection).length,byReason:counted(rejections.map(e=>e.screenRejection?.reason||'older_uninstrumented')),byAction:counted(rejections.map(e=>e.action)),targetUnchanged:rejections.filter(e=>e.screenRejection?.targetUnchanged===true).length,changedFields:counted(rejections.flatMap(e=>Object.keys(e.screenRejection?.changedFieldCounts||{})))},byAction:Object.fromEntries(Object.entries(byAction).map(([k,v])=>[k,summary(v)])),
 byDelivery:Object.fromEntries(['push','poll'].map(channel=>[channel,{commands:completed.filter(e=>e.deliveryChannel===channel).length,total:summary(completed.filter(e=>e.deliveryChannel===channel).map(e=>e.totalMs))}])),
 phoneExecution:summary(completed.map(e=>e.phoneExecutionMs)),phoneQueue:summary(completed.map(e=>e.phoneQueueMs)),
 sequenceCandidates:sequenceCandidates(completed),
 queueWait:summary(completed.map(e=>e.queueWaitMs)),dispatchToResult:summary(completed.map(e=>e.dispatchToResultMs)),
 resultPickup:summary(completed.map(e=>pickups.get(e.id)?.resultPickupMs)),
 inspectionsAfterActionWithObservation:completed.filter(e=>e.inspectionAfterActionWithObservation).length,
 possibleRedundantInspections:suspects.length,possibleRedundantInspectionCommandTime:summary(suspects.map(e=>e.totalMs)),
 notes:['Queue wait ends when a command is first offered by push or polling; it is not pure network latency.',
 'Dispatch-to-result includes network delivery, phone execution, existing waits and result return.',
 'Between-command gaps include model/tool orchestration and may include user pauses; they are not measured reasoning time.',
 'Identical complete screen observations identify candidate redundant inspections, not proof that the later read was unnecessary.',
 'Sequence candidates are observed successful runs to review, not proof that a local routine would work. Command time includes execution and networking; do not treat the whole time as removable.',
 'Do not add candidate inspection time to polling savings: the timings overlap.']},null,2));
