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
 assert.deepEqual(Object.keys(a).sort(),['awaitingSend','background','detail','finished','goal','id','key','kind','onScreen','result','started','status']);
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

