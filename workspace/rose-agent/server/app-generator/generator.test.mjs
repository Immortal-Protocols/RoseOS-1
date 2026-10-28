import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {AppGenerationStore} from './store.mjs';
import {AppGenerationDriver} from './driver.mjs';
import {GeneratedAppRuntime,resolve,selectorFor} from './runtime.mjs';
import {createAppGenerator} from './generator.mjs';
import {resultCheck,validateProof} from './workflow.mjs';
import {generationAdapter} from './driver.mjs';
function store(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'app-generation-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return new AppGenerationStore(dir);}
const app=packageName=>({packageName,label:packageName});
test('baseline is not treated as a new install; new apps queue exactly once',t=>{
 const s=store(t);s.scan([app('test.old')]);assert.equal(s.pending(),undefined);
 s.scan([app('test.old'),app('test.new')]);assert.equal(s.pending().packageName,'test.new');
 const before=s.pending();s.scan([app('test.old'),app('test.new')]);assert.equal(s.pending(),before);assert.equal(Object.keys(s.state.jobs).length,1);
});
test('known fast adapters are not regenerated and uninstall disables generated packs',t=>{
 const s=store(t);s.scan([]);s.scan([app('test.known'),app('test.new')],new Set(['test.known']));assert.equal(s.state.jobs['test.known'],undefined);
 s.publish({...app('test.new'),tools:[]});s.update('test.new',{status:'ready'});assert.equal(s.packs().length,1);
 s.scan([]);assert.equal(s.packs().length,0);assert.equal(s.state.jobs['test.new'].status,'removed');
});
test('unfinished jobs resume after a backend restart',t=>{
 const s=store(t);s.enqueue(app('test.new'));s.update('test.new',{status:'running'});
 const next=new AppGenerationStore(s.directory);assert.equal(next.pending().packageName,'test.new');
});
test('a user request interrupts generation and preserves its queued job',async t=>{
 const s=store(t);s.enqueue(app('test.new'));const d=new AppGenerationDriver({store:s,phone:async()=>{},client:{},route:()=>({}),canStart:()=>true,knownPackages:()=>new Set()});
 d.generator.generate=async(job,signal)=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted'))));
 d.start(s.pending());assert.equal(d.status().running,'test.new');await d.interrupt();assert.equal(d.status().running,null);assert.equal(s.pending().status,'queued');
});
test('generation waits while the ordinary task executor is busy',async t=>{
 const s=store(t);let called=0;const d=new AppGenerationDriver({store:s,phone:async()=>{called++;},client:{},route:()=>({}),canStart:()=>false,knownPackages:()=>new Set()});
 await d.tick();assert.equal(called,0);
});
test('a signed native catalog is registered without exploring its UI',async t=>{
 const s=store(t);s.enqueue(app('test.native'));let calls=0;
 const d=new AppGenerationDriver({store:s,phone:async c=>{calls++;assert.equal(c.action,'app_command');return {status:'ok',tools:[{name:'read_state',operation:'get_state',description:'Read state',inputSchema:{type:'object',properties:{}}}]};},client:{},route:()=>({}),canStart:()=>true,knownPackages:()=>new Set()});
 d.start(s.pending());await d.active.promise;assert.equal(calls,1);assert.equal(s.state.jobs['test.native'].status,'ready');assert.equal(s.pack('test.native').source,'native');
});
test('ambiguous controls are rejected instead of choosing an arbitrary target',()=>{
 assert.throws(()=>resolve({nodes:[{id:1,enabled:true,clickTarget:1,text:'Open'},{id:2,enabled:true,clickTarget:2,text:'Open'}]},{text:'Open'}),/ambiguous/);
});
test('search verifies text before submitting and does not repeat a rejected submission',async()=>{
 let submitted=0;const node={id:1,enabled:true,editable:true,focused:true,imeEnter:true,viewId:'search',text:''};
 const r=new GeneratedAppRuntime({client:{},route:()=>({}),phone:async c=>{
  if(c.action==='inspect_ui')return {status:'ok',packageName:'test.app',nodes:[{...node}]};
  if(c.action==='set_text'){node.text=c.text;return {status:'action_accepted'};}
  if(c.action==='ime_enter'){submitted++;return {status:'action_failed'};}
  throw Error('Unexpected command');
 }});
 await assert.rejects(r.step('test.app',{kind:'search',selector:{viewId:'search'}},{query:'example'}),/submission rejected/);assert.equal(submitted,1);
});
test('leaving the target app stops generation before an action',async()=>{
 // Generation runs in the active control workspace (foreground or background), so the wrong package there is refused before any action.
 let mutations=0;const r=new GeneratedAppRuntime({client:{},route:()=>({}),phone:async c=>{if(c.action!=='inspect_ui')mutations++;return {status:'ok',packageName:'another.app',nodes:[]};}});
 await assert.rejects(r.step('test.app',{kind:'tap',selector:{text:'Browse'}},{}),/Expected app is not available in the active control workspace/);assert.equal(mutations,0);
});
test('a password prompt prevents exploration or collection of its contents',async()=>{
 let mutations=0;const r=new GeneratedAppRuntime({client:{},route:()=>({}),phone:async c=>{if(c.action!=='inspect_ui')mutations++;return {status:'ok',packageName:'test.app',nodes:[{password:true,text:'[password]'}]};}});
 await assert.rejects(r.screen('test.app'),/Sign in/);assert.equal(mutations,0);
});
test('a generated workflow is published only after replay and final review',async()=>{
 let phase='home',query='',submits=0;
 const phone=async c=>{
  if(c.action==='open_app')return {status:'opened',packageName:'test.app',foregroundVerified:true};
  if(c.action==='screenshot')return {status:'ok',imageBase64:Buffer.from([255,216,1]).toString('base64')};
  if(c.action==='inspect_ui')return {status:'ok',packageName:'test.app',snapshotId:'fresh',nodes:[{id:1,text:'Search',description:'Search',viewId:'search_button',clickTarget:1,enabled:true},...(phase==='edit'?[{id:2,text:query,viewId:'search_field',editable:true,focused:true,imeEnter:true,enabled:true}]:phase==='results'?[{id:3,text:query,viewId:'query'},{id:4,text:'Results',viewId:'results'}]:[])]};
  if(c.action==='tap_label'){phase='edit';return {status:'action_accepted'};}
  if(c.action==='set_text'){query=c.text;return {status:'action_accepted'};}
  if(c.action==='ime_enter'){phase='results';submits++;return {status:'action_accepted'};}
  throw Error('Unexpected command');
 };
 const decisions=[{action:'tap',targetId:1},{action:'search',targetId:2,query:'example'},{action:'publish',name:'search',description:'Search the app for the requested query',fromStep:0,query:'another'},{action:'finish'}];
 const client={responses:{create:async opts=>{
  const schema=opts.text.format.schema;
  const answer=schema.properties.all?{all:[{viewId:'results'}],any:[],none:[{editable:true}],query:{selector:{viewId:'query'},related:'self'}}:schema.properties.controls?{controls:[]}:schema.properties.allowed?{allowed:true,reason:'Search navigation'}:schema.properties.status?{status:'complete',summary:'Results displayed',evidence:['results'],nextSteps:[]}:decisions.shift();
  return {status:'completed',output_text:JSON.stringify(answer)};
 }}};
 const result=await createAppGenerator({client,phone,route:()=>({model:'test',effort:'low'})}).generate(app('test.app'));
 assert.equal(result.status,'ready');assert.deepEqual(result.pack.tools.map(x=>x.name),['state','search']);assert.equal(submits,3);
});
test('interrupting a final review aborts its model request without more phone actions',async()=>{
 let reviewed;const started=new Promise(r=>reviewed=r);let commands=0;
 const client={responses:{create:async(_opts,{signal})=>{reviewed();return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));}}};
 const phone=async c=>{commands++;if(c.action==='open_app')return {status:'opened',packageName:'test.app',foregroundVerified:true};if(c.action==='inspect_ui')return {status:'ok',packageName:'test.app',snapshotId:'s',nodes:[{id:1,enabled:true,clickTarget:1,text:'Browse'}]};if(c.action==='tap_label')return {status:'action_accepted'};if(c.action==='screenshot')return {status:'ok',imageBase64:'AA=='};throw Error('Unexpected command');};
 const abort=new AbortController(),runtime=new GeneratedAppRuntime({client,phone,route:()=>({model:'test'})});
 const task=runtime.execute({packageName:'test.app',tools:[{name:'browse',description:'Show browse page',steps:[{kind:'tap',selector:{text:'Browse'}}]}]},'browse',{},abort.signal);
 await started;const before=commands;abort.abort();await assert.rejects(task,/aborted/);assert.equal(commands,before);
});

test('an empty search field resolves by its unique exposed editable role',()=>{
 const field={id:15,enabled:true,editable:true,imeEnter:true,viewId:'',text:'',description:'',clickTarget:15};
 const screen={nodes:[field,{id:16,enabled:true,description:'Search',clickTarget:15}]};
 assert.deepEqual(selectorFor(screen,field),{editable:true,imeEnter:true});assert.equal(resolve(screen,selectorFor(screen,field)).id,15);
 assert.throws(()=>selectorFor({nodes:[field,{...field,id:20,clickTarget:20}]},field),/lacks a stable/);
});
test('visible controls are inventoried together without clicking them',async()=>{
 let taps=0;const screen={status:'ok',packageName:'test.app',nodes:[{id:1,enabled:true,text:'Post',clickTarget:1},{id:2,enabled:true,text:'Home',clickTarget:2}]};
 const client={responses:{create:async opts=>({status:'completed',output_text:JSON.stringify(opts.text.format.schema.properties.controls?{controls:[{targetId:1,kind:'tap',name:'open_composer',description:'Open the composer on the current screen'},{targetId:2,kind:'tap',name:'home',description:'Open Home from the current screen'}]}:{action:'finish',reason:'Current controls mapped'})})}};
 const phone=async c=>{if(c.action==='open_app')return {status:'opened',packageName:'test.app',foregroundVerified:true};if(c.action==='inspect_ui')return screen;taps++;throw Error('No control should be activated');};
 const result=await createAppGenerator({client,phone,route:()=>({model:'test'})}).generate(app('test.app'));
 assert.equal(result.status,'limited');assert.equal(result.pack.tools.length,1);assert.equal(result.pack.controls.length,2);assert.equal(taps,0);
});
test('an observed consequential control cannot run without matching live user authorization',async()=>{
 let taps=0;const screen={status:'ok',packageName:'test.app',nodes:[{id:1,enabled:true,text:'Post',clickTarget:1}]};
 const pack={packageName:'test.app',tools:[{name:'publish',description:'Publish the current draft',currentScreenOnly:true,steps:[{kind:'tap',selector:{text:'Post'}}]}]};
 const phone=async c=>{if(c.action==='inspect_ui')return screen;taps++;throw Error('No click authorized');};
 const client={responses:{create:async()=>({status:'completed',output_text:JSON.stringify({allowed:false,reason:'The user asked only to inspect the app'})})}};
 const absent=new GeneratedAppRuntime({client,phone,route:()=>({model:'test'})});await assert.rejects(absent.execute(pack,'publish'),/explicit live user request/);
 const denied=new GeneratedAppRuntime({client,phone,route:()=>({model:'test'}),currentRequest:()=>"What controls does this app have?"});await assert.rejects(denied.execute(pack,'publish'),/only to inspect/);assert.equal(taps,0);
});
test('an observed control uses the authorized snapshot and never retargets after it goes stale',async()=>{
 let taps=0,inspections=0;const client={responses:{create:async()=>({status:'completed',output_text:JSON.stringify({allowed:true,reason:'Explicit request'})})}};
 const runtime=new GeneratedAppRuntime({client,route:()=>({model:'test'}),currentRequest:()=>"Like this post",phone:async c=>{if(c.action==='inspect_ui'){inspections++;return {status:'ok',packageName:'test.app',snapshotId:'authorized-screen',nodes:[{id:1,text:'Like',clickTarget:1,enabled:true}]};}if(c.action==='tap_label'){assert.equal(c.snapshotId,'authorized-screen');taps++;return {status:'stale_screen'};}throw Error('Unexpected action');}});
 await assert.rejects(runtime.execute({packageName:'test.app',tools:[{name:'like',description:'Like the currently displayed post',currentScreenOnly:true,steps:[{kind:'tap',selector:{text:'Like'}}]}]},'like'),/stale_screen/);assert.equal(taps,1);assert.equal(inspections,2);
});

test('filling an exposed text field verifies the text without pressing submit',async()=>{
 let text='',submitted=0;const runtime=new GeneratedAppRuntime({client:{},route:()=>({}),phone:async c=>{
  if(c.action==='inspect_ui')return {status:'ok',packageName:'test.app',nodes:[{id:1,enabled:true,editable:true,imeEnter:true,text}]};
  if(c.action==='set_text'){text=c.text;return {status:'action_accepted'};}submitted++;throw Error('Unexpected submit');
 }});await runtime.step('test.app',{kind:'fill',selector:{editable:true,imeEnter:true}},{text:'Draft text'});assert.equal(text,'Draft text');assert.equal(submitted,0);
});
test('a correction during authorization prevents an observed control from executing',async()=>{
 let request='Like this post',taps=0;
 const runtime=new GeneratedAppRuntime({currentRequest:()=>request,route:()=>({model:'test'}),client:{responses:{create:async()=>{request='Stop, do not like it';return {status:'completed',output_text:JSON.stringify({allowed:true,reason:'Original request permitted it'})};}}},phone:async c=>{if(c.action==='inspect_ui')return {status:'ok',packageName:'test.app',nodes:[{id:1,enabled:true,text:'Like',clickTarget:1}]};taps++;throw Error('Unexpected action');}});
 await assert.rejects(runtime.execute({packageName:'test.app',tools:[{name:'like',description:'Like the current post',currentScreenOnly:true,steps:[{kind:'tap',selector:{text:'Like'}}]}]},'like'),/request changed/);assert.equal(taps,0);
});
test('resuming generation retains previously discovered controls and checkpoints them',async()=>{
 const saved={packageName:'test.app',label:'Test',tools:[{name:'state',steps:[]},{name:'home',steps:[{kind:'tap',selector:{text:'Home'}}],currentScreenOnly:true}]};let checkpoint;
 const client={responses:{create:async opts=>({status:'completed',output_text:JSON.stringify(opts.text.format.schema.properties.controls?{controls:[]}:{action:'finish',reason:'Retained discovered controls'})})}};
 const phone=async c=>c.action==='open_app'?{status:'opened',packageName:'test.app',foregroundVerified:true}:{status:'ok',packageName:'test.app',nodes:[]};
 const result=await createAppGenerator({client,phone,route:()=>({model:'test'}),checkpoint:pack=>checkpoint=JSON.parse(JSON.stringify(pack))}).generate(app('test.app'),undefined,saved);
 assert.equal(result.status,'limited');assert.equal(result.pack.controls[0].name,'home');assert.equal(checkpoint.tools.length,1);
});
test('a non-actionable heading does not make its matching labeled button ambiguous',()=>{
 const screen={nodes:[{id:1,enabled:true,text:'Search'},{id:2,enabled:true,description:'Search',clickTarget:2}]};assert.equal(resolve(screen,{description:'Search'}).id,2);
 const repeated={nodes:[{id:1,enabled:true,description:'Search'},{id:2,enabled:true,description:'Search',clickTarget:2}]};assert.equal(resolve(repeated,{description:'Search'}).id,2);
});
test('a changing query label uses its stable accessible description',()=>{
 const screen={nodes:[{id:1,enabled:true,text:'old query',clickTarget:1},{id:2,enabled:true,description:'Search',clickTarget:1}]};assert.deepEqual(selectorFor(screen,screen.nodes[0]),{description:'Search'});
});


test('search result proof rejects an editor, wrong query, loading and sample-specific selectors',()=>{
 const proof={all:[{viewId:'results'}],any:[{viewId:'row'}],none:[{editable:true}],query:{selector:{viewId:'query'},related:'self'}};
 const screen={nodes:[{viewId:'results'},{viewId:'row'},{viewId:'query',text:'new query'}]};
 assert.equal(resultCheck(screen,proof,{query:'new query'}),true);
 assert.equal(resultCheck(screen,proof,{query:'old query'}),false);
 assert.equal(resultCheck({nodes:[...screen.nodes,{editable:true}]},proof,{query:'new query'}),false);
 assert.equal(resultCheck({nodes:screen.nodes.filter(n=>n.viewId!=='row')},proof,{query:'new query'}),false);
 assert.throws(()=>validateProof(screen,{...proof,all:[{text:'new query'}]},{query:'new query'}),/hard-code/);
});
test('verified workflow performs search and returns real results without model calls or screenshots',async()=>{
 let phase='home',query='',modelCalls=0;const actions=[];
 const phone=async c=>{actions.push(c.action);
  if(c.action==='open_app')return {status:'opened',packageName:'test.app',foregroundVerified:true};
  if(c.action==='inspect_ui')return {status:'ok',packageName:'test.app',snapshotId:'fresh',nodes:phase==='home'?[{id:1,text:'Search',enabled:true,clickTarget:1}]:phase==='edit'?[{id:2,editable:true,enabled:true,focused:true,imeEnter:true,text:query}]:[{text:query,viewId:'query'},{viewId:'results'},{text:'An actual result from the app'}]};
  if(c.action==='tap_label'){phase='edit';return {status:'action_accepted'};}
  if(c.action==='set_text'){query=c.text;return {status:'action_accepted'};}
  if(c.action==='ime_enter'){phase='results';return {status:'action_accepted'};}
  throw Error('Unexpected command '+c.action);
 };
 const runtime=new GeneratedAppRuntime({phone,client:{responses:{create:()=>{modelCalls++;throw Error('No model needed');}}},route:()=>({})});
 const result=await runtime.execute({packageName:'test.app',tools:[{name:'search',steps:[{kind:'tap',selector:{text:'Search'}},{kind:'search',selector:{editable:true,imeEnter:true}}],resultProof:{all:[{viewId:'results'}],any:[],none:[{editable:true}],query:{selector:{viewId:'query'},related:'self'}}}]},'search',{query:'fresh query'});
 assert.equal(result.status,'verified');assert.equal(result.query,'fresh query');assert.ok(result.labels.includes('An actual result from the app'));assert.equal(modelCalls,0);assert.equal(actions.includes('screenshot'),false);
});
test('catalog advertises verified complete operations and keeps observed controls private',async t=>{
 const s=store(t);s.enqueue(app('test.app'));s.update('test.app',{status:'ready'});s.publish({packageName:'test.app',label:'Test',source:'verified-ui-workflows',tools:[{name:'search',description:'Search and verify',testedAt:1,steps:[{kind:'search'}]},{name:'like',description:'Untested button',currentScreenOnly:true,steps:[]}]});
 const adapter=generationAdapter(s,()=>({status:()=>({}),generator:{runtime:{execute:()=>({status:'verified'})}}}));
 assert.ok(adapter.tools.some(t=>t.name==='app_test_app_search'));assert.ok(!adapter.tools.some(t=>t.name.endsWith('_like')));
 const catalog=await adapter.execute('generated_app_tools',{packageName:'test.app'});assert.deepEqual(catalog.tools.map(t=>t.name),['search']);
 const result=await adapter.execute('generated_app_run',{packageName:'test.app',toolName:'like'});assert.equal(result.status,'needs_assistance');
});
