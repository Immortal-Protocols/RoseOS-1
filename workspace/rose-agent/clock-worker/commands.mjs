export const clockOperations=Object.freeze(['timer_list','timer_get','timer_silence','timer_stop','timer_pause','timer_resume','timer_reset','timer_cancel','timer_add','timer_rename','stopwatch_get','stopwatch_start','stopwatch_pause','stopwatch_reset','stopwatch_lap','alarm_list','alarm_get','alarm_create','alarm_update','alarm_enable','alarm_disable','alarm_delete','alarm_snooze','alarm_dismiss','world_list','world_search','world_time','world_add','world_remove']);
export function validateClockParams(p){
 if(!p||typeof p!=='object'||Array.isArray(p)||!clockOperations.includes(p.operation))throw Error('Unsupported Clock operation');
 const fields=['operation','timerId','alarmId','label','newLabel','seconds','hour','minute','days','city','cityId'];
 for(const k of Object.keys(p))if(!fields.includes(k))throw Error('Unsupported Clock field: '+k);
 for(const k of ['timerId','alarmId'])if(p[k]!==undefined&&(!Number.isSafeInteger(p[k])||p[k]<0))throw Error('Invalid '+k);
 for(const k of ['label','newLabel','city','cityId'])if(p[k]!==undefined&&(typeof p[k]!=='string'||p[k].length>100))throw Error('Invalid '+k);
 for(const [k,min,max] of [['seconds',1,86400],['hour',0,23],['minute',0,59]])if(p[k]!==undefined&&(!Number.isInteger(p[k])||p[k]<min||p[k]>max))throw Error('Invalid '+k);
 if(p.days!==undefined&&(!Array.isArray(p.days)||p.days.length>7||p.days.some(n=>!Number.isInteger(n)||n<1||n>7)||new Set(p.days).size!==p.days.length))throw Error('days must be distinct ISO weekdays, Monday=1 through Sunday=7');
 if(p.operation==='alarm_create'&&(p.hour===undefined||p.minute===undefined))throw Error('Alarm needs a resolved local hour and minute');
 if(p.operation==='timer_add'&&p.seconds===undefined)throw Error('Timer extension needs seconds');
 if(p.operation==='timer_rename'&&p.newLabel===undefined)throw Error('Timer rename needs newLabel');
 if(['world_search','world_time','world_add','world_remove'].includes(p.operation)&&!p.city&&!p.cityId)throw Error('A city name or ID is required');
 const prefix=p.operation.split('_')[0];
 const allowed={timer:['timerId','label','newLabel','seconds'],stopwatch:[],alarm:['alarmId','label','newLabel','hour','minute','days'],world:['city','cityId']}[prefix];
 for(const k of Object.keys(p))if(k!=='operation'&&!allowed.includes(k))throw Error('Field does not belong to '+prefix+': '+k);
 if(p.operation==='alarm_update'&&!['newLabel','hour','minute','days'].some(k=>p[k]!==undefined))throw Error('Alarm update needs a changed field');
 const selectors=prefix==='timer'?['timerId','label']:prefix==='alarm'?['alarmId','label']:[];
 const byOperation={
  timer_add:[...selectors,'seconds'],timer_rename:[...selectors,'newLabel'],
  timer_list:[],alarm_list:[],world_list:[],
  alarm_create:['hour','minute','label','days'],alarm_update:[...selectors,'hour','minute','days','newLabel'],
 };
 const specific=byOperation[p.operation]??(prefix==='world'?['city','cityId']:selectors);
 for(const k of Object.keys(p))if(k!=='operation'&&!specific.includes(k))throw Error('Unexpected parameter for '+p.operation+': '+k);
 if(p.timerId!==undefined&&p.label!==undefined||p.alarmId!==undefined&&p.label!==undefined||p.cityId!==undefined&&p.city!==undefined)throw Error('Use one unambiguous selector');
 return {...p};
}
