// Owner Stop for the single running task. Once a Stop is acknowledged, no
// further phone action runs for that task; the worker turn and any result
// check are interrupted and the background workspace is closed. Repeating a
// Stop is safe. The terminal "interrupted" state is written when the turn ends.
const activeStates=new Set(['running','checking','stopping']);
const reason=error=>String(error?.message||error).slice(0,200);

export class TaskStop {
 constructor({task,stopping,interrupt,abortReview,closeWorkspace,announce=()=>{},cancelQueued=()=>{},onBegin=()=>{},now=Date.now,record=()=>{}}){
  Object.assign(this,{task,stopping,interrupt,abortReview,closeWorkspace,announce,cancelQueued,onBegin,now,record});this.requested=null;this.generation=null;this.turns=new Set();
 }
 // An explicit new user request starts generation taskId; only it reopens the Stop gate.
 begin(taskId=null){this.requested=null;this.generation=taskId;this.turns=new Set();try{this.onBegin(taskId);}catch(error){this.record({stage:'task_generation_publish_failed',reason:reason(error)});}}
 addTurn(turnId){if(this.generation&&turnId)this.turns.add(turnId);}
 // Fails closed for work that names another generation (a phone command's task ID, or a
 // tool call's worker turn). Calls carrying no identity keep the legacy path and the Stop gate.
 authorize({taskId,turnId}={}){
  if(!this.generation)return null;
  const stale=taskId?taskId!==this.generation:turnId?!this.turns.has(turnId):false;
  if(!stale)return null;
  this.record({stage:'task_generation_rejected',taskId:taskId||null,turnId:turnId||null,currentTaskId:this.generation});
  return {status:'stale_task',actionExecuted:false,reason:'This action belongs to an earlier or unknown task and was not run.',currentTaskId:this.generation};
 }
 active(){return !!this.requested;}
 // Blocks phone actions from an acknowledged Stop until an explicit new user task
 // calls begin(), including after the stopped task reaches its terminal state.
 guard(command){
  if(!this.requested)return null;
  this.record({stage:'task_stop_blocked_action',action:command?.action,operation:command?.operation,taskId:this.requested.taskId});
  return {status:'task_stopped',actionExecuted:false,reason:'The owner stopped this task. No further phone actions run for it.',taskId:this.requested.taskId};
 }
 async request({taskId,source='unknown'}={}){
  const current=this.task();
  if(!current||!activeStates.has(current.state))return {status:'not_active'};
  if(taskId&&taskId!==current.id)return {status:'stale_task',taskId:current.id};
  if(this.requested)return {status:'stopping',taskId:current.id,requestedAt:this.requested.at,duplicate:true};
  this.requested={taskId:current.id,source:String(source).slice(0,40),at:this.now()};
  this.stopping(this.requested);
  // Bar queued work synchronously, before any acknowledgment (including a duplicate) can return.
  try{this.cancelQueued(this.requested);}catch(error){this.record({stage:'task_stop_cancel_queued_failed',reason:reason(error)});}
  // Cancel speech already queued or playing for this task before anything else is awaited.
  try{this.announce({taskId:current.id,requestedAt:this.requested.at});}catch{/* Speech cancellation is best effort. */}
  this.record({stage:'task_stop_requested',taskId:current.id,source:this.requested.source});
  try{this.abortReview();}catch{/* A finished review has nothing to abort. */}
  const [worker,workspace]=await Promise.allSettled([this.interrupt(),this.closeWorkspace()]);
  const result={status:'stopping',taskId:current.id,requestedAt:this.requested.at,
   worker:worker.status==='fulfilled'?'interrupt_requested':'interrupt_failed: '+reason(worker.reason),
   workspace:workspace.status==='fulfilled'?'closed':'close_failed: '+reason(workspace.reason)};
  this.requested.outcome=result;
  this.record({stage:'task_stop_acknowledged',...result});
  return result;
 }
 // Truthful terminal text: a workspace that did not close is reported, not hidden.
 summary(){
  const workspace=this.requested?.outcome?.workspace;
  return workspace&&workspace!=='closed'?'Task stopped, but the background workspace did not confirm it closed ('+workspace+').':'Task stopped';
 }
}
