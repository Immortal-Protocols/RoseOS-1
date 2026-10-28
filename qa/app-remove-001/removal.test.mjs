// APP-REMOVE-001-Q: independent, advisory removal and core-preservation cases (QA-only path).
// Runs against a Rose Agent source tree (QA_SRC, default: this checkout's workspace/rose-agent)
// and compares it with the pre-removal reference (QA_BASE_REF, default eca1fef). Offline only:
// the real index.html runs in the maintained web-ui-harness; backend calls are answered by fixtures
// through Native.request/window.nativeResponse. No backend, network, device or preference access.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';

const REPO=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const SRC=path.resolve(process.env.QA_SRC||path.join(REPO,'workspace/rose-agent'));
const BASE=process.env.QA_BASE_REF||'eca1fef9e7baa01238b1abd87195af8c429eff65';
const {loadPage}=await import(pathToFileURL(path.join(SRC,'tests/web-ui-harness.mjs')).href);
const J='app/src/dev/pixel/voice/';
const cur=rel=>fs.readFileSync(path.join(SRC,rel),'utf8');
// MainActivity is compared with its own pre-change parent when given (QA_MAINACTIVITY_BASE_REF, e.g. the
// Product PR's parent): later unrelated MainActivity changes, such as OS #83 Home reuse, then don't count.
const MAIN_BASE=process.env.QA_MAINACTIVITY_BASE_REF||BASE;
const base=rel=>execFileSync('git',['-C',REPO,'show',`${rel.endsWith('MainActivity.java')?MAIN_BASE:BASE}:workspace/rose-agent/${rel}`],{encoding:'utf8',maxBuffer:64<<20});

const RETIRED={commands:['toggle_deepseek_trial','toggle_luna_first','toggle_artemis_trial'],
 functions:['toggleDeepseekTrial','toggleLunaFirst','toggleArtemisTrial'],
 titles:['DeepSeek Fast (trial)','Luna-first voice (trial)','ARTEMIS phone control (trial)']};
const TRIAL_ROUTES=['/task-model','/voice/routing','/artemis/config'];
// Bridge callbacks whose only effect is a trial mirror preference whose only reader (at BASE) is the
// matching trial checkbox in MainActivity.homeOptions. Removing them is in scope; nothing else may go.
const EXCLUSIVE_BRIDGE=new Set(['artemisTrialState','voiceRoutingState']);
const MIRRORS=['deepseek-trial','voice-routing','artemis-trial'];

function homeTitles(java){const i=java.indexOf('void homeOptions(');assert.ok(i>=0,'homeOptions missing');
 const body=java.slice(i,java.indexOf('\n',i));return [...body.matchAll(/\.add\("([^"]+)"\)/g)].map(m=>m[1]);}
function dispatch(java){const i=java.indexOf('String script=');assert.ok(i>=0,'command dispatch missing');
 const seg=java.slice(i,java.indexOf(';',i));return new Map([...seg.matchAll(/"([a-z_]+)"\.equals\(c\)\?"([^"]+)"/g)].map(m=>[m[1],m[2]]));}
function bridge(java){const s=java.slice(java.search(/class Bridge\b/));
 return new Set([...s.matchAll(/@JavascriptInterface\s+public\s+[\w<>[\]]+\s+(\w+)\s*\(/g)].map(m=>m[1]));}
const count=(s,needle)=>s.split(needle).length-1;

// Real page with fixture backend. Returns the page and the list of backend requests it made.
function page({mode='live',artemis=false,model='gpt-6-luna'}={}){
 const requests=[];let ctx;
 const reply={'GET /health':{ready:true,busy:false,taskModel:{model,label:model==='gpt-6-luna'?'Luna':'DeepSeek Fast'}},
  'GET /voice/config':{voiceRouting:{mode}},'GET /artemis/config':{enabled:artemis}};
 const p=loadPage({native:{request:(id,method,route,body)=>{requests.push({method,route,body:JSON.parse(body||'{}')});
  const data=reply[`${method} ${route}`]??{ok:true,enabled:!artemis,model,label:'fixture'};
  Promise.resolve().then(()=>ctx.nativeResponse(id,200,JSON.stringify(data)));}}});
 ctx=p.context;return {...p,requests};}
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r));};
const trialWrites=reqs=>reqs.filter(r=>r.method!=='GET'&&TRIAL_ROUTES.includes(r.route));

// ---- Removal cases (expected to fail on pre-removal source, for the named reason) ----

test('R1 retired page functions are absent, not renamed',()=>{
 const p=page();
 const present=RETIRED.functions.filter(f=>typeof p.window[f]==='function');
 assert.deepEqual(present,[],`still defined: ${present.join(', ')}`);
 const html=cur('app/assets/index.html');
 for(const r of TRIAL_ROUTES.filter(r=>r!=='/voice/routing'))
  assert.equal(count(html,`api('POST','${r}'`),0,`index.html still POSTs ${r}`);
});

test('R2 retired commands no longer dispatch, and invoking them causes no trial mutation',async()=>{
 const map=dispatch(cur(J+'VoiceService.java'));
 const mapped=RETIRED.commands.filter(c=>map.has(c));
 const mutations=[];
 for(const state of [{mode:'live',artemis:false,model:'gpt-6-luna'},{mode:'luna',artemis:true,model:'deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast'}]){
  for(const c of RETIRED.commands){const p=page(state);const script=map.get(c)||'';
   if(script){try{p.run(script);}catch{}await settle();}
   mutations.push(...trialWrites(p.requests).map(r=>`${c} [${state.mode}/${state.artemis}] → ${r.method} ${r.route}`));}}
 assert.deepEqual({mapped,mutations},{mapped:[],mutations:[]});
});

test('R3 Home menu drops exactly the three trial entries (no rename, no Advanced replacement)',()=>{
 const before=homeTitles(base(J+'MainActivity.java')),after=homeTitles(cur(J+'MainActivity.java'));
 assert.deepEqual(after,before.filter(t=>!RETIRED.titles.includes(t)));
 assert.ok(!after.some(t=>/advanced|trial/i.test(t)),'an Advanced/trial entry remains');
});

// ---- Preservation cases (expected to pass both before and after removal) ----

test('P1 every other command still dispatches to a page function that exists',()=>{
 const before=dispatch(base(J+'VoiceService.java')),after=dispatch(cur(J+'VoiceService.java'));
 for(const [c,s] of before)if(!RETIRED.commands.includes(c))assert.equal(after.get(c),s,`command ${c} changed`);
 const p=page();
 for(const [c,s] of after){const fn=s.match(/^window\.(\w+)\(/);if(fn)assert.equal(typeof p.window[fn[1]],'function',`${c} → missing ${fn[1]}`);}
});

test('P2 bridge keeps shared mirrors; only an exclusive trial callback may go',()=>{
 const before=bridge(base(J+'VoiceService.java')),after=bridge(cur(J+'VoiceService.java'));
 const removed=[...before].filter(m=>!after.has(m));
 assert.deepEqual(removed.filter(m=>!EXCLUSIVE_BRIDGE.has(m)),[],'shared bridge method removed');
 for(const m of ['taskModelState','decisionsRoutingState','request'])assert.ok(after.has(m),m);
});

test('P3 shared voice routing path survives: Use previous voice routing still reverts to live',async()=>{
 const p=page({mode:'luna'});
 assert.equal(typeof p.window.changeVoiceRouting,'function');
 p.node('revertRouting').onclick();await settle();
 assert.deepEqual(p.requests.filter(r=>r.method==='POST').map(r=>[r.route,r.body]),[['/voice/routing',{mode:'live'}]]);
});

test('P4 no automatic backend reset or preference wipe is introduced',()=>{
 const p=page({mode:'luna',artemis:true});
 assert.deepEqual(trialWrites(p.requests),[],'page load wrote a trial route');
 for(const f of ['VoiceService.java','MainActivity.java']){const b=base(J+f),c=cur(J+f);
  for(const pref of ['deepseek-trial','voice-routing','artemis-trial']){
   const wipes=s=>[...s.matchAll(new RegExp(`getSharedPreferences\\("${pref}",0\\)\\.edit\\(\\)\\.(clear|remove)\\(`,'g'))].length;
   assert.ok(wipes(c)<=wipes(b),`${f} adds a wipe of ${pref}`);}
  assert.ok(count(c,'stopCurrent')<=count(b,'stopCurrent'),`${f} adds stopCurrent`);}
 assert.ok(count(cur('app/assets/index.html'),'stopCurrent')<=count(base('app/assets/index.html'),'stopCurrent'));
});

test('P5 truthful model label still flows from /health to the bridge',async()=>{
 const p=page({model:'deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast'});
 assert.ok(cur('app/assets/index.html').includes("Native.taskModelState?.(JSON.stringify(h.taskModel"),'health path no longer reports the model');
 assert.ok(/taskModelLabel=data\.optString\("label"/.test(cur(J+'VoiceService.java')),'bridge no longer stores the label');
});

test('P6 setup, recovery, Update and HOME/Back wiring remain reachable',()=>{
 const after=homeTitles(cur(J+'MainActivity.java'));
 for(const t of ['Server connection','Pair with Mac','Location access','Your apps','Classic home'])assert.ok(after.includes(t),t);
 for(const f of ['SelfUpdate.java','UpdateCheck.java','RoseConversationActivity.java','DirectUpdate.java','Delivery.java','VoiceService.java'])
  assert.equal(count(cur(J+f),'Update Rose Agent'),count(base(J+f),'Update Rose Agent'),`${f} Update route changed`);
 assert.equal(count(cur('app/assets/index.html'),'Update Rose Agent'),count(base('app/assets/index.html'),'Update Rose Agent'));
 const m=cur(J+'MainActivity.java');
 for(const s of ['ACTION_MAIN','CATEGORY_HOME','onBackPressed'])assert.equal(count(m,s),count(base(J+'MainActivity.java'),s),s);
});

test('R4 trial mirrors: no code wipes them; after the paired removal nothing reads or writes them',()=>{
 const java=['VoiceService.java','MainActivity.java'].map(f=>cur(J+f)).join('\n');
 for(const pref of MIRRORS)assert.ok(!new RegExp(`"${pref}",0\\)\\.edit\\(\\)\\.(clear|remove)`).test(java),`${pref} wiped`);
 const left=MIRRORS.filter(pref=>java.includes(`getSharedPreferences("${pref}"`));
 // Stored bytes stay on the device (orphaned, not deleted); this asserts only that no stale UI reads them.
 assert.deepEqual(left,[],`still referenced: ${left.join(', ')}`);
});
