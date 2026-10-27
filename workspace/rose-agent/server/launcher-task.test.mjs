import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {LauncherTask} from './launcher-task.mjs';

test('single task remains running through progress, follow-ups and a proposed answer until completion',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'launcher-task-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 let now=100;const state=new LauncherTask(path.join(dir,'state.json'),{now:()=>now});
 assert.equal(state.snapshot().current,null);
 state.start('Check flights for Wednesday');const id=state.current.id;
 now=110;state.progress('Comparing the two airlines.');state.followup('Arrive before noon.');state.result('Proposed answer');
 assert.equal(state.current.id,id);assert.equal(state.current.state,'running');
 assert.equal(state.current.request,'Check flights for Wednesday');
 state.checking();assert.equal(state.current.state,'checking');
 state.running();state.progress('Checking the date.');assert.equal(state.current.state,'running');
 now=120;state.finish('completed','Final answer');
 assert.equal(state.current.finishedAt,120);assert.equal(state.current.result,'Final answer');
 state.progress('Late progress');state.finish('failed','Late failure');
 assert.equal(state.current.state,'completed');assert.equal(state.current.progress,'Checking the date.');
 await state.flush();
 const restored=new LauncherTask(state.file,{now:()=>130});assert.deepEqual(restored.snapshot(),{version:1,current:{...state.current,widgetStatus:'unavailable'}});
 assert.equal(fs.statSync(state.file).mode&0o777,0o600);
});

test('restart never presents an old running task as still live; next task replaces it',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'launcher-task-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const first=new LauncherTask(path.join(dir,'state.json'));first.start('Old task');await first.flush();
 const restored=new LauncherTask(first.file);assert.equal(restored.current.state,'interrupted');
 assert.match(restored.current.result,/outcome is unknown/);const id=restored.current.id;
 restored.start('New task');assert.notEqual(restored.current.id,id);assert.equal(restored.current.state,'running');assert.equal(restored.current.result,'');
 restored.finish('failed','Task failed');await restored.flush();assert.equal(restored.current.state,'failed');
});

test('status persistence failure has no effect on task execution or in-memory display',async()=>{
 const state=new LauncherTask('/dev/null/cannot-write');state.start('Read only display');state.progress('Working');state.finish('completed','Done');await state.flush();
 assert.equal(state.current.state,'completed');assert.equal(state.current.result,'Done');
});
