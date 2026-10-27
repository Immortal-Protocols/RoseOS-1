import crypto from 'node:crypto';
import {validateClockParams} from '../clock-worker/commands.mjs';
export class PhoneCommands {
 constructor(now=Date.now,mediaEnabled=()=>false,clockEnabled=()=>false){this.now=now;this.mediaEnabled=mediaEnabled;this.clockEnabled=clockEnabled;this.commands=new Map();}
 create(data){
  if(data.action==='debug_bridge_connect'){
   if(!/^[a-f0-9-]{36}$/.test(data.channelId||'')||! /^(127\.0\.0\.1|(?:\d{1,3}\.){3}\d{1,3})$/.test(data.targetHost||'')||!Number.isInteger(data.targetPort)||data.targetPort<1024||data.targetPort>65535||!Number.isFinite(data.expiresAt)||data.expiresAt<=this.now()||data.expiresAt>this.now()+20000)throw Error('Invalid debugging channel');
   const c={id:crypto.randomUUID(),action:data.action,channelId:data.channelId,targetHost:data.targetHost,targetPort:data.targetPort,expiresAt:data.expiresAt,created:this.now(),expires:data.expiresAt,status:'pending'};this.commands.set(c.id,c);return c;
  }
  if(!['bluebubbles_open_chat','app_command','migration_private_backup_open','migration_backup_open','migration_backup_status','screenshot','screen_gesture','type_text','app_context','environment','diagnostics','list_apps','open_app','control_status','setup_ui_control','inspect_ui','tap','long_press','tap_label','touch_label','touch_point','set_text','ime_enter','scroll','swipe','drag','home','back','apple_music_run','clock_timer_start','clock_control','media_control','voice_ducking','visualization_set','visualization_clear','visualization_status'].includes(data.action))throw Error('Unsupported phone action');
  if(data.action==='bluebubbles_open_chat'&&(typeof data.chatGuid!=='string'||data.chatGuid.length>300||! /^(?:iMessage|SMS|any);[+-];[^\x00-\x1f\x7f]+$/.test(data.chatGuid)))throw Error('A verified Messages conversation ID is required');
  if(data.action==='screenshot'&&data.visualOnly!==undefined&&typeof data.visualOnly!=='boolean')throw Error('visualOnly must be boolean');
  if(data.action==='screen_gesture'){
   if(typeof data.screenshotId!=='string'||!/^[a-f0-9-]{36}$/.test(data.screenshotId))throw Error('A fresh screenshotId is required');
   if(data.nodeId!==undefined||data.snapshotId!==undefined||data.label!==undefined)throw Error('Visual gestures do not accept accessibility targets');
   if(!Array.isArray(data.points)||![2,4].includes(data.points.length)||!data.points.every(n=>Number.isInteger(n)&&n>=0&&n<=20000))throw Error('Expected display-pixel point or endpoints');
   if(data.durationMs!==undefined&&(!Number.isInteger(data.durationMs)||data.durationMs<100||data.durationMs>2000))throw Error('Invalid gesture duration');
  }
  if(data.action==='type_text'){
   if(typeof data.screenshotId!=='string'||!/^[a-f0-9-]{36}$/.test(data.screenshotId))throw Error('A fresh screenshotId is required');
   if(typeof data.text!=='string'||data.text.length>4000)throw Error('Text must be at most 4000 characters');
   for(const key of ['commit','replaceExisting'])if(data[key]!==undefined&&typeof data[key]!=='boolean')throw Error(key+' must be boolean');
  }
  if(data.action==='app_command'){
   if(typeof data.packageName!=='string'||!/^([a-zA-Z][\w]*\.)+[\w]+$/.test(data.packageName))throw Error('An installed packageName is required');
   if(typeof data.operation!=='string'||!/^([a-z][a-z0-9_]{0,63})$/.test(data.operation))throw Error('An app operation name is required');
   if(data.params!==undefined&&(!data.params||typeof data.params!=='object'||Array.isArray(data.params)||JSON.stringify(data.params).length>16384))throw Error('App params must be a JSON object up to 16 KiB');
  }
  if(data.action==='visualization_set')validateVisual(data.visual);
  if(data.action==='clock_control'){if(!this.clockEnabled())throw Error('Clock controls disabled');validateClockParams(data.params);}
  if(data.action==='media_control'&&(!this.mediaEnabled()||data.app!=='apple_music'||!['get_state','pause','resume'].includes(data.operation)))throw Error('Media shortcut disabled or unsupported');
  if(data.action==='voice_ducking'&&((data.duckMode!==undefined&&!['light','system'].includes(data.duckMode))||(data.reductionDb!==undefined&&(!Number.isFinite(data.reductionDb)||data.reductionDb<1||data.reductionDb>12))))throw Error('Invalid voice ducking amount');
  if(data.action==='voice_ducking'&&((data.duckEnabled!==undefined&&typeof data.duckEnabled!=='boolean')||(data.testSeconds!==undefined&&(!Number.isInteger(data.testSeconds)||data.testSeconds<1||data.testSeconds>10))))throw Error('Invalid voice ducking setting');
  if(data.action==='apple_music_run'&&((typeof data.title!=='string'||!data.title.trim()||data.title.length>150||(data.artist!==undefined&&(typeof data.artist!=='string'||data.artist.length>150)))||(data.pauseAfter!==undefined&&typeof data.pauseAfter!=='boolean')))throw Error('Apple Music requires a title, optional artist and optional boolean pauseAfter');
  if(data.action==='clock_timer_start'&&(!Number.isInteger(data.seconds)||data.seconds<1||data.seconds>86400||(data.label!==undefined&&(typeof data.label!=='string'||data.label.length>100))))throw Error('Timer requires integer seconds from 1 to 86400 and an optional short label');
  if(data.action==='open_app'&&(typeof data.packageName!=='string'||!/^([a-zA-Z][\w]*\.)+[\w]+$/.test(data.packageName)))throw Error('An exact installed packageName is required');
  if(['tap','long_press','tap_label','touch_label','touch_point','set_text','ime_enter','scroll','swipe','drag'].includes(data.action)){
   if(typeof data.snapshotId!=='string'||! /^[a-f0-9-]{36}$/.test(data.snapshotId))throw Error('A fresh inspect_ui snapshotId is required');
   if(!['tap_label','touch_label'].includes(data.action)&&(!Number.isInteger(data.nodeId)||data.nodeId<0||data.nodeId>=300))throw Error('A visible nodeId from inspect_ui is required');
  }
  if(['tap_label','touch_label'].includes(data.action)&&(typeof data.label!=='string'||!data.label.trim()||data.label.length>200))throw Error('An exact visible label is required');
  if(['tap_label','touch_label'].includes(data.action)&&data.nodeId!==undefined&&(!Number.isInteger(data.nodeId)||data.nodeId<0||data.nodeId>=300))throw Error('A visible matching nodeId is required');
  if(data.action==='set_text'&&(typeof data.text!=='string'||data.text.length>4000))throw Error('Text must be at most 4000 characters');
  if(data.action==='scroll'&&!['forward','backward'].includes(data.direction))throw Error('Scroll direction must be forward or backward');
  if(data.action==='swipe'){
   if(!['left','right'].includes(data.direction))throw Error('Swipe direction must be left or right');
   if(!Array.isArray(data.bounds)||data.bounds.length!==4||!data.bounds.every(n=>Number.isInteger(n)&&n>=0&&n<=20000)||data.bounds[2]-data.bounds[0]<48||data.bounds[3]-data.bounds[1]<24)throw Error('Swipe requires visible screen bounds [left, top, right, bottom]');
  }
  if(data.action==='touch_point'&&(!Number.isInteger(data.x)||!Number.isInteger(data.y)||data.x<0||data.y<0||data.x>20000||data.y>20000))throw Error('Touch requires display-pixel x and y');
  if(data.action==='drag'){
   if(!Array.isArray(data.points)||data.points.length!==4||!data.points.every(n=>Number.isInteger(n)&&n>=0&&n<=20000)||(data.points[0]===data.points[2]&&data.points[1]===data.points[3]))throw Error('Drag requires distinct display-pixel endpoints');
   if(data.durationMs!==undefined&&(!Number.isInteger(data.durationMs)||data.durationMs<200||data.durationMs>2000))throw Error('Drag duration must be 200–2000 milliseconds');
  }
  for(const [id,c] of this.commands)if(this.now()-c.created>300000)this.commands.delete(id);
  // Completed results remain available for reconnects without consuming the
  // pending-work budget. Bound the five-minute result cache separately.
  if(this.pending().length>=100)throw Error('Phone pending command queue full');
  if(this.commands.size>=1000)throw Error('Phone command result cache full');
  const c={id:crypto.randomUUID(),action:data.action,chatGuid:data.chatGuid,screenshotId:data.screenshotId,commit:data.commit,replaceExisting:data.replaceExisting,visualOnly:data.visualOnly,params:data.action==='clock_control'?validateClockParams(data.params):data.action==='app_command'?(data.params||{}):undefined,app:data.app,operation:data.operation,seconds:data.seconds,duckMode:data.duckMode,reductionDb:data.reductionDb,duckEnabled:data.duckEnabled,testSeconds:data.testSeconds,title:data.title,artist:data.artist,pauseAfter:data.pauseAfter,packageName:data.packageName,snapshotId:data.snapshotId,nodeId:data.nodeId,label:data.label,text:data.text,direction:data.direction,bounds:data.bounds,x:data.x,y:data.y,points:data.points,durationMs:data.durationMs,visual:data.action==='visualization_set'?data.visual:undefined,created:this.now(),expires:this.now()+(data.action==='apple_music_run'?60000:20000),status:'pending'};
  this.commands.set(c.id,c);return c;
 }
 get(id){const c=this.commands.get(id);if(c&&c.status==='pending'&&this.now()>c.expires)c.status='expired';return c;}
 pending(){return [...this.commands.keys()].map(id=>this.get(id)).filter(c=>c.status==='pending'&&!c.internalOnly);}
 complete(id,result){const c=this.get(id);if(!c)throw Error('Unknown phone command');if(c.status!=='pending')return c;
  if(!result||typeof result.status!=='string')throw Error('Missing phone result');
  c.status='completed';c.result=result;c.completed=this.now();return c;
 }
}

// Mirrors VisualStore.sanitize on the phone. Declarative data only; an absent value means unknown, never zero.
const visualText=(v,max,required)=>v===undefined||v===null?!required:typeof v==='string'&&v.trim().length<=max&&(!required||v.trim().length>0)&&!/[\u0000-\u001f\u007f-\u009f]/.test(v.trim());
const visualMatch=(v,pattern)=>v===undefined||v===null||v.trim()===''||pattern.test(v.trim());
const visualList=(v,max)=>v===undefined||v===null?[]:Array.isArray(v)&&v.length<=max?v:null;
const visualObject=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export function validateVisual(v){
 const fail=at=>{throw Error(`Invalid visual: ${at}`);};
 if(!visualObject(v))fail('visual must be an object');
 let size;try{size=Buffer.byteLength(JSON.stringify(v));}catch{fail('visual must be plain JSON');}
 if(size>102400)fail('visual is larger than 100 KB');
 if(!visualText(v.id,64,true)||!/^[A-Za-z0-9._-]{1,64}$/.test(v.id.trim()))fail('id');
 if(!visualText(v.title,60,true))fail('title');
 if(!visualText(v.subtitle,80))fail('subtitle');
 if(!visualText(v.source,40))fail('source');
 if(!visualText(v.updatedAt,40)||!visualMatch(v.updatedAt,/^[0-9T:.Z+-]{4,40}$/))fail('updatedAt');
 const kind=v.kind===undefined?'meals':v.kind;
 if(!['meals','wind'].includes(kind))fail('kind');
 if(kind==='wind'){
  if(!visualObject(v.wind))fail('wind must be an object');
  for(const [field,max] of [['origin',50],['barrier',50],['destination',50],['direction',40],['effect',140],['peak',60]])
   if(!visualText(v.wind[field],max,true))fail(`wind.${field}`);
  if(!visualText(v.wind.caution,160))fail('wind.caution');
  return;
 }
 const days=visualList(v.days,14);if(!days||days.length<1)fail('days must hold 1 to 14 entries');
 let mealTotal=0;
 days.forEach((day,d)=>{
  const at=`days[${d}]`;if(!visualObject(day))fail(at);
  if(!visualText(day.date,10,true)||!/^\d{4}-\d{2}-\d{2}$/.test(day.date.trim()))fail(`${at}.date`);
  if(!visualText(day.label,24))fail(`${at}.label`);
  const meals=visualList(day.meals,50);if(!meals)fail(`${at}.meals`);
  mealTotal+=meals.length;if(mealTotal>50)fail('more than 50 meals');
  meals.forEach((meal,m)=>{
   const mealAt=`${at}.meals[${m}]`;if(!visualObject(meal))fail(mealAt);
   if(!visualText(meal.id,64)||!visualMatch(meal.id,/^[A-Za-z0-9._-]{1,64}$/))fail(`${mealAt}.id`);
   if(!visualText(meal.name,60,true))fail(`${mealAt}.name`);
   if(!visualText(meal.time,5)||!visualMatch(meal.time,/^([01]\d|2[0-3]):[0-5]\d$/))fail(`${mealAt}.time`);
   if(!visualText(meal.notes,160))fail(`${mealAt}.notes`);
   if(meal.estimated!==undefined&&meal.estimated!==null&&typeof meal.estimated!=='boolean')fail(`${mealAt}.estimated`);
   const nutrients=visualList(meal.nutrients,50);if(!nutrients)fail(`${mealAt}.nutrients`);
   nutrients.forEach((nutrient,n)=>{
    const nutrientAt=`${mealAt}.nutrients[${n}]`;if(!visualObject(nutrient))fail(nutrientAt);
    if(!visualText(nutrient.name,32,true))fail(`${nutrientAt}.name`);
    if(nutrient.value!==undefined&&nutrient.value!==null&&(typeof nutrient.value!=='number'||!Number.isFinite(nutrient.value)||nutrient.value<0||nutrient.value>1e9))fail(`${nutrientAt}.value must be a finite number that is not negative`);
    if(!visualText(nutrient.unit,12))fail(`${nutrientAt}.unit`);
    if(nutrient.group!==undefined&&nutrient.group!==null&&!['macro','micro'].includes(nutrient.group))fail(`${nutrientAt}.group`);
   });
  });
 });
}
