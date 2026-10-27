// Owner Stop for the single running task. Once a Stop is acknowledged, no
// further phone action runs for that task; the worker turn and any result
// check are interrupted and the background workspace is closed. Repeating a
// Stop is safe. The terminal "interrupted" state is written when the turn ends.
const activeStates=new Set(['running','checking','stopping']);
const reason=error=>String(error?.message||error).slice(0,200);

export class TaskStop {
 constructor({task,stopping,interrupt,abortReview,closeWorkspace,announce=()=>{},cancelQueued=()=>{},onBegin=()=>{},now=Date.now,record=()=>{}}){
  Object.assign(this,{task,stopping,interrupt,abortReview,closeWorkspace,announce,cancelQueued,onBegin,now,record});this.requested=null;this.generation=null;this.turnTask=new Map();
 }
 // An explicit new user request starts generation taskId; only it reopens the Stop gate.
 begin(taskId=null){this.requested=null;this.generation=taskId;try{this.onBegin(taskId);}catch(error){this.record({stage:'task_generation_publish_failed',reason:reason(error)});}}
 // Worker turns are bound to the task they were created for (Codex 'turnCreated'), never
 // inferred from the turn that happens to arrive; the map keeps recent turns only.
 bindTurn(turnId,taskId){if(!turnId||!taskId)return;this.turnTask.set(turnId,taskId);while(this.turnTask.size>64)this.turnTask.delete(this.turnTask.keys().next().value);}
 ownsTurn(turnId){const current=this.current();return !!turnId&&!!current&&this.turnTask.get(turnId)===current;}
 // Fails closed for work that names another or no generation: a phone command's taskId, or a
 // tool call's worker turn once this generation's turn is known. The current generation is
 // the one begin() named, else the displayed task.
 current(){return this.generation??this.task()?.id??null;}
 // required: a task path with no identity fails closed. A turn must be a registered turn of
 // the current generation; an unregistered or missing turn is never trusted.
 authorize({taskId,turnId,required=false}={}){
  const current=this.current();
  const stale=taskId?taskId!==current:turnId!==undefined?!this.ownsTurn(turnId):required;
  if(!stale)return null;
  this.record({stage:'task_generation_rejected',taskId:taskId||null,turnId:turnId||null,currentTaskId:current});
  return {status:'stale_task',actionExecuted:false,reason:'This action belongs to an earlier or unknown task and was not run.',currentTaskId:current};
 }
 active(){return !!this.requested;}
 // Output (progress, results, speech) from the worker: accepted only from a registered turn
 // of the current, unstopped generation. A missing or unregistered turn is refused.
 acceptsOutput(turnId){return !this.requested&&this.ownsTurn(turnId);}
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
  // Captured locally: a fresh begin() while this Stop awaits cleanup must not lose its acknowledgment.
  const requested=this.requested={taskId:current.id,source:String(source).slice(0,40),at:this.now()};
  this.stopping(requested);
  // Bar queued work synchronously, before any acknowledgment (including a duplicate) can return.
  try{this.cancelQueued(requested);}catch(error){this.record({stage:'task_stop_cancel_queued_failed',reason:reason(error)});}
  // Cancel speech already queued or playing for this task before anything else is awaited.
  try{this.announce({taskId:current.id,requestedAt:requested.at});}catch{/* Speech cancellation is best effort. */}
  this.record({stage:'task_stop_requested',taskId:current.id,source:requested.source});
  try{this.abortReview();}catch{/* A finished review has nothing to abort. */}
  const [worker,workspace]=await Promise.allSettled([this.interrupt(),this.closeWorkspace()]);
  const result={status:'stopping',taskId:current.id,requestedAt:requested.at,
   worker:worker.status==='fulfilled'?'interrupt_requested':'interrupt_failed: '+reason(worker.reason),
   workspace:workspace.status==='fulfilled'?'closed':'close_failed: '+reason(workspace.reason)};
  requested.outcome=result;
  this.record({stage:'task_stop_acknowledged',...result});
  return result;
 }
 // Truthful terminal text: a workspace that did not close is reported, not hidden.
 summary(){
  const workspace=this.requested?.outcome?.workspace;
  return workspace&&workspace!=='closed'?'Task stopped, but the background workspace did not confirm it closed ('+workspace+').':'Task stopped';
 }
}
