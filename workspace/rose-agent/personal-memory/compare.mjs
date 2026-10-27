import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {INSTRUCTIONS,openAIExtractor,validateCandidates,atomicJSON} from './memory.mjs';

// Offline comparison: never writes to the live store or installs proposals.
export function makePlan(state,events) {
 const index=new Map(events.map((e,i)=>[e.id,i]));
 const plan=state.batches.filter(b=>b.status==='completed').map(b=>{
  const first=index.get(b.eventIds[0]);
  if(first===undefined||b.eventIds.some(id=>!index.has(id)))throw Error('Missing original batch evidence');
  return {id:b.id,kind:'matched',events:[...events.slice(Math.max(0,first-2),first).map(e=>({...e,isNew:false})),...b.eventIds.map(id=>({...events[index.get(id)],text:events[index.get(id)].text.slice(0,6000),isNew:true}))],existing:state.candidates.filter(c=>c.createdAt<b.at),luna:state.candidates.filter(c=>c.batchId===b.id)};
 });
 for(let offset=state.processedCount;offset<events.length;){
  let take=0,size=0;
  while(offset+take<events.length&&take<40){const length=Math.min(events[offset+take].text.length,6000);if(take&&size+length>24000)break;size+=length;take++;}
  const fresh=events.slice(offset,offset+take);
  if(fresh.some(e=>e.role==='user'))plan.push({id:'backlog-'+offset,kind:'backlog',events:[...events.slice(Math.max(0,offset-2),offset).map(e=>({...e,isNew:false})),...fresh.map(e=>({...e,text:e.text.slice(0,6000),isNew:true}))],existing:state.candidates,luna:[]});
  offset+=take;
 }
 return plan;
}

export const modelRates={'gpt-6.1-sol':{input:2,cached:.1,output:10},'gpt-6-luna':{input:.1,cached:.01,output:.5}};
export function usageCost(usage,model){const r=modelRates[model];if(!r)throw Error('Unsupported comparison model');return ((usage.input-usage.cached)*r.input+usage.cached*r.cached+usage.output*r.output)/1e6;}
function summarize(dir,plan,model='gpt-6.1-sol'){
 const results=plan.map(p=>{const f=path.join(dir,'results',p.id+'.json');return fs.existsSync(f)?JSON.parse(fs.readFileSync(f,'utf8')):null;}).filter(Boolean);
 const scopes=items=>items.reduce((a,c)=>(a[c.scope]=(a[c.scope]||0)+1,a),{});
 const usage=results.reduce((a,r)=>({input:a.input+(r.usage?.input_tokens||0),output:a.output+(r.usage?.output_tokens||0),cached:a.cached+(r.usage?.input_tokens_details?.cached_tokens||0)}),{input:0,output:0,cached:0});
 const sol=results.flatMap(r=>r.candidates||[]),matched=results.filter(r=>r.kind==='matched'&&r.status==='completed');
 return {directory:dir,model,planned:plan.length,attempted:results.length,completed:results.filter(r=>r.status==='completed').length,failed:results.filter(r=>r.status!=='completed').length,matchedCompleted:matched.length,backlogCompleted:results.filter(r=>r.kind==='backlog'&&r.status==='completed').length,lunaMatched:scopes(plan.filter(p=>matched.some(r=>r.id===p.id)).flatMap(p=>p.luna)),solMatched:scopes(matched.flatMap(r=>r.candidates)),solAll:scopes(sol),evaluatedMatched:scopes(matched.flatMap(r=>r.candidates)),evaluatedAll:scopes(sol),usage,estimatedStandardCostUSD:usageCost(usage,model)};
}

async function main(){
 const args=process.argv.slice(2),value=k=>{const i=args.indexOf(k);return i<0?null:args[i+1];};
 const source=path.join(os.homedir(),'.config/pixel-voice/personal-memory-trial');
 let dir=value('--resume'),snapshot;
 if(dir){snapshot=JSON.parse(fs.readFileSync(path.join(dir,'snapshot.json'),'utf8'));}
 else {
  const prior=value('--from-snapshot')?JSON.parse(fs.readFileSync(value('--from-snapshot'),'utf8')):null;
  snapshot={at:new Date().toISOString(),model:value('--model')||'gpt-6.1-sol',reasoning:'low',prompt:value('--prompt-file')?fs.readFileSync(value('--prompt-file'),'utf8'):INSTRUCTIONS,label:value('--label')||'original-prompt',state:prior?.state||JSON.parse(fs.readFileSync(path.join(source,'state.json'),'utf8')),events:prior?.events||fs.readFileSync(path.join(source,'evidence.jsonl'),'utf8').trim().split('\n').filter(Boolean).map(l=>JSON.parse(l))};
  dir=path.join(source,'comparisons',snapshot.model+'-'+snapshot.label.replace(/[^a-zA-Z0-9-]/g,'-')+'-'+new Date().toISOString().replace(/[:.]/g,'-'));
 }
 if(!modelRates[snapshot.model])throw Error('Unsupported comparison model');
 const plan=makePlan(snapshot.state,snapshot.events);
 if(!args.includes('--run')){console.log(JSON.stringify({dryRun:true,directory:dir,model:snapshot.model,label:snapshot.label,matched:plan.filter(p=>p.kind==='matched').length,backlog:plan.filter(p=>p.kind==='backlog').length,messages:snapshot.events.length,promptSHA256:crypto.createHash('sha256').update(snapshot.prompt).digest('hex')},null,2));return;}
 fs.mkdirSync(path.join(dir,'results'),{recursive:true,mode:0o700});
 if(!fs.existsSync(path.join(dir,'snapshot.json')))atomicJSON(path.join(dir,'snapshot.json'),snapshot);
 let queue=plan.filter(p=>!fs.existsSync(path.join(dir,'results',p.id+'.json')));
 if(value('--limit'))queue=queue.slice(0,Number(value('--limit')));
 let next=0,inFlightReserve=0;
 async function worker(){
  while(next<queue.length){
   const p=queue[next++],estimatedInput=JSON.stringify({events:p.events,existing:p.existing}).length/2+snapshot.prompt.length/2;
   const reserve=usageCost({input:estimatedInput,cached:0,output:2400},snapshot.model);
   if(summarize(dir,plan,snapshot.model).estimatedStandardCostUSD+inFlightReserve+reserve>3)throw Error('Comparison reached $3 conservative cost guard');
   inFlightReserve+=reserve;
   const started=Date.now();let raw=null;
   const result={id:p.id,kind:p.kind,status:'failed',startedAt:new Date().toISOString(),model:snapshot.model,reasoning:'low'};
   try{
    const extract=openAIExtractor({keyFile:path.join(os.homedir(),'.config/pixel-voice/openai-api-key'),model:snapshot.model,request:async(url,options)=>{const body=JSON.parse(options.body);body.instructions=snapshot.prompt;const response=await fetch(url,{...options,body:JSON.stringify(body)});try{raw=await response.clone().json();}catch{}return response;}});
    const out=await extract({events:p.events,existing:p.existing});
    result.candidates=validateCandidates(out.result,p.events,p.existing);result.status='completed';result.model=out.model;
   }catch(e){result.error=String(e.message).replace(/sk[-_][\w-]+/g,'[redacted]');}
   result.elapsedMs=Date.now()-started;result.usage=raw?.usage||null;result.responseStatus=raw?.status||null;result.incompleteDetails=raw?.incomplete_details||null;
   if(raw)atomicJSON(path.join(dir,'results',p.id+'.response.json'),raw);
   atomicJSON(path.join(dir,'results',p.id+'.json'),result);
   inFlightReserve-=reserve;
   console.log(JSON.stringify({batch:p.id,kind:p.kind,status:result.status,candidates:result.candidates?.length||0,elapsedMs:result.elapsedMs,error:result.error}));
  }
 }
 await Promise.all(Array.from({length:Math.min(3,queue.length)},()=>worker()));
 const summary={...summarize(dir,plan,snapshot.model),label:snapshot.label||'original-prompt',promptSHA256:crypto.createHash('sha256').update(snapshot.prompt).digest('hex')};atomicJSON(path.join(dir,'summary.json'),summary);
 const review=plan.map(p=>({id:p.id,kind:p.kind,luna:p.luna,sol:fs.existsSync(path.join(dir,'results',p.id+'.json'))?JSON.parse(fs.readFileSync(path.join(dir,'results',p.id+'.json'),'utf8')):null}));
 atomicJSON(path.join(dir,'comparison.json'),review);
 const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const cards=items=>items.map(c=>`<section><b>${escape(c.scope)}</b><p>${escape(c.text)}</p><small>${escape(c.reason)}</small>${c.evidence.map(e=>`<blockquote>${escape(e.quote)}</blockquote>`).join('')}</section>`).join('')||'<p>No proposals.</p>';
 const html=`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'"><title>Memory selection comparison</title><style>body{font:17px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#182536;background:#f7f8fa}article{background:white;border:1px solid #ccd5de;border-radius:14px;padding:20px;margin:24px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}section{border-top:1px solid #ddd;padding:12px 0}blockquote{border-left:3px solid #91a5b8;padding-left:12px;margin-left:0}small{color:#526170}pre{white-space:pre-wrap}@media(max-width:650px){.columns{grid-template-columns:1fr}}</style><h1>Personal memory comparison: ${escape(summary.label)}</h1><p>Same historical input; prompt variant recorded below. Review only; no results are installed. Backlog batches have no Luna comparison.</p><details><summary>Exact tested prompt</summary><pre>${escape(snapshot.prompt)}</pre></details><pre>${escape(JSON.stringify(summary,null,2))}</pre>${review.filter(r=>r.luna.length||r.sol?.candidates?.length||r.sol?.status==='failed').map(r=>`<article><small>${escape(r.kind)} · ${escape(r.id)}</small><div class="columns"><div><h2>Luna · original prompt</h2>${r.kind==='backlog'?'<p>Not processed by Luna in the snapshot.</p>':cards(r.luna)}</div><div><h2>${escape(snapshot.model)} · ${escape(summary.label)}</h2>${r.sol?.status==='failed'?`<p>Failed: ${escape(r.sol.error)}</p>`:cards(r.sol?.candidates||[])}</div></div><details><summary>Source conversation</summary><pre>${escape(plan.find(p=>p.id===r.id).events.map(e=>`${e.isNew?'NEW':'CONTEXT'} ${e.role}: ${e.text}`).join('\n\n'))}</pre></details></article>`).join('')}</html>`;
 fs.writeFileSync(path.join(dir,'review.html'),html,{mode:0o600});
 console.log(JSON.stringify(summary,null,2));
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))await main();
