import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {createRequire} from 'node:module';
import {MediaWorkerConfig} from '../config.mjs';import {PhoneCommands} from '../../server/phone.mjs';
import vm from 'node:vm';
// The page loads this as a classic script; workspace/package.json is type:module, so require() would load it as an ES
// module where `module` is undefined and nothing is exported. Load it the way the page does.
const pageScript=rel=>{const window={};vm.runInThisContext('(function(window){'+fs.readFileSync(new URL(rel,import.meta.url),'utf8')+'\n})',{filename:rel})(window);return window;};
const create=pageScript('../media-tool.js').createMediaTool,match=pageScript('../../voice-providers/voice-commands.js').resolveVoiceCommand;
test('off by default; disable immediately rejects new media work',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-media-'));
 try{const config=new MediaWorkerConfig(path.join(dir,'config.json'));const commands=new PhoneCommands(()=>1000,()=>config.read().enabled);
  assert.equal(config.read().enabled,false);assert.throws(()=>commands.create({action:'media_control',app:'apple_music',operation:'pause'}));
  config.set(true);const r=commands.create({action:'media_control',app:'apple_music',operation:'pause'});assert.equal(r.operation,'pause');
  for(const extra of [{app:'any_app'},{operation:'toggle'},{operation:'next'},{operation:'delete'}])assert.throws(()=>commands.create({action:'media_control',app:'apple_music',operation:'pause',...extra}));
  commands.complete(r.id,{status:'verified_paused'});commands.complete(r.id,{status:'error'});assert.equal(commands.get(r.id).result.status,'verified_paused');
  config.set(false);assert.throws(()=>commands.create({action:'media_control',app:'apple_music',operation:'get_state'}));
  assert.equal(commands.create({action:'apple_music_run',title:'Dreams'}).action,'apple_music_run');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('narrow media routing preserves book/podcast controls and rollback',()=>{
 assert.equal(match('Pause the music',{media:true}).name,'media_control');
 assert.equal(match('Resume the music',{media:true}).params.operation,'resume');
 assert.equal(match('What song is playing?',{media:true}).params.operation,'get_state');
 assert.equal(match('Pause',{media:true,podcast:true}).name,'podcast_control');
 assert.equal(match('Keep reading',{media:true,book:true}).name,'book_control');
 assert.equal(match('Pause the music',{media:false}),null);
 assert.equal(match('What song is playing?',{media:false}),null);
 assert.equal(match('My friend said pause the music',{media:true}),null);
 assert.equal(match('Play Dreams by Fleetwood Mac',{media:true}).name,'apple_music_play');
 assert.equal(match('Play the album Grace',{media:true}),null);
});
test('one request and polling, never a second command on uncertain outcome',async()=>{
 let posts=0,polls=0;const tool=create({delay:async()=>{},api:async(method,route,body)=>{if(method==='POST'){posts++;assert.deepEqual(body,{action:'media_control',app:'apple_music',operation:'pause'});return {id:'one',status:'pending'};}polls++;return {status:'completed',result:{status:'verified_paused'}};}});
 assert.equal(JSON.parse(await tool({operation:'pause'})).status,'verified_paused');assert.equal(posts,1);assert.equal(polls,1);
 const failed=create({api:async()=>{throw Error('Disconnected');}});assert.equal(JSON.parse(await failed({operation:'resume'})).status,'outcome_unknown');
});
