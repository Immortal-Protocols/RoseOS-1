import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const Transport=createRequire(import.meta.url)('../app/assets/task-status-transport.js');
test('push wins over in-flight polling and reconnect receives the latest snapshot',()=>{
 const states=[],availability=[],t=new Transport({state:s=>states.push(s),stream:s=>availability.push(s)});
 t.polling({current:'initial'});
 t.receive({type:'transport',connected:true});t.receive({type:'hello',taskStatus:{current:'running'}});
 t.polling({current:'stale poll'});t.failed();
 t.receive({type:'task_status',taskStatus:{current:'finished'}});
 assert.deepEqual(states,[{current:'initial'},{current:'running'},{current:'finished'}]);
 t.receive({type:'transport',connected:false});t.polling({current:'poll fallback'});
 t.receive({type:'transport',connected:true});t.receive({type:'hello',taskStatus:{current:'new task'}});
 assert.deepEqual(availability,[true,false,true]);assert.equal(states.at(-1).current,'new task');
});
