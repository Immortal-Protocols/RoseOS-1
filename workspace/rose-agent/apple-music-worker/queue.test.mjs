import test from 'node:test';import assert from 'node:assert/strict';import {PhoneCommands} from '../server/phone.mjs';
test('narrow local command validates input and preserves ordinary command expiration',()=>{
 const q=new PhoneCommands(()=>1000);assert.equal(q.create({action:'apple_music_run',title:'a'}).title,'a');assert.throws(()=>q.create({action:'apple_music_run',title:''}));assert.throws(()=>q.create({action:'apple_music_run',title:'a',artist:12}));assert.throws(()=>q.create({action:'apple_music_run',title:'a',artist:'b',pauseAfter:'yes'}));
 const c=q.create({action:'apple_music_run',title:'Hello',artist:'Adele',pauseAfter:true});assert.equal(c.expires,61000);assert.equal(c.artist,'Adele');assert.equal(c.pauseAfter,true);
 assert.equal(q.create({action:'inspect_ui'}).expires,21000);q.complete(c.id,{status:'verified_playing'});assert.equal(q.get(c.id).result.status,'verified_playing');
});
