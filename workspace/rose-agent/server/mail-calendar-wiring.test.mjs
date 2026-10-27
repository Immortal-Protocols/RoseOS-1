import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {loadAppToolCatalog} from './app-tool-catalog.mjs';

const catalogs = {
 'dev.pixel.mail': {status:'ok',tools:JSON.parse(fs.readFileSync(new URL('../../pixel-thunderbird/app-thunderbird/src/main/assets/agent-tools.json',import.meta.url)))},
 'org.fossify.calendar.pixel': {status:'ok',tools:JSON.parse(fs.readFileSync(new URL('../../pixel-fossify-calendar/app/src/main/assets/agent-tools.json',import.meta.url)))},
};
function fixture(result={status:'verified'},opening={status:'opened',foregroundVerified:true}) {
 const commands=[];
 return {
  commands,
  create:()=>loadAppToolCatalog({packages:Object.keys(catalogs),catalogs,phone:async request=>{
   commands.push(request);
   return request.action==='open_app'?{...opening,packageName:request.packageName}:result;
  }}),
 };
}
test('mail and calendar register their complete native catalogs without losing either app',async()=>{
 const f=fixture(),tools=await f.create();
 assert.equal(tools.definitions.length,16);
 assert.ok(tools.definitions.some(x=>x.name==='mail_send_status'));
 assert.ok(tools.definitions.some(x=>x.name==='calendar_sync'));
 assert.deepEqual(f.commands,[]);
});
test('exact message and event opens require verified foreground before the app operation',async()=>{
 for(const [tool,args,pkg] of [
  ['mail_open_message',{accountId:'account',folderId:2,messageId:3},'dev.pixel.mail'],
  ['calendar_open_event',{eventId:4},'org.fossify.calendar.pixel'],
 ]){
  const f=fixture(),tools=await f.create();
  const request={threadId:'test',callId:tool,tool,arguments:args};
  assert.equal((await tools.handleToolCall(request)).success,true);
  await tools.handleToolCall(request);
  assert.deepEqual(f.commands.map(x=>[x.action,x.packageName]),[['open_app',pkg],['app_command',pkg]]);
  assert.deepEqual(f.commands[1].params,args);
 }
});
test('failed opening never calls the provider, and pending writes never claim success',async()=>{
 const failed=fixture({status:'verified'},{status:'launch_unverified',foregroundVerified:false});
 const first=await failed.create();
 assert.equal((await first.handleToolCall({threadId:'t',callId:'open',tool:'calendar_open_event',arguments:{eventId:4}})).success,false);
 assert.deepEqual(failed.commands.map(x=>x.action),['open_app']);
 const pending=fixture({status:'pending',operationId:'pending-operation'}),second=await pending.create();
 assert.equal((await second.handleToolCall({threadId:'t',callId:'send',tool:'mail_send_message',arguments:{accountId:'account',to:'test@example.invalid',subject:'Test',body:'Test'}})).success,false);
 assert.equal(pending.commands.length,1);
 assert.equal(pending.commands[0].operation,'mail_send_message');
});
