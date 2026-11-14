// LOCAL-TASK-FEED-002: the real path from the Mac's task producer to the native workspace overview.
// PR318 producer (fixtures/local_task_events.py) -> LocalRoseLink's forwarding -> VoiceProviders local() -> the
// voice page's onLocalTasks option (app/assets/index.html) -> Native.localTasks -> LocalTaskFeedGate ->
// WorkspaceOverviewFeed -> WorkspaceOverview model, the native half compiled from production sources.
// Fixture success is not phone acceptance; the contract-1 events are the PROPOSED producer fields, not PR318 output.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync,spawnSync} from 'node:child_process';
import {loadPage} from './web-ui-harness.mjs';

const ROOT=path.resolve(new URL('../../..',import.meta.url).pathname);
const SRC=path.join(ROOT,'workspace/rose-agent/app/src/dev/pixel/voice');
const produced=JSON.parse(execFileSync('python3',['-I',path.join(ROOT,'workspace/rose-agent/voice-providers/fixtures/local_task_events.py'),path.join(ROOT,'prototypes/voice-phone-demo')],{encoding:'utf8'}));
const forwarded=m=>m.replay?null:m.kind==='task'?{type:'task',task:m.task}:null; // LocalRoseLink.eventsLoop
const WS={controllerTaskId:'rose-ws-ab12cd34-1-com.google.android.apps.messaging',sessionId:'s-1',targetPackage:'com.google.android.apps.messaging'};

const out=fs.mkdtempSync(path.join(os.tmpdir(),'local-task-feed-'));
execFileSync('java',['-m','jdk.compiler/com.sun.tools.javac.Main','-source','8','-target','8','-Xlint:-options','-d',out,
 ...['WorkspaceOverview','MiniJson','WorkspaceOverviewFeed','LocalTaskFeedGate'].map(n=>path.join(SRC,n+'.java')),
 path.join(ROOT,'workspace/rose-agent/app/tests/LocalTaskFeedProbe.java')],{stdio:['ignore','ignore','inherit']});
function native(commands,ws=WS){
 const r=spawnSync('java',['-cp',out,'dev.pixel.voice.LocalTaskFeedProbe',ws.controllerTaskId,ws.sessionId,ws.targetPackage],{input:commands.join('\n')+'\n',encoding:'utf8'});
 assert.equal(r.status,0,r.stderr);
 return r.stdout.trim().split('\n').map(l=>JSON.parse(l));
}

// The voice page with its real scripts, in local Rose mode; Native.localTasks deliveries are recorded.
async function localPage(){
 const page=loadPage({native:{localRose:()=>true}});
 page.run('start(false)');
 await new Promise(r=>setImmediate(r));
 assert.equal(page.calls.filter(c=>c[0]==='localRoseStart').length,1,'the real local() provider started');
 const send=m=>page.run(`window.localRoseEvent&&window.localRoseEvent(${JSON.stringify(m)})`);
 return {page,send,live:()=>send({type:'status',status:'live',route:'wifi'}),
  feed:p=>{for(const m of p){const f=forwarded(m);if(f)send(f);}},
  deliveries:()=>page.calls.filter(c=>c[0]==='localTasks').map(c=>c[1])};
}
const states=r=>r.rows.map(x=>x.key+':'+x.state);

test('PR318 today: producer events reach the overview as tasks with results, unbound, with no Watch or Stop',async()=>{
 const p=await localPage();p.feed(produced.slice(0,2));
 assert.equal(p.deliveries().length,0,'nothing before the connection is live');
 p.live();p.feed(produced.slice(2));
 const d=p.deliveries();
 assert.ok(d.length>=3);for(const raw of d)assert.equal(typeof raw,'string');
 const last=JSON.parse(d.at(-1));
 assert.deepEqual(last.map(t=>[t.id,t.status]),[[1,'done'],[2,'cancelled'],[3,'failed'],[1,'running']]);
 for(const secret of ['secret model reply','Messages: Sam Lee','Running late, sorry!'])assert.ok(!d.join('').includes(secret),'not forwarded: '+secret);
 const r=native(['page 1',...d.map(raw=>'deliver 1 '+raw)]);
 assert.ok(r.slice(1).every(x=>x.handed),'each delivery from the current page is handed to the feed');
 const end=r.at(-1);
 assert.equal(end.rows.length,4);assert.equal(end.bound,0,'missing binding stays missing: none is inferred from the open workspace');
 assert.equal(end.scopeKnown,false);
 for(const row of end.rows)assert.ok(!row.watch&&!row.stop,'no Watch and no Stop for '+row.key);
 assert.ok(end.rows.some(x=>x.reason.length>0));
});

test('contract-1 events (proposed fields) bind only their explicit workspace',async()=>{
 const p=await localPage();p.live();
 p.send({type:'task',task:{...produced[1].task,contract:1,server:'srv-a',workspace:WS}});
 p.send({type:'task',task:{...produced[0].task,id:2,contract:1,server:'srv-a'}}); // same server, no workspace
 const d=p.deliveries();
 const r=native(['page 1','deliver 1 '+d.at(-1)]).at(-1);
 assert.equal(r.bound,1,'only the task with an explicit workspace is bound');assert.equal(r.scopeKnown,true);
 const other=native(['page 1','deliver 1 '+d.at(-1)],{...WS,sessionId:'s-9'}).at(-1);
 assert.ok(other.rows.every(x=>!x.watch),'a different live session never matches');
});

test('close, replacement and unpairing clear the overview through the page; nothing after',async()=>{
 for(const end of ['closed','unpaired']){
  const p=await localPage();p.live();p.feed(produced.slice(0,3));
  const before=p.deliveries().length;
  p.send(end==='closed'?{type:'closed',code:'4001'}:{type:'status',status:'unpaired'});
  const d=p.deliveries();assert.equal(d.length,before+1);assert.equal(d.at(-1),'[]');
  p.feed(produced.slice(3));assert.equal(p.deliveries().length,before+1,'nothing after '+end);
  const r=native(['page 1',...d.map(raw=>'deliver 1 '+raw)]);
  assert.equal(r.at(-2).rows.length,1);assert.equal(r.at(-1).rows.length,0,'the overview is empty after '+end);
 }
});

test('the user ending the call clears it; a new call\'s list is not cleared by the old one',async()=>{
 const p=await localPage();p.live();p.feed(produced.slice(0,3));
 p.page.run('cleanup("user_end")');
 const d=p.deliveries();assert.equal(d.at(-1),'[]','the old connection\'s [] passes although its start is no longer current');
 // A new start on the same page: its list arrives after the old [] and stays.
 p.page.run('ended=false;start(false)');await new Promise(r=>setImmediate(r));
 p.live();p.feed(produced.slice(8));
 const all=p.deliveries(),r=native(['page 1',...all.map(raw=>'deliver 1 '+raw)]).at(-1);
 assert.deepEqual(states(r).length,1);assert.equal(JSON.parse(all.at(-1))[0].connection!==JSON.parse(d.at(-2))[0].connection,true,'a new connection scope');
});

test('reconnecting keeps the list; a stale running event after done changes nothing',async()=>{
 const p=await localPage();p.live();p.feed(produced.slice(0,3));
 const n=p.deliveries().length;
 p.send({type:'status',status:'reconnecting'});p.feed([{...produced[1],replay:true},produced[1]]);
 assert.equal(p.deliveries().length,n,'no delivery: replay is skipped and running-after-done is stale');
 const r=native(['page 1',...p.deliveries().map(raw=>'deliver 1 '+raw)]).at(-1);
 assert.equal(r.rows.length,1);assert.ok(!r.rows[0].watch);
});

test('native gate: a replaced page\'s late delivery is refused; a dead page\'s list is cleared; destroy clears',()=>{
 const p1='[{"key":"@1#1","id":1,"server":null,"kind":"phone","goal":"Old page task","status":"running","detail":"","result":"","background":false,"onScreen":false,"awaitingSend":false,"started":1,"finished":null,"contract":null,"connection":"c1-a","binding":null}]';
 const p2=p1.replace('Old page task','New page task').replace('"@1#1"','"@2#1"').replace('c1-a','c1-b').replace('"started":1','"started":2');
 const r=native(['page 1','deliver 1 '+p1,'page 2','deliver 1 '+p1,'deliver 2 '+p2,'gone 1','gone 2','deliver 2 '+p2,'close','deliver 2 '+p2]);
 assert.deepEqual(r.map(x=>x.handed),[false,true,false,false,true,false,false,true,false,false]);
 assert.equal(r[1].rows[0].key,'@1#1');
 assert.equal(r[3].rows[0].key,'@1#1','page 1 is replaced: its late delivery never replaces the list');
 assert.equal(r[4].rows[0].key,'@2#1');
 assert.equal(r[5].rows.length,1,'page 1 gone: it holds nothing now, so page 2\'s list stays');
 assert.equal(r[6].rows.length,0,'page 2 gone without delivering []: the feed is cleared');
 assert.equal(r[7].rows.length,1);assert.equal(r[8].rows.length,0,'service destroyed: cleared');assert.equal(r[9].rows.length,0,'nothing is taken after');
});

test('invalid deliveries are dropped whole and the previous list stays',()=>{
 const ok='[{"key":"@1#1","id":1,"server":null,"kind":"phone","goal":"g","status":"running","detail":"","result":"","background":false,"onScreen":false,"awaitingSend":false,"started":1,"finished":null,"contract":null,"connection":"c1-a","binding":null}]';
 const bad=['{}','[1]','[{"key":"","status":"running"}]','['+ok.slice(1,-1)+','+ok.slice(1,-1).replace('c1-a','c2-b')+']',ok+'x','[{"key":"a","key":"b","status":"running","connection":"c"}]','x'.repeat(200001)];
 const r=native(['page 1','deliver 1 '+ok,...bad.map(b=>'deliver 1 '+b)]);
 for(const x of r.slice(2))assert.equal(x.rows.length,1,'previous list kept');
});

test('the released hunks are exactly these: page option, bridge method, teardown clears',()=>{
 const html=fs.readFileSync(path.join(ROOT,'workspace/rose-agent/app/assets/index.html'),'utf8');
 assert.equal((html.match(/Native\.localTasks/g)||[]).length,1);
 assert.match(html,/onLocalTasks:list=>\{if\(g===generation\|\|!list\.length\)Native\.localTasks\?\.\(JSON\.stringify\(list\)\);localTaskCard\(list,g\);\},/);
 const svc=fs.readFileSync(path.join(SRC,'VoiceService.java'),'utf8');
 assert.match(svc,/@JavascriptInterface public void localTasks\(String raw\)\{localTaskFeed\.deliver\(generation,raw\);\}/);
 assert.match(svc,/new LocalTaskFeedGate\(callbacks::owns,WorkspaceOverviewFeed::accept\)/);
 assert.match(svc,/localTaskFeed\.pageGone\(callbacks\.current\(\)\);[^\n]*\n  new Bridge\(\)\.audio\(false\);\n  dead\.destroy\(\);/);
 assert.match(svc,/localTaskFeed\.closeAll\(\);callbacks\.close\(\);web\.destroy\(\);/);
 assert.equal((svc.match(/WorkspaceOverviewFeed::accept/g)||[]).length,1);
 assert.ok(!/WorkspaceOverviewFeed\.\w+\(/.test(svc),'the service only hands deliveries over: it never reads or clears the feed itself');
});
