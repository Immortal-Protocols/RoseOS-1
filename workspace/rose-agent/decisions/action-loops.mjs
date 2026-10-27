// Ignore capture IDs and animated pixels when comparing navigation progress.
export function observationKey(screen){
 return JSON.stringify({packageName:screen.packageName,windowId:screen.windowId,nodes:(screen.nodes||[]).map(n=>({text:n.text,description:n.description,className:n.className,bounds:n.bounds,checked:n.checked,selected:n.selected,enabled:n.enabled,editable:n.editable,focused:n.focused}))});
}
export class ActionLoops {
 constructor(){this.attempts=[];}
 record(screen,action){
  const {snapshotId,screenshotId,...stableAction}=action;
  this.attempts.push({screen:observationKey(screen),action:JSON.stringify(stableAction)});
 }
 detect(screen){
  const current=observationKey(screen),a=this.attempts;
  for(let period=1;period<=4;period++){
   if(a.length<period*2)continue;
   const pattern=a.slice(-period),key=JSON.stringify(pattern);
   if(pattern[0].screen!==current)continue;
   let repeats=1;
   for(let end=a.length-period;end>=period;end-=period){
    if(JSON.stringify(a.slice(end-period,end))!==key)break;
    repeats++;
   }
   if(repeats>=2)return {key,repeats,actions:pattern.map(p=>JSON.parse(p.action))};
  }
  return null;
 }
}
