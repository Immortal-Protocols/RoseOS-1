import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createPlayStoreAdapter,knownListingPackage,knownPendingPackage,storeDetails} from './play-store.mjs';

function fixture(prepareStatus='prepared',finishStatus='installed',modern=false,transientAfterTap=false,cleanupReads=0,title='Southwest',installerTitle=title) {
 let screen=modern?'landing':'search',installs=0,finishCalls=0,installed=false,approvedPackage='com.southwestairlines.mobile';const calls=[];
 const node=(text,id,y,extra={})=>({text,description:'',id,enabled:true,bounds:[0,y,600,y+60],...extra});
 const phone=async c=>{
  calls.push(c);
  if(c.action==='list_apps')return {status:'ok',apps:installed?[{label:installerTitle,packageName:approvedPackage}]:[]};
  if(c.action==='inspect_ui'&&installerTitle!==title&&installs&&!installed)return {status:'ok',packageName:'com.android.packageinstaller',snapshotId:'different-label',nodes:[node(installerTitle,9,100,{packageName:'com.android.packageinstaller'})]};
  if(c.action==='inspect_ui'&&installed&&cleanupReads-->0)return {status:'ok',packageName:'com.android.packageinstaller',snapshotId:'old-dialog',nodes:[node('Southwest',9,100,{packageName:'com.android.packageinstaller'}),node('Install',10,180,{packageName:'com.android.packageinstaller',clickTarget:10})]};
  if(c.action==='inspect_ui'&&transientAfterTap&&installs&&!installed)return finishCalls===1
   ?{status:'ok',packageName:'com.android.vending',snapshotId:'transition',nodes:[]}
   :{status:'error',error:'Background session is not ready: blocked'};
  if(c.action==='inspect_ui'&&screen==='landing')return {status:'ok',packageName:'com.android.vending',snapshotId:'fresh',nodes:[node('',1,0,{bounds:[0,0,600,1000]}),node('Search',7,500,{clickTarget:7})]};
  if(c.action==='inspect_ui')return {status:'ok',packageName:'com.android.vending',snapshotId:'fresh',nodes:screen==='search'?[node('',1,0,{bounds:[0,0,600,1000]}),node('Southwest',2,30,{editable:true,imeEnter:true,clickTarget:2})]:screen==='results'?[node('',1,0,{bounds:[0,0,600,1000]}),node('',3,100,{description:title+'\nSouthwest Airlines\nTravel',clickTarget:3})]:[node(title,4,20),node('Southwest Airlines',5,90,{clickTarget:5}),node(installed?'Open':'Install',6,180,{clickTarget:6})]};
  if(c.action==='set_text')return {status:'action_accepted'};
  if(c.action==='ime_enter'){screen='results';return {status:'action_accepted'};}
  if(c.action==='tap_label'){
   if(c.label==='Install')installs++;else if(c.label==='Search')screen='search';else screen='details';
   return {status:'action_accepted'};
  }
  if(c.action==='app_command'){
   if(c.operation==='install_approval_prepare'){approvedPackage=c.params.packageName||approvedPackage;return {status:prepareStatus,reason:'disabled'};}
   const status=Array.isArray(finishStatus)?finishStatus[Math.min(finishCalls,finishStatus.length-1)]:finishStatus;
   finishCalls++;if(status==='installed')installed=true;
   return {status,packageName:approvedPackage,versionCode:123};
  }
  throw Error('Unexpected call '+JSON.stringify(c));
 };
 return {adapter:createPlayStoreAdapter(phone,{installTimeoutMs:40,cleanupTimeoutMs:10,pollIntervalMs:1}),calls,installs:()=>installs};
}
async function select(f){const found=await f.adapter.execute('play_store_search',{query:'Southwest'});assert.equal(found.status,'verified');return found.apps[0].appId;}
test('approval is prepared before Install, and actual installer result completes the operation',async()=>{
 const f=fixture(),appId=await select(f);const result=await f.adapter.execute('play_store_install',{appId});
 assert.equal(result.complete,true);assert.equal(result.packageName,'com.southwestairlines.mobile');assert.equal(f.installs(),1);
 assert.equal(result.installerDismissed,true);
 const prepared=f.calls.findIndex(c=>c.operation==='install_approval_prepare'),tap=f.calls.findIndex(c=>c.label==='Install');assert.ok(prepared<tap);
 assert.ok(!f.calls.some(c=>c.action==='touch_label'));
});
test('disabled approval leaves the Install button untouched',async()=>{
 const f=fixture('error'),appId=await select(f);const result=await f.adapter.execute('play_store_install',{appId});
 assert.equal(result.complete,false);assert.equal(result.phase,'approval_unavailable');assert.equal(f.installs(),0);
});
test('approval failure is not a success and never repeats Install',async()=>{
 const f=fixture('prepared','error'),appId=await select(f);const result=await f.adapter.execute('play_store_install',{appId});
 assert.equal(result.complete,false);assert.equal(result.phase,'approval_error');assert.equal(f.installs(),1);
});
test('a contradictory package identity is rejected before Install',async()=>{
 const f=fixture(),appId=await select(f);
 const result=await f.adapter.execute('play_store_install',{appId,packageName:'com.other.app'});
 assert.equal(result.status,'needs_assistance');assert.match(result.reason,/does not match/);
 assert.equal(f.installs(),0);
});
test('a verified transfer package handles differing store and installer names without app-specific mappings',async()=>{
 const f=fixture('prepared',['pending_download','approved_installing','installed'],false,false,0,'Sample App Wireless','Sample App'),appId=await select(f);
 const result=await f.adapter.execute('play_store_install',{appId,packageName:'org.example.sample'});
 assert.equal(result.complete,true);assert.equal(result.installerDismissed,true);assert.equal(result.packageName,'org.example.sample');
 assert.equal(f.calls.find(c=>c.operation==='install_approval_prepare').params.packageName,'org.example.sample');assert.equal(f.installs(),1);
});
test('one install operation waits through download and approval without another model call',async()=>{
 const f=fixture('prepared',['pending_download','pending_download','approved_installing','installed'],false,true),appId=await select(f);
 const done=await f.adapter.execute('play_store_install',{appId});
 assert.equal(done.complete,true);assert.equal(done.phase,'installed');assert.equal(f.installs(),1);
 assert.equal(f.calls.filter(c=>c.operation==='install_approval_prepare').length,1);
 assert.equal(f.calls.filter(c=>c.operation==='install_approval_finish').length,4);
 assert.ok(!f.calls.some(c=>c.action==='open_app'));
});
test('installed package does not complete the operation while its confirmation dialog remains',async()=>{
 const f=fixture('prepared','installed',false,false,3),appId=await select(f);
 const done=await f.adapter.execute('play_store_install',{appId});
 assert.equal(done.complete,true);assert.equal(done.installerDismissed,true);
 const verified=f.calls.findIndex(c=>c.action==='list_apps');
 assert.equal(f.calls.slice(verified+1).filter(c=>c.action==='inspect_ui').length,4);
 assert.equal(f.installs(),1);
});
test('a confirmation that never closes is reported as installed but incomplete',async()=>{
 const f=fixture('prepared','installed',false,false,100000),appId=await select(f);
 const done=await f.adapter.execute('play_store_install',{appId});
 assert.equal(done.complete,false);assert.equal(done.installed,true);assert.equal(done.phase,'installed_cleanup_pending');assert.equal(f.installs(),1);
});
test('an unresolved transition returns pending at the requested polling deadline',async()=>{
 const f=fixture('prepared',['pending_download'],false,true),appId=await select(f);
 await f.adapter.execute('play_store_install',{appId});
 const pending=await f.adapter.execute('play_store_installation_status',{appId,waitSeconds:0});
 assert.equal(pending.phase,'waiting_for_installer');assert.equal(pending.complete,false);assert.equal(f.installs(),1);
});
test('Fold Play Store navigation opens Search before entering the query',async()=>{
 const f=fixture('prepared','installed',true);await select(f);
 assert.equal(f.calls.filter(c=>c.label==='Search').length,1);
 assert.equal(f.calls.filter(c=>c.action==='ime_enter').length,1);
});
test('pending approval can finish without opening the blocked app display',async()=>{
 const calls=[];
 const adapter=createPlayStoreAdapter(async c=>{calls.push(c);if(c.action==='list_apps')return {status:'ok',apps:[{packageName:'com.southwestairlines.mobile'}]};if(c.action==='inspect_ui')return {status:'ok',packageName:'com.android.vending',nodes:[{text:'Open'}]};return c.operation==='install_approval_prepare'?{status:'prepared'}:{status:'installed',packageName:'com.southwestairlines.mobile',versionCode:123};});
 const result=await adapter.execute('play_store_finish_pending_install',{appName:'Southwest'});
 assert.equal(result.complete,true);assert.equal(result.packageName,'com.southwestairlines.mobile');assert.equal(calls.length,4);assert.equal(calls.some(c=>c.action==='open_app'),false);
});
test('known package identities require the selected official publisher',()=>{
 assert.equal(knownListingPackage('Google Maps','Google LLC'),'com.google.android.apps.maps');
 assert.equal(knownListingPackage('Google Maps','Other publisher'),null);
 assert.equal(knownListingPackage('Other Maps','Google LLC'),null);
 assert.equal(knownListingPackage('Uber Eats: Food and Grocery','Uber Technologies, Inc.'),'com.ubercab.eats');
 assert.equal(knownListingPackage('Uber Eats: Food and Grocery','Other publisher'),null);
 assert.equal(knownListingPackage('DoorDash: Food, Grocery, More','DoorDash'),'com.dd.doordash');
 assert.equal(knownListingPackage('ChatGPT','OpenAI'),'com.openai.chatgpt');
});
test('pending Maps approval binds the exact package despite a differing APK label',async()=>{
 const calls=[];
 const adapter=createPlayStoreAdapter(async c=>{calls.push(c);if(c.action==='list_apps')return {status:'ok',apps:[{packageName:'com.google.android.apps.maps'}]};if(c.action==='inspect_ui')return {status:'ok',packageName:'com.android.vending',nodes:[{text:'Open'}]};return c.operation==='install_approval_prepare'?{status:'prepared'}:{status:'installed',packageName:'com.google.android.apps.maps',versionCode:123};});
 const result=await adapter.execute('play_store_finish_pending_install',{appName:'Google Maps'});
 assert.equal(result.complete,true);
 assert.equal(calls[0].params.packageName,'com.google.android.apps.maps');
 assert.equal(calls[0].params.label,'Google Maps');
 assert.equal(knownPendingPackage('Some other maps app'),null);
 assert.equal(knownPendingPackage('Uber Eats'),'com.ubercab.eats');
});
test('Fold details use the selected right pane with publisher above the title',()=>{
 const screen=JSON.parse(fs.readFileSync(new URL('../../../experiments/play-store-install-approval/fixtures/fold-maps-details.json',import.meta.url),'utf8'));
 // A left-pane advertisement must not determine the selected app's state.
 for(const n of screen.nodes)if(n.bounds[0]<screen.width/2){if(n.text==='Install')n.text='Open';if(n.description==='Install')n.description='Open';}
 const result=storeDetails(screen,'Google Maps');assert.equal(result.publisher,'Google LLC');assert.equal(result.phase,'available');assert.deepEqual(result.controls,['Install']);
});
