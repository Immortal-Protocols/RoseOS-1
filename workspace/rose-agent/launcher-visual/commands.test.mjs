import test from 'node:test';
import assert from 'node:assert/strict';
import {PhoneCommands} from '../server/phone.mjs';
const fixture=()=>({id:'sample',title:'Sample meals',subtitle:'Two days',source:'A-List',updatedAt:'2026-10-12T20:00:00Z',days:[{date:'2026-10-12',label:'Oct 12',meals:[{id:'sample-1',name:'Breakfast',estimated:true,notes:'Estimated portions',nutrients:[{name:'Energy',value:452,unit:'kcal',group:'macro'},{name:'Calcium',value:100,unit:'mg',group:'micro'}]}]}]});
test('visual commands travel through the normal phone queue',()=>{
 const q=new PhoneCommands();const v=fixture();const c=q.create({action:'visualization_set',visual:v});
 assert.deepEqual(c.visual,v);assert.equal(q.pending()[0].id,c.id);
 q.complete(c.id,{status:'ok',id:v.id,mealCount:1,dayCount:1,visible:true,enabled:true});
 assert.equal(q.pending().length,0);
 for(const action of ['visualization_clear','visualization_status'])assert.equal(q.create({action}).action,action);
});
test('reject invalid visual structure, limits and nutrient types',()=>{
 const invalid=[null,[],{},'text'];
 for(const value of [-1,NaN,Infinity,'40']){const v=fixture();v.days[0].meals[0].nutrients[0].value=value;invalid.push(v);}
 {const v=fixture();v.days=Array.from({length:15},()=>structuredClone(v.days[0]));invalid.push(v);}
 {const v=fixture();v.days[0].meals=Array.from({length:51},()=>structuredClone(v.days[0].meals[0]));invalid.push(v);}
 {const v=fixture();v.days[0].meals[0].nutrients=Array.from({length:51},()=>({name:'Fiber',value:1,unit:'g',group:'macro'}));invalid.push(v);}
 {const v=fixture();v.title='x'.repeat(100001);invalid.push(v);}
 for(const visual of invalid)assert.throws(()=>new PhoneCommands().create({action:'visualization_set',visual}));
});
test('zero is a recorded value, absent nutrients stay absent',()=>{
 const v=fixture();v.days[0].meals[0].nutrients=[{name:'Energy',value:0,unit:'kcal',group:'macro'}];
 const c=new PhoneCommands().create({action:'visualization_set',visual:v});
 assert.deepEqual(c.visual.days[0].meals[0].nutrients,v.days[0].meals[0].nutrients);
});
