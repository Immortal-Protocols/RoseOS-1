import {test} from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {ConversationMode,conversationSession} from './mode.mjs';
function fixture(t,fn){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'meeting-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return new ConversationMode(path.join(dir,'meeting.json'),{client:{responses:{create:fn}}});}
test('notes-only model has no tools, fixed requested settings, persists notes before reply and deduplicates turns',async t=>{let calls=0;const mode=fixture(t,async r=>{calls++;assert.deepEqual(r.tools,[]);assert.equal(r.model,'gpt-6.1-sol');assert.equal(r.reasoning.effort,'low');assert.equal(r.service_tier,'ultrafast');assert.equal(r.store,false);return {output_text:JSON.stringify({reply:'We can discuss that.',appendNote:'Decision: use a notebook.'}),service_tier:'ultrafast'};});mode.prepare({title:'Planning',notes:'Project context'});mode.start();await mode.run('Write this down','id1');await mode.run('Write this down','id1');assert.equal(calls,1);assert.equal(mode.snapshot().meeting.notebook.length,1);const restored=new ConversationMode(mode.file);assert.equal(restored.snapshot().meeting.turns.length,1);assert.equal(restored.snapshot().meeting.notebook[0].text,'Decision: use a notebook.');mode.end();assert.equal(mode.snapshot().active,false);assert.throws(()=>mode.run('Run a shell','id2'));});
test('bad replies do not record a turn or pretend success; inactive preparation does not activate',async t=>{const mode=fixture(t,async()=>({output_text:'not json'}));mode.prepare({title:'Meeting',notes:'Notes'});assert.equal(mode.snapshot().active,false);mode.start();await assert.rejects(mode.run('hello','x'));assert.equal(mode.snapshot().meeting.turns.length,0);assert.equal(mode.snapshot().meeting.notebook.length,0);assert.throws(()=>mode.prepare({title:'Replacement',notes:'No'}));});

test('live session preloads notes and notebook with direct answers and no normal routing history',()=>{const s=conversationSession({title:'Review',notes:'The release is pending.',notebook:[{text:'Decision: test first.'}]});assert.equal(s.model,'gpt-live-1');assert.deepEqual(s.input,[]);assert.match(s.instructions,/The release is pending/);assert.match(s.instructions,/Decision: test first/);assert.match(s.instructions,/directly without delegation/);assert.doesNotMatch(s.instructions,/Always call.*EVERY/);});

test('failed notebook commit never returns or deduplicates unsaved success',async t=>{
 let calls=0;
 const mode=fixture(t,async()=>{calls++;return {output_text:JSON.stringify({reply:'Recorded decision.',appendNote:'Decision: test before release.'})};});
 mode.prepare({title:'Persistence',notes:'Test session'});mode.start();
 const before=mode.snapshot();const save=mode.save.bind(mode);
 mode.save=()=>{throw Error('disk write failed');};
 await assert.rejects(mode.run('Record our decision','retry-key'),/disk write failed/);
 assert.deepEqual(mode.snapshot(),before);
 assert.deepEqual(new ConversationMode(mode.file).snapshot(),before);
 mode.save=save;
 await mode.run('Record our decision','retry-key');
 await mode.run('Record our decision','retry-key');
 assert.equal(calls,2);
 const restored=new ConversationMode(mode.file).snapshot().meeting;
 assert.equal(restored.notebook.length,1);assert.equal(restored.turns.length,1);
});

test('failed mode transition leaves saved and in-memory states unchanged',t=>{
 const mode=fixture(t,async()=>{});mode.prepare({title:'Persistence',notes:'Test session'});
 const before=mode.snapshot();mode.save=()=>{throw Error('disk write failed');};
 assert.throws(()=>mode.start(),/disk write failed/);
 assert.deepEqual(mode.snapshot(),before);
 assert.deepEqual(new ConversationMode(mode.file).snapshot(),before);
});

test('explicit handoff intent uses host receipt; ordinary notes and call end never start work',async t=>{
 let starts=0,requested=false;
 const mode=fixture(t,async()=>({output_text:JSON.stringify({reply:'A model claim is not launch evidence.',appendNote:'Must not save a model launch claim.',startWork:requested})}));
 mode.onStartWork=async request=>{starts++;assert.equal(request.authorized,true);return {status:'started',url:'https://chatgpt.com/codex/tasks/task_test'};};
 mode.prepare({title:'Handoff',notes:'Action item: test.'});mode.start();
 await mode.run('Record an action item.','note');assert.equal(starts,0);
 requested=true;assert.match(await mode.run('Start the agreed work.','start'),/^Work has started/);
 await mode.run('Start the agreed work.','start');assert.equal(starts,1);
 assert.equal(mode.current().notebook.filter(n=>n.key==='start').length,0);
 mode.end();assert.equal(starts,1);
});
test('unconfigured or uncertain handoff never reports work started; voice design reaches conversation sessions',async t=>{
 const mode=fixture(t,async()=>({output_text:JSON.stringify({reply:'Started!',appendNote:'Started!',startWork:true})}));
 mode.prepare({title:'Handoff',notes:'Test'});mode.start();
 mode.onStartWork=async()=>{throw Error('Select an environment first');};
 assert.match(await mode.run('Start work','blocked'),/^Work has not started/);
 mode.onStartWork=async()=>({status:'uncertain'});
 assert.match(await mode.run('Start work','uncertain'),/^Work submission is uncertain/);
 assert.equal(mode.current().notebook.length,0);
 assert.match(conversationSession(mode.current(),{id:'voice_test'},'Voice design: warm Chicago baritone.').instructions,/warm Chicago baritone/);
});

test('cycle cleanup archives completed progress and exact duplicates without losing open work or decisions',t=>{
 const mode=fixture(t,async()=>{});mode.prepare({title:'Cycle',notes:'Context'});mode.start();const id=mode.current().id;
 mode.append(id,'Decision: preserve history','d1');mode.append(id,'Decision: preserve history','d2');
 mode.progress({meetingId:id,item:4,status:'completed',summary:'Done',key:'done'});
 mode.progress({meetingId:id,item:5,status:'blocked',summary:'Needs account',key:'blocked'});
 const count=mode.current().notebook.length;mode.end();const m=mode.current();
 assert.equal(m.notebook.length+m.archive.length,count);assert.equal(m.archive.length,2);
 assert.ok(m.notebook.some(n=>n.text.includes('Needs account')));assert.ok(m.notebook.some(n=>n.text.startsWith('Decision:')));
 mode.progress({meetingId:id,item:4,status:'completed',summary:'Done',key:'done'});assert.equal(mode.current().notebook.length,2);
 mode.start();assert.equal(mode.state.active,true);assert.equal(mode.current().id,id);
});

test('live context retains latest item status when historical progress is archived',()=>{
 const session=conversationSession({title:'Review',notes:'Old briefing',notebook:[],actionItems:{4:{status:'completed',summary:'Toggle installed'}}});
 assert.match(session.instructions,/Toggle installed/);assert.match(session.instructions,/completed/);
});
