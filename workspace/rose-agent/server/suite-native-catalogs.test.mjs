import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeNativeCatalogs,verifyInstalledCatalogs} from './suite-native-catalogs.mjs';
const catalog=(name)=>({status:'ok',schemaVersion:1,tools:[{name,operation:'read',description:'Read actual saved records.',inputSchema:{type:'object',properties:{},additionalProperties:false}}]});
test('native catalog merge preserves existing tools and rejects collisions without mutating live config',()=>{
 const previous={enabled:true,packages:['dev.pixel.old'],catalogs:{'dev.pixel.old':catalog('old_read')},verifiedAt:'old'};
 const next=mergeNativeCatalogs(previous,{'dev.pixel.new':catalog('new_read')});
 assert.deepEqual(previous.packages,['dev.pixel.old']);assert.deepEqual(next.packages,['dev.pixel.old','dev.pixel.new']);
 assert.equal(next.catalogs['dev.pixel.old'],previous.catalogs['dev.pixel.old']);
 assert.throws(()=>mergeNativeCatalogs(previous,{'dev.pixel.new':catalog('old_read')}),/Duplicate action/);
 assert.throws(()=>mergeNativeCatalogs(previous,{'dev.pixel.new':{status:'unavailable'}}),/Missing native/);
 assert.deepEqual(previous.packages,['dev.pixel.old']);
});
test('activation requires actual phone catalog equality; a missing or stale provider cannot pass',async()=>{
 const expected={'dev.pixel.new':catalog('new_read')};let requests=[];
 const actual=await verifyInstalledCatalogs(expected,async r=>{requests.push(r);return {...catalog('new_read'),versionCode:9};});
 assert.equal(actual['dev.pixel.new'].versionCode,9);assert.equal(requests[0].operation,'capabilities');
 await assert.rejects(verifyInstalledCatalogs(expected,async()=>({status:'unavailable'})),/unavailable/);
 await assert.rejects(verifyInstalledCatalogs(expected,async()=>catalog('different_read')),/differ/);
});
