#!/usr/bin/env node
// QA runtime evidence analyzer (RT-2, RT-5, C4d). Offline: reads backend JSONL logs copied from a
// candidate run (evaluation-recordings/**/*.jsonl and decisions-routing.jsonl) and applies the
// Stop rules: after a task's task_stop_acknowledged, until the next explicit request.started,
// no phone action, tool start, worker message delivery or task result may appear.
// Allowed after acknowledgment: task.stop events, *_rejected, task_stop_blocked_action,
// request.finished/interrupted and the Stop acknowledgment itself.
// Usage: node stop-evidence.mjs <log files or directories...>  -> JSON report, exit 1 on violation.
import fs from 'node:fs';import path from 'node:path';
const files=[];const walk=p=>{const st=fs.statSync(p);if(st.isDirectory())for(const f of fs.readdirSync(p))walk(path.join(p,f));else if(/\.jsonl$/.test(p))files.push(p);};
for(const a of process.argv.slice(2))walk(a);
const events=[];
for(const f of files)for(const line of fs.readFileSync(f,'utf8').split('\n')){if(!line.trim())continue;let e;try{e=JSON.parse(line);}catch{continue;}
 const at=Date.parse(e.at||e.data?.at||'');if(Number.isNaN(at))continue;
 const kind=e.kind||'decision';const stage=e.data?.stage||e.stage||'';events.push({at,kind,stage,file:path.basename(f),data:e.data||e});}
events.sort((a,b)=>a.at-b.at);
const VIOLATION=e=>(e.kind==='phone.background'&&/^background_(action|open)$/.test(e.stage))||e.kind==='phone.created'||e.kind==='phone.completed'||e.kind==='tool.started'||e.kind==='tool.finished'||e.kind==='worker.message'||e.kind==='model.web_lookup';
const windows=[];let open=null;
for(const e of events){
 if(e.stage==='task_stop_acknowledged'){open={taskId:e.data.taskId,ackAt:e.at,violations:[],allowed:0};windows.push(open);continue;}
 if(e.kind==='request.started'){open=null;continue;}
 if(open){if(VIOLATION(e))open.violations.push({at:new Date(e.at).toISOString(),kind:e.kind,stage:e.stage});else open.allowed++;}
}
const report={files:files.length,events:events.length,stops:windows.map(w=>({taskId:w.taskId,acknowledgedAt:new Date(w.ackAt).toISOString(),violations:w.violations,allowedEventsAfterAck:w.allowed})),
 verdict:windows.length===0?'NO_STOP_IN_LOGS':windows.every(w=>w.violations.length===0)?'PASS':'FAIL',
 limits:'Backend logs only. Audio already playing (RT-1) and on-device actions need the audio and logcat evidence too.'};
console.log(JSON.stringify(report,null,1));process.exit(report.verdict==='FAIL'?1:0);
