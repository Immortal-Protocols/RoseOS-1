import test from 'node:test';
import assert from 'node:assert/strict';
import {loadPage} from './web-ui-harness.mjs';

const task=(id,status='running',extra={})=>({id,kind:'phone',status,goal:'Task '+id,
 started:100+id,finished:status==='running'?null:200+id,detail:'Opening app '+id,result:'',
 contract:1,server:'synthetic-server',...extra});
async function page(){
 const p=loadPage({native:{localRose:()=>true}});
 p.run('start(false)');await new Promise(r=>setImmediate(r));
 p.send=m=>p.run(`window.localRoseEvent?.(${JSON.stringify(m)})`);
 p.send({type:'status',status:'live',route:'wifi'});
 p.card=()=>JSON.parse(p.calls.filter(c=>c[0]==='taskState').at(-1)?.[1]||'{}');
 return p;
}

test('local task progress reaches the main native task card, without raw screen or tool payloads',async()=>{
 const p=await page();
 p.send({type:'task',task:task(1,'running',{steps:[{screen:'PRIVATE_SCREEN'}],detail:'Opening Amazon'})});
 assert.equal(p.card().version,1);
 assert.equal(p.card().current.state,'running');
 assert.equal(p.card().current.source,'local');
 assert.equal(p.card().current.title,'Task 1');
 assert.equal(p.card().current.activity,'Opening Amazon');
 assert.ok(!JSON.stringify(p.card()).includes('PRIVATE_SCREEN'));
 p.send({type:'task',task:task(1,'running',{detail:'Looking through Amazon'})});
 assert.equal(p.card().current.activity,'Looking through Amazon');
});

test('parallel tasks share the current card; finishing one keeps the other visible; final result is plain text',async()=>{
 const p=await page();
 p.send({type:'task',task:task(1)});p.send({type:'task',task:task(2)});
 assert.match(p.card().current.title,/2/);
 assert.match(p.card().current.activity,/Opening app 1/);
 assert.match(p.card().current.activity,/Opening app 2/);
 p.send({type:'task',task:task(1,'done',{result:'First task finished.'})});
 assert.equal(p.card().current.state,'running');
 assert.equal(p.card().current.title,'Task 2');
 p.send({type:'task',task:task(2,'done',{result:'Second task finished.'})});
 assert.equal(p.card().current.state,'completed');
 assert.equal(p.card().current.result,'Second task finished.');
 assert.deepEqual(p.card().current.widget,{type:'Text',value:'Second task finished.'});
});

test('ending local voice clears its card; an older connection cannot clear the new one',async()=>{
 const p=await page();p.send({type:'task',task:task(1)});
 const old=p.context.localRoseEvent;
 p.run('cleanup("user_end")');
 assert.equal(p.card().current,null);
 p.run('ended=false;start(false)');await new Promise(r=>setImmediate(r));
 p.send({type:'status',status:'live',route:'wifi'});p.send({type:'task',task:task(2)});
 old({type:'closed',code:4001});
 assert.equal(p.card().current.title,'Task 2');
});

test('lost local events mark the retained card unavailable; a new task update restores it',async()=>{
 const p=await page();p.send({type:'task',task:task(1)});
 p.send({type:'status',status:'reconnecting'});
 assert.ok(p.calls.some(c=>c[0]==='taskStateUnavailable'));
 assert.equal(p.card().current.title,'Task 1');
 p.send({type:'task',task:task(1,'running',{detail:'Checking the app'})});
 assert.equal(p.card().current.activity,'Checking the app');
 assert.equal(p.calls.filter(c=>c[0]==='taskStateStream').at(-1)[1],true);
});
