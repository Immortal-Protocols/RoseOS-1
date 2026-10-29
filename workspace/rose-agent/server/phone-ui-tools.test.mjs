// APP-BUG-OPEN-002: general phone navigation and input through registered, request-bound
// tools. The real tool module, BackgroundPhone, PhoneCommands, TaskStop and LauncherTask run
// with server.mjs's own origin rules, fastPhone, rawFastPhone and handleToolCall wrapper
// (sliced from the source). Only the phone device and the background controller are fakes.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {AsyncLocalStorage} from 'node:async_hooks';
import {TaskStop} from './task-stop.mjs';
import {LauncherTask} from './launcher-task.mjs';
import {PhoneCommands} from './phone.mjs';
import {BackgroundPhone} from './background-phone.mjs';
import {createPhoneUiTools,phoneUiToolDefinitions} from './phone-ui-tools.mjs';
import {VisualVerification,genericUiActions} from './visual-verification.mjs';

const server=fs.readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
const line=from=>{const start=server.indexOf(from);assert.ok(start>0,from);return server.slice(start,server.indexOf('\n',start));};
const between=(from,to)=>{const start=server.indexOf(from),end=server.indexOf(to,start);assert.ok(start>0&&end>start,from);return server.slice(start,end);};
const source=[line('const phoneOrigin='),line('const readOnlyPhoneActions='),between('function legacyReadPhone(','function logDecision('),
 line('const phoneUiTools=createPhoneUiTools('),line('{const handle=appRuntime.handleToolCall;appRuntime.handleToolCall=')].join('\n');
const stopWiringSource=(()=>{const start=server.indexOf('cancelQueued:',server.indexOf('const taskStop=new TaskStop({'));return server.slice(start,server.indexOf(',record:',start));})();
const stopWiring=globals=>vm.runInNewContext('({'+stopWiringSource+'})',{...globals,Error,String});
// The visual gate's phone function exactly as server.mjs constructs it.
const visualPhoneSource=(()=>{const start=server.indexOf('const visualGuard=new VisualVerification({phone:')+'const visualGuard=new VisualVerification({phone:'.length;return server.slice(start,server.indexOf(',directory:',start));})();
const jpeg=tag=>Buffer.from([0xff,0xd8,0xff,0xe0,...Buffer.from(tag)]).toString('base64');
const PHONE_JPEG=jpeg('physical-phone'),WORKSPACE_JPEG=jpeg('workspace');
const SNAPSHOT='0f0e0d0c-0b0a-4908-8706-050403020100',SHOT='1f1e1d1c-1b1a-4918-9716-151413121110';
const settle=()=>new Promise(r=>setImmediate(r));

// mode 'foreground': commands go to the paired phone's queue. mode 'background': through BackgroundPhone.
function fixture({background=false,visual=false}={}){
 const launcher=new LauncherTask(path.join(fs.mkdtempSync(path.join(os.tmpdir(),'phone-ui-')),'task.json'));
 const phone=new PhoneCommands(),delivered=[],controller=[];let deviceHold=null,controllerHold=null;
 // The paired phone collects pending commands and answers; a held command waits until released.
 const device=setInterval(()=>{for(const c of phone.pending()){if(c.taken||deviceHold)continue;c.taken=true;delivered.push(c);
  const reply=c.action==='open_app'?{status:'opened',packageName:c.packageName,foregroundVerified:true}:c.action==='app_command'?controllerReply(c):c.action==='screenshot'?{status:'ok',mimeType:'image/jpeg',imageBase64:PHONE_JPEG}:{status:'action_accepted'};
  Promise.resolve(reply).then(r=>phone.complete(c.id,r));}},5);
 const controllerReply=c=>{controller.push({operation:c.operation,...c.params});
  if(controllerHold&&c.operation==='command')return controllerHold.then(()=>({status:'action_accepted'}));
  if(c.operation==='get_state')return controller.some(x=>x.operation==='start')?{status:'ready',sessionId:'s1',targetPackage:'com.openai.chatgpt'}:{status:'idle'};
  if(c.operation==='start')return {status:'ready',sessionId:'s1',targetPackage:c.params.packageName};
  if(c.operation==='command'&&c.params.action==='screenshot')return {status:'ok',mimeType:'image/jpeg',imageBase64:WORKSPACE_JPEG,sessionId:'s1'};
  if(c.operation==='command'&&c.params.action==='inspect_ui')return {status:'ok',packageName:'com.openai.chatgpt',snapshotId:SNAPSHOT,nodes:[{id:3,nodeId:3,text:'Message',clickable:true,bounds:[0,0,100,100]}],sessionId:'s1'};
  if(c.operation==='stop')return {status:'stopped',stopReason:'agent'};
  return {status:'action_accepted'};};
 const context=vm.createContext({backgroundEnabled:background,AsyncLocalStorage,Object,Set,Error,Promise,JSON,setTimeout,
  checkPausedApp:()=>{},measurePhone:()=>{},phonePush:{publish:()=>{}},taskStarted:0,lastRoute:null,phone,appRuntime:{},createPhoneUiTools,
  visualTrial:visual,busy:true,genericUiActions});
 const backgroundPhone=new BackgroundPhone({send:c=>context.rawFastPhone(c),enabled:()=>background,sleep:()=>settle()});
 const taskStop=new TaskStop({task:()=>launcher.current,stopping:d=>launcher.stopping(d),interrupt:async()=>{},abortReview:()=>{},
  // server.mjs's own wiring: Stop latches the workspace and withdraws queued phone commands.
  ...stopWiring({backgroundEnabled:background,backgroundPhone,phone,taskStop:{current:()=>taskStop.current()}})});
 Object.assign(context,{taskStop,backgroundPhone});
 vm.runInContext(source.replace('{const handle=appRuntime.handleToolCall;','{const handle=globalThis.inner;')+';globalThis.fastPhone=fastPhone;globalThis.rawFastPhone=rawFastPhone;globalThis.phoneUiTools=phoneUiTools;',
  Object.assign(context,{inner:request=>context.phoneUiTools.handleToolCall(request)}));
 const reviews=[];
 const visualGuard=context.visualGuard=new VisualVerification({phone:vm.runInContext('('+visualPhoneSource+')',context),directory:fs.mkdtempSync(path.join(os.tmpdir(),'visual-')),
  review:async input=>{reviews.push(input);return {status:'complete',summary:'ok',evidence:[],nextSteps:[]};},enabled:visual,background});
 const newRequest=(text,turn)=>{launcher.start(text);taskStop.begin(launcher.current.id);if(turn)taskStop.bindTurn(turn,launcher.current.id);return launcher.current.id;};
 const call=(turnId,tool,args)=>context.appRuntime.handleToolCall({tool,turnId,arguments:args});
 const read=reply=>JSON.parse(reply.contentItems[0].text);
 return {phone,delivered,controller,taskStop,launcher,newRequest,call,read,visualGuard,reviews,close:()=>clearInterval(device),
  holdDevice:()=>{let release;deviceHold=new Promise(r=>release=r);return ()=>{deviceHold=null;release();};},
  holdController:()=>{let release;controllerHold=new Promise(r=>release=r);return ()=>{controllerHold=null;release();};}};
}
const wire=c=>Object.fromEntries(Object.entries(c).filter(([k,v])=>v!==undefined&&!['id','created','expires','status','taken','result','completed'].includes(k)));

test('every registered phone tool sends the existing native command the paired phone validates',async()=>{
 const f=fixture();
 try{
  f.newRequest('Open ChatGPT and search','turn-A');
  const cases=[
   ['phone_open_app',{packageName:'com.openai.chatgpt'},{action:'open_app',packageName:'com.openai.chatgpt'}],
   ['phone_inspect_ui',{},{action:'inspect_ui'}],
   ['phone_tap_label',{snapshotId:SNAPSHOT,label:'Message'},{action:'tap_label',snapshotId:SNAPSHOT,label:'Message'}],
   ['phone_touch_label',{snapshotId:SNAPSHOT,label:'Send',nodeId:4},{action:'touch_label',snapshotId:SNAPSHOT,label:'Send',nodeId:4}],
   ['phone_tap',{snapshotId:SNAPSHOT,nodeId:3},{action:'tap',snapshotId:SNAPSHOT,nodeId:3}],
   ['phone_long_press',{snapshotId:SNAPSHOT,nodeId:3},{action:'long_press',snapshotId:SNAPSHOT,nodeId:3}],
   ['phone_set_text',{snapshotId:SNAPSHOT,nodeId:3,text:'Hello'},{action:'set_text',snapshotId:SNAPSHOT,nodeId:3,text:'Hello'}],
   ['phone_ime_enter',{snapshotId:SNAPSHOT,nodeId:3},{action:'ime_enter',snapshotId:SNAPSHOT,nodeId:3}],
   ['phone_scroll',{snapshotId:SNAPSHOT,nodeId:3,direction:'forward'},{action:'scroll',snapshotId:SNAPSHOT,nodeId:3,direction:'forward'}],
   ['phone_swipe',{snapshotId:SNAPSHOT,nodeId:3,direction:'left',bounds:[0,100,400,300]},{action:'swipe',snapshotId:SNAPSHOT,nodeId:3,direction:'left',bounds:[0,100,400,300]}],
   ['phone_touch_point',{snapshotId:SNAPSHOT,nodeId:3,x:50,y:60},{action:'touch_point',snapshotId:SNAPSHOT,nodeId:3,x:50,y:60}],
   ['phone_drag',{snapshotId:SNAPSHOT,nodeId:3,points:[10,10,90,90],durationMs:600},{action:'drag',snapshotId:SNAPSHOT,nodeId:3,points:[10,10,90,90],durationMs:600}],
   ['phone_screen_gesture',{screenshotId:SHOT,points:[50,60]},{action:'screen_gesture',screenshotId:SHOT,points:[50,60]}],
   ['phone_type_text',{screenshotId:SHOT,text:'Hi',commit:true},{action:'type_text',screenshotId:SHOT,text:'Hi',commit:true}],
   ['phone_back',{},{action:'back'}],
   ['phone_home',{},{action:'home'}],
  ];
  assert.deepEqual(cases.map(c=>c[0]).sort(),phoneUiToolDefinitions().map(t=>t.name).sort(),'every definition is covered');
  for(const [tool,args,expected] of cases){
   const reply=await f.call('turn-A',tool,args);
   assert.equal(reply.success,true,tool);
   assert.deepEqual(wire(f.delivered.at(-1)),expected,tool+' reaches the phone unchanged, with no task ID in the payload');
  }
  assert.equal(f.delivered.length,cases.length,'each call ran exactly once');
 }finally{f.close();}
});

test('the reported ChatGPT launch on the physical phone runs once as the current request (background workspace enabled)',async()=>{
 const f=fixture({background:true});
 try{
  f.newRequest('Open ChatGPT on my phone','turn-A');
  const reply=await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt',display:'phone'});
  assert.equal(reply.success,true);
  assert.deepEqual(f.delivered.map(wire),[{action:'open_app',packageName:'com.openai.chatgpt'}],'the physical phone gets one plain open_app; no workspace is started');
  assert.equal(f.read(reply).scope,'phone_display');
  assert.deepEqual(f.controller,[]);
 }finally{f.close();}
});

test('without display the launch selects the background workspace, tagged with the turn\'s task across async boundaries',async()=>{
 const f=fixture({background:true});
 try{
  const A=f.newRequest('Open ChatGPT','turn-A');
  const release=f.holdDevice();
  const pending=f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt'});
  for(let i=0;i<5;i++)await settle();
  release();
  const reply=await pending;
  assert.equal(f.read(reply).status,'opened');assert.equal(f.read(reply).backgroundVerified,true);
  const start=f.controller.find(c=>c.operation==='start');
  assert.deepEqual({packageName:start.packageName,taskId:start.taskId},{packageName:'com.openai.chatgpt',taskId:A},'origin survived the queued, delayed send');
  assert.ok(f.controller.filter(c=>['start','command'].includes(c.operation)).every(c=>c.taskId===A));
 }finally{f.close();}
});

test('missing, unregistered, earlier, stopped and spoofed origins never reach the phone',async()=>{
 const f=fixture({background:true});
 try{
  const A=f.newRequest('First request','turn-A');
  const status=async(turn,args={packageName:'com.openai.chatgpt',display:'phone'})=>f.read(await f.call(turn,'phone_open_app',args)).status;
  assert.equal(await status(undefined),'stale_task');
  assert.equal(await status('turn-unknown'),'stale_task');
  await assert.rejects(f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt',taskId:A}),/Unknown argument: taskId/,'a caller cannot name its task');
  f.newRequest('Second request','turn-B');
  assert.equal(await status('turn-A'),'stale_task','a turn of the earlier request');
  await f.taskStop.request({source:'tray'});
  assert.equal(await status('turn-B'),'task_stopped');
  // Stop's own workspace close is the only controller traffic; no launch or start ran.
  assert.deepEqual(f.delivered.filter(c=>!(c.action==='app_command'&&c.operation==='stop')),[]);
  assert.deepEqual(f.controller.map(c=>c.operation),['stop']);
 }finally{f.close();}
});

test('Stop withdraws a phone command queued for the paired phone before it runs (foreground)',async()=>{
 const f=fixture();
 try{
  f.newRequest('Open ChatGPT','turn-A');
  const release=f.holdDevice();
  const pending=f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt'});
  for(let i=0;i<3;i++)await settle();
  assert.equal(f.phone.pending().length,1,'queued for the phone, not yet collected');
  await f.taskStop.request({source:'tray'});release();
  const reply=await pending;
  assert.equal(reply.success,false);assert.equal(f.read(reply).status,'task_stopped');
  assert.deepEqual(f.delivered,[],'the phone never received it');
 }finally{f.close();}
});

test('Stop refuses work queued behind an in-flight workspace action (background)',async()=>{
 const f=fixture({background:true});
 try{
  f.newRequest('Open ChatGPT and type','turn-A');
  await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt'});
  const release=f.holdController();
  const first=f.call('turn-A','phone_tap',{snapshotId:SNAPSHOT,nodeId:3});
  for(let i=0;i<10;i++)await settle();
  const second=f.call('turn-A','phone_set_text',{snapshotId:SNAPSHOT,nodeId:3,text:'queued'});
  for(let i=0;i<3;i++)await settle();
  const stop=f.taskStop.request({source:'tray'});
  release();await stop;
  await first;
  const queued=await second;
  assert.equal(queued.success,false);
  assert.ok(['stopped_by_owner','task_stopped','not_executed'].includes(f.read(queued).status),JSON.stringify(f.read(queued)));
  assert.ok(!f.controller.some(c=>c.action==='set_text'),'the queued input never reached the workspace');
 }finally{f.close();}
});

test('failures are reported as they happened, never as success',async()=>{
 const f=fixture();
 try{
  f.newRequest('Open ChatGPT','turn-A');
  const reply=await f.call('turn-A','phone_open_app',{packageName:'not a package'}).catch(error=>error);
  assert.match(String(reply.message),/exact installed packageName/,'invalid input is refused before it is queued');
  await assert.rejects(f.call('turn-A','phone_scroll',{snapshotId:SNAPSHOT,nodeId:3,direction:'sideways'}),/Unsupported argument: direction/);
  await assert.rejects(f.call('turn-A','phone_drag',{snapshotId:SNAPSHOT,nodeId:3,points:[1,2,3]}),/Invalid argument: points/);
  assert.deepEqual(f.delivered,[]);
 }finally{f.close();}
 // A locked phone and an expired, uncollected command.
 const g=fixture();
 try{
  g.newRequest('Open ChatGPT','turn-A');
  const original=g.phone.complete.bind(g.phone);g.phone.complete=(id,result)=>original(id,{status:'locked',actionExecuted:false});
  const locked=await g.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt'});
  assert.equal(locked.success,false);assert.equal(g.read(locked).status,'locked');
  g.holdDevice();let now=Date.now();g.phone.now=()=>now;
  const pending=g.call('turn-A','phone_back',{});
  for(let i=0;i<3;i++)await settle();
  now+=21000;
  await assert.rejects(pending,/Phone command expired; inspect before retrying/);
 }finally{g.close();}
});

test('the definitions are typed, offer display only with the background workspace and accept no task',()=>{
 for(const background of [false,true])for(const tool of phoneUiToolDefinitions({background})){
  assert.equal(tool.inputSchema.additionalProperties,false,tool.name);
  assert.ok(!('taskId' in tool.inputSchema.properties),tool.name);
  assert.equal('display' in tool.inputSchema.properties,background,tool.name);
 }
});

test('server.mjs registers the phone tools outside ARTEMIS, routes them, and keys the worker thread on them',()=>{
 assert.match(server,/\.\.\.\(artemisEnabled\?artemisToolDefinitions:\[\.\.\.environmentTools\.definitions,\.\.\.phoneUiTools\.definitions\(\)\]\)/);
 assert.match(server,/const phoneUiTools=createPhoneUiTools\(\{phone:data=>visualTrial&&busy&&genericUiActions\.has\(data\.action\)\?visualGuard\.execute\(data\):fastPhone\(data\),background:\(\)=>backgroundEnabled\}\);/);
 // The shell path records an observed frame for the display it came from.
 assert.match(server,/if\(visualTrial&&busy&&data\.action==='screenshot'\)visualGuard\.record\(result,data\.display\);/);
 assert.match(server,/ if\(phoneUiTools\.names\.has\(request\.tool\)\)return phoneUiTools\.handleToolCall\(request\);/);
 // A thread resumed from before the tools existed would not have them: the state key must change.
 assert.match(server,/\['phone-ui',artemisEnabled\?\[\]:phoneUiToolDefinitions\(\{background:backgroundEnabled\}\)\]/);
 assert.ok(server.indexOf('const phoneUiTools=')<server.indexOf('Object.assign(appRuntime,{dynamicTools:'));
});

// Visual-verification trial enabled: the existing generic UI set passes the display-aware gate.
const visualStatus=(f,reply)=>f.read(reply).status;
const physical=f=>f.delivered.filter(c=>c.action!=='app_command').map(c=>c.action);
const seeLatest=f=>assert.equal(f.visualGuard.saw(f.visualGuard.frame.imagePath),true,'the model views the captured frame');

test('visual trial: an unseen phone screen blocks the action; nothing runs',async()=>{
 const f=fixture({background:true,visual:true});
 try{
  f.newRequest('Open ChatGPT on my phone','turn-A');
  const reply=await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt',display:'phone'});
  assert.equal(visualStatus(f,reply),'visual_check_required','a phone-screen launch is not exempt like a workspace launch');
  assert.equal(reply.success,false);
  assert.deepEqual(physical(f),['screenshot'],'only the physical screen was captured; no launch ran');
  assert.equal(f.visualGuard.frame.target,'phone');
 }finally{f.close();}
});

test('visual trial: a seen same-display frame lets the action run once, then captures that display and feeds the final review',async()=>{
 const f=fixture({background:true,visual:true});
 try{
  f.newRequest('Open ChatGPT on my phone','turn-A');
  await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt',display:'phone'});
  seeLatest(f);
  const reply=await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt',display:'phone'});
  assert.equal(visualStatus(f,reply),'opened');assert.equal(reply.success,true);
  assert.deepEqual(physical(f),['screenshot','open_app','screenshot'],'one launch, then a fresh physical-screen capture');
  assert.ok(f.read(reply).visualCheck.imagePath,'the post-action frame is returned for viewing');
  assert.deepEqual(f.visualGuard.history.map(h=>[h.action,h.display,h.status]),[['open_app','phone','opened']]);
  assert.equal(f.controller.filter(c=>c.operation==='command').length,0,'no workspace capture or action');
  // The completion review receives the action history and a frame and controls from the same display.
  const result=await f.visualGuard.assess('Opened ChatGPT',{model:'m'},{});
  assert.equal(result.status,'complete');
  assert.equal(f.reviews.length,1);assert.equal(f.reviews[0].history.length,1);
  assert.equal(f.reviews[0].frame.target,'phone');
  assert.deepEqual(physical(f).slice(-2),['screenshot','inspect_ui']);
 }finally{f.close();}
});

test('visual trial: a viewed workspace frame is not evidence for a physical-phone action',async()=>{
 const f=fixture({background:true,visual:true});
 try{
  f.newRequest('Type in ChatGPT','turn-A');
  await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt'});
  assert.equal(f.visualGuard.frame.target,'default','the workspace launch is exempt and captures the workspace');
  seeLatest(f);
  const reply=await f.call('turn-A','phone_tap_label',{snapshotId:SNAPSHOT,label:'Message',display:'phone'});
  assert.equal(visualStatus(f,reply),'visual_check_required');
  assert.ok(!f.delivered.some(c=>c.action==='tap_label'),'the phone tap did not run');
  assert.equal(f.visualGuard.frame.target,'phone','a physical-screen frame was captured for review');
  // The workspace action still uses the workspace frame once that one is viewed again.
  const workspace=await f.call('turn-A','phone_tap',{snapshotId:SNAPSHOT,nodeId:3});
  assert.equal(visualStatus(f,workspace),'visual_check_required','the phone frame is not evidence for the workspace either');
 }finally{f.close();}
});

test('visual trial: actions outside the existing covered set keep their direct request-bound route',async()=>{
 assert.deepEqual([...genericUiActions].sort(),['back','drag','home','ime_enter','open_app','screen_gesture','scroll','set_text','swipe','tap','tap_label','touch_label','touch_point']);
 const f=fixture({visual:true});
 try{
  f.newRequest('Inspect','turn-A');
  for(const [tool,args] of [['phone_inspect_ui',{}],['phone_long_press',{snapshotId:SNAPSHOT,nodeId:3}],['phone_type_text',{screenshotId:SHOT,text:'Hi'}]])
   assert.equal((await f.call('turn-A',tool,args)).success,true,tool);
  assert.deepEqual(physical(f),['inspect_ui','long_press','type_text'],'no screenshot gate');
 }finally{f.close();}
});

test('visual trial: Stop and a new task refuse an action queued behind the gate',async()=>{
 const f=fixture({visual:true});
 try{
  f.newRequest('First','turn-A');
  await f.call('turn-A','phone_back',{});seeLatest(f);
  const release=f.holdDevice();
  const first=f.call('turn-A','phone_back',{});
  const queued=f.call('turn-A','phone_home',{});
  for(let i=0;i<5;i++)await settle();
  await f.taskStop.request({source:'tray'});release();
  await first;
  // Its required fresh capture is refused by Stop, so the gate fails closed before the action.
  await assert.rejects(queued,/Screenshot unavailable: task_stopped/);
  assert.ok(!f.delivered.some(c=>c.action==='home'),'the queued action never reached the phone');
 }finally{f.close();}
 const g=fixture({visual:true});
 try{
  g.newRequest('First','turn-A');
  await g.call('turn-A','phone_back',{});seeLatest(g);
  const release=g.holdDevice();
  const first=g.call('turn-A','phone_back',{});
  const queued=g.call('turn-A','phone_home',{});
  for(let i=0;i<5;i++)await settle();
  g.newRequest('Second','turn-B');g.visualGuard.reset('Second');release();
  await first.catch(()=>{});
  const stale=await queued.catch(error=>({error}));
  assert.ok(stale.error||g.read(stale).status!=='action_accepted',JSON.stringify(stale.error?.message||g.read(stale)));
  assert.ok(!g.delivered.some(c=>c.action==='home'),'the earlier task\'s queued action never ran');
 }finally{g.close();}
});

test('visual trial disabled: the same calls run directly with their ordinary payloads',async()=>{
 const f=fixture({background:true,visual:false});
 try{
  f.newRequest('Open ChatGPT on my phone','turn-A');
  const reply=await f.call('turn-A','phone_open_app',{packageName:'com.openai.chatgpt',display:'phone'});
  assert.equal(visualStatus(f,reply),'opened');
  assert.deepEqual(f.delivered.map(wire),[{action:'open_app',packageName:'com.openai.chatgpt'}],'no screenshot gate when the trial is off');
 }finally{f.close();}
});
