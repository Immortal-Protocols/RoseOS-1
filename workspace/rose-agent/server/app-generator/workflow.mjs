// Declarative result checks learned from the app's exposed UI. These execute
// locally, like the state readers in the hand-built app adapters.
const selectorSchema={anyOf:['viewId','text','description','class','textPrefix'].map(key=>({type:'object',properties:{[key]:{type:'string'}},required:[key],additionalProperties:false})).concat(['editable','focused','imeEnter','selected','checked'].map(key=>({type:'object',properties:{[key]:{type:'boolean'}},required:[key],additionalProperties:false})))};
export const resultProofSchema={type:'object',properties:{
 all:{type:'array',minItems:1,items:selectorSchema},any:{type:'array',items:selectorSchema},none:{type:'array',items:selectorSchema},
 query:{anyOf:[{type:'null'},{type:'object',properties:{selector:selectorSchema,related:{type:'string',enum:['self','clickTarget']}},required:['selector','related'],additionalProperties:false}]}
},required:['all','any','none','query'],additionalProperties:false};
export function matchingNodes(screen, selector) {
 return screen.nodes.filter(n=>n.visible!==false&&Object.entries(selector).every(([k,v])=>
  k==='textPrefix'?typeof n.text==='string'&&n.text.startsWith(v):n[k]===v));
}
export function resultCheck(screen, proof, args={}) {
 if(!proof?.all?.length)return false;
 if(!proof.all.every(s=>matchingNodes(screen,s).length))return false;
 if(proof.any?.length&&!proof.any.some(s=>matchingNodes(screen,s).length))return false;
 if(proof.none?.some(s=>matchingNodes(screen,s).length))return false;
 if(proof.query){
  const matches=matchingNodes(screen,proof.query.selector);
  if(!matches.some(n=>{
   const related=proof.query.related==='clickTarget'?screen.nodes.filter(x=>(x.clickTarget??x.id)===(n.clickTarget??n.id)):[n];
   return related.some(x=>!x.editable&&(x.text===args.query||x.description===args.query));
  }))return false;
 }
 return true;
}
export function validateProof(screen,proof,args={}) {
 const allowed=new Set(['viewId','text','description','editable','focused','imeEnter','selected','checked','class','textPrefix']);
 for(const s of [...(proof?.all||[]),...(proof?.any||[]),...(proof?.none||[]),...(proof?.query?[proof.query.selector]:[])]){
  if(!Object.keys(s).length||Object.keys(s).some(k=>!allowed.has(k)))throw Error('Result proof contains an unsupported selector');
  if(args.query&&Object.values(s).some(v=>typeof v==='string'&&v.includes(args.query)))throw Error('Result proof must not hard-code the example query');
 }
 if(!resultCheck(screen,proof,args))throw Error('Result proof does not match the observed completed operation');
 if(args.query&&!proof.query)throw Error('Search proof must verify the requested query outside the editor');
 return proof;
}
export function workflowResult(screen,spec,args={}) {
 return {status:'verified',operation:spec.name,...(args.query?{query:args.query}:{}),
  verification:'Requested operation confirmed from fresh app controls and result state',
  // Preserve real result text for the caller; never synthesize content from a
  // training query or claim a result merely because the tap was accepted.
  labels:[...new Set(screen.nodes.filter(n=>n.visible!==false&&!n.password).flatMap(n=>[n.text,n.description]).filter(Boolean))]};
}
