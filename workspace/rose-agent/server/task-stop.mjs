// Owner Stop for the single running task. Once a Stop is acknowledged, no
// further phone action runs for that task; the worker turn and any result
// check are interrupted and the background workspace is closed. Repeating a
// Stop is safe. The terminal "interrupted" state is written when the turn ends.
const activeStates=new Set(['running','checking','stopping']);
const reason=error=>String(error?.message||error).slice(0,200);

export class TaskStop {
 constructor({task,stopping,interrupt,abortReview,closeWorkspace,now=Date.now,record=()=>{}}){
  Object.assign(this,{task,stopping,interrupt,abortReview,closeWorkspace,now,record});this.requested=null;
 }
 begin(){this.requested=null;}
 active(){return !!this.requested;}
 // Blocks phone actions only while the stopped task is still winding down.
 guard(command){
  const current=this.task();
  if(!this.requested||current?.id!==this.requested.taskId||!activeStates.has(current.state))return null;
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
  this.record({stage:'task_stop_requested',taskId:current.id,source:this.requested.source});
  try{this.abortReview();}catch{/* A finished review has nothing to abort. */}
  const [worker,workspace]=await Promise.allSettled([this.interrupt(),this.closeWorkspace()]);
  const result={status:'stopping',taskId:current.id,requestedAt:this.requested.at,
   worker:worker.status==='fulfilled'?'interrupt_requested':'interrupt_failed: '+reason(worker.reason),
   workspace:workspace.status==='fulfilled'?'closed':'close_failed: '+reason(workspace.reason)};
  this.record({stage:'task_stop_acknowledged',...result});
  return result;
 }
}
