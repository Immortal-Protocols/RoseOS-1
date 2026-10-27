import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {VisualVerification} from './visual-verification.mjs';

function fixture(t,options={}) {
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'visual-gate-'));
 t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 let frame=0,now=1000,failCapture=false;const actions=[],reviews=[];
 const gate=new VisualVerification({directory,enabled:true,now:()=>now,
  phone:async c=>{
   if(c.action==='screenshot'){
    if(failCapture)throw Error('screen unavailable');
    return {status:'ok',imageBase64:Buffer.from([255,216,frame++]).toString('base64'),capturedAt:now,width:100,height:100};
   }
   if(c.action==='inspect_ui')return {status:'ok',packageName:'maps',nodes:[]};
   actions.push(c);if(options.failAfterAction)failCapture=true;
   return {status:'action_accepted'};
  },review:async input=>{reviews.push(input);return options.review||{status:'complete',summary:'Visible result verified',evidence:['pin'],nextSteps:[]};}
 });
 gate.reset('Show the requested place in Maps');
 return {gate,actions,reviews,advance:ms=>now+=ms};
}
test('a mutation cannot happen before the current screenshot is viewed',async t=>{
 const {gate,actions}=fixture(t);const r=await gate.execute({action:'set_text',text:'somewhere'});
 assert.equal(r.status,'visual_check_required');assert.equal(actions.length,0);
 assert.equal(gate.saw(r.visualCheck.imagePath),true);
 const next=await gate.execute({action:'set_text',text:'somewhere'});
 assert.equal(actions.length,1);assert.ok(next.visualCheck.imagePath);
 const blocked=await gate.execute({action:'ime_enter'});
 assert.equal(blocked.status,'visual_check_required');assert.equal(actions.length,1);
 gate.saw(next.visualCheck.imagePath);await gate.execute({action:'ime_enter'});assert.equal(actions.length,2);
});
test('expired and unrelated pictures cannot authorize a UI action',async t=>{
 const {gate,actions,advance}=fixture(t);let r=await gate.execute({action:'home'});gate.saw(r.visualCheck.imagePath);advance(31000);
 r=await gate.execute({action:'home'});assert.equal(r.status,'visual_check_required');assert.equal(actions.length,0);
 assert.equal(gate.saw('/not/a/phone/image.jpg'),false);
});
test('a post-action capture failure never repeats that action',async t=>{
 const {gate,actions}=fixture(t,{failAfterAction:true});const first=await gate.execute({action:'tap'});gate.saw(first.visualCheck.imagePath);
 const r=await gate.execute({action:'tap'});assert.equal(actions.length,1);assert.equal(r.verificationRequired,true);assert.equal(r.visualCheck.status,'unavailable');
});
test('final claims need independent current-screen evidence and keep corrections',async t=>{
 const {gate,reviews}=fixture(t,{review:{status:'continue',summary:'Search field only',evidence:['keyboard open'],nextSteps:['Submit search']}});
 gate.active=true;gate.correct('No, nothing happened');
 const r=await gate.assess('The map is displayed',{model:'chosen-model',effort:'medium'});
 assert.equal(r.status,'continue');assert.match(r.prompt,/NOT complete/);
 assert.equal(reviews[0].request,'Show the requested place in Maps');assert.deepEqual(reviews[0].corrections,['No, nothing happened']);
 assert.equal(reviews[0].route.model,'chosen-model');assert.ok(reviews[0].frame.imageBase64);
});
test('app-tool-only work bypasses screenshot overhead',async t=>{
 const {gate,reviews}=fixture(t);const r=await gate.assess('Calculation complete',{});
 assert.equal(r.status,'bypass');assert.equal(reviews.length,0);assert.equal(gate.frame,null);
});
test('parallel attempts cannot reuse one viewed screenshot',async t=>{
 const {gate,actions}=fixture(t);const r=await gate.execute({action:'tap'});gate.saw(r.visualCheck.imagePath);
 const results=await Promise.all([gate.execute({action:'tap'}),gate.execute({action:'tap'})]);
 assert.equal(actions.length,1);assert.equal(results[1].status,'visual_check_required');
});
test('expired viewed evidence can refresh automatically only for an identical current image',async t=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'visual-refresh-'));
 t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 let now=0,captures=0,actions=0;
 const imageBase64=Buffer.from([255,216,3]).toString('base64');
 const gate=new VisualVerification({directory,enabled:true,now:()=>now,phone:async c=>{
  if(c.action==='screenshot'){captures++;return {status:'ok',imageBase64,capturedAt:now};}
  actions++;return {status:'action_accepted'};
 }});
 const first=await gate.execute({action:'scroll'});gate.saw(first.visualCheck.imagePath);now=31000;
 const r=await gate.execute({action:'scroll'});
 assert.equal(r.status,'action_accepted');assert.equal(actions,1);assert.equal(captures,3);
 assert.equal(r.visualCheck.seen,false,'post-action screen still needs review');
});
