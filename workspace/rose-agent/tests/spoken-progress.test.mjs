// Server task progress, heartbeat and failure lines (server.mjs speakProgress → /events 'live'
// session.commentary.append) reach the live conversation's announce() once, under the page's existing
// eligibility, and retained or repeated events are never spoken. Real page script; fake provider and backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import {loadPage} from './web-ui-harness.mjs';

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const live=(seq,content)=>({seq,type:'live',event:{type:'session.commentary.append',delegation_id:'d1',content}});

// A page with a live call whose provider records announce(); /events answers from window.__replies in order.
function livePage({announce=true}={}){
 const page=loadPage();
 page.run(`var spoken=[];var fake={muted:false,closed:false,setMuted(v){this.muted=!!v;},inputLevel:()=>0,outputLevel:()=>0,
  close(){this.closed=true;return Promise.resolve();},sendContext(){},sendMessage(){}${announce?",announce(t){spoken.push(t);}":''}};
  window.__replies=[];api=(method,route)=>route.startsWith('/events')?Promise.resolve(window.__replies.shift()||{epoch:'e1',events:[]}):Promise.resolve({});
  conversation=fake;ready=true;starting=false;ended=false;muted=false;guardianMode=false;`);
 return page;
}
const reply=(page,events,epoch='e1')=>{page.window.__replies.push({epoch,events});};
async function poll(page){page.run('poll()');await settle();}
const spoken=page=>JSON.parse(page.run('JSON.stringify(spoken)'));

test('R18: progress, heartbeat and failure lines are announced once during a live call',async()=>{
 const page=livePage();
 reply(page,[]);await poll(page); // first poll of this page: nothing retained to speak
 reply(page,[live(1,'Still looking up the opening hours.'),{seq:2,type:'status',status:'Codex working'}]);await poll(page);
 reply(page,[live(3,'Still running.')]);await poll(page);
 reply(page,[live(4,'The task stopped: Calculator was not available.')]);await poll(page);
 assert.deepEqual(spoken(page),['Still looking up the opening hours.','Still running.','The task stopped: Calculator was not available.']);
});

test('retained events from before this page or call are never spoken',async()=>{
 const page=livePage();
 reply(page,[live(1,'Old progress from an earlier session.'),live(2,'Old failure line.')]);await poll(page);
 assert.deepEqual(spoken(page),[],'first poll after load replays the retained queue silently');
 // Events emitted while no call was live, then the call starts: only later events are spoken.
 const idle=livePage();
 idle.run('ready=false;');reply(idle,[]);await poll(idle);
 reply(idle,[live(5,'Progress while idle.')]);await poll(idle);
 idle.run('ready=true;');reply(idle,[]);await poll(idle);
 reply(idle,[live(6,'Progress during the call.')]);await poll(idle);
 assert.deepEqual(spoken(idle),['Progress during the call.']);
});

test('a repeated event is announced only once',async()=>{
 const page=livePage();
 reply(page,[]);await poll(page);
 reply(page,[live(1,'Working on it.')]);await poll(page);
 page.run('lastEvent=0;');reply(page,[live(1,'Working on it.')]);await poll(page); // same seq delivered again
 assert.deepEqual(spoken(page),['Working on it.']);
});

test('existing eligibility: muted, Guardian, ended call or a stopped task is not spoken to',async()=>{
 for(const setup of ['muted=true','guardianMode=true','ready=false','conversation=null','globalThis.taskStopSpeechPending=true']){
  const page=livePage();
  reply(page,[]);await poll(page);
  page.run(setup+';');reply(page,[live(1,'Still running.')]);await poll(page);
  assert.deepEqual(spoken(page),[],setup);
 }
});

test('malformed or unrelated live events are ignored',async()=>{
 const page=livePage();
 reply(page,[]);await poll(page);
 reply(page,[{seq:1,type:'live'},{seq:2,type:'live',event:{type:'session.closed'}},{seq:3,type:'live',event:{type:'session.commentary.append',content:''}},
  {seq:4,type:'live',event:{type:'session.commentary.append',content:{text:'x'}}},{seq:5,type:'live',event:{type:'session.commentary.append',content:'x'.repeat(5000)}}]);
 await poll(page);
 assert.deepEqual(spoken(page),[]);
});

test('a provider without announce() is left alone',async()=>{
 const page=livePage({announce:false});
 reply(page,[]);await poll(page);
 reply(page,[live(1,'Still running.')]);await poll(page);
 assert.equal(page.run('lastEvent'),1,'the event cursor still advances');
});
