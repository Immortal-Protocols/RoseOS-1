import {createHash} from 'node:crypto';
import {createAppControls} from './controls.mjs';
const PKG='com.android.vending';
const spec=(name,description,properties={},required=Object.keys(properties))=>({name:'play_store_'+name,description,inputSchema:{type:'object',properties,required,additionalProperties:false}});
export const playStoreTools=[
 spec('search','Search Google Play for apps. Returns visible app IDs, names and publisher or rating metadata. Does not install anything.',{query:{type:'string'}}),
 spec('details','Open a returned Google Play app result and read its actual title, publisher and installation state.',{appId:{type:'string'}}),
 spec('install','Install a user-requested FREE app from its verified Google Play details page. Pass packageName when known from the user request, verified Play Store link, or app-transfer list; never guess it. It identifies the app even when its store name and installed label differ. Waits up to two minutes for download, approval, Android package verification and dismissal of its installer dialog in this one operation. Returns complete only after all those steps. Never purchases apps, subscribes, signs in, or opens the installed app. If still pending at the deadline, use installation_status; do not press Install again.',{appId:{type:'string'},packageName:{type:'string'}},['appId']),
 {...spec('installation_status','Check the selected app installation without pressing Install again. A completed result requires Google Play Open/Play plus a matching installed app. May wait up to 15 seconds.',{appId:{type:'string'},waitSeconds:{type:'integer',minimum:0,maximum:15}},['appId']),foregroundRequired:false},
 {...spec('finish_pending_install','Finish an already-downloaded Play Store app that the user asked to install. If the download is done and Android’s installer blocks background controls, call this tool instead of retrying the blocked screen or asking for a manual Install tap. Supply the actual app name, e.g. Southwest or Apple Music. Does not start a download or purchase anything. Returns verified installed, installing, or no_matching_pending_install.',{appName:{type:'string'}}),foregroundRequired:false},
 {...spec('cancel_install','Cancel a pending app download only when the user asks. Does not uninstall an installed app.',{appId:{type:'string'}}),foregroundRequired:false}
];
const text=n=>(n.description||n.text||'').trim();
export function parseStoreResults(s){
 const seen=new Set(),out=[];
 for(const n of s.nodes){
  if(!n.enabled||n.clickTarget===undefined||!n.description?.includes('\n'))continue;
  const parts=n.description.split('\n').map(x=>x.trim()).filter(Boolean);
  if(parts.length<2||/^(Search for|Refine search|Average rating|Downloaded |Content rating)/i.test(parts[0]))continue;
  if(seen.has(n.clickTarget))continue;seen.add(n.clickTarget);
  out.push({title:parts[0],metadata:parts.slice(1),label:n.description,target:n.clickTarget});
 }
 return out;
}
export function storePageState(s){
 const labels=[...new Set(s.nodes.map(text).filter(Boolean))];
 const actionNames=['Install','Open','Play','Update','Uninstall','Cancel','Enable'];
 const targets=s.nodes.filter(n=>actionNames.includes(text(n))&&n.enabled&&n.clickTarget!==undefined).sort((a,b)=>a.bounds[1]-b.bounds[1]);
 const first=targets[0],band=first?Math.max(100,first.bounds[3]-first.bounds[1]):0;
 const controls=[...new Set(targets.filter(n=>n.bounds[1]<=first.bounds[1]+band).map(text))];
 const progress=labels.filter(t=>/^(Pending|Installing|Downloading|Waiting for|\d+%|\d+(?:\.\d+)?\s*[KMGT]?B\s*\/)/i.test(t));
 return {controls,progress,phase:controls.includes('Open')||controls.includes('Play')?'installed':controls.includes('Cancel')||progress.length?'installing':controls.includes('Update')?'installed_update_available':controls.includes('Install')?'available':'unknown'};
}
function detailsScope(s,title){
 const header=s.nodes.find(n=>n.text===title&&n.clickTarget===undefined&&n.bounds);
 const width=s.displayWidth||s.width||s.nodes[0]?.bounds?.[2];
 if(!header||!width||header.bounds[0]<=width/2)return s;
 return {...s,nodes:s.nodes.filter(n=>n.bounds&&n.bounds[0]>=width/2)};
}
export function storeDetails(s,title){
 s=detailsScope(s,title);
 const header=s.nodes.find(n=>n.text===title&&n.clickTarget===undefined&&n.bounds);
 if(!header)return null;
 const margin=Math.max(130,(header.bounds[3]-header.bounds[1])*2);
 const publisher=s.nodes.find(n=>n.text&&n.text!==title&&n.clickTarget!==undefined&&n.bounds[1]>=header.bounds[1]-margin&&n.bounds[1]<=header.bounds[3]+margin&&!['Install','Open','Play','Update','Uninstall','Cancel','Enable'].includes(n.text));
 if(!publisher)return null;
 return {name:title,publisher:publisher.text,...storePageState(s)};
}
const norm=s=>s.toLowerCase().replace(/\s+for android$/,'').replace(/[^\p{L}\p{N}]+/gu,' ').trim();
export function matchInstalledApp(apps,title){const matches=apps.filter(a=>norm(a.label)===norm(title));return matches.length===1?matches[0]:null;}
// Canonical identities of the existing phone integrations, bound only after
// both the selected store title and its publisher have been read from the UI.
export function knownListingPackage(title,publisher){
 if(['Google Maps','Maps'].includes(title)&&publisher==='Google LLC')return 'com.google.android.apps.maps';
 if(['Southwest','Southwest Airlines','Southwest Airlines: Travel App'].includes(title)&&publisher==='Southwest Airlines')return 'com.southwestairlines.mobile';
 if(title==='Apple Music'&&publisher==='Apple')return 'com.apple.android.music';
 if(title==='ChatGPT'&&publisher==='OpenAI')return 'com.openai.chatgpt';
 if(title==='DoorDash: Food, Grocery, More'&&publisher==='DoorDash')return 'com.dd.doordash';
 if(title==='Uber Eats: Food and Grocery'&&publisher==='Uber Technologies, Inc.')return 'com.ubercab.eats';
 return null;
}
// These are the established app identities in our phone integrations. A
// pending request names the app the user asked for; its staged display label
// may differ from the store title (Google Maps is labelled Maps in its APK).
// Unknown names still require the installer's exact label match.
export function knownPendingPackage(title){
 const names={'google maps':'com.google.android.apps.maps','maps':'com.google.android.apps.maps','southwest':'com.southwestairlines.mobile','southwest airlines':'com.southwestairlines.mobile','southwest airlines travel app':'com.southwestairlines.mobile','apple music':'com.apple.android.music','chatgpt':'com.openai.chatgpt','doordash':'com.dd.doordash','doordash food grocery more':'com.dd.doordash','uber eats':'com.ubercab.eats','uber eats food and grocery':'com.ubercab.eats'};
 return names[norm(title)]||null;
}
export function createPlayStoreAdapter(phone,{installTimeoutMs=120000,cleanupTimeoutMs=6000,pollIntervalMs=500}={}){
 const ui=createAppControls({phone,packageName:PKG,timeoutMs:installTimeoutMs+cleanupTimeoutMs+25000});let saved=new Map();
 const approval=(operation,r)=>phone({action:'app_command',packageName:'dev.pixel.voice',operation:'install_approval_'+operation,params:{label:r.title,...(r.packageName?{packageName:r.packageName}:{})}});
 const topInput=s=>ui.one(s,n=>n.editable&&n.imeEnter&&n.bounds[1]<s.nodes[0].bounds[3]*.25);

 async function search({query}){
  if(!query.trim()||query.length>150)throw Error('Search needs an app name or a short query');let s=await ui.screen();
  if(!s.nodes.length)s=await ui.wait(x=>x.nodes.length>0,6000);
  if(!topInput(s)){
   const searchButton=x=>ui.label(x,'Search Google Play')||ui.label(x,'Search Apps & Games')||ui.label(x,'Search apps & games')||ui.label(x,'Search or ask Play')||ui.label(x,'Search');
   let button=searchButton(s);
   if(!button){const back=ui.label(s,'Navigate up');if(back){await ui.tap(s,back,{physical:false});s=await ui.screen();button=searchButton(s);}}
   if(!button)throw Error('Google Play search control is unavailable');await ui.tap(s,button,{physical:false});
   s=await ui.wait(x=>!!topInput(x)||!!(ui.label(x,'Search apps & games')||ui.label(x,'Search Google Play')||ui.label(x,'Search Apps & Games')||ui.label(x,'Search or ask Play')));
   if(!topInput(s)){button=searchButton(s);await ui.tap(s,button,{physical:false});s=await ui.wait(x=>!!topInput(x));}
  }
  let field=topInput(s);const set=await phone({action:'set_text',snapshotId:s.snapshotId,nodeId:field.id,text:query.trim()});if(set.status!=='action_accepted')throw Error('Search text was not accepted');
  s=await ui.screen();field=topInput(s);if(!field||field.text!==query.trim())throw Error('Search field did not contain the requested query');
  let enter=await phone({action:'ime_enter',snapshotId:s.snapshotId,nodeId:field.id});
  if(enter.status==='input_not_focused'){s=await ui.screen();field=topInput(s);if(!field?.focused)throw Error('Search field is not focused');enter=await phone({action:'ime_enter',snapshotId:s.snapshotId,nodeId:field.id});}
  if(enter.status!=='action_accepted')throw Error('Search was not submitted');
  s=await ui.wait(x=>!topInput(x)&&(parseStoreResults(x).length||x.nodes.some(n=>/No results found|No results for/i.test(text(n)))),6000);
  saved=new Map();const counts=new Map();const apps=parseStoreResults(s).map(r=>{const occurrence=counts.get(r.label)||0;counts.set(r.label,occurrence+1);const appId=createHash('sha256').update(query+'\n'+r.label+'\n'+occurrence).digest('hex').slice(0,18);saved.set(appId,{...r,occurrence,query,created:Date.now()});return {appId,name:r.title,metadata:r.metadata};});
  return {status:'verified',query,apps,scope:'Visible Google Play results; publisher is verified on the app details page before installation'};
 }
 function record(appId){const r=saved.get(appId);if(!r||Date.now()-r.created>30*60*1000)throw Error('App result expired; search again');return r;}
 async function details({appId}){
  const r=record(appId);let s=await ui.screen();
  if(!(storeDetails(s,r.title))){
   const target=screen=>{const row=parseStoreResults(screen).filter(x=>x.label===r.label)[r.occurrence];return row?screen.nodes.find(n=>n.clickTarget===row.target&&n.description===r.label):null;};
   let n=target(s);if(!n){await search({query:r.query});s=await ui.screen();n=target(s);}if(!n)throw Error('The selected app is no longer in the search results');
   await ui.tap(s,n,{physical:false});s=await ui.wait(x=>storeDetails(x,r.title),6000);
  }
  // Retain the result identity even when another search refreshed the catalog.
  const actual=storeDetails(s,r.title);if(!actual)throw Error('App title and publisher could not be verified');
  if(r.publisher&&r.publisher!==actual.publisher)throw Error('Publisher changed; inspect the listing before installing');
  r.publisher=actual.publisher;
  const knownPackage=knownListingPackage(actual.name,actual.publisher);
  if(r.packageName&&knownPackage&&r.packageName!==knownPackage)throw Error('Requested package does not match the verified listing');
  r.packageName=r.packageName||knownPackage;saved.set(appId,r);
  return {status:'verified',appId,resultMetadata:r.metadata,...actual};
 }
 async function installed(r,state){
  if(!['installed','installed_update_available'].includes(state.phase))return null;
  const list=await phone({action:'list_apps'});if(list.status!=='ok')throw Error('Could not verify installed applications');
  const app=matchInstalledApp(list.apps,r.title);
  return app?{status:'verified',name:r.title,phase:'installed',complete:true,packageName:app.packageName,verification:'Google Play state and Android installed app list'}:{status:'verified',name:r.title,phase:'store_reports_installed',complete:false,verification:'Google Play reports installed; app label could not be matched uniquely'};
 }
 async function completedInstall(r,checked){
  const list=await phone({action:'list_apps'});
  if(list.status!=='ok'||!list.apps.some(app=>app.packageName===checked.packageName))return {status:'uncertain',name:r.title,phase:'package_verification_pending',complete:false,reason:'Installer reported success but Android package verification has not completed; do not repeat Install'};
  const end=Date.now()+cleanupTimeoutMs;
  do{
   const screen=await phone({action:'inspect_ui'});
   const installerVisible=screen.packageName==='com.android.packageinstaller'||screen.nodes?.some(n=>n.packageName==='com.android.packageinstaller');
   if(screen.status==='ok'&&screen.packageName===PKG&&screen.nodes?.length&&!installerVisible)return {status:'verified',name:r.title,phase:'installed',complete:true,packageName:checked.packageName,versionCode:checked.versionCode,installerDismissed:true,verification:'Matching Android package installed and installer dialog dismissed'};
   if(Date.now()>=end)break;
   await ui.sleep(pollIntervalMs);
  }while(true);
  return {status:'verified',name:r.title,phase:'installed_cleanup_pending',installed:true,complete:false,packageName:checked.packageName,reason:'App is installed but its installer dialog has not been confirmed closed; continue installation_status, do not repeat Install'};
 }
 async function observe(r){
  if(r.approvalPrepared){
   const checked=await approval('finish',r);
   if(checked.status==='installed')return completedInstall(r,checked);
   if(['error','expired','failed','revoked'].includes(checked.status))return {status:'needs_assistance',name:r.title,phase:'approval_'+checked.status,complete:false,reason:checked.reason||'Install approval is unavailable; do not repeat Install'};
   if(checked.status==='approved_installing')return {status:'verified',name:r.title,phase:'installing',complete:false,approval:'approved'};
  }
  const raw=await phone({action:'inspect_ui'});
  if(raw.packageName==='com.android.packageinstaller'){
   // The prepared request matches the actual package/session on-device. The
   // installer label can legitimately differ from its Play Store title.
   if(!r.approvalPrepared&&!raw.nodes.some(n=>text(n)===r.title))throw Error('Another app installation is in the foreground');
   return r.approvalPrepared?{status:'verified',name:r.title,phase:'installing',complete:false,approval:'waiting_for_matching_session'}:{status:'verified',name:r.title,phase:'awaiting_user_install_confirmation',complete:false,requiredAction:'Tap Install on the phone. Install approval is not enabled. Do not retry installation.'};
  }
  if(raw.status!=='ok'||raw.packageName!==PKG)return r.approvalPrepared
   ?{status:'verified',name:r.title,phase:'waiting_for_installer',complete:false,reason:'The requested install is still awaiting a matching installer result; do not press Install again'}
   :{status:'verified',name:r.title,phase:'not_visible',complete:false};
  const state=storePageState(detailsScope(raw,r.title));
  if(r.approvalPrepared&&state.phase==='unknown')return {status:'verified',name:r.title,phase:'waiting_for_installer',complete:false,reason:'The install screen is transitioning; continue checking the existing request'};
  return await installed(r,state)||{status:'verified',name:r.title,...state,complete:false};
 }
 async function waitForInstall(r,waitMs){
  const end=Date.now()+waitMs;let observed;
  do{
   observed=await observe(r);
   if(observed.complete||!['available','installing','waiting_for_installer','installed_cleanup_pending','package_verification_pending'].includes(observed.phase)||Date.now()>=end)return observed;
   await ui.sleep(pollIntervalMs);
  }while(true);
 }
 async function install({appId,packageName}){
  const r=record(appId);
  if(packageName!==undefined){
   if(!/^(?:[A-Za-z][\w]*\.)+[\w]+$/.test(packageName))throw Error('Invalid package name');
   if(r.packageName&&r.packageName!==packageName)throw Error('Requested package does not match the verified listing');
   r.packageName=packageName;
  }
  if(r.approvalPrepared)return waitForInstall(r,installTimeoutMs);
  await details({appId});let s=detailsScope(await ui.screen(),r.title),state=storePageState(s);const existing=await installed(r,state);if(existing)return {...existing,alreadyInstalled:true};
  const alreadyInstalling=state.phase==='installing';
  const button=alreadyInstalling?null:ui.label(s,'Install');if(!alreadyInstalling&&!button)throw Error('A free Install control is not available; no purchase or subscription was attempted');
  const prepared=await approval('prepare',r);
  if(prepared.status!=='prepared')return {status:'needs_assistance',name:r.title,phase:'approval_unavailable',complete:false,reason:prepared.reason||'Install approval could not be prepared; nothing was pressed'};
  r.approvalPrepared=true;
  if(!alreadyInstalling){ui.markMutation();await ui.tap(s,button,{physical:false,changesContent:true});}
  r.installRequested=true;
  return waitForInstall(r,installTimeoutMs);
 }
 async function status({appId,waitSeconds=0}){
  const r=record(appId),apps=r.approvalPrepared?null:await phone({action:'list_apps'}),app=apps?.status==='ok'?matchInstalledApp(apps.apps,r.title):null;
  if(app)return {status:'verified',name:r.title,phase:'installed',complete:true,packageName:app.packageName,verification:'Android installed app list'};
  return waitForInstall(r,waitSeconds*1000);
 }
 async function finishPending({appName}){
  const r={title:appName.trim()};if(!r.title||r.title.length>200)throw Error('An app name is required');
  r.packageName=knownPendingPackage(r.title);
  const prepared=await approval('prepare',r);
  if(prepared.status!=='prepared')return {status:'needs_assistance',complete:false,reason:prepared.reason||'Could not prepare install approval'};
  const deadline=Date.now()+15000;
  do{
   const result=await approval('finish',r);
   if(result.status==='installed')return completedInstall(r,result);
   if(result.status==='pending_download'){await approval('cancel',r);return {status:'verified',phase:'no_matching_pending_install',complete:false,name:r.title,reason:'No matching downloaded app awaits approval; use Play Store search and install'};}
   if(result.status!=='approved_installing')return {status:'needs_assistance',complete:false,name:r.title,reason:result.reason||result.status};
   await ui.sleep(500);
  }while(Date.now()<deadline);
  return {status:'verified',phase:'installing',complete:false,name:r.title,reason:'Approval accepted; the installed package is not verified yet'};
 }
 async function cancel({appId}){const r=record(appId);if(r.approvalPrepared){await approval('cancel',r);r.approvalPrepared=false;}const current=await observe(r);if(current.phase==='awaiting_user_install_confirmation')return {...current,requiredAction:'Tap Cancel on the phone to dismiss the protected confirmation.'};if(current.phase!=='installing')return {...current,cancelled:false};const s=await ui.screen(),n=ui.label(s,'Cancel');if(!n)throw Error('Download cancellation control is unavailable');await ui.tap(s,n,{physical:false,changesContent:true});await ui.wait(x=>storePageState(x).phase!=='installing',6000);return {...await observe(r),cancelled:true};}

 return {packageName:PKG,reuseForeground:true,voiceContext:'Google Play can search for apps, inspect app listings, install requested free apps, and cancel requested downloads. When Android’s installer blocks background controls after a download, play_store_finish_pending_install completes that app’s approval without opening the blocked display.',tools:playStoreTools,execute:(name,args={})=>ui.run(()=>{const handlers={play_store_search:search,play_store_details:details,play_store_install:install,play_store_installation_status:status,play_store_finish_pending_install:finishPending,play_store_cancel_install:cancel};if(!handlers[name])throw Error('Unknown Play Store tool');return handlers[name](args);})};
}
