import test from 'node:test';import assert from 'node:assert/strict';import {Codex} from '../server/codex.mjs';
const attachment={type:'localImage',path:'/private/test/photo.jpg'};
// submit() first prepares the model route and backend instructions; those need a live backend, so only they are stubbed.
const codex=fields=>Object.assign(Object.create(Codex.prototype),{prepareRoute:async()=>{},ensureInstructions:async()=>{}},fields);
test('new backend turn receives an image part, not only a filename in text',async()=>{
 const calls=[],fake=codex({active:null,thread:'thread',rpc:async(method,params)=>{calls.push({method,params});return {turn:{id:'turn'}};}});
 await Codex.prototype.submit.call(fake,'What is this?',{model:'model',effort:'low'},[attachment]);
 assert.deepEqual(calls[0].params.input,[{type:'text',text:'What is this?'},attachment]);assert.equal(calls[0].method,'turn/start');
});
test('ongoing backend turn receives the same image through steering',async()=>{
 const calls=[],fake=codex({active:'active-turn',thread:'thread',rpc:async(method,params)=>{calls.push({method,params});return {};}});
 await Codex.prototype.submit.call(fake,'Look at my photo',{},[attachment]);
 assert.equal(calls[0].method,'turn/steer');assert.deepEqual(calls[0].params.input[1],attachment);assert.equal(calls[0].params.expectedTurnId,'active-turn');
});
