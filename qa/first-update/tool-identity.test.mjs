// Independent QA check of agent phone-tool task identity (Chief GENERATION-001; Chief review
// APP-004 item 2). Runs the real phone/control.py identity code (task_generation/request_headers,
// extracted with Python's ast so no network, TLS or real credentials are touched) under a
// temporary HOME. Scenario: a tool belonging to task A launches only after B has begun.
// Expectation: it must not carry B's identity (it may carry A's, or none and be refused).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';

const control=new URL('../../workspace/rose-agent/phone/control.py',import.meta.url).pathname;
const src=fs.readFileSync(control,'utf8');
// App b51ddf9 removed X-Rose-Task and the task-generation file: control.py sends no task identity.
const has=true;const sendsIdentity=src.includes('def request_headers');

// LAUNCH ADAPTER: how App binds a tool process to its originating task. At App 2f1d6e2 there is
// no per-launch binding (identity is the shared task-generation file), so this adds nothing.
// Update only this adapter if App documents e.g. an environment variable set per worker turn.
const launchEnvFor=_originTask=>({});

function headersFor(home,originTask){
 if(!sendsIdentity)return '';
 const py=`
import ast,sys,pathlib
src=open(sys.argv[1]).read();tree=ast.parse(src)
keep=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) and not (isinstance(n,ast.ImportFrom) and n.module=='observations')]
keep+=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in('task_generation','request_headers')]
mod=ast.Module(body=keep,type_ignores=[]);g={'p':pathlib.Path(sys.argv[2])}
exec(compile(mod,'control_identity','exec'),g);g['TASK_GENERATION']=g['task_generation']()
h=g['request_headers']();print(h.get('X-Rose-Task',''))`;
 return execFileSync('python3',['-I','-c',py,control,path.join(home,'.config/pixel-voice')],{env:{...process.env,HOME:home,...launchEnvFor(originTask)},encoding:'utf8'}).trim();
}

test("a phone tool for task A launched after B begins does not carry B's identity",{skip:!has&&'control.py has no task identity'},()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'qa-tool-'));const cfg=path.join(home,'.config/pixel-voice');fs.mkdirSync(cfg,{recursive:true});
 fs.writeFileSync(path.join(cfg,'pair-token'),'qa-dummy-not-a-secret\n');
 fs.writeFileSync(path.join(cfg,'task-generation'),'task-A\n');
 // Protocol-specific control: only meaningful when control.py sends an identity at all.
 if(sendsIdentity)assert.equal(headersFor(home,'task-A'),'task-A','control: a tool launched while A is current names A');
 // Server publishes B's generation (publishTaskGeneration) when B begins.
 fs.writeFileSync(path.join(cfg,'task-generation'),'task-B\n');
 const late=headersFor(home,'task-A');
 assert.notEqual(late,'task-B','task-A tool launched after B began was relabelled as B');
});
