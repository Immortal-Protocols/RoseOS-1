const test=require('node:test'),assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
// The page loads this file as a classic script (workspace/package.json is type:module, so require() would
// treat it as an ES module where `module` is undefined). Load it the same way the page does.
function pageScript(name){const window={};vm.runInThisContext('(function(window){'+require('node:fs').readFileSync(__dirname+'/'+name,'utf8')+'\n})',{filename:name})(window);return window;}
const {create}=pageScript('luna-first.js').LunaFirst;
test('audio remains closed until an authorized result drains prior speech',()=>{
 let now=100,muted=true;const sent=[];const c=create({dispatch(){},send:e=>sent.push(e),setMuted:v=>muted=v,now:()=>now});
 assert.equal(muted,true);c.tick(0,.1);assert.equal(c.audible(),false);
 c.speak('Authorized answer');now+=800;c.tick(0,.1);assert.equal(muted,true);
 now+=80;c.tick(0,0);now+=750;c.tick(0,0);assert.equal(muted,false);assert.equal(c.audible(),true);
 assert.equal(sent.at(-1).content,'Authorized answer');
 c.tick(0,.1);now+=100;c.tick(0,0);now+=1100;c.tick(0,0);assert.equal(muted,true);c.close();
});
test('confirmed new speech mutes audio and invalidates pending results',()=>{
 let now=100,muted=true;const c=create({dispatch(){},send(){},setMuted:v=>muted=v,now:()=>now});
 c.speak('Answer');c.tick(0,0);now+=800;c.tick(0,0);assert.equal(muted,false);
 const rev=c.state().revision;c.input('New user request');assert.equal(muted,true);assert.equal(c.accepts(rev),false);c.close();
});
test('closed or superseded input does not dispatch a second operation',async()=>{
 const calls=[];const c=create({dispatch:t=>calls.push(t),send(){},setMuted(){},settleMs:5});
 c.input('find');c.input('find a book');await new Promise(r=>setTimeout(r,20));assert.deepEqual(calls,['find a book']);
 c.input('late');c.close();await new Promise(r=>setTimeout(r,20));assert.equal(calls.length,1);
});

test('microphone activity alone cannot discard the authorized reply',()=>{
 let now=100,muted=true;const c=create({dispatch(){},send(){},setMuted:v=>muted=v,now:()=>now});
 const rev=c.state().revision;c.speak('Answer');c.tick(.2,0);now+=800;c.tick(.2,0);
 assert.equal(muted,false);assert.equal(c.accepts(rev),true);c.tick(.2,.2);assert.equal(muted,false);c.close();
});

test('a pending Live handoff receives the existing result without starting another task',()=>{
 let now=100,id='first';const events=[];const c=create({dispatch(){},send:e=>events.push(e),setMuted(){},now:()=>now});
 c.speak('{"speech":"Result"}',undefined,()=>id);id='current';c.tick(0,0);now+=800;c.tick(0,0);
 assert.equal(events.at(-1).type,'session.commentary.append');assert.equal(events.at(-1).delegation_id,'current');c.close();
});
