import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {VoiceRouting} from './voice-routing.mjs';
test('trial defaults to existing behavior and rolls back with one setting',()=>{
 const d=fs.mkdtempSync(path.join(os.tmpdir(),'voice-routing-'));try{const config=new VoiceRouting(d+'/mode.json');assert.equal(config.read().mode,'live');assert.equal(config.set('luna').mode,'luna');assert.equal(new VoiceRouting(d+'/mode.json').read().mode,'luna');assert.equal(config.set('live').mode,'live');assert.throws(()=>config.set('unknown'));}finally{fs.rmSync(d,{recursive:true});}
});
