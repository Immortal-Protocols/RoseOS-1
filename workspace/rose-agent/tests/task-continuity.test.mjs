// The conversation surface presents the server's single-task projection and must never stop it.
// Uses the real page script, the real LauncherTask model and the Android sources; no device or backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {loadPage,fakeConversation} from './web-ui-harness.mjs';
import {LauncherTask} from '../server/launcher-task.mjs';

const app=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,app),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('ending voice on the page sends no task Stop and keeps the task stream',async()=>{
 const page=loadPage();
 page.run(`var fake=${fakeConversation};conversation=fake;ready=true;starting=false;ended=false;liveSessionId='s1';`);
 page.run('endConversation()');await settle();
 const routes=page.calls.filter(c=>c[0]==='request').map(c=>c[3]);
 assert.ok(routes.includes('/voice-ended'),'the request capture sees voice end');
 assert.ok(!routes.includes('/task/stop'),'no /task/stop on voice end');
 assert.equal(page.calls.filter(c=>c[0]==='taskStateUnavailable').length,0,'task state is not dropped');
});

test('the server task snapshot carries every field and state the surface presents',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-'));
 let clock=1;const task=new LauncherTask(path.join(dir,'task.json'),{now:()=>clock++});
 const seen=[];const snap=()=>{const s=task.snapshot();seen.push(s);return s.current;};
 assert.equal(task.snapshot().current,null,'empty projection is current:null');
 task.start('What is 18% of 240?');const started=snap();
 task.setTitle(started.id,'Calculate eighteen percent tip');task.activity('Using Calculator');const running=snap();
 task.checking();snap();task.result('18% of 240 is **43.2**.');task.finish('completed');const done=snap();
 assert.equal(done.id,started.id,'progress and result belong to the same task id');
 assert.equal(running.activity,'Using Calculator');assert.equal(done.result,'18% of 240 is **43.2**.');assert.equal(done.activity,'');
 for(const s of seen)for(const key of ['id','state','title','activity','progress','result'])assert.ok(key in s.current,`snapshot has ${key}`);
 // Every lifecycle state the model can produce is handled explicitly by TaskPresentation.
 const handled=new Set([...source('TaskPresentation.java').matchAll(/case "([a-z]+)"/g)].map(m=>m[1]));
 const states=new Set(['running','checking','stopping','completed','failed','interrupted']);
 for(const state of states)assert.ok(handled.has(state),`TaskPresentation handles ${state}`);
 fs.rmSync(dir,{recursive:true,force:true});
});

test('task Stop is only the confirmed owner action, never voice End or mute',()=>{
 for(const name of ['TaskPresentation.java','ConversationState.java','TaskControl.java']){
  assert.ok(!/"\/task\/stop"/.test(source(name)),`${name} sends no task Stop request`);
 }
 const activity=source('RoseConversationActivity.java');
 // The single /task/stop request lives in sendStop, reached only from the confirmation's Stop button.
 assert.equal(activity.match(/"\/task\/stop"/g).length,1,'one Stop request');
 const sendStop=activity.slice(activity.indexOf('void sendStop('));
 assert.ok(sendStop.indexOf('"/task/stop"')>0&&sendStop.indexOf('"/task/stop"')<sendStop.indexOf('\n }'),'it is inside sendStop');
 assert.deepEqual([...activity.matchAll(/sendStop\(([^)]*)\)/g)].map(m=>m[1]).filter(a=>a!=='String id'),['id'],'sendStop has one caller');
 assert.match(activity,/setPositiveButton\("Stop task",\(d,w\)->sendStop\(id\)\)/,'that caller is the confirmation');
 assert.match(activity,/setNegativeButton\("Cancel",null\)/,'Cancel sends nothing');
 // Voice controls send only voice commands.
 const sends=[...activity.matchAll(/send\("([a-z_]+)"\)/g)].map(m=>m[1]);
 assert.ok(sends.every(c=>['talk','end','tray_mute','tray_unmute'].includes(c)),'voice controls send voice commands only');
 const server=fs.readFileSync(new URL('../server/server.mjs',import.meta.url),'utf8');
 const voiceEnded=server.match(/voice-ended'\)\{(.*?)return answer/s)?.[1];
 assert.ok(voiceEnded,'found /voice-ended handler');
 assert.ok(!/taskStop|launcherTask/.test(voiceEnded),'/voice-ended does not touch the task');
});

test('Stop answers from the real TaskStop are each handled by TaskControl',async()=>{
 const {TaskStop}=await import('../server/task-stop.mjs');
 const control=source('TaskControl.java');
 let current={id:'t1',state:'running'};
 const stop=new TaskStop({task:()=>current,stopping(){current={...current,state:'stopping'};},interrupt:async()=>{},abortReview(){},closeWorkspace:async()=>{throw new Error('timeout');}});
 const stale=await stop.request({taskId:'t0',source:'conversation'});
 const first=await stop.request({taskId:'t1',source:'conversation'});
 const again=await stop.request({taskId:'t1',source:'conversation'});
 current={id:'t1',state:'interrupted'};
 const finished=await stop.request({taskId:'t1',source:'conversation'});
 assert.equal(stale.status,'stale_task');assert.equal(first.status,'stopping');assert.equal(first.taskId,'t1');
 assert.ok(first.workspace.startsWith('close_failed'),'workspace close failure is reported');assert.equal(again.duplicate,true);assert.equal(finished.status,'not_active');
 for(const status of ['stale_task','not_active','stopping'])assert.ok(control.includes(`"${status}"`),`TaskControl handles ${status}`);
 assert.ok(control.includes('"close_failed"')&&control.includes('"interrupt_failed"'),'TaskControl reads the failure prefixes the server uses');
});

test('Watch carries the selected task and its session to the viewer, read after the tap (APP-FIX-004)',()=>{
 const activity=source('RoseConversationActivity.java');
 const open=activity.slice(activity.indexOf('void openWorkspace('),activity.indexOf('\n }',activity.indexOf('void openWorkspace(')));
 assert.match(open,/final String selected=shownTaskId;/,'the selected task id is captured at the tap');
 assert.ok(open.indexOf('controllerState()')>0&&open.indexOf('controllerState()')<open.indexOf('launchBoundViewer('),'controller state is read fresh before opening');
 assert.match(open,/TaskControl\.watchBinding\(selected,selected\.equals\(shownTaskId\),c\)/,'binding from the fresh read for the tapped task');
 assert.match(open,/if\(binding==null\)\{[^}]*return;\}/,'no binding, no viewer');
 assert.match(open,/launchBoundViewer\(binding\[0\],binding\[1\]\)/,'the viewer is opened with both ids');
 const launch=activity.slice(activity.indexOf('boolean launchBoundViewer('),activity.indexOf('\n }',activity.indexOf('boolean launchBoundViewer(')));
 assert.match(launch,/putExtra\(TaskControl\.EXTRA_WATCH_TASK_ID,controllerTaskId\)\.putExtra\(TaskControl\.EXTRA_WATCH_SESSION_ID,sessionId\)/,'the viewer intent carries both ids');
 assert.equal((activity.match(/AgentWorkspaceActivity/g)||[]).length,1,'the conversation surface has one, bound, viewer launch');
 assert.ok(!/task\/stop|sendStop|send\("end"\)/.test(open),'opening the viewer stops nothing and ends no voice');
 // Existing unbound entries (app reveal and automatic view) are unchanged.
 for(const name of ['WorkspaceLauncher.java','AutoAgentPip.java'])assert.ok(!/WATCH_(TASK|SESSION)_ID/.test(source(name)),`${name} stays unbound`);
});

test('Rose\'s apps never fails silently: a tap says why Watch can\'t open, from a fresh read (APP-WATCH-1031-C)',()=>{
 const activity=source('RoseConversationActivity.java');
 const open=activity.slice(activity.indexOf('void openWorkspace('),activity.indexOf('\n }',activity.indexOf('void openWorkspace(')));
 const at=s=>{const i=open.indexOf(s);assert.ok(i>0,s);return i;};
 // No silent early return when no task is shown: the tap reaches the fresh read and gets a reason.
 assert.ok(!/selected\.isEmpty\(\)/.test(open),'a tap with no task shown is not silently dropped');
 assert.match(open,/final String selected=shownTaskId;final boolean active=shownTaskActive;if\(watchOpening\)return;watchOpening=true;/,'task and its state captured at the tap; one read in flight');
 // The reason comes from App Core's watchState over the FRESH, classified controller read.
 assert.match(open,/final TaskControl\.ControllerRead c=controllerState\(\);/);
 assert.match(open,/TaskControl\.WatchState ws=TaskControl\.watchState\(selected,active,c\);/);
 assert.ok(at('controllerState()')<at('TaskControl.watchState('),'decided after the fresh read');
 assert.match(open,/if\(!ws\.canWatch\)\{Toast\.makeText\(this,ws\.reason,Toast\.LENGTH_LONG\)\.show\(\);return;\}/,'unavailable: Core\'s reason is shown and nothing opens');
 // Available: the existing bound route is unchanged and still re-checks the tapped task is the one shown.
 assert.ok(at('if(!ws.canWatch)')<at('TaskControl.watchBinding(selected,selected.equals(shownTaskId),c)'),'the reason check never replaces the binding check');
 assert.ok(at('TaskControl.watchBinding(')<at('launchBoundViewer('),'only a bound launch');
 // The UI shows Core's text as given: no reason sentence is written or chosen here.
 for(const r of ["isn't working in an app","isn't available on this phone","still starting","waiting for your consent","doesn't let Rose use",
   "has stopped","couldn't start","busy with another task","running a background workspace","couldn't check","wasn't allowed to check",
   "couldn't find its background","couldn't read","doesn't recognise","isn't linked"])assert.ok(!activity.includes(r),'no hard-coded watch reason: '+r);
 assert.ok(!/AutoAgentPip|PictureInPicture/.test(activity),'the automatic PiP fallback is not touched here');
});

test('The controller read keeps idle apart from a failed read, in fixed categories (APP-WATCH-1031-C-M002)',()=>{
 const activity=source('RoseConversationActivity.java');
 const read=activity.slice(activity.indexOf('TaskControl.ControllerRead controllerState('),activity.indexOf('\n }',activity.indexOf('static String stateField(')));
 // One classified read: Core's adapter decides idle/status/failure; nothing is discarded into empty strings.
 assert.match(read,/return TaskControl\.readController\(\(\)->\{/);
 assert.ok(!/catch\(Exception/.test(read),'no catch-all here: every failure goes to the adapter');
 assert.match(read,/acquireUnstableContentProviderClient\(CONTROLLER_AUTHORITY\);\s*if\(client==null\)throw new TaskControl\.NoProvider\(\);/,'absent provider is its own category');
 assert.match(read,/catch\(org\.json\.JSONException e\)\{throw new TaskControl\.InvalidReply\(\);\}/,'malformed JSON is an invalid answer');
 assert.match(read,/if\(!\(v instanceof String\)\)throw new TaskControl\.InvalidReply\(\);/,'a non-string field is an invalid answer');
 assert.match(read,/client\.call\("get_state",null,null\)/,'read-only: only get_state');
 assert.ok(!/"start"|"command"|"stop"/.test(read),'the read never starts, commands or stops anything');
 assert.match(read,/finally\{client\.close\(\);\}/);
 assert.ok(!/getMessage\(\)|toString\(\)|printStackTrace|Log\./.test(read),'no exception text is kept or logged');
 // State is the classified read everywhere; the first frame says it is still checking.
 assert.match(activity,/volatile TaskControl\.ControllerRead ctl=TaskControl\.ControllerRead\.PENDING_READ;/);
 assert.ok(!/ctlStatus|ctlSession|ctlTask/.test(activity),'no unclassified copies of the fields remain');
 assert.match(activity,/new Thread\(\(\)->\{ctl=controllerState\(\);ctlPolling=false;\},"rose-controller-state"\)/);
 assert.match(activity,/TaskControl\.WatchState ws=TaskControl\.watchState\(t\.id,t\.active\(\),ctl\);/);
 assert.match(activity,/\+ctl\.key\(\)\+/,'a changed read or category redraws the task');
});

test('W1: the generic phone-command path claims the id before posting and never turns a slow wait into an error',()=>{
 const voice=source('VoiceService.java');
 const start=voice.indexOf('PhoneCommandGate gate=new PhoneCommandGate(');
 const path=voice.slice(start,voice.indexOf('private void observeOpenedApp('));
 assert.ok(start>0,'the bridge uses PhoneCommandGate');
 assert.ok(path.indexOf('gate.claim(id)')<path.indexOf('main.post('),'the id is claimed before the action is scheduled');
 assert.match(path,/String owned=gate\.claim\(id\);if\(owned!=null\)return owned;/,'an owned id answers from its receipt');
 assert.match(path,/return gate\.await\(task,id,5000\);/,'the bounded wait answers from the receipt');
 assert.ok(!/task\.get\(5,TimeUnit\.SECONDS\)/.test(voice),'no bare five-second get whose timeout becomes an error');
 assert.equal((voice.match(/savePhoneResult\(saved,id,result\);\s*final JSONObject completed=result;/g)||[]).length,1,'the posted action still saves its real result first');
});

test('Rose\'s apps opens the managed workspace overview: read-only tasks, one workspace, no guessed Watch or Stop (MANAGED-WORKSPACE-OVERVIEW-001)',()=>{
 const activity=source('RoseConversationActivity.java');
 const block=activity.slice(activity.indexOf('// ---------- Managed workspace overview'),activity.indexOf('/** Confirms Stop for the task shown now.'));
 assert.ok(block.length>500,'the overview section is found');
 assert.match(activity,/agents\.setOnClickListener\(v->openOverview\(\)\)/,'the well opens the overview');
 assert.match(activity,/overview=new WorkspaceOverviewPanel\(this,WorkspaceOverviewPanel\.State\.INSTANCE\)/,'selection and a pending Stop live in the process-wide state');
 assert.match(activity,/public void onBackPressed\(\)\{if\(overview!=null&&overview\.isOpen\(\)\)\{overview\.close\(\);return;\}super\.onBackPressed\(\);\}/,'Back closes the overview first');
 // Tasks only from Core's onLocalTasks feed; never the backend projection or an open session.
 assert.match(block,/public java\.util\.List<WorkspaceOverview\.Task> tasks\(\)\{return WorkspaceOverviewFeed\.tasks\(\);\}/);
 assert.match(block,/public WorkspaceOverview\.Scope scope\(\)\{return WorkspaceOverviewFeed\.scope\(\);\}/);
 assert.ok(!/taskStateJson/.test(block),'the overview never reads the backend task projection');
 // No Stop transport exists: Stop is unavailable and nothing is sent.
 assert.match(block,/public boolean stopRoute\(\)\{return false;\}/);
 assert.match(block,/public void stop\(WorkspaceOverview\.Identity task\)\{\}/);
 assert.ok(!/task\/stop|sendStop|localRoseStop/.test(block),'the overview sends no Stop');
 // Watch: fresh read after the tap, the binding the row showed, the one bound launch.
 const watch=block.slice(block.indexOf('public void watch('));
 const at=s=>{const i=watch.indexOf(s);assert.ok(i>0,s);return i;};
 assert.ok(at('controllerState()')<at('WorkspaceOverview.watchBinding(task,atTap,')&&at('WorkspaceOverview.watchBinding(task,atTap,')<at('launchBoundViewer(b.controllerTaskId,b.sessionId)'),'fresh read, then the captured-binding check, then the bound launch');
 assert.match(watch,/if\(b==null\)\{Toast\.makeText\(RoseConversationActivity\.this,WorkspaceOverview\.WATCH_MOVED_ON/,'a changed binding opens nothing and says so');
 // targetPackage: optional, published with its read's key, never makes the read malformed.
 assert.match(activity,/ctlPackage=new String\[\]\{r\.key\(\),pkg instanceof String\?\(String\)pkg:""\};/);
 assert.match(block,/String\[\] p=ctlPackage;String pkg=r\.key\(\)\.equals\(p\[0\]\)\?p\[1\]:"";/);
 assert.ok(!/Log\.[dviwe]\(/.test(block),'no identifiers are logged');
 assert.ok(!/AutoAgentPip|PictureInPicture/.test(activity),'automatic PiP is untouched');
});
