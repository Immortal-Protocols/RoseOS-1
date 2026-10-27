import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ToolActivity,activityLabel} from './tool-activity.mjs';
import {LauncherTask} from './launcher-task.mjs';

const start=(activity,id='one')=>activity.event('turn/started',{turn:{id}});
const item=(activity,method,id,type,turnId='one',extra={})=>activity.event(method,{turnId,item:{id,type,...extra}});

test('tool events fill a silent task without replacing commentary, result or lifecycle',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'tool-activity-'));
 const state=new LauncherTask(path.join(dir,'task.json'));
 t.after(async()=>{await state.flush();fs.rmSync(dir,{recursive:true,force:true});});
 const activity=new ToolActivity(text=>state.activity(text));state.start('Find current information');start(activity);
 item(activity,'item/started','search','webSearch');
 assert.equal(state.current.activity,'Searching the web');assert.equal(state.current.progress,'');
 state.progress('Comparing the two sources.');
 item(activity,'item/completed','search','webSearch');
 assert.equal(state.current.activity,'');assert.equal(state.current.progress,'Comparing the two sources.');
 item(activity,'item/started','page','webSearch','one',{action:{type:'openPage'}});
 state.result('Final answer');state.finish('completed');
 item(activity,'item/completed','page','webSearch');
 assert.equal(state.current.state,'completed');assert.equal(state.current.result,'Final answer');assert.equal(state.current.activity,'');
});

test('finishing one overlapping tool leaves the other visible',()=>{
 const seen=[],activity=new ToolActivity(text=>seen.push(text));start(activity);
 item(activity,'item/started','search','webSearch');
 item(activity,'item/started','file','commandExecution','one',{commandActions:[{type:'read'}]});
 item(activity,'item/completed','file','commandExecution');
 assert.deepEqual(seen,['Searching the web','Reading files','Searching the web']);
 item(activity,'item/completed','search','webSearch');assert.equal(seen.at(-1),'');
});

test('late events and unknown tool progress cannot contaminate a later task',()=>{
 const seen=[],activity=new ToolActivity(text=>seen.push(text));start(activity);
 item(activity,'item/started','old','webSearch');start(activity,'two');
 item(activity,'item/started','new','fileChange','two');
 item(activity,'item/completed','old','webSearch');
 activity.event('item/mcpToolCall/progress',{turnId:'one',itemId:'old',message:'Old task'});
 activity.event('turn/completed',{turn:{id:'one'}});
 assert.equal(seen.at(-1),'Updating files');
 activity.event('item/mcpToolCall/progress',{turnId:'two',itemId:'unknown',message:'Unknown task'});
 assert.equal(seen.at(-1),'Updating files');
 activity.event('turn/completed',{turn:{id:'two'}});assert.equal(seen.at(-1),'');
});

test('tool-provided progress is used while the matching tool runs',()=>{
 const seen=[],activity=new ToolActivity(text=>seen.push(text));start(activity);
 item(activity,'item/started','app','mcpToolCall');
 activity.event('item/mcpToolCall/progress',{turnId:'one',itemId:'app',message:'Loading\n  page'});
 assert.equal(seen.at(-1),'Loading page');
 item(activity,'item/completed','app','mcpToolCall');
 activity.event('item/mcpToolCall/progress',{turnId:'one',itemId:'app',message:'Too late'});
 assert.equal(seen.at(-1),'');
});

test('labels do not expose raw commands, paths, queries or tool arguments',()=>{
 assert.equal(activityLabel({type:'commandExecution',command:'secret',commandActions:[{type:'read',path:'/secret'}]}),'Reading files');
 assert.equal(activityLabel({type:'webSearch',query:'private query',action:{type:'openPage',url:'private'}}),'Reading a web page');
 assert.equal(activityLabel({type:'dynamicToolCall',tool:'private',arguments:{secret:'value'}}),'Using an app tool');
 assert.equal(activityLabel({type:'agentMessage',text:'Private answer'}),'');
});
