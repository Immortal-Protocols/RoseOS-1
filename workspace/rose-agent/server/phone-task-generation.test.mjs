import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';

// control.py tags phone commands with the task generation it read at startup.
const tools=new URL('../phone/',import.meta.url).pathname;
function headers(generation){
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'task-generation-'));const config=path.join(home,'.config/pixel-voice');
 fs.mkdirSync(config,{recursive:true});fs.writeFileSync(path.join(config,'pair-token'),'test-token\n');
 if(generation!==undefined)fs.writeFileSync(path.join(config,'task-generation'),generation);
 const script='import ssl,sys,json;ssl.create_default_context=lambda **k:type("C",(),{})();sys.path.insert(0,sys.argv[1]);import control;h=control.request_headers();h.pop("Authorization");print(json.dumps(h))';
 return JSON.parse(execFileSync('python3',['-I','-c',script,tools],{env:{...process.env,HOME:home},encoding:'utf8'}));
}
test('the current generation is sent as X-Rose-Task',()=>{
 assert.equal(headers('3f1c2b9e-0d4a-4c51-9b7e-1a2b3c4d5e6f\n')['X-Rose-Task'],'3f1c2b9e-0d4a-4c51-9b7e-1a2b3c4d5e6f');
});
test('a missing or malformed generation sends no task identity',()=>{
 assert.equal(headers()['X-Rose-Task'],undefined);
 assert.equal(headers('bad value; rm -rf /')['X-Rose-Task'],undefined);
});
