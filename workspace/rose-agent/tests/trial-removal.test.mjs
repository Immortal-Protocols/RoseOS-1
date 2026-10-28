// APP-REMOVE-001: the DeepSeek Fast, Luna-first and ARTEMIS trial controls are removed from the
// page and from VoiceService's command dispatch. Shared model labelling, voice-routing revert and
// backend-reported trial state keep working, and nothing resets a backend that is already enabled.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {loadPage} from './web-ui-harness.mjs';

const read=path=>fs.readFileSync(new URL(path,import.meta.url),'utf8');
const html=read('../app/assets/index.html');
const service=read('../app/src/dev/pixel/voice/VoiceService.java');
const calls=(page,name)=>page.calls.filter(c=>c[0]===name).map(c=>c.slice(1));
const settle=()=>new Promise(resolve=>setImmediate(resolve));

// A backend that still reports every trial as enabled, as an already-enabled phone would see.
const enabledBackend={
 '/health':{ready:true,taskModel:{model:'deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast',label:'DeepSeek V4.1 Flash · Fast'}},
 '/voice/config':{provider:'fake',voiceRouting:{mode:'luna'},artemisTrial:{enabled:true}},
 '/artemis/config':{enabled:true},
};
function pageWithBackend(replies){
 const page=loadPage();
 page.run(`var sent=[];api=(method,route,data)=>{sent.push([method,route,data]);return Promise.resolve(${JSON.stringify(replies)}[route.split('?')[0]]||{});};`);
 return page;
}

test('the three trial toggles are gone from the page, not renamed',()=>{
 const page=loadPage();
 for(const name of ['toggleDeepseekTrial','toggleLunaFirst','toggleArtemisTrial'])
  assert.equal(page.run(`typeof window.${name}`),'undefined',name);
 const trialNamed=page.run(`Object.keys(window).filter(k=>typeof window[k]==='function'&&/deepseek|lunafirst|artemis/i.test(k))`);
 assert.deepEqual([...trialNamed],[],'no page function is still named for a trial');
 assert.ok(!html.includes("'/task-model'"),'no page code can switch the task model');
 assert.ok(!/api\('POST','\/artemis\/config'/.test(html),'no page code can switch ARTEMIS');
});

test('VoiceService no longer dispatches the trial commands or writes their menu mirrors',()=>{
 for(const command of ['toggle_deepseek_trial','toggle_luna_first','toggle_artemis_trial'])
  assert.ok(!service.includes('"'+command+'"'),command);
 for(const fn of ['toggleDeepseekTrial','toggleLunaFirst','toggleArtemisTrial'])
  assert.ok(!service.includes(fn),fn);
 for(const mirror of ['"deepseek-trial"','"artemis-trial"','"voice-routing"'])
  assert.ok(!service.includes(mirror),'mirror preference no longer written: '+mirror);
 // The remaining dispatch still maps the core commands.
 for(const kept of ['"toggle_conversation_mode"','"toggle_task_widgets"','"toggle_decisions_routing"','"talk"','"tray_mute"','"end"'])
  assert.ok(service.includes(kept),'kept command '+kept);
});

test('an already-enabled backend is read but never reset by the page',async()=>{
 const page=pageWithBackend(enabledBackend);
 await page.run('check()');await settle();
 assert.equal(page.run(`sent.filter(([method])=>method!=='GET').length`),0,'reading state sends no POST');
 assert.ok(page.run('sent').every(([,route])=>!route.startsWith('/task-model')&&!route.startsWith('/artemis/config')));
});

test('the shared model label still reaches native code truthfully',async()=>{
 const page=pageWithBackend(enabledBackend);
 await page.run('check()');await settle();
 const label=JSON.parse(calls(page,'taskModelState').at(-1)[0]);
 assert.equal(label.label,'DeepSeek V4.1 Flash · Fast','the label shows the model the backend actually uses');
 assert.match(service,/taskModelLabel=data\.optString\("label","Model unavailable"\)/,'native keeps the label');
});

test('a backend left in Luna routing can still be returned to Live from the page',async()=>{
 // changeVoiceRouting stays: the page's revert control is its remaining caller.
 const page=pageWithBackend(enabledBackend);
 await page.run('check()');await settle();
 page.run(`$('revertRouting').onclick()`);await settle();
 const revert=page.run(`JSON.stringify(sent.filter(([method,route])=>method==='POST'&&route==='/voice/routing'))`);
 assert.equal(revert,JSON.stringify([['POST','/voice/routing',{mode:'live'}]]),'revert sends Live and nothing else');
});

test('backend-reported ARTEMIS state still gates the page tools it governs',async()=>{
 const page=pageWithBackend(enabledBackend);
 await page.run('check()');await settle();
 assert.equal(page.run('artemisTrialEnabled'),true,'enabled-state stays truthful while the backend reports it');
 page.run('applyRoutingConfig({artemisTrial:{enabled:false}})');
 assert.equal(page.run('artemisTrialEnabled'),false);
});
