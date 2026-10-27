const commit=/\b(send(?: message)?|submit|confirm|call|dial|place (?:your )?order|buy now|add to (?:cart|basket)|checkout|subscribe|start (?:free )?trial|delete|remove|pay now|purchase|allow|grant|uninstall|install)\b/i;
export function textChoices(request){
 const values=[],seen=new Set();const add=s=>{s=s.trim();if(s&&!seen.has(s)&&s.length<=2000){seen.add(s);values.push({value:'t'+values.length,description:s});}};
 for(const m of request.matchAll(/["“]([^"”]+)["”]/g))add(m[1]);
 const tokens=[...request.matchAll(/\S+/g)];
 // Prefer spans near the end, where the explicit query/body usually occurs.
 for(let len=Math.min(tokens.length,20);len>=1;len--)for(let start=tokens.length-len;start>=0;start--){if(values.length>=250)break;add(request.slice(tokens[start].index,tokens[start+len-1].index+tokens[start+len-1][0].length).replace(/^["“]|["”]$/g,'').replace(/[.!?]$/,''));}
 return values;
}
export function actionChoices(s,{withImage=false}={}){
 const out=[{value:'finish',description:'The whole requested screen action is visibly complete.'},{value:'luna',description:'Return current progress to Luna for planning, interpretation, visual recovery, or an irreversible final action.'},{value:'wait',description:'Wait briefly for a visibly loading screen, then inspect.'}];
 if(s?.status!=='ok'||(s.truncated&&!withImage)||s.nodes?.some(n=>n.password))return [out[1],out[2]];
 const seen=new Set();
 const add=(description,command)=>{const key=JSON.stringify(command);if(seen.has(key)||out.length>=250)return;seen.add(key);out.push({value:'a'+out.length,description,command:{...command,snapshotId:s.snapshotId}});};
 for(const n of s.nodes||[]){
  if(n.enabled===false||n.visible===false||n.password)continue;
  const label=n.description||n.text||'';const target=n.clickTarget??n.id;
  if(label&&label.length<=200&&(n.clickable||n.clickTarget!==undefined)&&!commit.test(label)){
   const targets=new Set((s.nodes||[]).filter(x=>(x.text===label||x.description===label)&&(x.clickable||x.clickTarget!==undefined)&&x.enabled!==false&&x.visible!==false).map(x=>x.clickTarget??x.id));
   add('Tap '+JSON.stringify(label),{action:'tap_label',...(targets.size>1?{nodeId:target}:{}),label});
  }
  // Unlabelled clicks can be final controls; leave them to visual recovery.
  if(n.editable)add('Enter exact supplied text in '+JSON.stringify(label||n.class)+' (current field value '+JSON.stringify(n.text||'')+')',{action:'select_text',nodeId:n.id});
  if(n.imeEnter&&n.focused&&s.nodes.some(x=>/\bsearch\b/i.test((x.description||'')+' '+(x.text||''))))add('Submit this focused search field using its keyboard action',{action:'ime_enter',nodeId:n.id});
  if(n.scrollable)add('Scroll this visible container forward',{action:'scroll',nodeId:n.id,direction:'forward'});
 }
 add('Go back one screen',{action:'back'});add('Go to the phone home screen',{action:'home'});
 return out;
}
export function isCommitLabel(label){return commit.test(label);}

// Image mode offers physical alternatives at observed, visible label bounds.
// Coordinates come only from the current tree; the model judges them against
// the current screenshot. Final/permission controls remain excluded.
export function visualChoices(s,base){
 const root=s?.nodes?.find(n=>n.id===0);if(!root?.bounds)return [];
 const out=[],seen=new Set();
 for(const n of s.nodes||[]){
  const label=n.description||n.text;if(!label||label.length>200||isCommitLabel(label)||n.password||n.enabled===false||n.visible===false||!n.bounds)continue;
  const [l,t,r,b]=n.bounds,x=Math.round((l+r)/2),y=Math.round((t+b)/2);
  if(r<=l||b<=t||x<root.bounds[0]||x>=root.bounds[2]||y<root.bounds[1]||y>=root.bounds[3])continue;
  const key=x+':'+y;if(seen.has(key)||base.length+out.length>=300)continue;seen.add(key);
  out.push({value:'visual'+n.id,description:'Physical tap on visible '+JSON.stringify(label)+' at display ('+x+','+y+'). Use only if the screenshot confirms this location and target; use after an ineffective semantic tap.',command:{action:'touch_point',snapshotId:s.snapshotId,nodeId:root.id,x,y}});
 }
 return out;
}
