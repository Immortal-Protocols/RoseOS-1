// LOCAL-TASK-BRIDGE-001: the Mac's task events reach the voice page through the real local() provider.
// Fixtures come from the real PR318 producer (fixtures/local_task_events.py); fixture success is not phone acceptance.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'../../..');
const produced=JSON.parse(execFileSync('python3',['-I',path.join(__dirname,'fixtures/local_task_events.py'),path.join(ROOT,'prototypes/voice-phone-demo')],{encoding:'utf8'}));
const link=fs.readFileSync(path.join(ROOT,'workspace/rose-agent/app/src/dev/pixel/voice/LocalRoseLink.java'),'utf8');
// What LocalRoseLink.eventsLoop does with each /ws/events message (asserted against its source below).
const forwarded=m=>m.replay?null:m.kind==='task'?{type:'task',task:m.task}:null;

function page({onLocalTasks=true}={}){
 const calls=[],lists=[],backend=[];
 const context={console,setTimeout,clearTimeout,setInterval,clearInterval,Map,Set,Promise,Date,JSON,Number,Math};context.window=context;
 context.Native={localRoseStart:m=>calls.push(['start',m]),localRoseStop:()=>calls.push(['stop']),localRoseMute:m=>calls.push(['mute',m]),
  taskState:raw=>backend.push(raw),stopTask:()=>calls.push(['stopTask'])};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'voice-providers.js'),'utf8'),context);
 const events=[],o={provider:'local',onMessage:m=>events.push(['message',JSON.parse(JSON.stringify(m))]),onConnect:()=>events.push(['connect']),onDisconnect:d=>events.push(['disconnect',d.reason]),onError:e=>events.push(['error',e])};
 if(onLocalTasks)o.onLocalTasks=raw=>{const list=JSON.parse(JSON.stringify(raw));lists.push(list);events.push(['tasks',list.map(t=>t.key+':'+t.status)]);};
 const started=context.VoiceProviders.start(o);
 return {context,calls,lists,backend,events,started,send:m=>context.localRoseEvent?.(m),last:()=>lists[lists.length-1]};
}
const live=p=>p.send({type:'status',status:'live',route:'wifi'});
const deliver=(p,list)=>{for(const m of list){const f=forwarded(m);if(f)p.send(f);}};

test('the fixtures are the real producer\'s task events, and LocalRoseLink forwards them as the bridge expects',()=>{
 assert.match(link,/kind\.equals\("task"\)\)\s*\{\s*emit\(new JSONObject\(\)\.put\("type", "task"\)\.put\("task", m\.optJSONObject\("task"\)\)\);/);
 assert.match(link,/if \(m\.optBoolean\("replay"\)\) return;/,'replayed history is never forwarded');
 assert.equal(produced.length,9);
 assert.ok(produced.every(m=>m.kind==='task'&&typeof m.task.id==='number'&&typeof m.task.started==='number'));
 assert.deepEqual(Object.keys(produced[0].task).sort(),['background','confirmed_send','detail','finished','goal','id','kind','ms','on_screen','ready_to_send','result','started','status','steps'].sort(),'PR318 Task.to_dict schema');
});

test('ordered producer events become one current, validated list after live',async()=>{
 const p=page();deliver(p,produced.slice(0,2));
 assert.equal(p.lists.length,0,'nothing before live: the connection is not established yet');
 live(p);const handle=await p.started;
 assert.deepEqual(p.events.slice(0,2).map(e=>e[0]),['connect','tasks'],'the buffered task arrives after onConnect');
 assert.deepEqual(p.last().map(t=>[t.id,t.status,t.detail,t.background]),[[1,'running','typing the reply',true]]);
 deliver(p,produced.slice(2));
 const final=p.last();
 assert.deepEqual(final.map(t=>[t.id,t.kind,t.status]),[[1,'phone','done'],[2,'think','cancelled'],[3,'phone','failed'],[1,'phone','running']],'ordered by start; the restarted Mac\'s task 1 is a new task');
 assert.equal(new Set(final.map(t=>t.key)).size,4,'identity is id@started');
 const [a,b,c]=final;
 assert.equal(a.awaitingSend,true);assert.equal(a.result,'Typed the reply; waiting for you to say send.');assert.equal(a.detail,'');
 assert.equal(b.result,'Cancelled.');assert.equal(c.onScreen,true);assert.equal(a.onScreen,false);
 // Privacy: steps, screen text, the model's reply and the typed draft never leave the provider.
 const all=JSON.stringify(p.lists);
 for(const secret of ['secret model reply','Messages: Sam Lee','Running late, sorry!','steps','ready_to_send','confirmed_send'])assert.ok(!all.includes(secret),'not forwarded: '+secret);
 assert.deepEqual(Object.keys(a).sort(),['awaitingSend','background','binding','connection','contract','detail','finished','goal','id','key','kind','onScreen','result','server','started','status']);
 assert.match(a.connection,/^c\d+-[a-z0-9]+$/);assert.ok(final.every(x=>x.connection===a.connection&&x.contract===null));
 assert.ok(final.every(x=>x.binding===null&&x.server===null),'PR318 sends no workspace binding or server instance: none is inferred');
 // Read-only: no Stop or task command, nothing joins the backend projection; the handle keeps its contract.
 assert.equal(p.backend.length,0);assert.ok(!p.calls.some(c=>c[0]==='stopTask'));
 assert.deepEqual(Object.keys(handle).sort(),['close','inputLevel','outputLevel','sendContext','sendMessage','setMuted']);
});

test('duplicate and stale events change nothing; a terminal status is final',async()=>{
 const p=page();live(p);await p.started;
 const [run1,run2,done]=produced.slice(0,3);
 deliver(p,[run1,run1,run2,run2]);
 assert.equal(p.lists.length,2,'identical snapshots are dropped');
 deliver(p,[done,run2,run1,done]);
 assert.equal(p.lists.length,3,'running after done is stale; a repeated terminal event is a duplicate');
 assert.equal(p.last()[0].status,'done');
 // A late terminal event of another status for a finished task is refused too.
 p.send({type:'task',task:{...done.task,status:'failed',result:'late'}});
 assert.equal(p.last()[0].status,'done');assert.equal(p.lists.length,3);
});

test('malformed task events are refused and never relabel an existing task',async()=>{
 const p=page();live(p);await p.started;
 deliver(p,[produced[0]]);const before=p.lists.length;
 const t=produced[0].task;
 for(const bad of [null,{},'x',{...t,id:0},{...t,id:'1'},{...t,id:1.5},{...t,kind:'shell'},{...t,status:'paused'},{...t,status:'idle'},{...t,goal:7},{...t,started:null},{...t,started:'now'}])
  p.send({type:'task',task:bad});
 assert.equal(p.lists.length,before);assert.equal(p.last()[0].status,'running');
 const long=p.context.VoiceProviders.localTask({...t,goal:'g'.repeat(2000),detail:'d'.repeat(2000)});
 assert.equal(long.goal.length,500);assert.equal(long.detail.length,500);
});

test('reconnecting keeps the list; replay is skipped; close, replaced and unpaired end delivery with one []',async()=>{
 for(const end of ['close','closed','unpaired']){
  const p=page();live(p);const handle=await p.started;deliver(p,produced.slice(0,2));
  p.send({type:'status',status:'reconnecting'});
  deliver(p,[{...produced[2],replay:true}]);
  assert.equal(p.last()[0].status,'running','a replayed event is not forwarded by the link');
  deliver(p,[produced[2]]);assert.equal(p.last()[0].status,'done','events after reconnect still apply');
  const n=p.lists.length;
  if(end==='close')await handle.close();else if(end==='closed')p.send({type:'closed',code:'4001'});else p.send({type:'status',status:'unpaired'});
  assert.equal(p.lists.length,n+1);assert.deepEqual(p.last(),[],'cleared once on '+end);
  deliver(p,produced.slice(3));p.send({type:'task',task:produced[3].task});
  assert.equal(p.lists.length,n+1,'nothing after '+end);
  assert.ok(p.calls.some(c=>c[0]==='stop'),'the link is stopped on '+end);
 }
});

test('a connection that never goes live delivers nothing; a stale handler after close is inert',async()=>{
 const p=page();deliver(p,produced.slice(0,3));
 const handler=p.context.localRoseEvent;
 p.send({type:'error',message:'Rose on the Mac is not reachable.'});
 await assert.rejects(p.started);
 handler({type:'status',status:'live'});handler({type:'task',task:produced[3].task});
 assert.equal(p.lists.length,0);assert.equal(p.backend.length,0);
});

test('without onLocalTasks, and for other events, local() behaves as before',async()=>{
 const p=page({onLocalTasks:false});deliver(p,produced.slice(0,2));live(p);await p.started;deliver(p,produced.slice(2));
 p.send({type:'message',role:'user',id:1,text:'hi'});p.send({type:'message',role:'user',id:1,text:'hi'});
 p.send({type:'mode',mode:'speaking'});
 assert.deepEqual(p.events.filter(e=>e[0]!=='connect'),[['message',{role:'user',message:'hi'}]]);
 assert.equal(p.backend.length,0);
 // Only the local provider knows task events; the other providers' option sets are untouched by this bridge.
 assert.throws(()=>p.context.VoiceProviders.start({provider:'nope'}),/Unknown voice provider/);
});

test('a throwing consumer never stops the link from closing or the provider from handling events',async()=>{
 const p=page({onLocalTasks:false});live(p);await p.started; // then the real provider again, with a consumer that throws
 const ctx=p.context,calls=[];
 const q=ctx.VoiceProviders.start({provider:'local',onLocalTasks:()=>{calls.push('tasks');throw Error('consumer bug');},onMessage:m=>calls.push('message')});
 ctx.localRoseEvent({type:'status',status:'live'});const handle=await q;
 ctx.localRoseEvent({type:'task',task:produced[0].task});ctx.localRoseEvent({type:'message',role:'user',id:9,text:'still here'});
 assert.deepEqual(calls,['tasks','message']);
 await handle.close();
 assert.ok(p.calls.some(c=>c[0]==='stop'),'the native link stopped despite the throwing consumer');
});

// --- Workspace binding (App lead correction): the controller taskId is a reusable workspace generation, so a logical
// task is bound ONLY by the producer's explicit `workspace`. `server` and `workspace` below are the PROPOSED producer
// fields (synthetic additions to real producer events), not something PR318 emits today.
const VP=()=>{const c={console,Map,Set,Promise,JSON,Number,Math,Date,setTimeout,clearTimeout};c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(__dirname,'voice-providers.js'),'utf8'),c);return c.VoiceProviders;};
const WS={controllerTaskId:'rose-ws-ab12cd34-1-com.google.android.apps.messaging',sessionId:'s-1',targetPackage:'com.google.android.apps.messaging'};
const ctl=(b,status='ready')=>({status,sessionId:b.sessionId,taskId:b.controllerTaskId,targetPackage:b.targetPackage});
const plain=x=>JSON.parse(JSON.stringify(x));
const bound=(m,extra)=>({type:'task',task:{...m.task,contract:1,...extra}});

test('missing binding: listed with its status, but Watch and Stop are unavailable',()=>{
 const vp=VP(),t=plain(vp.localTask(produced[1].task));
 assert.equal(t.binding,null);
 assert.deepEqual(plain(vp.localTaskActions({key:t.key,binding:null},[t],ctl(WS))),{watch:false,stop:false,reason:'unbound'},'never the workspace that happens to be open');
 for(const bad of [{...WS,sessionId:''},{...WS,controllerTaskId:7},{...WS,targetPackage:'not a package'},{controllerTaskId:WS.controllerTaskId},'rose-ws-1',null])
  assert.equal(vp.localTask({...produced[1].task,contract:1,server:'srv-a',workspace:bad}).binding,null,'malformed binding is no binding');
 // A binding needs contract 1 and a server scope: without either it is absent, never trusted.
 assert.equal(vp.localTask({...produced[1].task,server:'srv-a',workspace:WS}).binding,null,'no contract version');
 assert.equal(vp.localTask({...produced[1].task,contract:2,server:'srv-a',workspace:WS}).binding,null,'unknown contract version');
 assert.equal(vp.localTask({...produced[1].task,contract:1,workspace:WS}).binding,null,'no server scope');
});

test('two logical tasks sharing one workspace are separate tasks with the same binding',async()=>{
 const p=page();live(p);await p.started;
 p.send(bound(produced[0],{server:'srv-a',workspace:WS}));
 p.send(bound(produced[3],{server:'srv-a',workspace:WS,id:2}));
 const list=p.last(),[a,b]=list;
 assert.equal(list.length,2);assert.notEqual(a.key,b.key);assert.deepEqual(a.binding,b.binding);
 const vp=p.context.VoiceProviders;
 assert.equal(vp.localTaskActions({key:a.key,binding:a.binding},list,ctl(WS)).watch,true);
 assert.equal(vp.localTaskActions({key:b.key,binding:b.binding},list,ctl(WS)).watch,true);
 // The first finishes in the shared workspace: its result stays, without a live session; the second is still watchable.
 p.send(bound(produced[2],{server:'srv-a',workspace:WS}));
 const after=p.last();
 assert.equal(after.find(t=>t.id===1).result,'Typed the reply; waiting for you to say send.');
 assert.deepEqual(plain(vp.localTaskActions({key:a.key,binding:a.binding},after,ctl(WS))),{watch:false,stop:false,reason:'finished'});
 assert.equal(vp.localTaskActions({key:b.key,binding:b.binding},after,ctl(WS)).watch,true);
});

test('replacement during selection: a changed binding, a new workspace generation or a gone task never opens',()=>{
 const vp=VP(),t=plain(vp.localTask({...produced[1].task,contract:1,server:'srv-a',workspace:WS})),sel={key:t.key,binding:t.binding};
 const moved=plain(vp.localTask({...produced[1].task,contract:1,server:'srv-a',workspace:{...WS,sessionId:'s-2'}}));
 assert.equal(vp.localTaskActions(sel,[moved],ctl(moved.binding)).reason,'replaced','the task moved to another session after the tap');
 const next={...WS,controllerTaskId:'rose-ws-ab12cd34-2-com.android.chrome',sessionId:'s-9',targetPackage:'com.android.chrome'};
 assert.equal(vp.localTaskActions(sel,[t],ctl(next)).reason,'workspace_changed','the controller now holds another generation');
 assert.equal(vp.localTaskActions(sel,[t],{...ctl(WS),targetPackage:'com.android.chrome'}).reason,'workspace_changed','all three fields must match');
 assert.equal(vp.localTaskActions(sel,[t],{...ctl(WS),sessionId:'s-2'}).reason,'workspace_changed');
 assert.equal(vp.localTaskActions(sel,[t],ctl(WS,'idle')).reason,'no_workspace');
 assert.equal(vp.localTaskActions(sel,[t],ctl(WS,'stopped')).reason,'no_workspace');
 assert.equal(vp.localTaskActions(sel,[t],null).reason,'controller_unknown');
 assert.equal(vp.localTaskActions(sel,[],ctl(WS)).reason,'gone');
 assert.deepEqual(plain(vp.localTaskActions(sel,[t],ctl(WS,'blocked'))),{watch:true,stop:true,reason:''});
});

test('server restart, re-pair and stale reconnect with reused ids never merge tasks',async()=>{
 // Restart: the same id from another server instance is another task, even if a running snapshot follows a done one.
 const p=page();live(p);await p.started;
 p.send(bound(produced[2],{server:'srv-a',workspace:WS}));
 p.send(bound(produced[0],{server:'srv-b',workspace:{...WS,controllerTaskId:'rose-ws-ffff0000-1-com.google.android.apps.messaging',sessionId:'s-7'}}));
 const list=p.last();
 assert.deepEqual(list.map(t=>[t.key,t.status]),[['srv-a#1','done'],['srv-b#1','running']]);
 // Without a server id the restart is still told apart by start time (PR318 today).
 assert.notEqual(produced[0].task.started,produced[8].task.started);
 // Re-pair / replacement: the old connection ends (one []), its stale handler is inert, and a new connection starts empty.
 const oldHandler=p.context.localRoseEvent;p.send({type:'closed',code:'4001'});
 assert.deepEqual(p.last(),[]);const n=p.lists.length;
 oldHandler(bound(produced[1],{server:'srv-a',workspace:WS}));assert.equal(p.lists.length,n);
 const q=page();live(q);await q.started;assert.equal(q.lists.length,0,'a new connection inherits nothing');
 // Stale reconnect: a running snapshot of srv-a#1 after its done event is refused.
 const r=page();live(r);await r.started;
 r.send(bound(produced[2],{server:'srv-a',workspace:WS}));r.send({type:'status',status:'reconnecting'});r.send(bound(produced[1],{server:'srv-a',workspace:WS}));
 assert.equal(r.last()[0].status,'done');assert.equal(r.lists.length,1);
 // A malformed server id is refused rather than merged.
 r.send(bound(produced[3],{server:'has space'}));assert.equal(r.lists.length,1);
});

test('Stop: an ACK is stopping, duplicates change nothing, timeout is unconfirmed, only the task\'s own event ends it',()=>{
 const vp=VP(),t=plain(vp.localTask({...produced[1].task,contract:1,server:'srv-a',workspace:WS})),key=t.key;
 const s=vp.localStop(key,{timeoutMs:1000,now:0});
 assert.equal(s.state,'requested');assert.equal(s.tap(),'duplicate','a second tap sends nothing');
 assert.equal(s.ack('a1'),'stopping');assert.equal(s.ack('a1'),'stopping');assert.equal(s.ack('a2'),'stopping','duplicate ACKs change nothing');
 assert.equal(s.tap(),'duplicate');
 assert.equal(s.observe([t],500),'stopping','an ACK is never stopped');
 assert.equal(s.observe([t],1000),'unconfirmed','no terminal event by the deadline: it may still be running');
 const cancelled=plain(vp.localTask({...produced[5].task,id:t.id,started:produced[1].task.started,contract:1,server:'srv-a',workspace:WS}));
 assert.equal(cancelled.key,key);
 assert.equal(s.observe([cancelled],1500),'stopped','its own cancelled event confirms, even after the deadline');
 assert.equal(s.observe([t],1600),'stopped','final');
 const e=vp.localStop(key,{timeoutMs:1000,now:0});e.ack();
 assert.equal(e.observe([{...cancelled,status:'done'}],10),'ended','finished on its own: not claimed as stopped');
 const u=vp.localStop(key,{timeoutMs:1000,now:0});
 assert.equal(u.observe([],10),'unknown','connection ended: nothing is claimed');
 const other=vp.localStop(key,{timeoutMs:1000,now:0});
 assert.equal(other.observe([{...cancelled,key:'srv-b#1'}],10),'unknown','another server\'s task with the same id never confirms');
});

test('completed results stay readable without a live session, and do not depend on the workspace',async()=>{
 const p=page();live(p);await p.started;
 for(const m of produced.slice(0,8))p.send(bound(m,{server:'srv-a'}));
 const list=p.last(),vp=p.context.VoiceProviders;
 assert.deepEqual(list.map(t=>[t.id,t.status,t.result]),[[1,'done','Typed the reply; waiting for you to say send.'],[2,'cancelled','Cancelled.'],[3,'failed','Something went wrong with that task.']]);
 for(const t of list)assert.equal(vp.localTaskActions({key:t.key,binding:t.binding},list,ctl(WS,'idle')).reason,'finished');
});

test('Stop answers (proposed contract 1) are taken only for this request and task',()=>{
 const vp=VP(),key='srv-a#1',mk=()=>vp.localStop(key,{request:'r1',timeoutMs:1000,now:0});
 let s=mk();
 assert.equal(s.answer({kind:'task_stop',request:'r0',server:'srv-a',id:1,status:'accepted'}),'requested','another request is ignored');
 assert.equal(s.answer({kind:'task_stop',request:'r1',server:'srv-b',id:1,status:'accepted'}),'requested','another server\'s task with the same id is ignored');
 assert.equal(s.answer({kind:'task_stop',request:'r1',server:'srv-a',id:2,status:'accepted'}),'requested','another task is ignored');
 assert.equal(s.answer({kind:'task_stop',request:'r1',server:'srv-a',id:1,status:'accepted'}),'stopping');
 assert.equal(s.answer({kind:'task_stop',request:'r1',server:'srv-a',id:1,status:'refused'}),'stopping','a duplicate or late answer changes nothing');
 for(const [status,state] of [['not_running','not_running'],['refused','refused'],['replaced','replaced'],['stale','replaced'],['weird','requested']]){
  s=mk();assert.equal(s.answer({request:'r1',server:'srv-a',id:1,status}),state,status);
 }
 s=mk();s.answer({request:'r1',server:'srv-a',id:1,status:'replaced'});
 assert.equal(s.observe([{key,status:'cancelled'}],5),'replaced','a refused Stop is final: a later cancel of a reused id never settles it');
});

test('each connection has its own scope id; a re-paired connection never shares it',async()=>{
 const p=page();live(p);await p.started;p.send(bound(produced[0],{server:'srv-a',workspace:WS}));
 const q=page();live(q);await q.started;q.send(bound(produced[0],{server:'srv-a',workspace:WS}));
 assert.notEqual(p.last()[0].connection,q.last()[0].connection);
 assert.deepEqual(p.last()[0].binding,WS);
});

test('closing a connection twice never stops or deafens a newer connection',async()=>{
 const p=page();live(p);const old=await p.started;await old.close();
 const ctx=p.context,next=ctx.VoiceProviders.start({provider:'local',onLocalTasks:()=>{}});
 const handler=ctx.localRoseEvent;assert.equal(typeof handler,'function');
 const stops=p.calls.filter(c=>c[0]==='stop').length;
 await old.close();
 assert.equal(ctx.localRoseEvent,handler,'the newer connection keeps its handler');
 assert.equal(p.calls.filter(c=>c[0]==='stop').length,stops,'and its native link');
 handler({type:'status',status:'live'});await next;
});



test('executing receipts refresh only on actual producer samples, not client timers',async()=>{
 const p=page();live(p);await p.started;
 const task={...produced[0].task,status:'running',server:'activity-test',contract:1,background:true,
  activity:{version:1,sample:100,executing:true}};
 p.send({type:'task',task});const first=p.lists.length;
 assert.equal(p.last()[0].activity.executing,true);
 p.send({type:'task',task});assert.equal(p.lists.length,first,'same sample dedupes');
 p.send({type:'task',task:{...task,activity:{...task.activity,sample:101}}});
 assert.equal(p.lists.length,first+1,'new authoritative sample reaches native');
 p.send({type:'task',task:{...task,status:'done',activity:{version:1,sample:102,executing:false}}});
 assert.equal(p.last()[0].activity.executing,false);
 p.send({type:'task',task:{...task,activity:{...task.activity,sample:103}}});
 assert.equal(p.last()[0].status,'done','late executing receipt cannot resurrect completed task');
});
