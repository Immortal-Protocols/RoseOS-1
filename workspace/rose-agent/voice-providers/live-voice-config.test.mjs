import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {LiveVoiceConfig} from './live-voice-config.mjs';
import {openaiVoiceSession} from './openai-session.mjs';
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-live-voice-'));
process.on('exit',()=>fs.rmSync(temp,{recursive:true,force:true}));
test('without a saved profile the existing voice is unchanged',()=>{
 const p=new LiveVoiceConfig(path.join(temp,'missing.json')).read();assert.equal(p.voice,'vesper');assert.equal(p.instructions,'');
});
test('saved profile supplies the custom voice and its design only at session creation',()=>{
 const file=path.join(temp,'profile.json');fs.writeFileSync(file,JSON.stringify({id:'voice_example',name:'SoCal',prompt:'An adult SoCal voice.',languageStyle:'Use light, casual slang.'}));
 const p=new LiveVoiceConfig(file).read(),s=openaiVoiceSession('Base instructions\n'+p.instructions,[],new Date(0),p.voice);
 assert.deepEqual(s.audio.output.voice,{id:'voice_example'});assert.match(s.instructions,/adult SoCal/);assert.match(s.instructions,/Base instructions/);assert.match(s.instructions,/casual slang/);
});
test('invalid profiles fail rather than silently claiming a custom voice',()=>{
 const file=path.join(temp,'invalid.json');fs.writeFileSync(file,JSON.stringify({id:'bad',prompt:'voice'}));assert.throws(()=>new LiveVoiceConfig(file).read(),/Invalid saved/);
});
test('saved project and audition prompt are preserved without conflicting design instructions',()=>{
 const file=path.join(temp,'audition.json');fs.writeFileSync(file,JSON.stringify({id:'voice_example',project:'proj_example',prompt:'A restrained voice.',sessionPrompt:'super exaggerated Gen Z surfer-bro'}));
 const p=new LiveVoiceConfig(file).read();assert.equal(p.project,'proj_example');assert.equal(p.instructions,'super exaggerated Gen Z surfer-bro');
});
