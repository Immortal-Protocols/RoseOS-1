// Offline candidate selection only. Does not change prompts or execute actions.
const actions=new Set(['tap','tap_label','touch_label','touch_point','set_text','ime_enter','scroll']);
const successful=new Set(['ok','action_accepted']);
export function sequenceCandidates(events){
 const commands=events.filter(e=>e.kind==='completed');const candidates=[];let run=[];
 function finish(){
  const actionCount=run.filter(e=>actions.has(e.action)).length;
  if(actionCount>=2)candidates.push({commandIds:run.map(e=>e.id),actions:run.map(e=>e.action),taskStartedAt:run[0].taskStartedAt,actionCount,
   observedCommandMs:run.reduce((n,e)=>n+e.totalMs,0),observedBetweenCommandMs:run.slice(1).reduce((n,e)=>n+e.betweenCommandsMs,0),
   candidateRoundTrips:run.length-1,
   requiresScreenReview:true});
  run=[];
 }
 for(const e of commands){
  const eligible=e.taskStartedAt!=null&&successful.has(e.resultStatus)&&(actions.has(e.action)||e.action==='inspect_ui');
  if(!eligible){finish();continue;}
  const previous=run.at(-1);
  const joins=previous&&e.previousId===previous.id&&e.taskStartedAt===previous.taskStartedAt&&Number.isFinite(e.betweenCommandsMs)&&e.betweenCommandsMs<10000;
  if(previous&&!joins)finish();run.push(e);
  if(run.length===8)finish();
 }
 finish();return candidates;
}
