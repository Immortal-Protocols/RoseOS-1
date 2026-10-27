import {createHash} from 'node:crypto';
import {createAppControls} from './controls.mjs';
const PKG='com.apple.android.music';
const spec=(name,description,properties={},required=Object.keys(properties))=>({name:'apple_music_'+name,description,inputSchema:{type:'object',properties,required,additionalProperties:false}});
export const appleMusicTools=[
 spec('search','Search the official Apple Music app. Returns current visible item IDs, titles, artists and types. Does not play or use an Apple Music API.',{query:{type:'string'},kind:{type:'string',enum:['all','songs','artists','albums','playlists']}},['query']),
 spec('items','Read the visible songs, albums, artists or playlists on the current Apple Music page. Optional scrolling reads another page.',{scroll:{type:'string',enum:['forward','backward']}},[]),
 spec('open','Open an album, artist or playlist from a returned item ID without starting playback. For a song, opens its action menu.',{itemId:{type:'string'}}),
 spec('play','Play a returned item ID, or play the collection currently open when itemId is omitted. Changes Apple Music playback only.',{itemId:{type:'string'}},[]),
 spec('state','Read Apple Music now-playing title, artist, position, playback controls and visible mode settings. Does not expose lyrics text.'),
 spec('playback','Pause, resume, go to next track or restart/previous via the Apple Music player. A missing next track is reported instead of guessed.',{action:{type:'string',enum:['pause','resume','next','previous']}}),
 spec('view','Open a main Apple Music section, the player, queue or lyrics. These are UI views, not playback commands.',{view:{type:'string',enum:['home','new','radio','library','search','player','queue','lyrics']}}),
 spec('mode','Set shuffle, repeat or autoplay in the current Apple Music queue with on-screen state confirmation.',{setting:{type:'string',enum:['shuffle','repeat','autoplay']},value:{type:'string',enum:['off','on','all','one']}}),
 spec('sleep_timer','Set Apple Music sleep timer to a listed duration, end of the current song, or off.',{preset:{type:'string',enum:['off','15_minutes','30_minutes','45_minutes','1_hour','song_end']}}),
 spec('item_action','Perform an explicitly requested action on a returned item or current song: add to library, favorite/unfavorite, play next, or show credits. Only currently exposed, verifiable actions are available.',{itemId:{type:'string'},action:{type:'string',enum:['add_library','favorite','unfavorite','play_next','credits']}},['action'])
];
export function parseProgress(text=''){
 const parts=text.split('Elapsed');if(parts.length!==2)return null;
 function seconds(s){let n=0,found=false;for(const m of s.matchAll(/(\d+)\s*(Hour|Minute|Second)s?/gi)){found=true;n+=Number(m[1])*({hour:3600,minute:60,second:1}[m[2].toLowerCase()]);}return found?n:null;}
 const position=seconds(parts[0]),duration=seconds(parts[1]);return position===null||duration===null?null:{positionSeconds:position,durationSeconds:duration};
}
export function readMusicState(s){
 const node=id=>s.nodes.find(n=>n.viewId===PKG+':id/'+id),p=node('play_pause')||node('mini_player_play_btn');
 const progress=parseProgress(node('progress')?.description);
 return {status:'ok',title:node('mini_player_title')?.text||null,artist:node('mini_player_subtitle')?.text||null,playing:p?.description==='Pause'?true:p?.description==='Play'?false:null,...progress,
  view:node('player_lyrics')?.selected?'lyrics':node('player_queue')?.selected?'queue':node('play_pause')?'player':'browse',
  lyricsVisible:node('player_lyrics')?.selected??null,shuffle:node('button_shuffle')?.description||null,repeat:node('button_repeat')?.description||null,autoplay:node('button_autoplay')?.description||null,
  collection:node('collection_header_title')?.text||null,verification:'Current Apple Music UI controls and displayed state; not acoustic verification'};
}
export function parseMusicItems(s){
 const rows=[];
 for(const n of s.nodes){if(n.viewId!==PKG+':id/title'||!n.text||n.clickTarget===undefined)continue;
  const siblings=s.nodes.filter(x=>x.parentId===n.parentId),sub=siblings.find(x=>x.viewId===PKG+':id/subtitle')?.text||'';
  const kind=/^Music Video\b/.test(sub)?'music_video':/^Album\b/.test(sub)?'album':/^Playlist\b/.test(sub)?'playlist':sub==='Artist'?'artist':/^Song\b/.test(sub)||siblings.some(x=>x.description==='Song Menu Options'||x.viewId.endsWith(':id/list_item_track_index'))?'song':'item';
  rows.push({title:n.text,subtitle:sub,kind});
 }
 return rows;
}
export function readQueueItems(s){
 const byId=new Map(s.nodes.map(n=>[n.id,n]));
 return s.nodes.filter(n=>n.viewId===PKG+':id/title'&&n.text).filter(n=>{
  const seen=new Set();let p=byId.get(n.parentId);
  while(p&&!seen.has(p.id)){if(p.viewId===PKG+':id/queue_main_content')return true;seen.add(p.id);p=byId.get(p.parentId);}return false;
 }).map(n=>({title:n.text,artist:s.nodes.find(x=>x.parentId===n.parentId&&x.viewId===PKG+':id/subtitle')?.text||null}));
}
export function createAppleMusicAdapter(phone){
 const ui=createAppControls({phone,packageName:PKG,timeoutMs:22000});let saved=new Map(),context='',savedView='';
 const raw=(s,id)=>s.nodes.find(n=>n.viewId===PKG+':id/'+id);
 async function back(){const r=await phone({action:'back'});if(r.status!=='action_accepted')throw Error('Could not leave current panel');await ui.sleep(250);}
 async function browse(){let s=await ui.screen();if(raw(s,'lyrics_thumbnail_container')||raw(s,'queue_thumbnail_container')||raw(s,'play_pause')){await back();s=await ui.screen();}
  if(s.nodes.some(n=>/^Showing options for /.test(n.description))){await back();s=await ui.screen();}return s;}
 async function player(){let s=await ui.screen();if(s.nodes.some(n=>/^Showing options for /.test(n.description))){await back();s=await ui.screen();}if(raw(s,'play_pause'))return s;
  if(raw(s,'lyrics_thumbnail_container')||raw(s,'queue_thumbnail_container')){await back();s=await ui.wait(x=>!!raw(x,'mini_player_content'));}
  const n=raw(s,'mini_player_content');if(!n)throw Error('No current Apple Music player');await ui.tap(s,n);return ui.wait(x=>!!raw(x,'play_pause'));
 }
 async function queue(){let s=await ui.screen();if(raw(s,'button_repeat'))return s;s=await player();s=await ui.wait(x=>!!raw(x,'player_queue'));const n=raw(s,'player_queue');if(!n.selected){await ui.tap(s,n);s=await ui.wait(x=>!!raw(x,'button_repeat'));}return s;}
 function collect(s){const data=parseMusicItems(s);saved=new Map();savedView=JSON.stringify(data);const seen=new Map();const items=data.map((r,index)=>{const key=r.title+'\n'+r.subtitle,occurrence=seen.get(key)||0;seen.set(key,occurrence+1);const id=createHash('sha256').update(context+'\n'+key+'\n'+occurrence).digest('hex').slice(0,20);saved.set(id,{...r,occurrence});return {id,position:index+1,...r};});return {status:'verified',context,items,scope:'Currently visible Apple Music items'};}
 async function selected(id){const item=saved.get(id);if(!item)throw Error('Item expired; search or read items again');const s=await browse();
  if(JSON.stringify(parseMusicItems(s))!==savedView)throw Error('Visible results changed; read items again before selecting');
  const matches=s.nodes.filter(n=>n.viewId===PKG+':id/title'&&n.text===item.title&&n.clickTarget!==undefined&&((s.nodes.find(x=>x.parentId===n.parentId&&x.viewId===PKG+':id/subtitle')?.text||'')===item.subtitle));
  if(!matches[item.occurrence])throw Error('Selected item is no longer visible');return {s,n:matches[item.occurrence],item};
 }
 async function itemMenu(id){if(!id){const current=await ui.screen();if(current.nodes.some(n=>/^Showing options for /.test(n.description)))return current;}if(id){const {s,n,item}=await selected(id);const m=s.nodes.find(x=>x.parentId===n.parentId&&/Menu Options$/.test(x.description)&&x.clickTarget!==undefined);if(!m)throw Error('No exposed menu for selected item');await ui.tap(s,m);return ui.wait(x=>x.nodes.some(n=>n.description==='Showing options for '+item.title+'.'));}
  let s=await player();const n=raw(s,'list_left_icon')||ui.label(s,'More options');await ui.tap(s,n);return ui.wait(x=>x.nodes.some(n=>/^Showing options for /.test(n.description)));}
 async function state(){const s=await player();return {...readMusicState(s),queue:readQueueItems(s)};}
 async function search({query,kind='all'}){if(!query.trim()||query.length>200)throw Error('Query must contain 1–200 characters');let s=await browse();
  let f=raw(s,'search_src_text');for(let i=0;!f&&i<4;i++){const n=ui.label(s,'Search')||ui.label(s,'Navigate up')||ui.label(s,'Navigate up.');if(!n)throw Error('Search navigation unavailable');await ui.tap(s,n);await ui.sleep(250);s=await ui.screen();f=raw(s,'search_src_text');}if(!f)throw Error('Search field unavailable');
  if(!f.imeEnter){await ui.tap(s,raw(s,'dummyView')||f);s=await ui.wait(x=>raw(x,'search_src_text')?.imeEnter);f=raw(s,'search_src_text');}
  let r=await phone({action:'set_text',snapshotId:s.snapshotId,nodeId:f.id,text:query});if(r.status!=='action_accepted')throw Error('Search text was not accepted');
  s=await ui.screen();f=raw(s,'search_src_text');if(f?.text!==query||!f.imeEnter)throw Error('Search field changed');r=await phone({action:'ime_enter',snapshotId:s.snapshotId,nodeId:f.id});if(r.status!=='action_accepted')throw Error('Search submission was not accepted');
  s=await ui.wait(x=>x.nodes.some(n=>n.text==='SONGS'||n.description==='SONGS')&&!!parseMusicItems(x).length,7000);context=query+' / '+kind;
  if(kind!=='all'){const text=kind.toUpperCase(),n=ui.label(s,text);if(n){await ui.tap(s,n);s=await ui.wait(x=>x.nodes.some(n=>(n.text===text||n.description===text)&&n.selected)&&!!parseMusicItems(x).length&&(kind!=='songs'||parseMusicItems(x).every(n=>n.kind==='song')));}}
  return collect(s);
 }
 async function open({itemId}){const {s,n,item}=await selected(itemId);if(item.kind==='song'){await itemMenu(itemId);return {status:'verified',item,availableActions:(await ui.screen()).nodes.filter(n=>n.viewId===PKG+':id/list_title').map(n=>n.text)};}
  await ui.tap(s,n);const before=JSON.stringify(parseMusicItems(s));const next=await ui.wait(x=>JSON.stringify(parseMusicItems(x))!==before&&x.nodes.some(v=>v.text===item.title));context=item.title;return {status:'verified',opened:item,...collect(next)};
 }
 async function play({itemId}){
  let expected=null;if(itemId){const {s,n,item}=await selected(itemId);expected=item.kind==='song'?item.title:null;await ui.tap(s,n,{changesContent:item.kind==='song'});if(item.kind!=='song')await ui.wait(x=>!!raw(x,'button_play'));}
  let s=await ui.screen();if(!itemId||expected===null){const n=raw(s,'button_play');if(n)await ui.tap(s,n,{changesContent:true});else return playback({action:'resume'});}
  const keepQueue=x=>x.nodes.some(n=>/^After this plays, do you want to keep playing or clear the \d+ songs you previously queued\?$/.test(n.text))&&ui.label(x,'KEEP')&&ui.label(x,'CLEAR')&&ui.label(x,'CANCEL');
  const playing=x=>{const st=readMusicState(x);return st.playing===true&&(!expected||st.title===expected);};
  s=await ui.wait(x=>playing(x)||keepQueue(x),6500);
  // This labeled choice preserves the existing queue; never clear it as a
  // side effect of selecting a song.
  if(keepQueue(s)){await ui.tap(s,ui.label(s,'KEEP'),{changesContent:true});s=await ui.wait(playing,6500);}
  return {...readMusicState(s),status:'verified'};
 }
 async function playback({action}){let s=await player();const before=readMusicState(s);if(action==='pause'||action==='resume'){
  const desired=action==='resume';if(before.playing===desired)return {...before,status:'verified',alreadyInRequestedState:true};await ui.tap(s,raw(s,'play_pause'),{changesContent:true});s=await ui.wait(x=>readMusicState(x).playing===desired);return {...readMusicState(s),status:'verified'};}
  const n=raw(s,action==='next'?'next_fast_forward':'previous_rewind');await ui.tap(s,n,{changesContent:true});s=await ui.wait(x=>{const st=readMusicState(x);return action==='previous'?(st.title!==before.title||(st.positionSeconds??Infinity)<3):st.title!==before.title;},6500);return {...readMusicState(s),status:'verified'};
 }
 async function view({view}){if(['player','queue','lyrics'].includes(view)){let s=view==='queue'?await queue():await player();if(view==='lyrics'){const n=raw(s,'player_lyrics');if(!n)throw Error('Lyrics control unavailable');if(!n.selected){await ui.tap(s,n);s=await ui.wait(x=>raw(x,'player_lyrics')?.selected===true||!!raw(x,'lyrics_thumbnail_container'));}}return {...readMusicState(s),status:'verified',openedView:view,...(view==='queue'?{queue:readQueueItems(s)}:{})};}
  const labels={home:'Home',new:'New',radio:'Radio',library:'Library',search:'Search'};let s=await browse(),n=ui.label(s,labels[view]);if(n)await ui.tap(s,n);else if(!s.nodes.some(n=>n.description===labels[view]&&n.selected))throw Error('Section control unavailable');await ui.sleep(250);s=await ui.screen();context=view;return {status:'verified',openedView:view,...collect(s)};
 }
 async function mode({setting,value}){let s=await queue();const key={shuffle:'button_shuffle',repeat:'button_repeat',autoplay:'button_autoplay'}[setting];
  if(setting!=='repeat'&&!['on','off'].includes(value)||setting==='repeat'&&!['off','all','one'].includes(value))throw Error('Invalid mode value');
  const match=n=>n&&n.description.toLowerCase()===(setting+' '+(value==='one'?'once':value)).toLowerCase();
  for(let i=0;i<3;i++){const n=raw(s,key);if(match(n))return {...readMusicState(s),status:'verified'};if(!n)throw Error('Mode control unavailable');const old=n.description;await ui.tap(s,n,{changesContent:true});s=await ui.wait(x=>raw(x,key)?.description!==old);}
  if(!match(raw(s,key)))throw Error('Requested mode was not confirmed');return {...readMusicState(s),status:'verified'};
 }
 const sleepLabels={off:'Off','15_minutes':'In 15 Minutes','30_minutes':'In 30 Minutes','45_minutes':'In 45 Minutes','1_hour':'In 1 Hour',song_end:'When Current Song Ends'};
 async function sleepTimer({preset}){let s=await ui.screen();const timerEntry=s=>s.nodes.find(n=>n.clickTarget!==undefined&&(n.text==='Sleep Timer'||/^Time Left:/.test(n.text)||/^\d+ Songs? Left$/.test(n.text)));
  if(!s.nodes.some(n=>n.text==='Sleep Timer'&&n.clickTarget===undefined)){s=await itemMenu();await ui.tap(s,timerEntry(s));s=await ui.wait(x=>x.nodes.some(n=>n.text==='Sleep Timer'&&n.clickTarget===undefined));}
  const target=ui.label(s,sleepLabels[preset]);if(!target)throw Error('Sleep timer option unavailable');await ui.tap(s,target,{changesContent:true});await ui.sleep(300);s=await itemMenu();
  const value=timerEntry(s)?.text||'';let confirmed=preset==='off'?value==='Sleep Timer':false;
  const duration={"15_minutes":900,"30_minutes":1800,"45_minutes":2700,"1_hour":3600}[preset],m=value.match(/Time Left:\s*(\d+):(\d+)/);
  if(duration&&m){const remaining=Number(m[1])*60+Number(m[2]);confirmed=remaining<=duration&&remaining>=duration-30;}
  if(preset==='song_end')confirmed=/song/i.test(value);
  if(!confirmed)throw Error('Sleep timer setting not confirmed');await back();return {status:'verified',sleepTimer:preset,display:value};
 }
 async function itemAction({itemId,action}){let s=await itemMenu(itemId);const labels={add_library:'Add to Library',favorite:'Favorite',unfavorite:'Undo Favorite',play_next:'Play Next',credits:'View Credits'};let n=ui.label(s,labels[action]);if(!n){if(action==='favorite'&&ui.label(s,'Undo Favorite'))return {status:'verified',favorite:true,alreadyInRequestedState:true};if(action==='unfavorite'&&ui.label(s,'Favorite'))return {status:'verified',favorite:false,alreadyInRequestedState:true};if(action==='add_library'&&(ui.label(s,'Delete from Library')||ui.label(s,'Remove from Library')))return {status:'verified',inLibrary:true,alreadyInRequestedState:true};throw Error('Requested action is not exposed for this item');}
  const title=s.nodes.find(n=>/^Showing options for /.test(n.description))?.description;await ui.tap(s,n,{changesContent:action!=='credits'});await ui.sleep(300);s=await ui.screen();
  if(action==='credits'){s=await ui.wait(x=>x.nodes.some(n=>/^(PERFORMING ARTISTS|COMPOSITION & LYRICS|PRODUCTION & ENGINEERING)$/.test(n.text)));return {status:'verified',opened:'credits',details:s.nodes.filter(n=>n.text).map(n=>n.text).slice(0,35)};}
  // Re-open the same menu to confirm state changes, or queue to verify insertion.
  if(action==='play_next'){const expected=title?.replace(/^Showing options for /,'').replace(/\.$/,'');s=await queue();s=await ui.wait(x=>readQueueItems(x)[0]?.title===expected);return {status:'verified',queuedNext:expected,queue:readQueueItems(s)};}
  s=await itemMenu(itemId);const confirmed=action==='favorite'?!!ui.label(s,'Undo Favorite'):action==='unfavorite'?!!ui.label(s,'Favorite'):!!(ui.label(s,'Delete from Library')||ui.label(s,'Remove from Library'));if(!confirmed)throw Error('Library action dispatched but state was not confirmed');await back();return {status:'verified',action,item:title};
 }
 const handlers={search,items:async({scroll})=>{let s=await browse();if(scroll){const n=s.nodes.find(n=>n.scrollable&&n.bounds[3]-n.bounds[1]>400);if(!n)throw Error('No scrollable item list');const r=await phone({action:'scroll',snapshotId:s.snapshotId,nodeId:n.id,direction:scroll});if(r.status!=='action_accepted')throw Error('Scroll not accepted');await ui.sleep(250);s=await ui.screen();}return collect(s);},open,play,state,playback,view,mode,sleep_timer:sleepTimer,item_action:itemAction};
 return {packageName:PKG,tools:appleMusicTools,execute:(name,args)=>ui.run(()=>handlers[name.replace(/^apple_music_/,'')](args))};
}
