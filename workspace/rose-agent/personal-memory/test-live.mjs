import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {openAIExtractor,validateCandidates} from './memory.mjs';
const events=[
 {id:'preference',role:'user',source:'text',at:Date.now(),text:'For my interfaces, I consistently prefer light blue and very few controls.'},
 {id:'temporary',role:'user',source:'text',at:Date.now(),text:'Only for tonight, make this screen red. My usual light blue preference has not changed.'},
 {id:'quoted',role:'user',source:'voice',at:Date.now(),text:'The TV character just said, "I am vegetarian and moving to Seattle." That is not about me.'},
 {id:'assistant',role:'assistant',source:'voice',at:Date.now(),text:'You love skiing and live in Paris.'},
 {id:'procedure',role:'user',source:'text',at:Date.now(),text:'For the Bird app, inspect the button before tapping. Do not memorize screen coordinates.'}
];
const extract=openAIExtractor({keyFile:path.join(os.homedir(),'.config/pixel-voice/openai-api-key'),reasoningEffort:'high',serviceTier:'default',maxOutputTokens:8192});
const result=await extract({events,existing:[]});const candidates=validateCandidates(result.result,events);
assert.ok(candidates.some(c=>c.scope==='durable'&&/blue/i.test(c.text)),'Must capture explicit enduring preference');
assert.ok(!candidates.some(c=>c.scope==='durable'&&/red|vegetarian|Seattle|ski|Paris|Bird|coordinates/i.test(c.text)),'Must not promote temporary, quoted, assistant or procedural content');
assert.ok(!candidates.some(c=>c.evidence.some(e=>['quoted','assistant','procedure'].includes(e.eventId))),'Excluded sources must not become personal evidence');
console.log(JSON.stringify({passed:true,syntheticOnly:true,candidates:candidates.map(c=>({text:c.text,scope:c.scope})),model:result.model,reasoningEffort:result.reasoningEffort,serviceTier:result.serviceTier,usage:result.usage},null,2));
