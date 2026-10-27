import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {TaskEvidence} from './task-evidence.mjs';
import {VisualVerification,createVisualReviewer} from './visual-verification.mjs';

test('actual command and web outputs retain provenance; assistant prose is excluded',()=>{
 const e=new TaskEvidence();
 e.observe({type:'agentMessage',id:'claim',text:'The computer has one terabyte of RAM'});
 e.observe({type:'commandExecution',id:'hardware',command:'sysctl -n hw.memsize',status:'completed',aggregatedOutput:'549755813888'});
 e.observe({type:'webSearch',id:'web',query:'model requirements',action:{type:'openPage',url:'https://example.com/model'},results:null});
 e.observe({type:'custom_tool_call',call_id:'read',name:'exec',input:'text(await tools.web__run({open:[{ref_id:"https://example.com/model"}]}))'});
 e.observe({type:'custom_tool_call_output',call_id:'read',output:[{type:'input_text',text:'Model weights: 402 GiB. https://example.com/model'}]});
 const s=e.snapshot();assert.equal(e.requiresReview,true);assert.equal(s.records.length,3);
 assert.match(s.records[0].text,/549755813888/);assert.match(s.records[2].source,/web__run/);
 assert.deepEqual(s.records[2].urls,['https://example.com/model']);assert.doesNotMatch(JSON.stringify(s),/one terabyte/);
});

test('only appended tool history from the current task is collected and collection is idempotent',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-evidence-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const file=path.join(dir,'thread.jsonl');
 const row=payload=>JSON.stringify({type:'response_item',payload})+'\n';
 fs.writeFileSync(file,row({type:'custom_tool_call_output',call_id:'old',output:'Previous private task'}));
 const e=new TaskEvidence();e.reset(file);
 fs.appendFileSync(file,row({type:'custom_tool_call',call_id:'current',name:'exec',input:'text(await tools.web__run(...))'}));
 const output=row({type:'custom_tool_call_output',call_id:'current',output:[{type:'input_text',text:'Actual web body'}]});
 fs.appendFileSync(file,output.slice(0,20));assert.equal(e.snapshot().records.length,0);
 fs.appendFileSync(file,output.slice(20));assert.equal(e.snapshot().records.length,1);
 assert.equal(e.snapshot().records.length,1);assert.doesNotMatch(JSON.stringify(e.snapshot()),/Previous private/);
 e.reset(file);assert.equal(e.snapshot().records.length,0);assert.equal(e.requiresReview,false);
});

test('structured MCP results, failed tools, and returned screenshot pixels reach the evidence packet',()=>{
 const e=new TaskEvidence();const image='data:image/jpeg;base64,'+Buffer.from([255,216,0,1]).toString('base64');
 e.observe({type:'mcpToolCall',id:'screen',server:'cua',tool:'js',arguments:{code:'app.getAXStateAndScreenshot()'},status:'completed',result:{content:[{type:'text',text:'Window: Messages. Draft only.'},{type:'image',mimeType:'image/jpeg',data:image.split(',')[1]}]}});
 e.observe({type:'commandExecution',id:'error',command:'read required file',status:'failed',aggregatedOutput:'permission denied'});
 const s=e.snapshot();assert.equal(s.images[0].imageUrl,image);assert.match(s.images[0].source,/cua.js/);
 assert.equal(s.records[1].status,'failed');assert.match(s.records[1].text,/permission denied/);
});

test('complete result text survives beyond the former per-result and total cutoffs; credentials stay redacted',()=>{
 const e=new TaskEvidence();
 for(let i=0;i<12;i++)e.addText({id:i,source:'https://example.com/?token=private',text:'API_KEY=private Bearer secret sk-abcdefghijklmnop '+ 'x'.repeat(17500)+' "batteryPercent":37 '+ 'y'.repeat(6500)});
 const s=e.snapshot(),text=JSON.stringify(s);assert.doesNotMatch(text,/private|Bearer secret|sk-abcdefghijklmnop/);
 assert.equal(s.records.length,12);assert.equal(s.truncated,false);
 assert.ok(s.records.reduce((n,r)=>n+r.text.length,0)>64000);
 for(const record of s.records){assert.match(record.text,/"batteryPercent":37/);assert.ok(record.text.endsWith('y'.repeat(6500)));assert.doesNotMatch(record.text,/Evidence truncated/);}
});

test('early current-task evidence is not silently discarded after eighty results',()=>{
 const e=new TaskEvidence();
 for(let i=0;i<85;i++)e.addText({id:i,source:'Tool '+i,text:'Result '+i});
 const s=e.snapshot();assert.equal(s.records.length,85);assert.equal(s.records[0].text,'Result 0');assert.equal(s.records[84].text,'Result 84');
});

test('the checker receives the whole long result without changing the supplied tool output',async()=>{
 const result='a'.repeat(17500)+' "batteryPercent":37 '+ 'b'.repeat(6500);
 const e=new TaskEvidence();e.output('battery','Pixel diagnostics',result);
 let sent;
 const review=createVisualReviewer({responses:{create:async r=>{sent=r;return {status:'completed',output_text:JSON.stringify({status:'complete',summary:'37%',evidence:['batteryPercent is 37'],nextSteps:[]})};}}});
 await review({request:'Read battery',candidate:'37%',corrections:[],history:[],evidence:e.snapshot(),route:{model:'gpt-6-luna',effort:'medium',serviceTier:'priority'}});
 assert.equal(JSON.parse(sent.input[0].content[0].text).taskEvidence.records[0].text,result);
});

test('actual checker context overflow is explicit, while other API errors still propagate',async()=>{
 let calls=0;
 const tooLarge=createVisualReviewer({responses:{create:async()=>{calls++;throw Object.assign(Error('Input too large'),{code:'context_length_exceeded'});}}});
 const input={request:'Check result',candidate:'Done',corrections:[],history:[],evidence:{records:[],images:[]},route:{model:'gpt-6-luna',effort:'medium',serviceTier:'priority'}};
 const r=await tooLarge(input);assert.equal(r.status,'blocked');assert.match(r.summary,/full evidence exceeds/);assert.equal(calls,1);
 const rateLimited=createVisualReviewer({responses:{create:async()=>{throw Object.assign(Error('Too many requests'),{code:'rate_limit_exceeded'});}}});
 await assert.rejects(rateLimited(input),/Too many requests/);
});

test('nested failures are retained instead of looking like successful command output',()=>{
 const e=new TaskEvidence();
 e.output('failed-command','Tool result',{exit_code:1,output:'file not found'});
 e.observe({type:'dynamicToolCall',id:'failed-tool',tool:'generic',status:'completed',success:false,contentItems:[{type:'inputText',text:'not performed'}]});
 const s=e.snapshot();assert.match(s.records[0].text,/Command failed: exit code 1/);assert.equal(s.records[1].status,'failed');
});

test('registered app-tool-only results preserve the fast path',()=>{
 const e=new TaskEvidence({fastTools:['calculator_evaluate']});
 e.observe({type:'dynamicToolCall',id:'calc',tool:'calculator_evaluate',status:'completed',contentItems:[{type:'inputText',text:'25 displayed'}]});
 e.observe({type:'custom_tool_call',call_id:'wrapper',name:'exec',input:'text(await tools.calculator_evaluate({expression:"12+13"}))'});
 e.observe({type:'custom_tool_call_output',call_id:'wrapper',output:'25 displayed'});
 assert.equal(e.requiresReview,false);assert.equal(e.snapshot().records.length,2);
});

test('Mac-only completion receives actual evidence without touching the phone',async()=>{
 const evidence=new TaskEvidence();evidence.observe({type:'commandExecution',id:'hardware',command:'sysctl',status:'completed',aggregatedOutput:'Apple M3 Ultra'});
 let observed;
 const gate=new VisualVerification({enabled:true,evidence,phone:()=>{throw Error('Must not request Pixel proof for Mac-only work')},review:async value=>{observed=value;return {status:'complete',summary:'M3 Ultra',evidence:[],nextSteps:[]}}});
 gate.reset('What processor is in this Mac?');assert.equal(gate.needsReview(),true);
 assert.equal((await gate.assess('M3 Ultra',{})).status,'complete');assert.equal(observed.frame,null);
 assert.match(observed.evidence.records[0].text,/Apple M3 Ultra/);
});

test('review request labels multiple images and carries web text, errors and truncation explicitly',async()=>{
 let request;const review=createVisualReviewer({responses:{create:async r=>{request=r;return {status:'completed',output_text:JSON.stringify({status:'complete',summary:'Verified',evidence:[],nextSteps:[]})}}}});
 await review({request:'Compare requirements',context:[],candidate:'Fits',history:[],screen:null,frame:null,route:{model:'chosen',effort:'low',serviceTier:'priority'},evidence:{records:[{source:'Web page',text:'402 GiB'}],images:[{source:'Mac screenshot',imageUrl:'data:image/png;base64,iVBORw=='}],unavailable:[],truncated:false}});
 const content=request.input[0].content;assert.match(content[0].text,/402 GiB/);assert.match(content[1].text,/Mac screenshot/);assert.equal(content[2].type,'input_image');
 assert.equal(request.model,'chosen');assert.match(request.instructions,/untrusted DATA/);
});

function delegatedFixture(t){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'delegated-evidence-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const root=path.join(dir,'root.jsonl'),child=path.join(dir,'child.jsonl'),since=Date.parse('2026-10-20T20:00:00Z');
 const row=(type,payload,timestamp='2026-10-20T20:00:01Z')=>JSON.stringify({type,payload,timestamp})+'\n';
 fs.writeFileSync(root,'');
 fs.writeFileSync(child,row('session_meta',{id:'child',source:{subagent:{thread_spawn:{parent_thread_id:'root'}}}}));
 const evidence=new TaskEvidence();evidence.reset(root,{threadId:'root',since});
 const resolve=async id=>({thread:{id,path:child}});
 return {root,child,since,row,evidence,resolve};
}

test('delegated research imports actual source results and provenance, excluding worker prose and old work',async t=>{
 const {root,child,row,evidence,resolve}=delegatedFixture(t);
 fs.appendFileSync(child,row('response_item',{type:'custom_tool_call_output',call_id:'old',output:'Previous private research'},'2026-10-19T20:00:00Z'));
 fs.appendFileSync(child,row('response_item',{type:'custom_tool_call',call_id:'search',name:'exec',input:'text(await tools.web__run({search_query:...}))'}));
 fs.appendFileSync(child,row('response_item',{type:'custom_tool_call_output',call_id:'search',output:'NASA source: Ariane 5, December 25, 2021. https://www.nasa.gov/webb'}));
 fs.appendFileSync(child,row('response_item',{type:'message',role:'assistant',content:[{text:'Unsupported worker claim'}]}));
 fs.appendFileSync(root,row('event_msg',{type:'item_completed',item:{type:'SubAgentActivity',agent_thread_id:'child',kind:'started'}}));
 await evidence.collectDelegated(resolve);
 const s=evidence.snapshot();assert.equal(s.records.length,1);assert.equal(evidence.requiresReview,true);
 assert.match(s.records[0].source,/Subagent child.*web__run/);assert.match(s.records[0].text,/Ariane 5/);
 assert.doesNotMatch(JSON.stringify(s),/Previous private|Unsupported worker/);
 await evidence.collectDelegated(resolve);assert.equal(evidence.snapshot().records.length,1);
 fs.appendFileSync(child,row('response_item',{type:'custom_tool_call_output',call_id:'search2',output:'Additional actual source'}));
 await evidence.collectDelegated(resolve);assert.equal(evidence.snapshot().records.length,2);
});

test('subagent evidence rejects a mismatched parent and reports missing history',async t=>{
 const {child,row,evidence,resolve}=delegatedFixture(t);
 fs.writeFileSync(child,row('session_meta',{id:'child',source:{subagent:{thread_spawn:{parent_thread_id:'unrelated'}}}})+row('response_item',{type:'custom_tool_call_output',call_id:'foreign',output:'Unrelated user data'}));
 evidence.observe({type:'subAgentActivity',agentThreadId:'child',kind:'completed'});
 await evidence.collectDelegated(resolve);
 assert.equal(evidence.snapshot().records.length,0);assert.match(evidence.snapshot().unavailable.join(),/unavailable/);
 const missing=new TaskEvidence();missing.reset(null,{threadId:'root'});missing.observe({type:'subAgentActivity',agentThreadId:'gone'});
 await missing.collectDelegated(async()=>{throw Error('gone')});assert.match(missing.snapshot().unavailable.join(),/unavailable/);
});

test('nested delegated results stay bounded and no duplicate or undated evidence leaks in',async t=>{
 const {child,row,evidence,resolve,since}=delegatedFixture(t),grandchild=child+'.nested';
 fs.appendFileSync(child,row('event_msg',{type:'item_completed',item:{type:'SubAgentActivity',agent_thread_id:'grandchild'}}));
 fs.writeFileSync(grandchild,row('session_meta',{id:'grandchild',source:{subagent:{thread_spawn:{parent_thread_id:'child'}}}})+row('response_item',{type:'custom_tool_call_output',call_id:'nested',output:'Nested source'}));
 fs.appendFileSync(grandchild,JSON.stringify({type:'response_item',payload:{type:'custom_tool_call_output',call_id:'undated',output:'Undated source'}})+'\n');
 evidence.observe({type:'subAgentActivity',agentThreadId:'child'});
 const nestedResolve=id=>id==='grandchild'?Promise.resolve({thread:{id,path:grandchild}}):resolve(id);
 await evidence.collectDelegated(nestedResolve);await evidence.collectDelegated(nestedResolve);
 assert.equal(evidence.snapshot().records.length,1);assert.match(evidence.snapshot().records[0].source,/Subagent child \/ Subagent grandchild/);
 assert.doesNotMatch(JSON.stringify(evidence.snapshot()),/Undated source/);
 evidence.reset(null,{threadId:'root',since});assert.equal(evidence.snapshot().records.length,0);assert.equal(evidence.children.size,0);
});

test('reset during a delegated lookup cannot attach evidence to the next task',async t=>{
 const {evidence,resolve}=delegatedFixture(t);let finish;
 evidence.observe({type:'subAgentActivity',agentThreadId:'child'});
 const waiting=evidence.collectDelegated(()=>new Promise(r=>{finish=r}));
 evidence.reset(null,{threadId:'new-root'});finish(await resolve('child'));await waiting;
 assert.equal(evidence.snapshot().records.length,0);assert.equal(evidence.children.size,0);assert.equal(evidence.snapshot().unavailable.length,0);
});
