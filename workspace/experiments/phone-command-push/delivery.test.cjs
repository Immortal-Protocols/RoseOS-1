const {test}=require('node:test');
const assert=require('node:assert/strict');
const Delivery=require('./delivery.js');
const command=(id='a')=>({id,action:'tap',expires:Date.now()+60000});
const tick=()=>new Promise(setImmediate);
const message=(commands,epoch='one')=>({type:'commands',commands,epoch});
// The server still holds each command pending (GET /phone/commands/:id); other requests go to the test's api.
const pending=api=>async(m,r,x)=>m==='GET'&&r.startsWith('/phone/commands/')?{status:'pending'}:api(m,r,x);

test('default timers preserve browser Window invocation requirements',async()=>{
 const vm=require('node:vm'),fs=require('node:fs');
 const context=vm.createContext({});
 vm.runInContext(`function setTimeout(){'use strict';if(this!==undefined&&this!==globalThis)throw Error('Illegal invocation');return 1;}function clearTimeout(){'use strict';if(this!==undefined&&this!==globalThis)throw Error('Illegal invocation');}`,context);
 vm.runInContext(fs.readFileSync(require.resolve('./delivery.js'),'utf8'),context);
 vm.runInContext(`var d=new PhoneCommandDelivery({api:async()=>({epoch:'one',commands:[]}),execute:()=>({status:'ok'})});d.started=true;`,context);
 await vm.runInContext('d.poll()',context);
 vm.runInContext("d.receive({type:'transport',connected:false});d.stop();",context);
});

test('push and poll duplicates share one serial executor',async()=>{
 const calls=[],posted=[];let release;
 const d=new Delivery({api:pending(async(m,r,x)=>posted.push(x)),execute:async c=>{calls.push(c.id);if(c.id==='a')await new Promise(r=>release=r);return {status:'ok'};}});
 d.receive(message([command()]));await tick();
 d.accept(message([command(),command('b')]),'poll');d.receive(message([command('b')]));
 assert.deepEqual(calls,['a']);release();await tick();await tick();
 assert.deepEqual(calls,['a','b']);assert.equal(posted.length,2);assert.equal(posted[0]._delivery.channel,'push');
});
test('a lost result reply retries the result, not the action',async()=>{
 let calls=0,posts=0;const d=new Delivery({sleep:async()=>{},execute:()=>{calls++;return {status:'ok'};},api:pending(async()=>{if(++posts<3)throw Error('network');})});
 d.receive(message([command()]));await tick();assert.equal(posts,3);assert.equal(calls,1);
});
test('running routines finish before subsequent actions',async()=>{
 let running=0;const calls=[];const d=new Delivery({sleep:async()=>{},api:pending(async()=>{}),execute:c=>{calls.push(c.id);return {status:c.id==='a'&&++running<3?'running':'ok'};}});
 d.receive(message([command(),command('b')]));await tick();assert.deepEqual(calls,['a','a','a','b']);
});
test('new epoch drops queued old commands, preserves a single executor',async()=>{
 let release;const calls=[];const posts=[];const d=new Delivery({api:pending(async(m,r)=>posts.push(r)),execute:async c=>{calls.push(c.id);if(c.id==='a')await new Promise(r=>release=r);return {status:'ok'};}});
 d.receive(message([command(),command('stale')]));await tick();
 d.receive(message([command('new')],'two'));release();await tick();await tick();
 assert.deepEqual(calls,['a','new']);assert.deepEqual(posts,['/phone/commands/new']);
 d.accept(message([command('stale')],'one'),'poll');assert.equal(d.epoch,'two');
});
test('disconnect restores 800ms fallback; healthy stream uses 5s safety poll',async()=>{
 const timers=[];let response=message([]);const d=new Delivery({api:async()=>response,execute:()=>{},schedule:(fn,ms)=>{timers.push({fn,ms});return timers.length;},cancel:()=>{}});
 d.start();await tick();assert.equal(timers.at(-1).ms,800);
 d.receive(message([]));await d.poll();assert.equal(timers.at(-1).ms,5000);
 d.receive({type:'transport',connected:false});assert.equal(timers.at(-1).ms,0);
 await timers.at(-1).fn();assert.equal(timers.at(-1).ms,800);d.stop();
});

// W2 (APP-FIX-005): the real server command store and Owner Stop with this delivery client. Native
// execution, the network and the worker are fakes; the cancellation and status contracts are the real ones.
async function stopRig(){
 const {PhoneCommands}=await import('../../rose-agent/server/phone.mjs'),{TaskStop}=await import('../../rose-agent/server/task-stop.mjs');
 const phone=new PhoneCommands();let task={id:'t1',state:'running'};
 const stop=new TaskStop({task:()=>task,stopping(){},interrupt:async()=>{},abortReview(){},closeWorkspace:async()=>{},
  cancelQueued:({taskId})=>phone.cancelPending({status:'task_stopped',actionExecuted:false,taskId})});
 stop.begin('t1');
 // server.mjs: GET /phone/commands lists pending(); GET /phone/commands/:id answers the record or 404; POST completes it.
 const requests=[];
 const api=async(m,r,x)=>{requests.push(m+' '+r);
  if(m==='GET'&&r==='/phone/commands')return {epoch:'e1',commands:phone.pending()};
  const id=r.split('/').pop();
  if(m==='GET'){const c=phone.get(id);if(!c)throw Object.assign(Error('Unknown command'),{status:404});return c;}
  return phone.complete(id,x);
 };
 const executed=[];const held=new Map();
 const d=new Delivery({api,sleep:async()=>{},execute:c=>{executed.push(c.id);return held.has(c.id)?held.get(c.id).promise:{status:'ok'};}});
 const hold=id=>{let release;held.set(id,{promise:new Promise(r=>release=()=>r({status:'ok'}))});return ()=>release();};
 const settle=async()=>{for(let i=0;i<20;i++)await tick();};
 return {phone,stop,d,api,executed,hold,settle,requests,setTask:t=>task=t};
}
test('W2: a fetched command queued behind a running one never runs after an acknowledged Stop',async()=>{
 const {phone,stop,d,api,executed,hold,settle}=await stopRig();
 const c1=phone.create({action:'home'}),c2=phone.create({action:'back'});const release=hold(c1.id);
 d.accept(await api('GET','/phone/commands'),'poll');await settle();
 assert.deepEqual(executed,[c1.id],'command 1 is running; command 2 was fetched and is queued');
 const ack=await stop.request({taskId:'t1',source:'owner'});
 assert.equal(ack.status,'stopping');assert.equal(phone.get(c2.id).status,'cancelled');
 release();await settle();
 assert.deepEqual(executed,[c1.id],'command 2 is never executed');
 assert.equal(phone.get(c2.id).status,'cancelled','its record stays cancelled, not reported as run');
 assert.equal(phone.get(c1.id).status,'cancelled','command 1 had already started; its late result does not rewrite the Stop');
});
test('W2: a batch fetched before Stop but delivered after it runs nothing',async()=>{
 const {phone,stop,d,api,executed,settle}=await stopRig();
 phone.create({action:'home'});phone.create({action:'back'});
 const stale=await api('GET','/phone/commands'); // the poll response is in flight
 await stop.request({taskId:'t1'});
 d.accept(stale,'poll');d.receive({type:'commands',epoch:'e1',commands:stale.commands});await settle();
 assert.deepEqual(executed,[],'neither the late poll nor a late push reintroduces cancelled commands');
 d.accept(await api('GET','/phone/commands'),'poll');await settle();assert.deepEqual(executed,[]);
});
test('W2: normal queued work still runs in order, once, and a new task proceeds after a Stop',async()=>{
 const {phone,stop,d,api,executed,settle,setTask}=await stopRig();
 const a=phone.create({action:'home'}),b=phone.create({action:'back'});
 d.accept(await api('GET','/phone/commands'),'poll');d.accept(await api('GET','/phone/commands'),'poll');await settle();
 assert.deepEqual(executed,[a.id,b.id]);assert.equal(phone.get(a.id).status,'completed');assert.equal(phone.get(b.id).status,'completed');
 await stop.request({taskId:'t1'});
 setTask({id:'t2',state:'running'});stop.begin('t2');
 const fresh=phone.create({action:'home'});
 d.accept(await api('GET','/phone/commands'),'poll');await settle();
 assert.deepEqual(executed,[a.id,b.id,fresh.id],'the explicit new task is not blocked by the earlier Stop');
 assert.equal(phone.get(fresh.id).status,'completed');
});
test('W2: the status check is retried, never guessed, and a lost result reply never reruns the action',async()=>{
 const {phone,d,executed,settle,requests}=await stopRig();
 const c=phone.create({action:'home'});let lookups=0,posts=0;
 const real=d.api;d.api=async(m,r,x)=>{if(m==='GET'&&r.includes(c.id)&&++lookups<3)throw Error('network');if(m==='POST'&&++posts<3)throw Error('network');return real(m,r,x);};
 d.accept({epoch:'e1',commands:[c]},'poll');await settle();
 assert.deepEqual(executed,[c.id],'run once after the status check succeeds');assert.equal(lookups,3);assert.equal(posts,3);
 assert.equal(phone.get(c.id).status,'completed');
 assert.equal(requests.filter(r=>r==='GET /phone/commands/'+c.id).length,1,'one successful status check, not one per result retry');
});

// W1 (APP-FIX-006): the bridge answers 'running' when its wait for the main thread runs out (PhoneCommandGate),
// and keeps answering from the id's receipt. A fake bridge with that contract, the real client and server store.
function slowBridge(){
 const receipts=new Map();let actions=0,release;const done=new Promise(r=>release=r);
 const execute=async c=>{
  if(receipts.has(c.id))return receipts.get(c.id);
  receipts.set(c.id,{status:'running'});actions++;
  done.then(result=>receipts.set(c.id,result)); // the one posted action finishes later
  return {status:'running'}; // the five-second wait ran out
 };
 return {execute,release,actions:()=>actions};
}
test('W1: a native wait that runs out is not reported as a failure; the real result is posted once it exists',async()=>{
 const {phone,api,settle}=await stopRig();const bridge=slowBridge();
 const d=new Delivery({api,sleep:async()=>{await tick();},execute:bridge.execute});
 const c=phone.create({action:'open_app',packageName:'com.example.app'});
 d.accept(await api('GET','/phone/commands'),'poll');await settle();
 assert.equal(phone.get(c.id).status,'pending','the server is not told the action failed while it may still run');
 bridge.release({status:'launch_requested',packageName:'com.example.app'});await settle();
 assert.equal(phone.get(c.id).status,'completed');assert.equal(phone.get(c.id).result.status,'launch_requested');
 assert.equal(bridge.actions(),1,'re-queries of the same id never start a second action');
});
test('W1: still running at the command deadline is reported as uncertain, not error, and not repeated',async()=>{
 const {phone,api,settle}=await stopRig();const bridge=slowBridge();let now=Date.now();
 const d=new Delivery({api,now:()=>now,sleep:async()=>{now+=1000;await tick();},execute:bridge.execute});
 const c=phone.create({action:'open_app',packageName:'com.example.app'});
 d.accept(await api('GET','/phone/commands'),'poll');await settle();await settle();
 const record=phone.get(c.id);
 assert.equal(record.status,'completed');assert.equal(record.result.status,'uncertain');assert.match(record.result.reason,/not repeated/);
 assert.equal(bridge.actions(),1);
});
