export const environmentToolDefinitions=[{
 type:'function',name:'get_phone_environment',
 description:'Read the paired Pixel’s current local date, weekday, time and time zone, plus its latest cached location, neighborhood/city, location age and accuracy. Read-only; does not open an app or wait for GPS. Use when the request needs current time/date or the user’s current location (including weather here). Stale or unavailable location is explicit, never an exact address.',
 inputSchema:{type:'object',properties:{},additionalProperties:false}
}];
export const environmentToolPolicy='Current time and location are available only through get_phone_environment. Call it when those facts are relevant, including relative dates and implicit here/near me. Do not use old timestamps or coordinates from conversation history as current facts. Respect returned location age, accuracy and unavailable/stale status. Neighborhood labels are approximate; never invent a street or intersection from coordinates.';

export function cleanPhoneEnvironment(value,now=Date.now()){
 if(value?.status!=='ok'||!Number.isFinite(value.observedAt)||Math.abs(now-value.observedAt)>10000)throw Error('Fresh phone environment was not returned');
 const clock=value.clock;
 if(!clock||!/^\d{4}-\d{2}-\d{2}$/.test(clock.localDate)||! /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$/.test(clock.weekday)||!/^\d{2}:\d{2}:\d{2}$/.test(clock.localTime)||typeof clock.timeZone!=='string'||! /^[A-Za-z0-9_+\-/]{1,80}$/.test(clock.timeZone)||! /^(Z|[+-]\d{2}:\d{2})$/.test(clock.utcOffset))throw Error('Phone clock is unavailable');
 const result={status:'verified',source:'paired_pixel',observedAt:value.observedAt,clock:{localDate:clock.localDate,weekday:clock.weekday,localTime:clock.localTime,timeZone:clock.timeZone,utcOffset:clock.utcOffset},location:{status:'unavailable'}};
 const loc=value.location;
 if(['permission_required','disabled','unavailable'].includes(loc?.status))result.location={status:loc.status};
 else if(['available','stale'].includes(loc?.status)&&Number.isFinite(loc.latitude)&&Math.abs(loc.latitude)<=90&&Number.isFinite(loc.longitude)&&Math.abs(loc.longitude)<=180&&Number.isFinite(loc.accuracyMeters)&&loc.accuracyMeters>=0&&Number.isFinite(loc.observedAt)&&loc.observedAt<=now+10000&&Number.isFinite(loc.ageSeconds)&&loc.ageSeconds>=0){
  const age=Math.max(loc.ageSeconds,Math.max(0,Math.floor((now-loc.observedAt)/1000)));
  result.location={status:age>300?'stale':loc.status,latitude:loc.latitude,longitude:loc.longitude,accuracyMeters:loc.accuracyMeters,ageSeconds:age,observedAt:loc.observedAt,approximate:loc.approximate===true};
  for(const k of ['neighborhood','city','region','country'])if(typeof loc[k]==='string'&&loc[k].trim())result.location[k]=loc[k].replace(/[\x00-\x1f\x7f]/g,' ').trim().slice(0,100);
 }
 return result;
}

export function createEnvironmentTools({phone,now=Date.now}){
 async function handleToolCall(request){
  try{
   if(request.tool!=='get_phone_environment')throw Error('Unknown environment tool');
   const args=request.arguments;
   if(!args||typeof args!=='object'||Array.isArray(args)||Object.keys(args).length)throw Error('get_phone_environment takes no arguments');
   // Fetch for each invocation: the phone reads its clock now and supplies the
   // cache’s age. Never reuse a prior tool result as the current environment.
   const result=cleanPhoneEnvironment(await phone({action:'environment'}),now());
   return {success:true,contentItems:[{type:'inputText',text:JSON.stringify(result)}]};
  }catch(error){return {success:false,contentItems:[{type:'inputText',text:JSON.stringify({status:'unavailable',reason:error.message})}]};}
 }
 return {definitions:environmentToolDefinitions,handleToolCall};
}
