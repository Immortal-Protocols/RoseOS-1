// Internal helpers for complete suite actions. No coordinates or keyword routing.
export const visibleText=n=>(n.text||n.description||'').trim();
export const hasId=(n,pkg,id)=>n.viewId===`${pkg}:id/${id}`;
export const rawId=(s,pkg,id)=>s.nodes.filter(n=>hasId(n,pkg,id)&&n.enabled&&n.visible!==false);
export function unique(nodes,what){if(nodes.length!==1)throw Error(nodes.length?`${what} is ambiguous`:`${what} is unavailable`);return nodes[0];}
export function requireText(value,what,max=200){if(typeof value!=='string'||!value.trim()||value.length>max||/[\u0000-\u001f\u007f]/.test(value))throw Error(`${what} must contain 1–${max} printable characters`);return value.trim();}
export const tool=(name,description,properties={},required=Object.keys(properties))=>({name,description,inputSchema:{type:'object',properties,required,additionalProperties:false}});
export function descendants(s,parent){const ids=new Set([parent.id]);let changed=true;while(changed){changed=false;for(const n of s.nodes)if(ids.has(n.parentId)&&!ids.has(n.id)){ids.add(n.id);changed=true;}}return s.nodes.filter(n=>ids.has(n.id));}
export async function writeField(ui,field,text,{submit=false,verify=(actual,expected)=>actual===expected}={}){
 let s=await ui.screen(),n=field(s);if(!n.editable)throw Error('Requested input is not editable');
 const result=await ui.phone({action:'set_text',snapshotId:s.snapshotId,nodeId:n.id,text});
 if(result.status!=='action_accepted')throw Error('Text entry rejected: '+result.status);
 s=await ui.screen();n=field(s);if(!verify(n.text,text))throw Error('Requested text was not verified in the input');
 if(submit){if(!n.imeEnter)throw Error('Input has no supported submission action');const r=await ui.phone({action:'ime_enter',snapshotId:s.snapshotId,nodeId:n.id});if(r.status!=='action_accepted')throw Error('Submission rejected: '+r.status);}
 return s;
}
export function assertKnown(name,handlers){if(!Object.hasOwn(handlers,name))throw Error('Unknown suite tool');return handlers[name];}
// Content identities (a saved file result or current-page share control) must
// not be re-resolved by label alone after their snapshot becomes stale.
export async function tapOnce(ui,s,n){
 const label=n?.description||n?.text;
 if(!label||n.clickTarget===undefined)throw Error('Selected control has no verified label and target');
 const r=await ui.phone({action:'tap_label',snapshotId:s.snapshotId,nodeId:n.clickTarget,label});
 if(r.status!=='action_accepted')throw Error('Selected control changed or was rejected: '+r.status+'; inspect again');
}
