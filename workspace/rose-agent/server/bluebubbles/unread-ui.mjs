import crypto from 'node:crypto';

const PKG='com.bluebubbles.messaging';
const text=n=>String(n.description||n.text||'').trim();
const timeLine=/^(?:\d{1,2}:\d{2}(?:\s*[AP]M)?|Yesterday|Today|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?)$/i;
const fingerprint=s=>crypto.createHash('sha256').update(s).digest('hex').slice(0,24);

export function unreadRows(screen){
 const rows=(screen.nodes||[]).flatMap(n=>{
  if(!n.clickable||n.visible===false||!n.bounds)return [];
  const label=text(n),lines=label.split('\n'),at=lines.findIndex(s=>timeLine.test(s.trim()));
  if(at<1||lines.length<=at+1)return [];
  return [{entryId:fingerprint(label),label,title:lines[at-1].trim(),displayTime:lines[at].trim(),preview:lines.slice(at+1).join('\n'),node:n}];
 });
 if(new Set(rows.map(r=>r.entryId)).size!==rows.length)throw Error('Messages contains indistinguishable rows; no conversation was guessed');
 return rows;
}

/** Uses the official app's own unread filter; never infers unread from the Mac. */
export function createUnreadUI(phone,{sleep=ms=>new Promise(r=>setTimeout(r,ms))}={}){
 let resumedConversation=false;
 async function screen(){const s=await phone({action:'inspect_ui'});if(!s||s.status!=='ok'||s.packageName!==PKG||s.truncated)throw Error('Messages is not available in the foreground');return s;}
 async function wait(predicate){for(let i=0;i<30;i++){const s=await screen();if(predicate(s))return s;await sleep(100);}throw Error('Messages did not reach the expected screen');}
 const has=(s,label)=>(s.nodes||[]).some(n=>text(n)===label);
 async function tap(s,label){
  for(let attempt=0;attempt<2;attempt++){
   const nodes=(s.nodes||[]).filter(n=>text(n)===label&&n.clickable&&n.enabled!==false);
   if(nodes.length!==1)throw Error('Messages control is missing or ambiguous: '+label);
   const r=await phone({action:'tap_label',snapshotId:s.snapshotId,nodeId:nodes[0].id,label});
   if(r.status==='action_accepted')return;
   // The official menu can change node IDs during its opening animation.
   // Re-observe only after an explicit non-executed stale-target rejection.
   if(attempt===0&&['label_target_mismatch','stale_screen','node_unavailable'].includes(r.status)){s=await screen();continue;}
   throw Error('Messages control was not accepted: '+r.status);
  }
 }
 async function top(){
  let s=await screen();
  for(let i=0;i<30;i++){
   const scroll=s.nodes.find(n=>n.scrollable&&n.visible!==false);if(!scroll)break;
   const before=JSON.stringify(unreadRows(s).map(r=>r.entryId));
   const result=await phone({action:'scroll',snapshotId:s.snapshotId,nodeId:scroll.id,direction:'backward'});
   s=await screen();if(result.status==='action_failed'||JSON.stringify(unreadRows(s).map(r=>r.entryId))===before)break;
   if(i===29)throw Error('Could not reach the start of Messages without truncating the review');
  }
  return s;
 }
 async function filters(unread=true){
  let s=await phone({action:'open_app',packageName:PKG});if(!s||s.status!=='opened'||!s.foregroundVerified)throw Error('Messages opening was not verified');
  s=await screen();
  // The official app can restore its last conversation on launch. Opening a
  // chat may update its read state before we can back out; report that honestly.
  if(s.nodes.some(n=>n.editable)&&!has(s,'Chat Filters'))resumedConversation=true;
  for(let i=0;i<4&&!has(s,'Chat Filters')&&!has(s,'Filter Chats')&&!has(s,'Messages');i++){await phone({action:'back'});s=await screen();}
  if(!has(s,'Chat Filters')){
   if(!has(s,'Filter Chats')){
    s=await top();const header=s.nodes.find(n=>text(n)==='Messages');if(!header?.bounds)throw Error('Messages inbox header is unavailable');
    // The two unlabelled controls in the official iOS header are Search and
    // More. Select More from this freshly observed header, never screen pixels.
    const buttons=s.nodes.filter(n=>n.clickable&&n.enabled!==false&&!text(n)&&n.bounds&&n.bounds[1]>=header.bounds[1]&&n.bounds[3]<=header.bounds[3]);
    if(buttons.length!==2)throw Error('Messages header controls changed; no action was guessed');
    const more=buttons.sort((a,b)=>b.bounds[0]-a.bounds[0])[0];
    const result=await phone({action:'tap',snapshotId:s.snapshotId,nodeId:more.id});if(result.status!=='action_accepted')throw Error('Messages menu did not open');
    s=await wait(s=>has(s,'Filter Chats'));
   }
   await tap(s,'Filter Chats');s=await wait(s=>has(s,'Chat Filters'));
  }
  // Other active filters would silently exclude unread conversations. Clear
  // only the current filter selection, never save a new user default.
  const nonDefault=s.nodes.some(n=>n.selected&&text(n)!=='All'&&!['All Messages','Unread Messages'].includes(text(n)));
  const customGroups=s.nodes.some(n=>n.selected&&/group/i.test(text(n)));
  if(nonDefault||customGroups){
   await tap(s,'Reset Filters');s=await wait(s=>has(s,'Clear All Filters'));await tap(s,'Clear All Filters');s=await wait(s=>has(s,'Chat Filters'));
  }
  const wanted=unread?'Unread Messages':'All Messages';
  if(!s.nodes.some(n=>text(n)===wanted&&n.selected)){await tap(s,wanted);s=await wait(s=>s.nodes.some(n=>text(n)===wanted&&n.selected));}
  await tap(s,'Dismiss');
  await wait(s=>!has(s,'Chat Filters')&&has(s,'Messages')&&
   (unreadRows(s).length>0||has(s,'No conversations match your filters')||has(s,'No conversations yet')));
  return top();
 }
 async function scan(limit=20,unread=true){
  resumedConversation=false;
  let s=await filters(unread);const found=new Map();let moreAvailable=false,finished=false;
  for(let page=0;page<50;page++){
   for(const row of unreadRows(s)){if(!found.has(row.entryId))found.set(row.entryId,row);}
   const scroll=s.nodes.find(n=>n.scrollable&&n.visible!==false);
   if(!scroll){finished=true;break;}
   if(found.size>=limit){moreAvailable=true;break;}
   const before=JSON.stringify(unreadRows(s).map(r=>r.entryId));
   const result=await phone({action:'scroll',snapshotId:s.snapshotId,nodeId:scroll.id,direction:'forward'});
   s=await screen();
   if(result.status==='action_failed'||JSON.stringify(unreadRows(s).map(r=>r.entryId))===before){finished=true;break;}
   if(page===49)moreAvailable=true;
  }
  const rows=[...found.values()];if(rows.length>limit)moreAvailable=true;
  return {rows:rows.slice(0,limit).map(({node,...row})=>row),moreAvailable,total:finished?rows.length:null,source:unread?'Pixel Messages unread filter':'Pixel Messages all messages filter',observedAt:Date.now(),readStateMayHaveChanged:resumedConversation};
 }
 async function findRow(entry,unread){
  let s=await filters(unread);
  for(let page=0;page<50;page++){
   const rows=unreadRows(s),match=rows.filter(r=>r.entryId===entry.entryId);
   if(match.length===1)return {screen:s,row:match[0]};
   const changed=rows.find(r=>r.title===entry.title);if(changed)throw Error('This conversation changed; refresh it before marking read');
   const scroll=s.nodes.find(n=>n.scrollable);if(!scroll)return null;
   const before=JSON.stringify(rows.map(r=>r.entryId));const r=await phone({action:'scroll',snapshotId:s.snapshotId,nodeId:scroll.id,direction:'forward'});s=await screen();
   if(r.status==='action_failed'||JSON.stringify(unreadRows(s).map(r=>r.entryId))===before)return null;
  }
  throw Error('Unread verification exceeded its scan limit');
 }
 async function setRead(entry,read){
  const found=await findRow(entry,read);if(!found){if(read)return {markedRead:true,alreadyRead:true};throw Error('The exact conversation is not visible; no read state was changed');}
  if(!found.row.node.longClickable)throw Error('Messages does not expose a safe conversation menu');
  const press=await phone({action:'long_press',snapshotId:found.screen.snapshotId,nodeId:found.row.node.id});
  if(press.status!=='action_accepted')throw Error('Conversation menu did not open');
  const label=read?'Mark Read':'Mark Unread';const menu=await wait(s=>has(s,label));await tap(menu,label);
  await wait(s=>has(s,'Messages')&&!has(s,label));
  const verification=await findRow(entry,true);
  if(read&&verification||!read&&!verification)throw Error('The requested read state was not verified');
  return {markedRead:read,markedUnread:!read,verification:'Exact conversation checked against the Pixel unread filter'};
 }
 return {scan,setRead};
}
