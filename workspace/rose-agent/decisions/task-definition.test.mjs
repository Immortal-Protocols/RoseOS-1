import test from 'node:test';
import assert from 'node:assert/strict';
import {taskDefinition,observedCompletion,isLeaveAppRequest} from './task-definition.mjs';
const anime={packageName:'dev.pixel.anime',label:'Anime'};
test('ordinary close app binds original target and leave-foreground completion',()=>{
 const task=taskDefinition('Close the app',anime,anime);
 assert.equal(task.completion.kind,'leave_app');assert.ok(Object.isFrozen(task.targetApp));
 assert.equal(observedCompletion(task,{status:'ok',packageName:'dev.pixel.voice'},[]),null);
 assert.ok(observedCompletion(task,{status:'ok',packageName:'dev.pixel.voice'},[{action:'home',result:'action_accepted'}]));
 assert.equal(task.targetApp.packageName,'dev.pixel.anime');
 assert.equal(observedCompletion(task,{status:'ok',packageName:'dev.pixel.anime'},[{action:'home',result:'action_accepted'}]),null);
});
test('force stopping or closing a tab is not silently redefined as leaving foreground',()=>{
 for(const text of ['Force close the app','Close the app and delete it','Close the browser tab','Close the keyboard','Close the account','Close the app from recents'])assert.equal(isLeaveAppRequest(text),false,text);
});
test('explicit named app already in background need not be reopened',()=>{
 const task=taskDefinition('Close Anime',{packageName:'dev.pixel.voice'},anime);
 assert.ok(observedCompletion(task,{status:'ok',packageName:'dev.pixel.voice'},[]));
});
