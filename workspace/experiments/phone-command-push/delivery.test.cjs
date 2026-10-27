const {test}=require('node:test');
const assert=require('node:assert/strict');
const Delivery=require('./delivery.js');
const command=(id='a')=>({id,action:'tap',expires:Date.now()+60000});
const tick=()=>new Promise(setImmediate);
const message=(commands,epoch='one')=>({type:'commands',commands,epoch});

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
 const d=new Delivery({api:async(m,r,x)=>posted.push(x),execute:async c=>{calls.push(c.id);if(c.id==='a')await new Promise(r=>release=r);return {status:'ok'};}});
 d.receive(message([command()]));await tick();
 d.accept(message([command(),command('b')]),'poll');d.receive(message([command('b')]));
 assert.deepEqual(calls,['a']);release();await tick();await tick();
 assert.deepEqual(calls,['a','b']);assert.equal(posted.length,2);assert.equal(posted[0]._delivery.channel,'push');
});
test('a lost result reply retries the result, not the action',async()=>{
 let calls=0,posts=0;const d=new Delivery({sleep:async()=>{},execute:()=>{calls++;return {status:'ok'};},api:async()=>{if(++posts<3)throw Error('network');}});
 d.receive(message([command()]));await tick();assert.equal(posts,3);assert.equal(calls,1);
});
test('running routines finish before subsequent actions',async()=>{
 let running=0;const calls=[];const d=new Delivery({sleep:async()=>{},api:async()=>{},execute:c=>{calls.push(c.id);return {status:c.id==='a'&&++running<3?'running':'ok'};}});
 d.receive(message([command(),command('b')]));await tick();assert.deepEqual(calls,['a','a','a','b']);
});
test('new epoch drops queued old commands, preserves a single executor',async()=>{
 let release;const calls=[];const posts=[];const d=new Delivery({api:async(m,r)=>posts.push(r),execute:async c=>{calls.push(c.id);if(c.id==='a')await new Promise(r=>release=r);return {status:'ok'};}});
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
