import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {NumberedExecutor} from './numbered-executor.mjs';
import {DecisionsConfig} from './config.mjs';
import {chooseRoute} from '../server/routing.mjs';

const image='data:image/jpeg;base64,aW1hZ2U=';
const capture={status:'ok',screenshotId:'frame',capturedAt:1,imageBase64:'aW1hZ2U=',width:500,height:1000,displayWidth:1000,displayHeight:2000};
const frame={status:'ok',snapshotId:'tree',packageName:'example.app',nodes:[{id:3,text:'Submit',description:'INTERNAL LABEL MUST NOT REACH THE MODEL',clickable:true,visible:true,enabled:true,bounds:[200,400,600,600]}]};
test('unchanged Home loop reaches Luna after two actions rather than thirty',async()=>{
 const actions=[],triggers=[];
 const result=await new NumberedExecutor({client:mockClient(['home','home']),completionReviewer:'luna',review:async(_,input)=>{triggers.push(input.trigger);return {status:'complete',summary:'Gmail is no longer on screen.',evidence:['Home screen'],nextSteps:[],inputValues:[],elapsedMs:1};},render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'action_accepted'};}}).run('Close Gmail',{resume:true});
 assert.equal(result.status,'verified');assert.deepEqual(triggers,['repeated_action_loop']);assert.equal(actions.filter(a=>a.action==='home').length,2);
});
test('a repeated cycle after reviewer guidance stops instead of running to the budget',async()=>{
 const actions=[];let reviews=0;
 const result=await new NumberedExecutor({client:mockClient(['home','home','home']),completionReviewer:'luna',review:async()=>{reviews++;return {status:'continue',summary:'Try another route.',evidence:[],nextSteps:['Use another route.'],inputValues:[],elapsedMs:1};},render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'action_accepted'};}}).run('Close Gmail',{resume:true});
 assert.equal(result.status,'stopped');assert.match(result.reason,/continued after review/);assert.equal(reviews,1);assert.equal(actions.filter(a=>a.action==='home').length,3);
});
function mockClient(picks,probability=1){const calls=[];return {calls,decisions:{create:async b=>{calls.push(b);const q=b.questions[0];if(q.type==='predicate')return {answers:[{name:'complete',type:'predicate',probability:picks[0]==='finish'?probability:0}]};const wanted=picks.shift(),c=q.choices.find(c=>c.value===wanted||c.description===wanted);assert.ok(c,'Missing choice '+wanted);return {answers:[{name:'selection',type:'choice',choice:c.value}]};}}};}
test('close app finishes after Home without accepting a reviewer suggestion to end Rose Agent',async()=>{
 let foreground='dev.pixel.anime',reviewInput;const actions=[],api=mockClient(['home']);
 const r=await new NumberedExecutor({client:api,completionReviewer:'luna',review:async(_,input)=>{reviewInput=input;return {status:'continue',summary:'Still open.',evidence:[],nextSteps:['Tap the pink X.'],inputValues:[],visualTargets:[{label:'End',x:500,y:900}],elapsedMs:1};},render:async()=>image,phone:async c=>{actions.push(c);if(c.action==='home'){foreground='dev.pixel.voice';return {status:'action_accepted'};}return c.action==='screenshot'?capture:{...frame,packageName:foreground};}}).run('Close the app',{resume:true});
 assert.equal(r.status,'verified');assert.equal(r.completionReviewer,'observed_task_condition');
 assert.equal(reviewInput.taskDefinition.targetApp.packageName,'dev.pixel.anime');assert.ok(reviewInput.observedCompletion);
 assert.deepEqual(actions.filter(c=>!['inspect_ui','screenshot'].includes(c.action)),[{action:'home'}]);
 assert.equal(api.calls.filter(c=>c.questions[0].type==='choice').length,1);
});
test('close named background app binds it without opening it or touching launcher controls',async()=>{
 const actions=[],api=mockClient(['com.google.android.gm']);
 const r=await new NumberedExecutor({client:api,completionReviewer:'luna',review:async()=>({status:'complete',summary:'Gmail is not foreground.',evidence:[],nextSteps:[],inputValues:[],elapsedMs:1}),render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='list_apps'?{status:'ok',apps:[{label:'Gmail',packageName:'com.google.android.gm'}]}:{...frame,packageName:'dev.pixel.voice'};}}).run('Close Gmail');
 assert.equal(r.status,'verified');assert.equal(r.taskDefinition.targetApp.packageName,'com.google.android.gm');assert.ok(!actions.some(c=>['open_app','home','screen_gesture'].includes(c.action)));
});
test('unchanged repeated inspection cannot continue forever after review',async()=>{
 const result=await new NumberedExecutor({client:mockClient(['inspect','inspect','inspect']),completionReviewer:'luna',review:async()=>({status:'continue',summary:'Inspect again.',evidence:[],nextSteps:['Inspect the screen.'],inputValues:[],elapsedMs:1}),render:async()=>image,phone:async c=>c.action==='screenshot'?capture:frame}).run('Find a missing control',{resume:true});
 assert.equal(result.status,'stopped');assert.match(result.reason,/continued after review/);
});
test('numbered physical tap includes Submit without labels or another authorization call',async()=>{
 const api=mockClient(['target_1','finish']),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'gesture_dispatched'};}}).run('Press the visible Continue button',{resume:true});
 assert.equal(r.status,'verified');assert.equal(api.calls.length,3);assert.ok(!JSON.stringify(api.calls).includes('INTERNAL LABEL'));
 assert.deepEqual(actions.find(c=>c.action==='screen_gesture'),{action:'screen_gesture',screenshotId:'frame',points:[400,500]});
 assert.ok(!actions.some(c=>c.action==='tap_label'));assert.equal(r.executor,'numbered');
});
test('generic literal input uses current request, not the old Hanes test',async()=>{
 const api=mockClient(['type','lunar eclipses','finish']),actions=[];
 const focused={...frame,nodes:[{...frame.nodes[0],editable:true,focused:true}]};
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?focused:{status:'action_accepted'};}}).run('Search for "lunar eclipses"',{resume:true});
 assert.equal(r.status,'verified');assert.equal(actions.find(c=>c.action==='set_text').text,'lunar eclipses');
});
test('failed phone action stops with its real reason and never silently hands off',async()=>{
 const r=await new NumberedExecutor({client:mockClient(['target_1']),render:async()=>image,phone:async c=>c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'stale_screenshot'}}).run('Tap Continue',{resume:true});
 assert.equal(r.status,'stopped');assert.match(r.reason,/stale_screenshot/);assert.equal(r.history.length,1);
});
test('mode switch preserves selected executor across existing home-option toggles',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'numbered-routing-'));try{
  const c=new DecisionsConfig(path.join(dir,'config'));
  assert.equal(c.set({enabled:true,executor:'numbered'}).executor,'numbered');
  c.set({enabled:false});assert.equal(c.set({enabled:true}).executor,'numbered');
  assert.equal(c.set({enabled:true,executor:'semantic'}).executor,'semantic');
  assert.throws(()=>c.set({enabled:true,executor:'unknown'}));
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('numbered router does not force authorized cart deletion to Luna',async()=>{
 let body;const api={decisions:{create:async b=>{body=b;return {answers:[{name:'selection',type:'choice',choice:'fast'}]};}}};
 const r=await chooseRoute(api,'Clear the Amazon cart',{enabled:true,executor:'numbered',decider:'decisions'});
 assert.equal(r.route,'fast');assert.match(body.questions[0].instructions,/Do not route a phone task to Luna merely/);
});
test('app selection identifies actual package names rather than transient app indexes',async()=>{
 const api=mockClient(['com.amazon.shopping','finish']),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='list_apps'?{status:'ok',apps:[{label:'Amazon',packageName:'com.amazon.shopping'}]}:c.action==='screenshot'?capture:c.action==='open_app'?{status:'opened'}:frame;}}).run('Open Amazon');
 assert.equal(r.status,'verified');assert.equal(actions.find(c=>c.action==='open_app').packageName,'com.amazon.shopping');
});
test('stop music already paused finishes without tapping the play toggle',async()=>{
 const api=mockClient(['finish']),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:frame;}}).run('Stop the music',{resume:true});
 assert.equal(r.status,'verified');
 assert.ok(!actions.some(c=>c.action==='screen_gesture'));
});
test('pause action followed by successful completion never toggles playback again',async()=>{
 const api=mockClient(['target_1','finish']),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'gesture_dispatched'};}}).run('Stop the music',{resume:true});
 assert.equal(r.status,'verified');
 assert.equal(actions.filter(c=>c.action==='screen_gesture').length,1);
});
test('continuing in the current app keeps its screen and provides it to app selection',async()=>{
 const api=mockClient(['current','finish']),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='list_apps'?{status:'ok',apps:[{label:'Example',packageName:frame.packageName}]}:c.action==='screenshot'?capture:frame;}}).run('Stop the music');
 assert.equal(r.status,'verified');
 assert.ok(!actions.some(c=>c.action==='open_app'));
 const content=api.calls[0].input[0].content;
 assert.ok(content.some(c=>c.type==='input_image'));
 assert.match(content.find(c=>c.type==='input_text').text,/example.app/);
});
test('completion prevents a toggle even if action selection would keep choosing it',async()=>{
 let checks=0,choices=0;const actions=[];
 const api={decisions:{create:async b=>{const q=b.questions[0];if(q.type==='predicate')return {answers:[{name:'complete',type:'predicate',probability:checks++?1:0}]};choices++;return {answers:[{name:'selection',type:'choice',choice:'target_1'}]};}}};
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'gesture_dispatched'};}}).run('Stop the music',{resume:true});
 assert.equal(r.status,'verified');assert.equal(choices,1);
 assert.equal(actions.filter(c=>c.action==='screen_gesture').length,1);
});
test('conflicting completion decisions stop without repeatedly mutating the screen',async()=>{
 const api=mockClient(['finish','finish'],0),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:frame;}}).run('Stop the music',{resume:true});
 assert.equal(r.status,'stopped');assert.match(r.reason,/cannot confirm/);
 assert.ok(!actions.some(c=>c.action==='screen_gesture'));
});
test('a wrong initial app can be corrected inside the action loop',async()=>{
 const api=mockClient(['current','switch_app','com.apple.android.music','finish']),actions=[];
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='list_apps'?{status:'ok',apps:[{label:'Apple Music',packageName:'com.apple.android.music'}]}:c.action==='screenshot'?capture:c.action==='open_app'?{status:'opened'}:{...frame,packageName:'dev.pixel.voice'};}}).run('Play Dreams by Fleetwood Mac');
 assert.equal(r.status,'verified');assert.equal(actions.filter(c=>c.action==='open_app').length,1);
 assert.equal(actions.find(c=>c.action==='open_app').packageName,'com.apple.android.music');
});
test('follow-up can enter the referenced page name rather than only the words open it',async()=>{
 const api=mockClient(['reference_0','The Infatuation LA sushi guide','type','The Infatuation LA sushi guide','finish']),actions=[];
 const focused={...frame,nodes:[{...frame.nodes[0],editable:true,focused:true}]};
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?focused:{status:'action_accepted'};}}).run('Open it here on the Pixel',{resume:true,context:[{role:'assistant',text:'The Infatuation LA sushi guide'}]});
 assert.equal(r.status,'verified');assert.equal(actions.find(c=>c.action==='set_text').text,'The Infatuation LA sushi guide');
});
test('selecting the same app records a no-op and tells the executor to navigate within it',async()=>{
 const api=mockClient(['switch_app','current','finish']);
 const r=await new NumberedExecutor({client:api,render:async()=>image,phone:async c=>c.action==='list_apps'?{status:'ok',apps:[]}:c.action==='screenshot'?capture:frame}).run('Open it',{resume:true});
 assert.equal(r.status,'verified');assert.ok(r.history.some(h=>h.action==='switch_app'&&h.result==='already_current'));
});
test('Luna has final authority over a Decisions completion candidate and guides the next tap',async()=>{
 let reviews=0;const actions=[];
 const api={decisions:{create:async b=>b.questions[0].type==='predicate'?{answers:[{name:'complete',type:'predicate',probability:1}]}:{answers:[{name:'selection',type:'choice',choice:'target_1'}]}}};
 const result=await new NumberedExecutor({client:api,completionReviewer:'luna',review:async()=>++reviews===1?{status:'continue',summary:'The other date is selected.',evidence:[],nextSteps:['Select October 15.'],inputValues:[],elapsedMs:1}:{status:'complete',summary:'October 15 has a 2 PM opening.',evidence:['October 15 selected.'],nextSteps:[],inputValues:[],elapsedMs:1},render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?frame:{status:'gesture_dispatched'};}}).run('Check availability October 15',{resume:true});
 assert.equal(result.status,'verified');assert.equal(result.answer,'October 15 has a 2 PM opening.');assert.equal(reviews,2);assert.equal(actions.filter(c=>c.action==='screen_gesture').length,1);
});
test('Luna gives a stuck Decisions executor new search text without taking over phone actions',async()=>{
 const api=mockClient(['stop','type','t0','finish']);const actions=[];let reviews=0;
 const result=await new NumberedExecutor({client:api,completionReviewer:'luna',review:async()=>++reviews===1?{status:'continue',summary:'Search for the shop.',evidence:[],nextSteps:['Type the supplied search query into the focused field.'],inputValues:["Omar's Venice haircut"],elapsedMs:1}:{status:'complete',summary:'The requested shop is open.',evidence:['Matching shop'],nextSteps:[],inputValues:[],elapsedMs:1},render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:c.action==='inspect_ui'?{...frame,nodes:[{...frame.nodes[0],editable:true,focused:true}]}:{status:'action_accepted'};}}).run('Find the barber',{resume:true});
 assert.equal(result.status,'verified');assert.equal(actions.find(c=>c.action==='set_text').text,"Omar's Venice haircut");assert.equal(reviews,2);
});
test('a blocked Luna review returns useful partial progress without another mutation',async()=>{
 const actions=[];const result=await new NumberedExecutor({client:mockClient(['stop']),completionReviewer:'luna',review:async()=>({status:'blocked',summary:'Reached the calendar; selecting a staff member requires your choice.',evidence:[],nextSteps:[],inputValues:[],elapsedMs:1}),render:async()=>image,phone:async c=>{actions.push(c);return c.action==='screenshot'?capture:frame;}}).run('Check appointment times',{resume:true});
 assert.equal(result.status,'stopped');assert.match(result.answer,/Reached the calendar/);assert.ok(!actions.some(c=>c.action==='screen_gesture'));
});
test('reviewer rollback and preference survive unrelated option updates',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'reviewer-routing-'));try{
  const c=new DecisionsConfig(path.join(dir,'config'));c.set({enabled:true,executor:'numbered',completionReviewer:'luna'});
  assert.equal(c.set({enabled:false}).completionReviewer,'luna');assert.equal(c.set({enabled:true,completionReviewer:'decisions'}).completionReviewer,'decisions');
  assert.throws(()=>c.set({enabled:true,completionReviewer:'invalid'}));
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('visual reviewer proposal uses its exact reviewed screenshot and bypasses bad accessibility coordinates',async()=>{
 let reviews=0,screens=0;const actions=[];
 const api={decisions:{create:async b=>b.questions[0].type==='predicate'?{answers:[{name:'complete',type:'predicate',probability:1}]}:{answers:[{name:'selection',type:'choice',choice:'visual_0'}]}}};
 const result=await new NumberedExecutor({client:api,completionReviewer:'luna',review:async()=>++reviews===1?{status:'continue',summary:'Select Thursday.',evidence:[],nextSteps:['Tap October 15.'],inputValues:[],visualTargets:[{label:'October 15',x:250,y:400}],elapsedMs:1}:{status:'complete',summary:'Thursday is selected.',evidence:[],nextSteps:[],inputValues:[],visualTargets:[],elapsedMs:1},render:async()=>image,phone:async c=>{actions.push(c);if(c.action==='screenshot'){screens++;return {...capture,screenshotId:'frame-'+screens};}if(c.action==='inspect_ui')return frame;assert.equal(screens,1);return {status:'gesture_dispatched'};}}).run('Show Thursday',{resume:true});
 assert.equal(result.status,'verified');assert.deepEqual(actions.find(c=>c.action==='screen_gesture'),{action:'screen_gesture',screenshotId:'frame-1',points:[250,800]});
});
