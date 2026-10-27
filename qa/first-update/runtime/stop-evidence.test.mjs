// Self-test of the runtime analyzer on synthetic logs (no runtime involved).
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {spawnSync} from 'node:child_process';
const tool=new URL('./stop-evidence.mjs',import.meta.url).pathname;
const run=lines=>{const d=fs.mkdtempSync(path.join(os.tmpdir(),'qa-rt-'));fs.writeFileSync(path.join(d,'events.jsonl'),lines.map(l=>JSON.stringify(l)).join('\n'));const r=spawnSync(process.execPath,[tool,d],{encoding:'utf8'});return {code:r.status,report:JSON.parse(r.stdout)};};
const t=s=>new Date(Date.UTC(2026,9,27,19,0,s)).toISOString();
test('passes when only rejections follow the acknowledgment',()=>{
 const r=run([{kind:'request.started',at:t(0),data:{}},{kind:'phone.background',at:t(1),data:{stage:'background_action'}},{kind:'task.stop',at:t(2),data:{stage:'task_stop_acknowledged',taskId:'A'}},{kind:'worker.message_rejected',at:t(3),data:{}},{kind:'request.started',at:t(5),data:{}},{kind:'phone.background',at:t(6),data:{stage:'background_action'}}]);
 assert.equal(r.report.verdict,'PASS');assert.equal(r.code,0);
});
test('fails on a phone action or delivered message after the acknowledgment',()=>{
 const r=run([{kind:'task.stop',at:t(2),data:{stage:'task_stop_acknowledged',taskId:'A'}},{kind:'phone.background',at:t(3),data:{stage:'background_action'}},{kind:'worker.message',at:t(4),data:{}}]);
 assert.equal(r.report.verdict,'FAIL');assert.equal(r.code,1);assert.equal(r.report.stops[0].violations.length,2);
});
