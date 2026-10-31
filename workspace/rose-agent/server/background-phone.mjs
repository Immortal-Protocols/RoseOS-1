import {isBackgroundSafeAppCommand,backgroundAppCommandRejection} from './background-app-policy.mjs';

export const backgroundPhonePolicy = `Phone app control normally runs in a separate background workspace, independent of the user's visible screen. Registered app tools open their app there and verify the result. phone_open_app selects an app in the workspace, and the other registered phone tools act in it; set display to phone on any of them to operate the user's physical phone screen for the requested task instead. Phone-display actions affect the user's visible screen. The shell phone tools in python3 rose-agent/phone/control.py only observe: inspect_ui and screenshot read the background workspace, and with --display phone they read the physical phone screen instead. Keep screenshot IDs, node IDs and coordinates with the display that produced them. The shell cannot open apps, tap, type, go home or act in any other way on either display (see the phone control boundary). phone_home ends the selected workspace or returns the phone screen home, according to display. System dialogs may appear on either display, or may fail to open; inspect rather than assuming. Do not use an unverified native app operation as a workaround. background_target_not_allowed means this phone's OS does not let Rose use that app in the background workspace and nothing opened: tell the user, and use the physical phone screen for it only if they want that. When the OS lets Rose's controller drive any app (control_status shows anyAppControl), the workspace follows the task into other apps such as Android's installer or a sign-in screen, and inspection shows their controls. Press an installer's Install control only for an app the user asked to install in this request, then verify the installation; Android's permission, unknown-source and settings prompts, security warnings and anything asking for the owner's credentials still wait for the owner. A reported unsupported or blocked action did not run; report the actual result without claiming success. While the phone is locked or its screen is off, phone-display commands return locked and the background workspace continues only if the owner allowed app control while locked; installer, permission, settings, authentication and secure screens still wait for the owner. A locked result did not run: tell the user the phone needs them, and never try to get around the lock screen or those screens. Device environment and diagnostics still describe the physical phone. Complete the requested task and verify the actual result before answering.`;

const uiActions=new Set(['screenshot','screen_gesture','type_text','inspect_ui','tap','long_press','tap_label','touch_label','touch_point','set_text','ime_enter','scroll','swipe','drag','back','home','app_context','setup_ui_control']);
const directActions=new Set(['environment','diagnostics','list_apps','control_status','voice_ducking','visualization_set','visualization_clear','visualization_status','debug_bridge_connect','migration_backup_status']);
const PACKAGE='dev.pixel.backgroundcontrol';
const nodeActions=new Set(['tap','long_press','touch_point','set_text','ime_enter','scroll','swipe','drag']);
const labelActions=new Set(['tap_label','touch_label']);
// A "stopped" controller while this workspace still holds its session means
// something other than the agent ended it: the owner's Stop, or the 600 s
// deadline. Agent stop/home clears the session first, so it never matches.
// Schema 3 controllers report stopReason and stoppedSessionId; schema 2 keeps
// the last sessionId (Controller.stop) and has no reason.
const ownerStopped=(s,session)=>{
 if(!session||s?.status!=='stopped'||s.stopReason==='agent')return false;
 const ended=s.stoppedSessionId||s.sessionId;
 return !ended||ended===session.sessionId;
};
// Rose Agent refuses background work while the owner is away unless they armed locked control,
// and Android refuses or blocks locked sessions at owner gates. Neither is a retryable failure.
const ownerUnlockRequired=s=>s?.status==='locked'||s?.reason==='owner_unlock_required'||s?.reason==='device_locked';
const lockedResult=(details,extra={})=>({status:'locked',actionExecuted:false,backgroundExecution:true,reason:details?.message||'The phone needs its owner: unlock it, or allow app control while locked, before this can continue.',lockedControl:details?.lockedControl??(details?.reason==='device_locked'?'os_refused':'owner_gate'),...extra,details});
const nodeKey=n=>JSON.stringify([n.packageName??'',n.className??n.class??'',n.viewId??'',n.text??'',n.description??'',n.bounds??[],!!n.clickable,!!n.editable,!!n.scrollable,!!n.password]);

export class BackgroundPhone {
 constructor({send,enabled=()=>false,now=Date.now,sleep=ms=>new Promise(r=>setTimeout(r,ms)),record=()=>{},onOwnerStop=()=>{},taskId=()=>null}){
  Object.assign(this,{send,enabled,now,sleep,record,onOwnerStop,taskId});this.queue=Promise.resolve();this.session=null;this.observations=new Map();this.stoppedByOwner=null;this.closeGeneration=0;this.origin=null;
 }
 // A new task may open a workspace again after an owner Stop ended the previous one.
 // Only an explicit new user task reopens the workspace; it starts without the old task's session.
 beginTask(){this.stoppedByOwner=null;this.session=null;this.observations.clear();}
 // A backend Stop (POST /task/stop) latches at once, so work already queued behind an
 // in-flight action is refused when it reaches the front; the close then waits its turn.
 latchStop(){this.stoppedByOwner??=this.session?.sessionId||'task_stop';}
 endForStop(taskId=null){this.latchStop();this.closeGeneration++;const work=this.queue.catch(()=>{}).then(()=>this.withOrigin(taskId,()=>this.closeNow()));this.queue=work;return work;}
 // An owner Stop not yet seen by an action: check before acting on the phone screen or closing.
 async undetectedOwnerStop(){if(!this.session)return null;const state=await this.call('get_state');return ownerStopped(state,this.session)?state:null;}
 // Reports which task the controller says was stopped, so a late report cannot stop a newer task.
 ownerStop(state,taskId=state?.stoppedTaskId||null){
  const sessionId=this.stoppedByOwner??this.session?.sessionId??state?.stoppedSessionId??null;
  if(!this.stoppedByOwner){
   this.stoppedByOwner=sessionId||'unknown';this.session=null;this.observations.clear();
   this.record({stage:'background_owner_stop',sessionId,stopReason:state?.stopReason??'inferred'});
   try{this.onOwnerStop({sessionId,stopReason:state?.stopReason||null,taskId});}catch{/* Reporting cannot reopen the workspace. */}
  }
  return {status:'stopped_by_owner',actionExecuted:false,backgroundExecution:true,sessionId,reason:'The owner stopped the background workspace. Rose does not reopen it for this task.'};
 }
// origin: the task generation that issued this work (absent for non-task callers). It is sent
 // as taskId on every controller start/command/stop so a schema 3 controller refuses stale
 // work instead of driving whichever task now holds the workspace.
 async withOrigin(taskId,work){const previous=this.origin;this.origin=taskId||null;try{return await work();}finally{this.origin=previous;}}
 execute(command,{taskId=null}={}){
  if(!this.enabled())return this.send(command);
  // Serialize selection and UI operations, including calls from separate APIs.
  // An action admitted before close() was called is refused when it reaches the front.
  const generation=this.closeGeneration;
  const work=this.queue.catch(()=>{}).then(()=>generation===this.closeGeneration?this.withOrigin(taskId,()=>this.run(command)):this.superseded(command));this.queue=work;return work;
 }
 state(){return {enabled:this.enabled(),mode:this.enabled()?'background':'foreground',session:this.session?{...this.session}:null};}
 call(operation,params={}){
  const tagged=this.origin&&['start','command','stop'].includes(operation)?{...params,taskId:this.origin}:params;
  return this.send({action:'app_command',packageName:PACKAGE,operation,params:tagged});
 }
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
  if(current.status==='error'&&/not ready: stopped/i.test(current.error||'')){const state=await this.call('get_state');if(ownerStopped(state,this.session))return {ownerStop:state};}
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
  if(ownerStopped(state,this.session))return this.ownerStop(state);
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
   // Only the originating task names the workspace; the taskId option is a legacy fallback.
   const taskId=this.origin??this.taskId();
   state=await this.call('start',{packageName,...(taskId?{taskId}:{})});
   // Schema 3: the owner already stopped this task; nothing was opened.
   // Schema 3 names the refused task in rejectedTaskId; the workspace under "current" is untouched.
   if(state.status==='user_stopped'){
    const rejected=state.rejectedTaskId||state.stoppedTaskId||taskId;
    if(rejected&&rejected!==this.taskId()){this.record({stage:'background_stale_task_refused',rejectedTaskId:rejected});return {status:'not_executed',actionExecuted:false,backgroundExecution:true,rejectedTaskId:rejected,reason:'The controller refused a start for an earlier, stopped task. The current task is unchanged.'};}
    return this.ownerStop(state,rejected);
   }
   const deadline=this.now()+12000;
   while(['starting','launching','scheduled'].includes(state.status)&&this.now()<deadline){await this.sleep(150);state=await this.call('get_state');}
  }
  if(state.status!=='ready'||!state.sessionId||state.targetPackage!==packageName){
   this.session=null;
   if(ownerUnlockRequired(state))return lockedResult(state,{packageName});
   // Android's ComputerControl allowlist refused this app: this OS build does not let Rose's controller use it.
   if(state.status==='target_not_allowed'){this.record({stage:'background_target_not_allowed',packageName});return {status:'background_target_not_allowed',actionExecuted:false,packageName,backgroundExecution:true,anyAppControl:state.anyAppControl===true,reason:'This phone\'s OS does not let Rose use this app in its background workspace. Nothing was opened.',details:state};}
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
  // After an owner Stop no workspace or phone-screen action runs until the next task.
  if(this.stoppedByOwner&&!directActions.has(command.action))return this.ownerStop();
  if(command.display==='phone'&&this.session){const state=await this.undetectedOwnerStop();if(state)return this.ownerStop(state);}
  if(command.display==='phone'){
   const {display,...request}=command;
   if(!uiActions.has(request.action)&&request.action!=='open_app')throw Error('Display selection applies to phone UI commands');
   const result=await this.send(request);
   this.record({stage:'phone_display_action',action:request.action,status:result.status});
   return {...result,scope:'phone_display',backgroundExecution:false};
  }
  if(command.action==='open_app')return this.start(command.packageName);
  if(command.action==='app_command'){
   if(!isBackgroundSafeAppCommand(command))return backgroundAppCommandRejection(command);
   if(this.session){const state=await this.call('get_state');if(ownerStopped(state,this.session))return this.ownerStop(state);}
   return this.send(command);
  }
  if(directActions.has(command.action)){
   const result=await this.send(command);
   return command.action==='control_status'?{...result,backgroundControl:this.state()}:result;
  }
  if(!uiActions.has(command.action))return {status:'not_executed',actionExecuted:false,reason:'This operation is not verified for background control. Use background phone UI controls.',backgroundExecution:true};
  if(command.action==='home'){const state=await this.undetectedOwnerStop();if(state)return this.ownerStop(state);await this.closeNow();return {status:'action_accepted',actionExecuted:true,backgroundExecution:true,workspaceClosed:true};}
  if(!this.session){
   const state=await this.call('get_state');
   if(state.status==='ready'&&state.sessionId&&state.targetPackage)this.session={sessionId:state.sessionId,targetPackage:state.targetPackage};
  }
  if(!this.session)return {status:'no_background_app',actionExecuted:false,backgroundExecution:true,reason:'Use open_app to select an app in the background workspace first.'};
  let request={...command,sessionId:this.session.sessionId};
  if(nodeActions.has(command.action)||labelActions.has(command.action)){
   const refreshed=await this.refreshTarget(command);
   if(refreshed.locked)return lockedResult(refreshed.locked,{sessionId:this.session.sessionId});
   if(refreshed.ownerStop)return this.ownerStop(refreshed.ownerStop);
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
  // Schema 3: the workspace belongs to another task; nothing ran.
  if(result.status==='stale_task')return {status:'not_executed',actionExecuted:false,backgroundExecution:true,rejectedTaskId:result.rejectedTaskId??null,reason:'The background workspace belongs to another task; this action did not run.'};
  // The controller reports a blocked session as not ready; its state says whether the owner must act.
  if(result.status==='error'&&/not ready: blocked/.test(result.error||'')){const state=await this.call('get_state');if(ownerUnlockRequired(state))result=state;}
  if(result.status==='error'&&/not ready: stopped/i.test(result.error||'')){const state=await this.call('get_state');if(ownerStopped(state,this.session))return this.ownerStop(state);}
  if(ownerUnlockRequired(result)){this.record({stage:'background_locked',action:command.action,packageName:this.session.targetPackage,lockedControl:result.lockedControl??result.reason});return lockedResult(result,{sessionId:this.session.sessionId});}
  this.remember(result);
  this.record({stage:'background_action',action:command.action,packageName:this.session.targetPackage,sessionId:this.session.sessionId,status:result.status});
  return {...result,backgroundExecution:true,sessionId:this.session.sessionId};
 }
 // Returns the controller's stop state; "cleanup_failed" means the workspace may still be open.
 async closeNow(){
  // Clearing the session would otherwise forget an owner Stop nobody has acted on yet (schema 2).
  try{const state=await this.undetectedOwnerStop();if(state)this.ownerStop(state);}catch{/* Closing must still be attempted. */}
  try{return await this.call('stop');}finally{this.session=null;this.observations.clear();}
 }
 superseded(command){
  if(this.stoppedByOwner&&!directActions.has(command.action))return this.ownerStop();
  this.record({stage:'background_action_superseded',action:command.action});
  return {status:'not_executed',actionExecuted:false,backgroundExecution:true,reason:'The background workspace was closed before this action ran.'};
 }
 // taskId: the task whose workspace is being closed. Without it, whatever workspace exists is closed.
 close(taskId=null){this.closeGeneration++;const work=this.queue.catch(()=>{}).then(()=>this.withOrigin(taskId,()=>this.closeNow()));this.queue=work;return work;}
}
