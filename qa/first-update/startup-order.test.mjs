// Independent QA check (Chief review QA-007 P1): with the registered app-tool catalog enabled (needed
// for the origin-bound Calculator flow), server.mjs must not read phoneOrigin/NON_TASK before their
// declarations. Runs the real catalog statement and the real declaration statement IN SOURCE ORDER in
// one scope with stubbed I/O. Limit: a declaration-order slice, not a full module start (server.mjs
// starts processes and listeners at top level).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {AsyncLocalStorage} from 'node:async_hooks';

const server=fs.readFileSync(new URL('../../workspace/rose-agent/server/server.mjs',import.meta.url),'utf8');
const lineAt=i=>server.slice(server.lastIndexOf('\n',i)+1,server.indexOf('\n',i));
const useAt=server.indexOf('phoneOrigin.run(NON_TASK');
const declAt=server.indexOf('const phoneOrigin=');
const catalogAt=server.indexOf('const catalog=');
const has=useAt>=0&&declAt>=0&&catalogAt>=0&&lineAt(useAt)===lineAt(catalogAt);

test('enabling the app-tool catalog does not read phoneOrigin before it is declared',{skip:!has&&'no catalog/phoneOrigin pattern in this tree'},async()=>{
 const ordered=declAt<catalogAt?[lineAt(declAt),lineAt(catalogAt)]:[lineAt(catalogAt),lineAt(declAt)];
 const context=vm.createContext({AsyncLocalStorage,Object,Set,artemisEnabled:false,appToolsConfig:{packages:['dev.pixel.calculator.agent'],catalogs:{}},
  loadAppToolCatalog:async()=>({definitions:[]}),fastPhone:async()=>({status:'ok'}),appAdapters:[],taskStarted:0});
 const run=vm.runInContext('(async()=>{'+ordered.join('\n')+'\nreturn typeof catalog;})()',context);
 await assert.doesNotReject(run,'startup with the app-tool catalog enabled throws (declaration order)');
});
