import {isBackgroundSafeAppCommand,backgroundAppCommandRejection} from './background-app-policy.mjs';

export const backgroundPhonePolicy = `Phone app control normally runs in a separate background workspace. Use the normal phone tools: open_app selects an app, then inspect_ui and screenshot observe the current workspace. A task may open another app, including Android's installer; follow the screen actually shown and continue the user's requested work there. The user's visible screen is independent. To inspect or operate the physical phone screen for the user's requested task, use python3 rose-agent/phone/control.py --display phone followed by the normal command. This explicitly selects the phone screen for that call; omitted --display keeps using the background workspace. Keep screenshot IDs, node IDs and coordinates with the display that produced them. Phone-display actions affect the user's visible screen; background actions continue in the separate workspace. System dialogs may appear on either display, or may fail to open; inspect rather than assuming. For an install, read the displayed app and press the installer's normal Install control, then verify installation; do not depend on app-name-specific approval exceptions. Use screenshot-grounded screen_gesture when a visible control is absent from inspect_ui. After focusing an editor by a visible tap, type_text writes to that focused editor using a fresh screenshotId; commit submits it. Do not use an unverified native app operation as a workaround. A reported unsupported or blocked action did not run; report the actual result without claiming success. home ends the selected workspace or returns the phone screen home, according to --display. While the phone is locked or its screen is off, phone-display actions return locked and the background workspace continues only if the owner allowed app control while locked; installer, permission, settings, authentication and secure screens still wait for the owner. A locked result did not run: tell the user the phone needs them, and never try to get around the lock screen or those screens. Device environment and diagnostics still describe the physical phone. Complete the requested task and verify the actual result before answering.`;

const uiActions=new Set(['screenshot','screen_gesture','type_text','inspect_ui','tap','long_press','tap_label','touch_label','touch_point','set_text','ime_enter','scroll','swipe','drag','back','home','app_context','setup_ui_control']);
const directActions=new Set(['environment','diagnostics','list_apps','control_status','voice_ducking','visualization_set','visualization_clear','visualization_status','debug_bridge_connect','migration_backup_status']);
const PACKAGE='dev.pixel.backgroundcontrol';
const nodeActions=new Set(['tap','long_press','touch_point','set_text','ime_enter','scroll','swipe','drag']);
const labelActions=new Set(['tap_label','touch_label']);
// Rose Agent refuses background work while the owner is away unless they armed locked control,
// and Android refuses or blocks locked sessions at owner gates. Neither is a retryable failure.
const ownerUnlockRequired=s=>s?.status==='locked'||s?.reason==='owner_unlock_required'||s?.reason==='device_locked';
const lockedResult=(details,extra={})=>({status:'locked',actionExecuted:false,backgroundExecution:true,reason:details?.message||'The phone needs its owner: unlock it, or allow app control while locked, before this can continue.',lockedControl:details?.lockedControl??(details?.reason==='device_locked'?'os_refused':'owner_gate'),...extra,details});
const nodeKey=n=>JSON.stringify([n.packageName??'',n.className??n.class??'',n.viewId??'',n.text??'',n.description??'',n.bounds??[],!!n.clickable,!!n.editable,!!n.scrollable,!!n.password]);

export class BackgroundPhone {
 constructor({send,enabled=()=>false,now=Date.now,sleep=ms=>new Promise(r=>setTimeout(r,ms)),record=()=>{}}){
  Object.assign(this,{send,enabled,now,sleep,record});this.queue=Promise.resolve();this.session=null;this.observations=new Map();
 }
 execute(command){
  if(!this.enabled())return this.send(command);
  // Serialize selection and UI operations, including calls from separate APIs.
  const work=this.queue.catch(()=>{}).then(()=>this.run(command));this.queue=work;return work;
 }
 state(){return {enabled:this.enabled(),mode:this.enabled()?'background':'foreground',session:this.session?{...this.session}:null};}
 call(operation,params={}){return this.send({action:'app_command',packageName:PACKAGE,operation,params});}
 remember(observation){
  if(observation.status!=='ok'||!observation.snapshotId||!Array.isArray(observation.nodes))return;
  this.observations.set(observation.snapshotId,{sessionId:this.session?.sessionId,nodes:observation.nodes.map(n=>({...n,bounds:n.bounds?.slice()}))});
  while(this.observations.size>16)this.observations.delete(this.observations.keys().next().value);
 }
 async refreshTarget(command){
  const prior=this.observations.get(command.snapshotId);
  const hasNode=Number.isInteger(command.nodeId);
  if(nodeActions.has(command.action)&&!hasNode)return {error:'A previously observed target is required; inspect the current controls.'};
  const target=hasNode&&prior?.sessionId===this.session.sessionId?prior.nodes.find(n=>(n.nodeId??n.id)===command.nodeId):null;
  if(hasNode&&!target)return {error:'The observed target is no longer available; inspect the current controls.'};
  const current=await this.call('command',{action:'inspect_ui',sessionId:this.session.sessionId});
  if(ownerUnlockRequired(current))return {locked:current};
  this.remember(current);
  if(current.status!=='ok'||!current.snapshotId||current.sessionId&&current.sessionId!==this.session.sessionId)return {error:'Could not refresh controls in the existing background workspace.'};
  let nodeId=command.nodeId;
  if(target){
   const matches=(current.nodes??[]).filter(n=>nodeKey(n)===nodeKey(target));
   if(matches.length!==1)return {error:'The observed target changed or is ambiguous; inspect the current controls.'};
   nodeId=matches[0].nodeId??matches[0].id;
  }
  this.record({stage:'background_target_refreshed',action:command.action,packageName:this.session.targetPackage});
  return {command:{...command,snapshotId:current.snapshotId,...(hasNode?{nodeId}:{})}};
 }
 async start(packageName){
  let state=await this.call('get_state');
  // Reopening the same app while Android's installer blocks it must not
  // destroy the live workspace. Approval/status tools can finish the pending
  // operation and the next observation will resume that same session.
  if(state.status==='blocked'&&state.targetPackage===packageName&&state.sessionId){
   if(this.session?.sessionId!==state.sessionId)this.observations.clear();
   this.session={sessionId:state.sessionId,targetPackage:packageName};
   if(ownerUnlockRequired(state))return {status:'background_blocked',actionExecuted:false,packageName,backgroundExecution:true,sessionId:state.sessionId,sessionRetained:true,ownerUnlockRequired:true,details:state,reason:'The background app reached a screen that needs the owner while the phone is locked. The workspace has been retained; it continues after the owner unlocks and completes that screen.'};
   return {status:'background_blocked',actionExecuted:false,packageName,backgroundExecution:true,sessionId:state.sessionId,sessionRetained:true,details:state,reason:'The existing background app is blocked by Android. Resolve its pending operation before continuing; the workspace has been retained.'};
  }
  if(state.status!=='ready'||state.targetPackage!==packageName){
   state=await this.call('start',{packageName});
   const deadline=this.now()+12000;
   while(['starting','launching','scheduled'].includes(state.status)&&this.now()<deadline){await this.sleep(150);state=await this.call('get_state');}
  }
  if(state.status!=='ready'||!state.sessionId||state.targetPackage!==packageName){
   this.session=null;
   if(ownerUnlockRequired(state))return lockedResult(state,{packageName});
   return {status:state.status==='consent_required'?'background_consent_required':'background_unavailable',actionExecuted:false,packageName,backgroundExecution:true,details:state};
  }
  if(this.session?.sessionId!==state.sessionId)this.observations.clear();
  this.session={sessionId:state.sessionId,targetPackage:packageName};
  const observation=await this.call('command',{...this.session,action:'inspect_ui'});
  if(ownerUnlockRequired(observation))return lockedResult(observation,{packageName,sessionId:state.sessionId});
  this.remember(observation);
  const verified=observation.status==='ok'&&observation.packageName===packageName;
  this.record({stage:'background_open',packageName,sessionId:state.sessionId,verified});
  return {status:verified?'opened':'launch_unverified',packageName,backgroundExecution:true,backgroundVerified:verified,foregroundVerified:false,sessionId:state.sessionId,observation};
 }
 async run(command){
  if(command.display!==undefined&&command.display!=='background'&&command.display!=='phone')throw Error('Unknown display; choose background or phone');
  if(command.display==='phone'){
   const {display,...request}=command;
   if(!uiActions.has(request.action)&&request.action!=='open_app')throw Error('Display selection applies to phone UI commands');
   const result=await this.send(request);
   this.record({stage:'phone_display_action',action:request.action,status:result.status});
   return {...result,scope:'phone_display',backgroundExecution:false};
  }
  if(command.action==='open_app')return this.start(command.packageName);
  if(command.action==='app_command')return isBackgroundSafeAppCommand(command)?this.send(command):backgroundAppCommandRejection(command);
  if(directActions.has(command.action)){
   const result=await this.send(command);
   return command.action==='control_status'?{...result,backgroundControl:this.state()}:result;
  }
  if(!uiActions.has(command.action))return {status:'not_executed',actionExecuted:false,reason:'This operation is not verified for background control. Use background phone UI controls.',backgroundExecution:true};
  if(command.action==='home'){await this.closeNow();return {status:'action_accepted',actionExecuted:true,backgroundExecution:true,workspaceClosed:true};}
  if(!this.session){
   const state=await this.call('get_state');
   if(state.status==='ready'&&state.sessionId&&state.targetPackage)this.session={sessionId:state.sessionId,targetPackage:state.targetPackage};
  }
  if(!this.session)return {status:'no_background_app',actionExecuted:false,backgroundExecution:true,reason:'Use open_app to select an app in the background workspace first.'};
  let request={...command,sessionId:this.session.sessionId};
  if(nodeActions.has(command.action)||labelActions.has(command.action)){
   const refreshed=await this.refreshTarget(command);
   if(refreshed.locked)return lockedResult(refreshed.locked,{sessionId:this.session.sessionId});
   if(refreshed.error)return {status:'stale_screen',actionExecuted:false,reason:refreshed.error,backgroundExecution:true};
   request={...refreshed.command,sessionId:this.session.sessionId};
  }
  // The public back command has no snapshot argument; the scoped controller
  // requires a current frame before every navigation action.
  if(command.action==='back'&&!command.snapshotId){
   const observation=await this.call('command',{action:'inspect_ui',sessionId:this.session.sessionId});
   if(ownerUnlockRequired(observation))return lockedResult(observation,{sessionId:this.session.sessionId});
   if(observation.status!=='ok'||!observation.snapshotId)return {status:'not_executed',actionExecuted:false,backgroundExecution:true,reason:'Could not observe the background app before navigating back.',details:observation};
   request.snapshotId=observation.snapshotId;
  }
  let result=await this.call('command',request);
  // The controller reports a blocked session as not ready; its state says whether the owner must act.
  if(result.status==='error'&&/not ready: blocked/.test(result.error||'')){const state=await this.call('get_state');if(ownerUnlockRequired(state))result=state;}
  if(ownerUnlockRequired(result)){this.record({stage:'background_locked',action:command.action,packageName:this.session.targetPackage,lockedControl:result.lockedControl??result.reason});return lockedResult(result,{sessionId:this.session.sessionId});}
  this.remember(result);
  this.record({stage:'background_action',action:command.action,packageName:this.session.targetPackage,sessionId:this.session.sessionId,status:result.status});
  return {...result,backgroundExecution:true,sessionId:this.session.sessionId};
 }
 async closeNow(){try{await this.call('stop');}finally{this.session=null;this.observations.clear();}}
 close(){const work=this.queue.catch(()=>{}).then(()=>this.closeNow());this.queue=work;return work;}
}
