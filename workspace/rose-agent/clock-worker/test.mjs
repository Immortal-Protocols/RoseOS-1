import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {validateClockParams} from './commands.mjs';
import {PhoneCommands} from '../server/phone.mjs';
import {appWorkerPolicy} from '../voice-providers/app-worker-policy.mjs';
const require=createRequire(import.meta.url),resolve=require('../voice-providers/voice-commands.js'),createTool=require('./clock-tool.js');
test('flag gates only expanded Clock actions',()=>{
 const p=new PhoneCommands(()=>100,()=>false,()=>false);
 assert.throws(()=>p.create({action:'clock_control',params:{operation:'stopwatch_start'}}));
 assert.equal(p.create({action:'clock_timer_start',seconds:20}).action,'clock_timer_start');
 assert.equal(new PhoneCommands(()=>100,()=>false,()=>true).create({action:'clock_control',params:{operation:'stopwatch_start'}}).params.operation,'stopwatch_start');
});
test('parameters reject malformed or ambiguous model output before dispatch',()=>{
 for(const p of [{operation:'delete_all'},{operation:'alarm_create',hour:7},{operation:'alarm_create',hour:25,minute:2},{operation:'timer_add',seconds:-2},{operation:'timer_rename'},{operation:'alarm_update',alarmId:2},{operation:'world_time',city:''},{operation:'alarm_create',hour:7,minute:5,days:[1,1]},{operation:'stopwatch_start',alarmId:1}])assert.throws(()=>validateClockParams(p),JSON.stringify(p));
 assert.deepEqual(validateClockParams({operation:'alarm_create',hour:7,minute:0,days:[1,2,3,4,5]}).days,[1,2,3,4,5]);
});
test('routing is explicit, gated, and leaves music/book/podcast pause alone',()=>{
 assert.equal(resolve('start the stopwatch',{}),null);
 assert.equal(resolve('start the stopwatch',{clock:true}).params.operation,'stopwatch_start');
 assert.equal(resolve('stop the timer',{clock:true}).params.operation,'timer_stop');
 assert.equal(resolve('stop the beeping',{clock:true}).params.operation,'timer_silence');
 assert.equal(resolve('pause the timer',{clock:true}).params.operation,'timer_pause');
 assert.equal(resolve('pause',{clock:true,book:true}).name,'book_control');
 assert.equal(resolve('pause',{clock:true,podcast:true}).name,'podcast_control');
 assert.equal(resolve('pause music',{clock:true,media:true}).name,'media_control');
 assert.equal(resolve('set an alarm for seven',{clock:true}),null);
});
test('tool sends a single command and does not replay uncertainty',async()=>{
 let writes=0,reads=0;
 const tool=createTool({api:async(method)=>{if(method==='POST'){writes++;return{id:'a',status:'pending'}}reads++;return{status:'completed',result:{status:'uncertain'}}},delay:async()=>{}});
 assert.equal(JSON.parse(await tool({operation:'stopwatch_lap'})).status,'uncertain');assert.equal(writes,1);assert.equal(reads,1);
});
test('policy enumerates capabilities only when enabled',()=>{
 assert.ok(!appWorkerPolicy('/x').includes('Expanded Pixel Clock controls are enabled'));
 const p=appWorkerPolicy('/x',{clock:true});for(const s of ['stopwatch_lap','timer_pause','alarm_create','world_time','uncertain','current local timezone'])assert.ok(p.includes(s),s);
});
