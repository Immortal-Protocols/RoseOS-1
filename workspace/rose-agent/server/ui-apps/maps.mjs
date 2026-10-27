import {createAppControls} from './controls.mjs';

const PKG='com.google.android.apps.maps';
const raw=(s,id)=>s.nodes.find(n=>n.viewId===PKG+':id/'+id);
const text=n=>(n.description||n.text||'').trim();
const field=s=>raw(s,'search_omnibox_edit_text');
export const mapsTools=[
 {name:'maps_search',description:'Search Google Maps for a city, address, intersection or named place and show it on the map. Opens Maps, enters and submits the query, then verifies the selected place details. Returns the actual place name and current controls. Multiple-result lists with unreadable place names require the general visual tools; do not invent results. Does not start navigation, call, book, or save.',inputSchema:{type:'object',properties:{query:{type:'string'}},required:['query'],additionalProperties:false}},
 {name:'maps_state',description:'Read the current Google Maps search and selected place from labeled UI controls. Distinguishes an unfinished search from a displayed place. Does not start navigation or perform a search.',inputSchema:{type:'object',properties:{},additionalProperties:false}}
];

export function readMapsState(s){
 const input=field(s),box=raw(s,'search_omnibox_text_box');
 if(input?.editable)return {phase:'editing',query:input.text,complete:false};
 const query=text(box||{});
 const labels=[...new Set(s.nodes.filter(n=>n.visible!==false).map(text).filter(Boolean))];
 const destinations=[...new Set(labels.filter(t=>/^Directions to .+/.test(t)).map(t=>t.slice('Directions to '.length).replace(/,\s*\d.*$/,'')))];
 const close=raw(s,'terra_navigation_header_close_button');
 if(destinations.length===1&&close){
  const title=destinations[0];
  const heading=s.nodes.find(n=>n.text===title&&n.viewId!==PKG+':id/search_omnibox_text_box');
  if(heading){
   const siblings=s.nodes.filter(n=>n.parentId===heading.parentId&&n.id!==heading.id).map(text).filter(Boolean);
   return {phase:'place',query,complete:true,place:{name:title,details:siblings},controls:labels.filter(t=>/^(Directions to |Start$|Call |Save |Share |Website$|Check availability)/.test(t)),verification:'Selected Google Maps place heading and matching Directions control are visible'};
  }
 }
 if(labels.some(t=>/^(No results found|No results for|Google Maps can.t find)/i.test(t)))return {phase:'no_results',query,complete:true};
 if(close&&labels.includes('Directions'))return {phase:'results',query,complete:false,reason:'Search results are visible, but their place names are not exposed by the current accessibility controls'};
 return {phase:query&&query!=='Search here'?'loading_or_unknown':'map',query:query==='Search here'?'':query,complete:false};
}

export function createMapsAdapter(phone){
 const ui=createAppControls({phone,packageName:PKG,timeoutMs:18000});
 async function search({query}){
  if(typeof query!=='string'||!query.trim()||query.length>200)throw Error('Maps needs a query of 1–200 characters');
  query=query.trim();let s=await ui.screen();
  if(!field(s)){
   const box=raw(s,'search_omnibox_text_box');
   if(!box||!text(box))throw Error('Maps search is not exposed on this screen; use visual controls');
   const label=text(box);
   const targets=new Set(s.nodes.filter(n=>n.enabled&&n.clickTarget!==undefined&&(n.description===label||n.text===label)).map(n=>n.clickTarget));
   // Results can repeat the query in a second heading. Disambiguate that case
   // with the freshly observed search-box target; otherwise match by label.
   let opened=targets.size===1?await phone({action:'tap_label',snapshotId:s.snapshotId,label}):await ui.tap(s,box,{physical:false});
   if(opened.status==='stale_screen'){
    s=await ui.screen();const fresh=raw(s,'search_omnibox_text_box');
    if(fresh&&text(fresh)===label)opened=await ui.tap(s,fresh,{physical:false});
   }
   if(opened.status!=='action_accepted')throw Error('Maps search control was not opened: '+opened.status);
   s=await ui.wait(x=>field(x)?.editable&&field(x)?.focused&&field(x)?.imeEnter,2500);
  }
  let f=field(s);
  if(!f?.imeEnter)throw Error('Maps search submission is unavailable');
  let written=await phone({action:'set_text',snapshotId:s.snapshotId,nodeId:f.id,text:query});
  // Maps can expose the field while its opening transition is still running.
  // Rejected set-text calls get bounded retries from a freshly resolved field.
  for(let attempt=0;attempt<2&&['action_failed','stale_screen','node_unavailable'].includes(written.status);attempt++){
   await ui.sleep(150*(attempt+1));s=await ui.screen();f=field(s);
   if(!f?.editable||!f.focused||!f.imeEnter)break;
   written=await phone({action:'set_text',snapshotId:s.snapshotId,nodeId:f.id,text:query});
  }
  if(written.status!=='action_accepted')throw Error('Maps did not accept the query: '+written.status);
  s=await ui.screen();f=field(s);
  if(!f?.focused||!f.imeEnter||f.text!==query)throw Error('The query was not verified in the focused Maps search field');
  const submitted=await phone({action:'ime_enter',snapshotId:s.snapshotId,nodeId:f.id});
  if(submitted.status!=='action_accepted')throw Error('Maps search was not submitted');
  // Enter is sent once. Poll observations only; never submit a second time
  // merely because results are still loading.
  s=await ui.wait(x=>{const state=readMapsState(x);return state.query===query&&['place','no_results','results'].includes(state.phase);},8000);
  const state=readMapsState(s);
  if(!state.complete)return {status:'needs_assistance',...state,searchSubmitted:true};
  return {status:'verified',...state,searchSubmitted:true};
 }
 return {packageName:PKG,reuseForeground:true,voiceContext:'Google Maps has a verified search-and-show tool for cities, addresses, intersections and named places, plus a tool to read the selected place. Other Maps actions use the visual fallback.',tools:mapsTools,execute:(name,args={})=>ui.run(()=>{
  if(name==='maps_search')return search(args);
  if(name==='maps_state')return ui.screen().then(s=>({status:'verified',...readMapsState(s)}));
  throw Error('Unknown Maps tool');
 })};
}
