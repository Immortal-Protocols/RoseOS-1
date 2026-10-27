import test from 'node:test';
import assert from 'node:assert/strict';
import {cleanPhoneEnvironment,createEnvironmentTools} from './environment-tools.mjs';
import {requestAppContext} from './phone-app-context.mjs';
const snapshot=(now=100000)=>({status:'ok',observedAt:now,clock:{localDate:'2026-10-17',weekday:'Saturday',localTime:'21:10:00',timeZone:'America/Los_Angeles',utcOffset:'-07:00'},location:{status:'available',latitude:33.9,longitude:-118.4,accuracyMeters:12,ageSeconds:10,observedAt:now-10000,neighborhood:'Venice',city:'Los Angeles',region:'California',country:'United States'}});
test('tool reads fresh phone facts once per call, without app navigation or cached backend answers',async()=>{
 let now=100000;const calls=[];
 const tools=createEnvironmentTools({now:()=>now,phone:async command=>{calls.push(command);return snapshot(now);}});
 assert.equal(calls.length,0);
 const first=await tools.handleToolCall({tool:'get_phone_environment',arguments:{}});
 now+=2000;const next=await tools.handleToolCall({tool:'get_phone_environment',arguments:{}});
 assert.deepEqual(calls,[{action:'environment'},{action:'environment'}]);
 assert.equal(first.success,true);assert.equal(next.success,true);
 assert.equal(JSON.parse(next.contentItems[0].text).observedAt,now);
 assert.equal(JSON.parse(first.contentItems[0].text).location.neighborhood,'Venice');
});
test('clock survives missing permission; stale or invalid coordinates never become current',()=>{
 for(const status of ['permission_required','unavailable','disabled']){const s=snapshot();s.location={status};assert.equal(cleanPhoneEnvironment(s,100000).location.status,status);assert.equal(cleanPhoneEnvironment(s,100000).clock.weekday,'Saturday');}
 const old=snapshot(400001);old.location.observedAt=1;old.location.ageSeconds=1;assert.equal(cleanPhoneEnvironment(old,400001).location.status,'stale');
 for(const patch of [{latitude:100},{longitude:200},{accuracyMeters:-1},{observedAt:Infinity},{ageSeconds:-1}]){const s=snapshot();Object.assign(s.location,patch);assert.deepEqual(cleanPhoneEnvironment(s,100000).location,{status:'unavailable'});}
 const s=snapshot();s.location.street='Never invent or copy this';s.location.secret='discard';const result=cleanPhoneEnvironment(s,100000);assert.equal(result.location.street,undefined);assert.equal(result.location.secret,undefined);
});
test('old client environments are stripped from normal requests',()=>{
 const result=requestAppContext({...snapshot(),foregroundApp:{packageName:'dev.pixel.books',label:'Books'},environment:snapshot()},100000);
 assert.equal(result.value.environment,undefined);assert.doesNotMatch(result.text,/Venice|21:10|latitude|environment|location/);
});
test('tool errors are explicit and cannot silently return historical data',async()=>{
 const tools=createEnvironmentTools({phone:async()=>{throw Error('Phone offline');}});
 const error=await tools.handleToolCall({tool:'get_phone_environment',arguments:{}});assert.equal(error.success,false);assert.match(error.contentItems[0].text,/Phone offline/);
 const bad=await tools.handleToolCall({tool:'get_phone_environment',arguments:{city:'guess'}});assert.equal(bad.success,false);
 assert.throws(()=>cleanPhoneEnvironment(snapshot(1),100000),/Fresh/);
});
