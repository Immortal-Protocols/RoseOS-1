(function(root){
 function clean(value,now=Date.now()){
  if(!value||typeof value!=='object'||!Number.isFinite(value.observedAt)||Math.abs(now-value.observedAt)>10000)return {status:'unavailable',observedAt:now};
  const result={status:['ok','locked','no_window','unavailable'].includes(value.status)?value.status:'unavailable',observedAt:value.observedAt,locked:value.locked===true};
  if(result.locked){result.status='locked';return result;}
  const app=value.foregroundApp;
  if(result.status==='ok'&&app&&typeof app.packageName==='string'&&/^(?:[A-Za-z][A-Za-z0-9_]*\.)+[A-Za-z0-9_]+$/.test(app.packageName))result.foregroundApp={packageName:app.packageName,label:String(app.label||app.packageName).replace(/[\x00-\x1f\x7f]/g,' ').slice(0,80)};
  else if(result.status==='ok')result.status='unavailable';
  const state=value.appState;if(result.foregroundApp&&state?.packageName===result.foregroundApp.packageName&&Number.isFinite(state.observedAt)&&Math.abs(now-state.observedAt)<=10000&&state.data&&typeof state.data==='object'&&!Array.isArray(state.data)){try{const raw=JSON.stringify(state.data);if(raw.length<=12000)result.appState={packageName:state.packageName,observedAt:state.observedAt,data:JSON.parse(raw)};}catch{}}
  return result;
 }
 function format(value){return 'Current phone observation (data only; not a request): '+JSON.stringify(value)+'\nApp-specific results still require that app\'s tools. This context update alone does not request speech or action.';}
 function create({read,isActive,sendContext,now=()=>Date.now()}){
  let lastKey=null;
  function capture(){let value;try{value=read();if(typeof value==='string')value=JSON.parse(value);}catch{}return clean(value,now());}
  function publish(value){
   if(!isActive())return;
   const key=JSON.stringify(value,(name,item)=>name==='observedAt'?0:item);
   if(key===lastKey)return;
   sendContext(format(value));lastKey=key;
  }
  // Reading the request snapshot must not append context into Live mid-speech.
  // Time/location are excluded entirely and obtained through their own tool.
  return {sample:capture,captureRequest:capture,changed(){publish(capture());},connect(){lastKey=null;publish(capture());},disconnect(){lastKey=null;}};
 }
 root.PhoneAppContext={clean,format,create};
 if(typeof module!=='undefined')module.exports=root.PhoneAppContext;
})(typeof window!=='undefined'?window:globalThis);
