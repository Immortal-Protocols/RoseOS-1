const test=require('node:test'),assert=require('node:assert/strict');
const {create,clean}=require('../app/assets/phone-app-context.js');
const app=(name,now)=>({status:'ok',observedAt:now,foregroundApp:{packageName:name,label:name}});
test('app changes update context once; every request gets a fresh local observation',()=>{
 let now=1000,current='com.google.android.youtube',reads=0,active=true;const sent=[];
 const context=create({read:()=>{reads++;return app(current,now);},now:()=>now,isActive:()=>active,sendContext:s=>sent.push(s)});
 context.connect();context.changed();assert.equal(sent.length,1);
 now++;current='com.apple.android.music';context.changed();assert.equal(sent.length,2);assert.match(sent[1],/com.apple.android.music/);
 now++;assert.equal(context.captureRequest().observedAt,now);assert.equal(sent.length,2,'request capture is silent');
 now++;assert.equal(context.captureRequest().observedAt,now);assert.equal(sent.length,2,'fresh request state does not interrupt Live');
 active=false;context.disconnect();context.changed();assert.equal(sent.length,2);
 active=true;context.connect();assert.equal(sent.length,3);assert.equal(reads,7);
});
test('missing or stale state never reuses the old foreground app',()=>{
 for(const value of [null,{...app('com.example.old',1)}, {status:'ok',observedAt:20000,foregroundApp:{packageName:'bad name'}}, {status:'ok',observedAt:20000,locked:true,foregroundApp:{packageName:'com.example.secret'}}]){
  assert.equal(clean(value,20000).foregroundApp,undefined);
 }
});
test('only bounded app identity is passed, never screen text or extra metadata',()=>{
 const result=clean({...app('com.example.app',1000),nodes:[{text:'secret'}],foregroundApp:{packageName:'com.example.app',label:'a\nb'.repeat(100),screenText:'secret'}},1000);
 assert.equal(result.foregroundApp.label.length,80);assert.doesNotMatch(result.foregroundApp.label,/\n/);
 assert.equal(JSON.stringify(result).includes('secret'),false);
});
test('fresh state from the foreground native app accompanies the request',()=>{
 const state={packageName:'dev.pixel.books',observedAt:1000,data:{status:'verified',title:'The Count of Monte Cristo',index:20,passage:'A ship arrived.'}};
 const result=clean({...app('dev.pixel.books',1000),appState:state},1000);assert.deepEqual(result.appState,state);
 assert.equal(clean({...app('dev.pixel.books',1000),appState:{...state,packageName:'dev.pixel.notes.agent'}},1000).appState,undefined);
 assert.equal(clean({...app('dev.pixel.books',20000),appState:state},20000).appState,undefined);
 assert.equal(clean({...app('dev.pixel.books',1000),appState:{...state,data:{text:'x'.repeat(13000)}}},1000).appState,undefined);
});
test('fresh nested timestamps do not repeatedly announce unchanged book state',()=>{
 let now=1000,index=15;const sent=[];
 const context=create({now:()=>now,isActive:()=>true,sendContext:s=>sent.push(s),read:()=>({...app('dev.pixel.books',now),appState:{packageName:'dev.pixel.books',observedAt:now,data:{observedAt:now,index,passage:'Current paragraph'}}})});
 context.connect();now++;context.changed();now++;context.changed();assert.equal(sent.length,1);
 index++;now++;context.changed();assert.equal(sent.length,2,'a real position change is still delivered');
 now++;context.captureRequest();assert.equal(sent.length,2,'request capture does not append a mid-speech update');
});
const env=(now,patch={})=>({clock:{localDate:'2026-10-17',weekday:'Saturday',localTime:'14:23:10',timeZone:'America/Los_Angeles',utcOffset:'-07:00'},location:{status:'available',latitude:37.7,longitude:-122.4,accuracyMeters:80,ageSeconds:10,observedAt:now-10000,approximate:true,city:'San Francisco',...patch}});
test('time and location never enter automatic context, including from an older phone client',()=>{
 let now=100000,clock='14:23:10',city='San Francisco',foreground='dev.pixel.books';const sent=[];
 const read=()=>{const environment=env(now,{city});environment.clock.localTime=clock;return {...app(foreground,now),environment};};
 const context=create({now:()=>now,isActive:()=>true,sendContext:s=>sent.push(s),read});
 context.connect();assert.equal(sent.length,1);assert.doesNotMatch(sent[0],/environment|clock|location|San Francisco/);
 now+=60000;clock='14:24:10';city='Oakland';context.changed();assert.equal(sent.length,1);
 foreground='com.google.android.youtube';context.changed();assert.equal(sent.length,2);assert.doesNotMatch(sent[1],/environment|clock|location|Oakland/);
 const request=context.captureRequest();assert.equal(request.environment,undefined);assert.equal(request.foregroundApp.packageName,foreground);assert.equal(sent.length,2);
 now+=60000;context.changed();context.captureRequest();assert.equal(sent.length,2);
 context.disconnect();context.connect();assert.equal(sent.length,3);assert.doesNotMatch(sent[2],/environment|clock|location/);
 for(const status of ['unavailable','locked'])assert.equal(clean({...read(),status,locked:status==='locked'},now).environment,undefined);
});
