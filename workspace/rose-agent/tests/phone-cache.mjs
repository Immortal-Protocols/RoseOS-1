import test from 'node:test';import assert from 'node:assert/strict';
import {PhoneCommands} from '../server/phone.mjs';
test('completed work does not consume pending capacity; results survive reconnects',()=>{
 let now=1000;const p=new PhoneCommands(()=>now);let first;
 for(let i=0;i<1000;i++){const c=p.create({action:'inspect_ui'});first??=c.id;p.complete(c.id,{status:'ok',marker:i});}
 assert.equal(p.get(first).result.marker,0);
 assert.equal(p.pending().length,0);
 assert.throws(()=>p.create({action:'inspect_ui'}),/result cache full/);
 now+=300001;assert.doesNotThrow(()=>p.create({action:'inspect_ui'}));assert.equal(p.get(first),undefined);
});
test('pending work stays bounded independently; expired work releases capacity',()=>{
 let now=1000;const p=new PhoneCommands(()=>now);
 for(let i=0;i<100;i++)p.create({action:'inspect_ui'});
 assert.throws(()=>p.create({action:'inspect_ui'}),/pending command queue full/);
 now+=20001;assert.doesNotThrow(()=>p.create({action:'inspect_ui'}));assert.equal(p.pending().length,1);
});
