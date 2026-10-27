import test from 'node:test';
import assert from 'node:assert/strict';
import {ActionLoops} from './action-loops.mjs';
const frame=text=>({packageName:'app',snapshotId:Math.random(),nodes:[{text,bounds:[0,0,100,100]}]});
test('detects repeated Home despite fresh snapshot IDs',()=>{
 const loops=new ActionLoops();loops.record(frame('Home'),{action:'home'});assert.equal(loops.detect(frame('Home')),null);
 loops.record(frame('Home'),{action:'home'});assert.equal(loops.detect(frame('Home')).repeats,2);
});
test('detects a repeated two-screen cycle',()=>{
 const loops=new ActionLoops();for(let i=0;i<2;i++){loops.record(frame('A'),{action:'back'});loops.record(frame('B'),{action:'home'});}
 assert.equal(loops.detect(frame('A')).actions.length,2);
});
test('repeated cart deletion is progress when observed items change',()=>{
 const loops=new ActionLoops();loops.record(frame('3 items'),{action:'screen_gesture',screenshotId:'1',points:[50,50]});loops.record(frame('2 items'),{action:'screen_gesture',screenshotId:'2',points:[50,50]});
 assert.equal(loops.detect(frame('1 item')),null);
});
test('fresh screenshot IDs do not hide repeated ineffective coordinate taps',()=>{
 const loops=new ActionLoops();loops.record(frame('A'),{action:'screen_gesture',screenshotId:'1',points:[50,50]});loops.record(frame('A'),{action:'screen_gesture',screenshotId:'2',points:[50,50]});
 assert.ok(loops.detect(frame('A')));
});
