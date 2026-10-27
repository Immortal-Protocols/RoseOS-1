// Geometry comes from the current accessibility snapshot, never its labels.
// Every enabled visible clickable/editable control is eligible, including Submit.
export function numberedTargets(screen,{width,height,displayWidth,displayHeight}){
 if(screen?.status!=='ok'||![width,height,displayWidth,displayHeight].every(n=>Number.isFinite(n)&&n>0))return [];
 const out=[],seen=new Set();
 for(const n of screen.nodes||[]){
  if((!n.clickable&&!n.editable)||n.enabled===false||n.visible===false||n.password||!Array.isArray(n.bounds)||n.bounds.length!==4)continue;
  const [l,t,r,b]=n.bounds;if(![l,t,r,b].every(Number.isFinite))continue;
  const left=Math.max(0,l),top=Math.max(0,t),right=Math.min(displayWidth,r),bottom=Math.min(displayHeight,b);
  if(right<=left||bottom<=top)continue;
  const key=[left,top,right,bottom].join(',');if(seen.has(key))continue;seen.add(key);
  out.push({number:out.length+1,nodeId:n.id,editable:!!n.editable,focused:!!n.focused,
   displayPoint:[Math.min(displayWidth-1,Math.floor((left+right)/2)),Math.min(displayHeight-1,Math.floor((top+bottom)/2))],
   box:[Math.round(left*width/displayWidth),Math.round(top*height/displayHeight),Math.round(right*width/displayWidth),Math.round(bottom*height/displayHeight)]});
 }
 return out;
}

export function targetChoices(targets){return targets.map(t=>({value:'target_'+t.number,description:'Tap numbered target '+t.number+' shown on the screenshot.'}));}
