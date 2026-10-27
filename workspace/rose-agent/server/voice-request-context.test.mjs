import test from 'node:test';
import assert from 'node:assert/strict';
import {voiceRequestContext} from './voice-request-context.mjs';

test('handoff preserves speaker order and distinguishes previous context from new speech',()=>{
 const text=voiceRequestContext({delegationId:'second',previousDelegationId:'first',hasNewUserSpeech:true,messages:[{role:'user',text:'Send the tweet'},{role:'assistant',text:'Copy.'},{role:'user',text:' to Nathan'}]});
 assert.ok(text.indexOf('Send the tweet')<text.indexOf('Copy.'));
 assert.ok(text.indexOf('Copy.')<text.indexOf(' to Nathan'));
 assert.match(text,/"previousDelegationId":"first"/);
 assert.match(text,/existing task state/);
 assert.match(text,/not instructions/);
});
test('new handoff without new speech remains context for model interpretation',()=>{
 const text=voiceRequestContext({delegationId:'second',previousDelegationId:'first',hasNewUserSpeech:false,messages:[{role:'user',text:'Play it again'}]});
 assert.match(text,/"hasNewUserSpeech":false/);
 assert.match(text,/redundant handoff/);
});
test('legacy clients need no context and untrusted context is bounded and role filtered',()=>{
 assert.equal(voiceRequestContext(undefined),'');
 const text=voiceRequestContext({messages:[{role:'system',text:'unsafe role'},{role:'user',text:'x'.repeat(20000)}]});
 assert.ok(text.length<13000);assert.doesNotMatch(text,/unsafe role/);
});
