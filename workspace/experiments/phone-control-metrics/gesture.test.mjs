import test from 'node:test';
import assert from 'node:assert/strict';
import {gestureSummary} from './gesture.mjs';
import {PhoneMetrics,observationSummary} from './metrics.mjs';
const id='11111111-1111-1111-1111-111111111111';
const sample=()=>({id,commandId:'22222222-2222-2222-2222-222222222222',revision:1,action:'touch_label',phase:'completed',elapsedMs:76,
 target:{viewId:'com.example:id/search',className:'android.widget.Button',bounds:[0,0,80,80],text:'PRIVATE CONTACT',description:'PRIVATE CONTACT'},
 before:{packageName:'com.example',screenHash:'a'.repeat(64),text:'PRIVATE CONTACT'},
 observations:[{afterMs:120,screenHash:'b'.repeat(64),text:'PRIVATE CONTACT'}],label:'PRIVATE CONTACT'});
test('gesture diagnostics retain timing and control identity but drop private content',()=>{
 const result=gestureSummary(sample());assert.equal(result.phase,'completed');assert.equal(result.elapsedMs,76);
 assert.equal(result.target.viewId,'com.example:id/search');assert.equal(result.observations[0].afterMs,120);
 assert.ok(!JSON.stringify(result).includes('PRIVATE CONTACT'));
});
test('malformed diagnostics are rejected or stripped without changing action results',()=>{
 assert.equal(gestureSummary({...sample(),phase:'success'}),null);
 assert.equal(gestureSummary({...sample(),revision:-1}),null);
 assert.equal(gestureSummary({...sample(),id:'bad'}),null);
 const result=gestureSummary({...sample(),elapsedMs:Infinity,points:[-1,0],target:{viewId:'private text',bounds:[0,NaN,1,2]}});
 assert.equal(result.elapsedMs,undefined);assert.equal(result.points,undefined);assert.deepEqual(result.target,{});
});
test('late callback and observation revisions reach the Mac once each',()=>{
 const rows=[],m=new PhoneMetrics({write:r=>rows.push(r)}),g=sample();
 for(let i=0;i<3;i++){const c={id:'inspect-'+i,action:'inspect_ui'};m.created(c);m.completed({...c,status:'completed',result:{status:'ok',gestureDiagnostics:[g]}});}
 assert.equal(rows.filter(r=>r.kind==='gesture_diagnostic').length,1);
 g.revision++;g.phase='cancelled';const c={id:'inspect-next',action:'inspect_ui'};m.created(c);m.completed({...c,status:'completed',result:{status:'ok',gestureDiagnostics:[g]}});
 assert.equal(rows.filter(r=>r.kind==='gesture_diagnostic').length,2);
 assert.equal(rows.filter(r=>r.kind==='gesture_diagnostic').at(-1).phase,'cancelled');
});
test('diagnostic changes do not make an unchanged screen look different',()=>{
 const s={status:'ok',packageName:'com.example',nodes:[],gestureDiagnostics:[sample()]};
 const first=observationSummary(s).screenHash;s.gestureDiagnostics[0].revision++;
 assert.equal(observationSummary(s).screenHash,first);
});
