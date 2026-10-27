import test from 'node:test';
import assert from 'node:assert/strict';
import {BackgroundPhone} from './background-phone.mjs';
import {loadAppToolCatalog} from './app-tool-catalog.mjs';
import {VisualVerification} from './visual-verification.mjs';
import {taskSetupPolicy} from './task-setup.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function fixture(){
 const calls=[];let selected=null;
 const send=async c=>{calls.push(c);
  if(c.action!=='app_command'||c.packageName!=='dev.pixel.backgroundcontrol')return {status:'ok',physical:true};
  if(c.operation==='get_state')return selected?{status:'ready',targetPackage:selected,sessionId:'session-a'}:{status:'stopped'};
  if(c.operation==='start'){selected=c.params.packageName;return {status:'ready',targetPackage:selected,sessionId:'session-a'};}
  if(c.operation==='stop'){selected=null;return {status:'stopped'};}
  if(c.operation==='command')return {status:'ok',packageName:selected,snapshotId:'snapshot-a',nodes:[]};
  throw Error('unexpected operation');};
 return {calls,phone:new BackgroundPhone({send,enabled:()=>true})};
}
test('open and subsequent UI operations use background provider, never physical UI',async()=>{
 const {calls,phone}=fixture();const opened=await phone.execute({action:'open_app',packageName:'com.google.android.apps.maps'});
 assert.equal(opened.backgroundVerified,true);assert.equal(opened.foregroundVerified,false);
 await phone.execute({action:'tap_label',snapshotId:'snapshot-a',label:'Directions'});
 assert.equal(calls.every(c=>c.action==='app_command'&&c.packageName==='dev.pixel.backgroundcontrol'),true);
 assert.equal(calls.at(-1).params.label,'Directions');assert.equal(calls.at(-1).params.sessionId,'session-a');
});
test('explicit phone-display observations and actions retain the background workspace',async()=>{
 const {calls,phone}=fixture();await phone.execute({action:'open_app',packageName:'maps'});
 const before=phone.state().session;calls.length=0;
 const seen=await phone.execute({action:'screenshot',display:'phone'});
 assert.equal(seen.scope,'phone_display');assert.equal(seen.backgroundExecution,false);
 // The held workspace is checked for an undetected owner Stop before touching the phone screen.
 assert.deepEqual(calls.filter(c=>c.operation!=='get_state'),[{action:'screenshot'}]);
 await phone.execute({action:'screen_gesture',display:'phone',screenshotId:'phone-frame',points:[12,34]});
 assert.deepEqual(calls.at(-1),{action:'screen_gesture',screenshotId:'phone-frame',points:[12,34]});
 assert.deepEqual(phone.state().session,before);
});
test('invalid display names do not dispatch commands',async()=>{
 const {calls,phone}=fixture();
 await assert.rejects(phone.execute({action:'screenshot',display:'unknown'}),/Unknown display/);
 assert.deepEqual(calls,[]);
});
test('unknown native UI operations fail before dispatch; audited headless operations remain available',async()=>{
 const {calls,phone}=fixture();
 const rejected=await phone.execute({action:'app_command',packageName:'dev.pixel.voice',operation:'open_panel'});
 assert.equal(rejected.nativeOperationDispatched,false);assert.equal(calls.length,0);
 await phone.execute({action:'app_command',packageName:'dev.pixel.calculator.agent',operation:'evaluate',params:{expression:'2+2'}});
 assert.equal(calls.length,1);
});
test('bare back observes the background app before scoped navigation',async()=>{
 const {calls,phone}=fixture();await phone.execute({action:'open_app',packageName:'maps'});calls.length=0;
 await phone.execute({action:'back'});
 assert.deepEqual(calls.map(c=>c.params.action),['inspect_ui','back']);
 assert.equal(calls[1].params.snapshotId,'snapshot-a');assert.equal(calls[1].params.sessionId,'session-a');
});
test('bare back never navigates when the background observation fails',async()=>{
 const calls=[];const phone=new BackgroundPhone({enabled:()=>true,send:async c=>{calls.push(c);return c.operation==='get_state'?{status:'ready',targetPackage:'maps',sessionId:'session-a'}:{status:'unavailable'};}});
 const result=await phone.execute({action:'back'});
 assert.equal(result.actionExecuted,false);assert.equal(calls.some(c=>c.params?.action==='back'),false);
});
test('missing session and unsupported commands never fall back to foreground',async()=>{
 const {calls,phone}=fixture();
 assert.equal((await phone.execute({action:'screenshot'})).status,'no_background_app');
 assert.equal((await phone.execute({action:'apple_music_run',title:'a'})).status,'not_executed');
 assert.equal((await phone.execute({action:'migration_backup_open'})).status,'not_executed');
 assert.equal(calls.every(c=>c.action==='app_command'),true);
});
test('cleanup closes orphaned sessions after a server restart',async()=>{
 const {calls,phone}=fixture();await phone.close();assert.equal(calls.at(-1).operation,'stop');
});
test('reopening the same installer-blocked app preserves its session until approval completes',async()=>{
 const calls=[];let blocked=true;
 const phone=new BackgroundPhone({enabled:()=>true,send:async c=>{
  calls.push(c);
  if(c.operation==='get_state')return {status:blocked?'blocked':'ready',blocked,targetPackage:'com.android.vending',sessionId:'retained-session'};
  if(c.operation==='command')return {status:'ok',packageName:'com.android.vending',snapshotId:'after-install',nodes:[]};
  throw Error('Unexpected session replacement or physical action');
 }});
 for(let i=0;i<2;i++){
  const result=await phone.execute({action:'open_app',packageName:'com.android.vending'});
  assert.equal(result.status,'background_blocked');assert.equal(result.actionExecuted,false);
  assert.equal(result.sessionRetained,true);assert.equal(result.sessionId,'retained-session');
 }
 assert.equal(phone.state().session.sessionId,'retained-session');
 assert.deepEqual(calls.map(c=>c.operation),['get_state','get_state']);
 blocked=false;
 const resumed=await phone.execute({action:'open_app',packageName:'com.android.vending'});
 assert.equal(resumed.status,'opened');assert.equal(resumed.sessionId,'retained-session');
 assert.equal(calls.some(c=>c.operation==='start'||c.operation==='stop'),false);
 assert.equal(calls.every(c=>c.action==='app_command'&&c.packageName==='dev.pixel.backgroundcontrol'),true);
});
test('an installer window in the same workspace accepts its freshly observed control',async()=>{
 const calls=[];
 const node={id:1,nodeId:1,packageName:'com.android.packageinstaller',className:'Button',text:'Install',bounds:[10,10,80,40],clickable:true};
 const phone=new BackgroundPhone({enabled:()=>true,send:async c=>{
  calls.push(c);
  if(c.operation==='get_state')return {status:'ready',targetPackage:'com.android.vending',sessionId:'workspace'};
  if(c.params.action==='inspect_ui')return {status:'ok',packageName:'com.android.packageinstaller',sessionId:'workspace',snapshotId:'installer',nodes:[node]};
  return {status:'action_accepted',sessionId:'workspace'};
 }});
 await phone.execute({action:'inspect_ui'});
 const result=await phone.execute({action:'tap',snapshotId:'installer',nodeId:1});
 assert.equal(result.status,'action_accepted');
 assert.equal(calls.at(-1).params.sessionId,'workspace');
 assert.equal(calls.at(-1).params.nodeId,1);
 assert.equal(calls.some(c=>c.operation==='start'||c.operation==='stop'),false);
});
test('consent requirement is explicit and no app navigation follows it',async()=>{
 const calls=[];const phone=new BackgroundPhone({enabled:()=>true,send:async c=>{calls.push(c);return c.operation==='start'?{status:'consent_required'}:{status:'stopped'};}});
 const r=await phone.execute({action:'open_app',packageName:'maps'});
 assert.equal(r.status,'background_consent_required');assert.equal(r.actionExecuted,false);
 assert.equal(calls.some(c=>c.operation==='command'),false);
});
test('home closes only the background session; disabled mode preserves normal routing',async()=>{
 const {calls,phone}=fixture();await phone.execute({action:'open_app',packageName:'maps'});
 assert.equal((await phone.execute({action:'home'})).workspaceClosed,true);
 assert.equal(calls.at(-1).operation,'stop');assert.equal(phone.state().session,null);
 const old=new BackgroundPhone({enabled:()=>false,send:async c=>({original:c})});
 assert.deepEqual(await old.execute({action:'home'}),{original:{action:'home'}});
});
test('registered app tools accept a verified background launch without claiming foreground',async()=>{
 const calls=[];
 const catalog=await loadAppToolCatalog({phone:async c=>{calls.push(c);return {status:'opened',packageName:'maps',backgroundVerified:true,backgroundExecution:true,foregroundVerified:false};},packages:[],adapters:[{packageName:'com.test.maps',tools:[{name:'maps_test',description:'test',inputSchema:{type:'object',properties:{}}}],execute:async()=>({status:'ok'})}]});
 // Match the adapter package; a different observed package must still fail.
 let result=await catalog.handleToolCall({threadId:'a',callId:'b',tool:'maps_test',arguments:{}});
 assert.equal(result.success,false);
 const valid=await loadAppToolCatalog({phone:async()=>({status:'opened',packageName:'com.test.maps',backgroundVerified:true,backgroundExecution:true,foregroundVerified:false}),packages:[],adapters:[{packageName:'com.test.maps',tools:[{name:'maps_test',description:'test',inputSchema:{type:'object',properties:{}}}],execute:async()=>({status:'ok'})}]});
 result=await valid.handleToolCall({threadId:'a',callId:'c',tool:'maps_test',arguments:{}});
 assert.equal(result.success,true);const body=JSON.parse(result.contentItems[0].text);assert.equal(body.backgroundVerified,true);assert.notEqual(body.foregroundVerified,true);
});
test('initial background app launch does not demand a screenshot of the user foreground',async()=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'background-visual-'));const calls=[];
 try{
  const guard=new VisualVerification({enabled:true,background:true,directory,review:async()=>({}),phone:async c=>{calls.push(c.action);return c.action==='screenshot'?{status:'ok',imageBase64:Buffer.from([255,216,255,217]).toString('base64')}:{status:'opened',backgroundVerified:true};}});
  const r=await guard.execute({action:'open_app',packageName:'maps'});
  assert.equal(r.backgroundVerified,true);assert.deepEqual(calls,['open_app','screenshot']);
 }finally{fs.rmSync(directory,{recursive:true,force:true});}
 assert.match(taskSetupPolicy({backgroundEnabled:true}),/separate background (display|workspace)/);
});
function lockedFixture(responses){
 const calls=[];
 const send=async c=>{calls.push(c);const handler=responses[c.operation];if(!handler)throw Error('unexpected operation '+c.operation);return typeof handler==='function'?handler(c,calls):handler;};
 return {calls,phone:new BackgroundPhone({send,enabled:()=>true})};
}
test('locked start refused by Rose Agent reports locked without retrying or touching the phone screen',async()=>{
 const refusal={status:'locked',reason:'owner_unlock_required',lockedControl:'authentication_expired',message:'Unlock the phone with your PIN or strong biometric to renew app control while locked.',actionExecuted:false};
 const {calls,phone}=lockedFixture({get_state:{status:'stopped'},start:refusal});
 const result=await phone.execute({action:'open_app',packageName:'com.example.notes'});
 assert.equal(result.status,'locked');assert.equal(result.actionExecuted,false);assert.equal(result.lockedControl,'authentication_expired');
 assert.equal(result.reason,refusal.message);assert.equal(result.packageName,'com.example.notes');assert.equal(phone.state().session,null);
 assert.deepEqual(calls.map(c=>c.operation),['get_state','start']);
 assert.equal(calls.every(c=>c.action==='app_command'),true);
});
test('Android refusing a locked session is reported as an OS refusal',async()=>{
 const {phone}=lockedFixture({get_state:{status:'stopped'},start:{status:'rejected',reason:'device_locked',error:'Android session error 2',deviceLocked:true}});
 const result=await phone.execute({action:'open_app',packageName:'com.example.notes'});
 assert.equal(result.status,'locked');assert.equal(result.lockedControl,'os_refused');assert.equal(result.details.error,'Android session error 2');
});
test('a session blocked at an owner gate while locked is retained and explained',async()=>{
 const {calls,phone}=lockedFixture({get_state:{status:'blocked',blocked:true,reason:'owner_unlock_required',deviceLocked:true,targetPackage:'com.android.vending',sessionId:'kept'}});
 const result=await phone.execute({action:'open_app',packageName:'com.android.vending'});
 assert.equal(result.status,'background_blocked');assert.equal(result.ownerUnlockRequired,true);assert.equal(result.sessionRetained,true);
 assert.deepEqual(calls.map(c=>c.operation),['get_state']);
});
test('locked results during a session keep the workspace and never fall back to the phone screen',async()=>{
 let locked=false;
 const {calls,phone}=lockedFixture({
  get_state:{status:'stopped'},start:{status:'ready',targetPackage:'com.example.notes',sessionId:'s1'},
  command:c=>locked?{status:'locked',reason:'owner_unlock_required',lockedControl:'not_enabled',message:'App control while locked is off.'}:{status:'ok',packageName:'com.example.notes',snapshotId:'snap-1',nodes:[{nodeId:3,text:'Save',clickable:true,bounds:[0,0,10,10]}]}});
 await phone.execute({action:'open_app',packageName:'com.example.notes'});
 locked=true;
 const tapped=await phone.execute({action:'tap',snapshotId:'snap-1',nodeId:3});
 assert.equal(tapped.status,'locked');assert.equal(tapped.lockedControl,'not_enabled');assert.equal(tapped.sessionId,'s1');
 const shot=await phone.execute({action:'screenshot'});
 assert.equal(shot.status,'locked');assert.equal(shot.actionExecuted,false);
 const back=await phone.execute({action:'back'});
 assert.equal(back.status,'locked');
 assert.deepEqual(phone.state().session,{sessionId:'s1',targetPackage:'com.example.notes'});
 assert.equal(calls.every(c=>c.action==='app_command'&&c.packageName==='dev.pixel.backgroundcontrol'),true);
});
test('a blocked command is reported as locked only when the controller says the owner is needed',async()=>{
 let ownerGate=true;
 const {phone}=lockedFixture({
  get_state:()=>ownerGate?{status:'blocked',blocked:true,reason:'owner_unlock_required',sessionId:'s1',targetPackage:'com.example.notes'}:{status:'blocked',blocked:true,reason:'',sessionId:'s1',targetPackage:'com.example.notes'},
  command:{status:'error',error:'java.lang.IllegalStateException: Background session is not ready: blocked'}});
 phone.session={sessionId:'s1',targetPackage:'com.example.notes'};
 const gated=await phone.execute({action:'screenshot'});
 assert.equal(gated.status,'locked');assert.equal(gated.lockedControl,'owner_gate');
 ownerGate=false;
 const other=await phone.execute({action:'screenshot'});
 assert.equal(other.status,'error');
});
test('reusing a ready session while locked reports locked instead of an unverified launch',async()=>{
 const {calls,phone}=lockedFixture({get_state:{status:'ready',targetPackage:'com.example.notes',sessionId:'s1'},command:{status:'locked',reason:'owner_unlock_required',lockedControl:'authentication_expired',message:'Unlock the phone to renew app control while locked.'}});
 const result=await phone.execute({action:'open_app',packageName:'com.example.notes'});
 assert.equal(result.status,'locked');assert.equal(result.lockedControl,'authentication_expired');assert.equal(result.sessionId,'s1');
 assert.deepEqual(calls.map(c=>c.operation),['get_state','command']);
});
