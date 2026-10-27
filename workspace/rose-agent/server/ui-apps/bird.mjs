import {createAppControls} from './controls.mjs';
import {randomUUID} from 'node:crypto';
const PKG='co.bird.android';
const spec=(name,description,properties={},required=Object.keys(properties))=>({name:'bird_'+name,description,inputSchema:{type:'object',properties,required,additionalProperties:false}});
export const birdTools=[
 {name:'bird_state',description:'Read the official Bird app current screen and selected scooter details. Range is estimated riding range, not distance to the scooter. The map markers do not expose coordinates or distance, so this cannot identify the nearest scooter. The user may select a scooter in Bird to expose its details. This does not reserve, scan, unlock, purchase or start a ride.',inputSchema:{type:'object',properties:{},required:[],additionalProperties:false}},
 spec('find','Find the visually closest scooter marker to the user-location marker on the visible Bird map. Selects that candidate and returns its readable details and a temporary candidate ID. This is approximate on-map proximity, NOT walking distance or a guarantee about scooters outside the visible map. No reservation is made.'),
 spec('inspect','Select a candidate returned by bird_find and read its scooter identity, estimated riding range and ride price. Returns distance as unavailable when Bird does not expose it. Does not reserve or start a ride.',{candidateId:{type:'string'}}),
 spec('reserve','Prepare the reservation for a candidate, returning its actual reservation price. Set confirm=true ONLY when the human has explicitly requested a paid reservation or approved the quoted charge. Otherwise stops before payment. Verifies the same scooter and active reservation; never starts a ride.',{candidateId:{type:'string'},confirm:{type:'boolean'}},['candidateId']),
 spec('cancel_reservation','Cancel the active Bird reservation when the user asks, then verify cancellation. Does not cancel or end an active ride.')
];
export function readBirdState(s){
 const texts=[...new Set(s.nodes.map(n=>n.text).filter(Boolean))];
 const name=texts.find(t=>/^(?:Bird|Spin) Scooter\b/.test(t))||null;
 const rangeText=name?texts.find(t=>/^\d+(?:[.,]\d+)?\s*(miles?|mi|kilometers?|km)$/.test(t))||null:null;
 const priceText=name?texts.find(t=>/\bto start\b|\bper minute\b|\/min\b/.test(t))||null:null;
 const markers=s.nodes.filter(n=>n.description==='Bird'&&n.clickTarget!==undefined);
 const has=t=>texts.includes(t),reservation=texts.find(t=>/^[A-Z0-9]+ reserved$/.test(t))||null;
 const phase=has('Ride canceled')?'ride_cancelled':has('Reservation canceled')?'reservation_cancelled':has('End')&&has('Pause')?'ride_active':reservation?'reserved':has('Start your reservation')?'reservation_preview':has('Scan QR Code')?'scanner':name?'scooter_selected':'map_or_other';
 return {status:'verified',phase,reservation,selectedScooter:name?{displayName:name,estimatedRideRangeText:rangeText,priceText}:null,visibleScooterMarkerCount:markers.length,
  markerLimit:'Markers have no readable vehicle identity, numeric distance or coordinates. Do not infer walking distance from the marker count. bird_find uses approximate visual proximity only.',
  visibleLabeledActions:[...new Set(s.nodes.filter(n=>n.enabled&&n.clickTarget!==undefined).map(n=>n.description||n.text).filter(v=>v&&!['Bird','Google Map','Map Marker'].includes(v)))],
  verification:'Current Bird screen text and labeled controls; no reservation or ride action performed'};
}
export function nearbyMarkers(s){
 const me=s.nodes.find(n=>n.description==='My Location');if(!me)return [];
 const byId=new Map(s.nodes.map(n=>[n.id,n]));
 const inMap=n=>{for(let p=n;p;p=byId.get(p.parentId))if(p.description==='Google Map'||p.viewId===PKG+':id/mapView')return true;return false;};
 const overlays=s.nodes.filter(n=>n.enabled&&n.clickTarget===n.id&&!inMap(n));
 const hit=n=>[(n.bounds[0]+n.bounds[2])/2,(n.bounds[1]+n.bounds[3])/2];
 const point=n=>[(n.bounds[0]+n.bounds[2])/2,n.bounds[3]],origin=point(me);
 const rows=s.nodes.filter(n=>n.description==='Bird'&&n.enabled&&n.clickTarget!==undefined&&n.bounds[2]-n.bounds[0]>50&&n.bounds[3]-n.bounds[1]>50).filter(n=>{const [x,y]=hit(n);return !overlays.some(o=>x>=o.bounds[0]&&x<=o.bounds[2]&&y>=o.bounds[1]&&y<=o.bounds[3]);}).map(n=>{
  const p=point(n),dx=p[0]-origin[0],dy=p[1]-origin[1];return {node:n,dx,dy,score:Math.hypot(dx,dy)};
 }).sort((a,b)=>a.score-b.score);
 const distinct=[];for(const row of rows)if(!distinct.some(x=>Math.hypot(row.dx-x.dx,row.dy-x.dy)<45))distinct.push(row);
 return distinct;
}
export function createBirdAdapter(phone){
 const ui=createAppControls({phone,packageName:PKG,timeoutMs:30000});let candidates=new Map();
 const texts=s=>s.nodes.map(n=>n.text).filter(Boolean);
 const reserved=s=>texts(s).find(t=>/^[A-Z0-9]+ reserved$/.test(t));
 const prefix=t=>t.replace(/^(?:Bird|Spin) Scooter\s+|^Reserve\s+|\s+reserved$/g,'').replace(/[•.]+$/,'');
 const sameVehicle=(a,b)=>prefix(a)===prefix(b)||(/[•]/.test(a+b)&&(prefix(a).startsWith(prefix(b))||prefix(b).startsWith(prefix(a))));
 async function semantic(s,label){
  // Compose exposes the sheet's labels before its entrance animation is done.
  let last='',since=Date.now();for(let i=0;i<14;i++){s=await ui.screen();const n=ui.label(s,label);if(!n)throw Error('Bird control disappeared: '+label);const key=JSON.stringify(n.bounds);if(key!==last){last=key;since=Date.now();}else if(Date.now()-since>=350)break;await ui.sleep(100);}
  const r=await phone({action:'tap_label',snapshotId:s.snapshotId,label});if(r.status!=='action_accepted')throw Error('Bird did not accept '+label+': '+r.status);
  await ui.sleep(200);
 }
 async function stableMap(){let last=null,stableSince=0,s;for(let i=0;i<24;i++){s=await ui.screen();const signature=JSON.stringify(s.nodes.filter(n=>['My Location','Bird'].includes(n.description)).map(n=>n.bounds.map(v=>Math.round(v/3))));if(signature!==last){last=signature;stableSince=Date.now();}else if(Date.now()-stableSince>=650)return s;await ui.sleep(150);}throw Error('Map is still moving; no scooter was selected');}
 async function clearSelection(){let s=await ui.screen();if(reserved(s)||readBirdState(s).phase==='ride_active')throw Error('A reservation or ride is already active; finish it before finding another scooter');
  for(let i=0;i<3&&texts(s).some(t=>['Receipt','How was your ride?','Scan QR Code','Enter Code','Get Directions'].includes(t));i++){
   const r=await phone({action:'back'});if(r.status!=='action_accepted')throw Error('Could not return to the map');await ui.sleep(200);s=await ui.screen();
  }
  if(texts(s).some(t=>/^Reserve\s/.test(t))){const r=await phone({action:'back'});if(r.status!=='action_accepted')throw Error('Could not leave reservation preview');s=await ui.wait(x=>!texts(x).includes('Start your reservation'));}
  if(readBirdState(s).selectedScooter){const r=await phone({action:'back'});if(r.status!=='action_accepted')throw Error('Could not dismiss selected scooter');s=await ui.wait(x=>!readBirdState(x).selectedScooter);await ui.sleep(350);}
  return s;
 }
 async function center(s){
  const width=s.nodes.find(n=>n.parentId===-1)?.bounds[2];
  const byId=new Map(s.nodes.map(n=>[n.id,n]));
  const inMap=n=>{for(let p=n;p;p=byId.get(p.parentId))if(p.description==='Google Map'||p.viewId===PKG+':id/mapView')return true;return false;};
  const options=s.nodes.filter(n=>n.clickTarget===n.id&&n.enabled&&!n.text&&!n.description&&!inMap(n)&&n.bounds[0]>width*.8&&n.bounds[2]-n.bounds[0]<width*.2&&s.nodes.some(x=>x.parentId===n.parentId&&x.clickTarget===x.id&&x.bounds[0]<width*.2));
  if(options.length!==1)throw Error('The observed Bird recenter-button layout changed');
  const r=await phone({action:'tap',snapshotId:s.snapshotId,nodeId:options[0].id});if(r.status!=='action_accepted')throw Error('Recenter control was not accepted');
  await ui.wait(x=>x.nodes.some(n=>n.description==='My Location'));await ui.sleep(400);return stableMap();
 }
 async function selectMarker(s,match,candidateId){
  const c=candidates.get(candidateId),map=s.nodes.find(n=>n.description==='Google Map');if(!map)throw Error('Map surface unavailable');
  const b=match.node.bounds,r=await phone({action:'touch_point',snapshotId:s.snapshotId,nodeId:map.id,x:Math.round((b[0]+b[2])/2),y:Math.round(b[1]+(b[3]-b[1])*.25)});
  if(r.status!=='gesture_dispatched')throw Error('Map tap was not dispatched: '+r.status);
  await ui.sleep(500);try{s=await ui.wait(x=>!!readBirdState(x).selectedScooter,2500);}catch{throw Error('Scooter icon tap did not open its details; no reservation made');}const detail=readBirdState(s).selectedScooter;
  if(c.identity&&!sameVehicle(detail.displayName,c.identity))throw Error('Selected scooter identity changed; no reservation made');c.identity=detail.displayName;c.detail=detail;
  return {status:'verified',candidateId,...detail,distanceMeters:null,distanceStatus:'Estimated riding range is not distance to this scooter'};
 }
 async function find(){let s=await clearSelection();s=nearbyMarkers(s).length?await stableMap():await center(s);const rows=nearbyMarkers(s);if(!rows.length)throw Error('Bird must show your location marker and scooter markers; no nearest result can be inferred');
  candidates=new Map();const r=rows[0],id=randomUUID();candidates.set(id,{dx:r.dx,dy:r.dy,created:Date.now()});
  const detail=await selectMarker(s,r,id);
  return {status:'verified',candidates:[{...detail,mapProximityRank:1}],proximityBasis:'Approximate straight-line order on the current map around the My Location marker. Not walking distance; not a global-nearest guarantee.',distanceStatus:'Bird does not expose meters or walking time on this screen'};
 }
 async function inspect({candidateId}){const c=candidates.get(candidateId);if(!c||Date.now()-c.created>5*60*1000)throw Error('Candidate expired; find scooters again');
  const current=await ui.screen(),selected=readBirdState(current).selectedScooter;
  if(c.identity&&selected&&sameVehicle(selected.displayName,c.identity))return {status:'verified',candidateId,...selected,distanceMeters:null,distanceStatus:'Estimated riding range is not distance to this scooter'};
  await clearSelection();const s=await stableMap(),match=nearbyMarkers(s).find(r=>Math.hypot(r.dx-c.dx,r.dy-c.dy)<20);if(!match)throw Error('Map moved or scooter changed; find scooters again');
  return selectMarker(s,match,candidateId);
 }
 async function reserve({candidateId,confirm=false}){
  const candidate=candidates.get(candidateId);if(!candidate||Date.now()-candidate.created>5*60*1000)throw Error('Candidate expired; find scooters again');
  let s=await ui.screen(),detail=candidate.detail;const existingTitle=texts(s).find(t=>/^Reserve /.test(t));
  if(!(candidate.identity&&existingTitle&&sameVehicle(existingTitle,candidate.identity)&&texts(s).includes('Start your reservation'))){
   detail=await inspect({candidateId});s=await ui.screen();await semantic(s,'Reserve');
   s=await ui.wait(x=>texts(x).includes('Start your reservation'),5000);
  }
  const title=texts(s).find(t=>/^Reserve /.test(t)),quote=texts(s).find(t=>/per minute/.test(t));
  if(!title||!sameVehicle(title,detail.displayName)||!quote)throw Error('Reservation identity or price not confirmed; nothing paid');
  const c=candidates.get(candidateId),changedQuote=c.quote&&c.quote!==quote;c.quote=quote;
  if(changedQuote)return {status:'verified',phase:'price_changed_confirmation_required',candidateId,scooter:title,reservationPriceText:quote,paid:false};
  if(!confirm)return {status:'verified',phase:'awaiting_user_confirmation',candidateId,scooter:title,reservationPriceText:quote,paid:false};
  // Do not retry a paid dispatch when the outcome is unknown.
  ui.markMutation();await semantic(s,'Start your reservation');s=await ui.wait(x=>!!reserved(x),8000);
  if(!sameVehicle(reserved(s),detail.displayName))throw Error('Unexpected reserved scooter; inspect Bird before continuing');
  return {status:'verified',phase:'reserved',scooter:reserved(s),reservationPriceText:quote,rideStarted:false};
 }
 async function cancel(){let s=await ui.screen();if(!reserved(s))return {status:'verified',phase:'no_active_reservation',cancelled:false};
  ui.markMutation();await semantic(s,'Cancel');s=await ui.wait(x=>texts(x).includes('Reservation canceled'),6000);
  const chargeMessage=texts(s).find(t=>/charged|charge/.test(t))||null;await semantic(s,'GOT IT');s=await ui.wait(x=>!reserved(x)&&!texts(x).includes('Reservation canceled'));
  return {status:'verified',phase:'reservation_cancelled',chargeMessage};
 }
 async function start({candidateId,confirm=false}){
  let s=await ui.screen();if(readBirdState(s).phase==='ride_active')return {...readBirdState(s),alreadyActive:true};
  let detail,control='Start';
  if(reserved(s)){
   const c=candidateId?candidates.get(candidateId):null;if(candidateId&&(!c?.identity||!sameVehicle(reserved(s),c.identity)))throw Error('Reserved scooter does not match requested candidate');
   detail={displayName:reserved(s),priceText:texts(s).find(t=>/to start|\/min/.test(t))||null};control='Unlock';
  }else detail=candidateId?await inspect({candidateId}):readBirdState(s).selectedScooter;
  if(!detail)throw Error('Select or reserve a scooter first');
  if(!confirm)return {status:'verified',phase:'awaiting_paid_ride_confirmation',candidateId,...detail,started:false};
  s=await ui.screen();ui.markMutation();await semantic(s,control);
  s=await ui.wait(x=>readBirdState(x).phase==='ride_active'||texts(x).includes('BEFORE YOU RIDE')||texts(x).includes('Scan QR Code')||texts(x).some(t=>/unable|too far|move closer|failed/i.test(t)),15000);
  if(readBirdState(s).phase==='ride_active')return {...readBirdState(s),started:true};
  return {status:'uncertain',phase:'start_requires_attention',started:false,visibleText:texts(s),reason:'Start was requested, but an active ride is not yet confirmed. Inspect before retrying.'};
 }
 async function end(){let s=await ui.screen(),state=readBirdState(s);if(state.phase!=='ride_active')return {status:'verified',phase:state.phase,ended:false,reason:'No active ride with an End control was found; no action taken'};
  ui.markMutation();await semantic(s,'End');s=await ui.wait(x=>texts(x).some(t=>/Ride canceled|Ride ended|Ride complete|photo|parking/i.test(t)),15000);
  const result=readBirdState(s),chargeMessage=texts(s).find(t=>/charged|charge/.test(t))||null;
  if(result.phase==='ride_cancelled'){const a=await ui.screen();if(texts(a).includes('OK'))await phone({action:'tap_label',snapshotId:a.snapshotId,label:'OK'});return {status:'verified',phase:'ride_cancelled',ended:true,chargeMessage};}
  if(texts(s).some(t=>/Ride ended|Ride complete/i.test(t)))return {status:'verified',phase:'ride_ended',ended:true,visibleText:texts(s)};
  return {status:'uncertain',phase:'parking_photo_required',ended:false,visibleText:texts(s),reason:'Follow the current parking instructions with a genuine photo of the actual scooter; billing stop is not yet verified'};
 }
 return {packageName:PKG,voiceContext:'Bird is the installed scooter-rental app. Its backend tools can find a nearby scooter on the map, read its details, reserve it when requested, and cancel a reservation.',reuseForeground:true,tools:birdTools,execute:(name,args={})=>ui.run(()=>{
  const handlers={bird_state:async()=>readBirdState(await ui.screen()),bird_find:find,bird_inspect:inspect,bird_reserve:reserve,bird_cancel_reservation:cancel,bird_start:start,bird_end:end};
  if(!handlers[name])throw Error('Unknown Bird tool');return handlers[name](args);
 })};
}
