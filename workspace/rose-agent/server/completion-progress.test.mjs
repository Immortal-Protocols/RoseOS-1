import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {EventEmitter} from 'node:events';
import {VoiceTasks} from './elevenlabs.mjs';
import {taskReviewTarget,taskReviewMatches} from './task-review.mjs';

// Exercise the actual server callbacks without starting the server or a model.
const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
const callbacks=server.slice(server.indexOf('function deliverAgentMessage('),server.indexOf("codex.on('done',"));
async function fixture(review){
 const events=[],codex=new EventEmitter(),voiceTasks=new VoiceTasks();
 const record=await voiceTasks.start('progress-test','Show the requested result',async()=>{});
 let resolveReview;
 const reviewed=new Promise(resolve=>{resolveReview=resolve;});
 const launcherUpdates=[];
 const context=vm.createContext({codex,voiceTasks,visualTrial:true,
  withdrawTaskGeneration:()=>{},taskStop:{stopped:false,active(){return this.stopped;},acceptsOutput(){return !this.stopped;},summary:()=>'Task stopped'},backgroundEnabled:false,AbortController,reviewAbort:null,evaluation:null,taskReviewTarget,taskReviewMatches,
  taskEvidence:{collectDelegated:async()=>{}},
  capture:{record(){}},captureContext:()=>({}),launcherTask:{progress:t=>launcherUpdates.push(['progress',t]),result:t=>launcherUpdates.push(['result',t]),checking:()=>launcherUpdates.push(['checking']),running:()=>launcherUpdates.push(['running']),finish:(state)=>launcherUpdates.push(['finish',state])},
  visualGuard:{needsReview:()=>true,assess:()=>reviewed},pendingVisualAnswer:'',
  activeRequestSource:'voice',conversationTimeline:{add:()=>assert.fail('not a typed request')},
  lastRoute:null,taskStarted:0,activeSession:'test-session',delegation:'test-delegation',busy:true,
  spokenProgress:{update:()=>{},finish:()=>{},start:()=>{}},
  emit:(type,data)=>events.push({type,...data}),speakProgress:()=>{},visualVerificationPolicy:'',
 });
 codex.submit=async()=>{};
 vm.runInContext(callbacks,context);
 return {events,codex,record,context,launcherUpdates,approve:()=>resolveReview(review)};
}
test('progress reaches the voice during review, but the proposed final answer waits for approval',async()=>{
 const f=await fixture({status:'complete',summary:'Confirmed: the requested result is visible'});
 f.codex.emit('message','Still checking the result','commentary');
 assert.equal(f.record.progress,'Still checking the result');
 assert.equal(f.record.state,'running');
 assert.ok(f.events.some(e=>e.type==='live'&&e.event.type==='session.thinking.append'&&e.event.content==='Still checking the result'));
 f.codex.emit('message','The requested result is visible.','final_answer');
 assert.equal(f.record.text,'');
 assert.ok(!f.events.some(e=>e.type==='live'&&e.event.type==='session.commentary.append'));
 const finishing=f.context.finishAgentTurn({status:'completed'});
 f.codex.emit('message','The final check is still running','commentary');
 assert.equal(f.record.progress,'The final check is still running');
 assert.equal(f.record.state,'running');
 assert.ok(f.launcherUpdates.some(e=>e[0]==='checking'));
 assert.ok(!f.launcherUpdates.some(e=>e[0]==='finish'));
 f.approve();await finishing;
 assert.equal(f.record.state,'completed');
 assert.deepEqual(f.launcherUpdates.at(-1),['finish','completed']);
 assert.equal(f.record.text,'The requested result is visible.');
 assert.equal(f.events.filter(e=>e.type==='live'&&e.event.type==='session.commentary.append').length,1);
 assert.ok(!JSON.stringify(f.events).includes('Confirmed: the requested result is visible'));
});
test('a rejected answer stays hidden while correction progress reaches the voice',async()=>{
 const f=await fixture({status:'continue',prompt:'Finish the missing step'});
 f.codex.emit('message','Unverified claim of success','final_answer');
 const finishing=f.context.finishAgentTurn({status:'completed'});
 f.approve();await finishing;
 f.codex.emit('message','The result is not confirmed; finishing the missing step','commentary');
 assert.equal(f.record.state,'running');
 assert.equal(f.record.text,'');
 assert.equal(f.record.progress,'The result is not confirmed; finishing the missing step');
 assert.ok(!f.events.some(e=>e.type==='voice_task_done'||e.type==='live'&&e.event.type==='session.commentary.append'));
});
test('a blocked result reports the reviewed failure instead of the proposed success',async()=>{
 const f=await fixture({status:'blocked',summary:'Unable to verify the result'});
 f.codex.emit('message','Unverified claim of success','final_answer');
 const finishing=f.context.finishAgentTurn({status:'completed'});
 f.approve();await finishing;
 assert.equal(f.record.state,'failed');
 assert.equal(f.record.text,'Unable to verify the result');
 assert.ok(!JSON.stringify(f.events).includes('Unverified claim of success'));
});

test('after an acknowledged Stop the late answer is neither shown nor spoken and the task ends stopped',async()=>{
 const f=await fixture({status:'complete',summary:'unused'});
 f.codex.emit('message','Opening Calculator','commentary');
 f.context.taskStop.stopped=true;
 f.codex.emit('message','34.50 plus a 20% tip is 41.40.','final_answer');
 await f.context.finishAgentTurn({status:'interrupted'});
 assert.equal(f.record.state,'failed');assert.equal(f.record.text,'Task stopped');
 assert.deepEqual(f.launcherUpdates.at(-1),['finish','interrupted']);
 assert.ok(!f.launcherUpdates.some(e=>e[0]==='checking'),'no result check after Stop');
 assert.ok(!JSON.stringify(f.events).includes('41.40'));
});


for(const answer of [
 'Nathan confirmed Wednesday works.',
 'The forecast is rain in the afternoon, with a high of sixty-four degrees.',
 'The file is saved at /tmp/example-summary.txt. It contains three paragraphs.'
]){
 test('approval preserves the actual answer: '+answer,async()=>{
  const f=await fixture({status:'complete',summary:'The answer is supported by the evidence.'});
  f.codex.emit('message',answer,'final_answer');
  const finishing=f.context.finishAgentTurn({status:'completed'});f.approve();await finishing;
  assert.equal(f.record.text,answer);
  const spoken=f.events.filter(e=>e.type==='live'&&e.event.type==='session.commentary.append');
  assert.equal(spoken.length,1);assert.equal(spoken[0].event.content,answer);
  assert.ok(!JSON.stringify(f.events).includes('The answer is supported by the evidence.'));
 });
}
