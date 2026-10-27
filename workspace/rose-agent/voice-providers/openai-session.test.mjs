import test from 'node:test';import assert from 'node:assert/strict';import {openaiVoiceSession} from './openai-session.mjs';
test('old acknowledgments remain historical context rather than active assistant turns',()=>{
 const s=openaiVoiceSession('Current rules',[{messages:[{role:'user',text:'Play Dreams by Fleetwood Mac.'},{role:'assistant',text:'Cueing it up.'}]}]);
 assert.deepEqual(s.input,[]);assert.match(s.instructions,/Historical conversation/);assert.match(s.instructions,/Play Dreams/);assert.match(s.instructions,/Cueing it up/);assert.match(s.instructions,/not new requests/);assert.match(s.instructions,/Current rules/);assert.equal(s.store,false);
});
