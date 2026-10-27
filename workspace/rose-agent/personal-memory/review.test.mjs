import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {MemoryReviewStore} from './review-store.mjs';
import {memorySnapshot} from './context.mjs';

function fixture(t){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'memory-review-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const candidates=['durable','temporary','uncertain'].map((scope,i)=>({id:'note-'+i,scope,status:'proposed',text:'Exact memory '+i+'.',createdAt:1000+i,attribution:'voice-speaker-unverified',relatedCandidateIds:[],evidence:[{eventId:'source',quote:'Exact memory '+i+'.'}]}));
 fs.writeFileSync(path.join(dir,'state.json'),JSON.stringify({version:1,candidates}));
 fs.writeFileSync(path.join(dir,'control.json'),JSON.stringify({useEnabled:true,collectionEnabled:true}));
 return {dir,store:new MemoryReviewStore(dir),original:fs.readFileSync(path.join(dir,'state.json'),'utf8')};
}
test('keep and remove edit the sent list without rewriting notes, source metadata, or flags',t=>{
 const f=fixture(t),before=fs.readFileSync(path.join(f.dir,'control.json'),'utf8');
 assert.deepEqual(memorySnapshot(f.dir).records.map(x=>x.id),['note-0']);
 const response=f.store.mutate({action:'keep',candidateId:'note-1',operationId:'keep-1',revision:0});
 assert.equal(response.keptCount,2);assert.equal(response.pending.length,2);
 const records=memorySnapshot(f.dir).records;assert.deepEqual(records.map(x=>x.id),['note-0','note-1']);
 assert.deepEqual(Object.keys(records[1]),['id','text','updatedAt','attribution']);
 assert.equal(records[1].text,'Exact memory 1.');assert.equal(records[1].attribution,'voice-speaker-unverified');
 f.store.mutate({action:'remove',candidateId:'note-0',operationId:'remove-1',revision:1});
 assert.deepEqual(memorySnapshot(f.dir).records.map(x=>x.id),['note-1']);
 assert.equal(fs.readFileSync(path.join(f.dir,'state.json'),'utf8'),f.original);
 assert.equal(fs.readFileSync(path.join(f.dir,'control.json'),'utf8'),before);
 assert.equal(new MemoryReviewStore(f.dir).snapshot().keptCount,1);
});
test('uncertain notes can be explicitly kept; undo restores the exact earlier eligibility',t=>{
 const f=fixture(t);f.store.mutate({action:'keep',candidateId:'note-2',operationId:'keep-2',revision:0});
 assert.equal(memorySnapshot(f.dir).records.length,2);
 const u=f.store.mutate({action:'undo',targetOperationId:'keep-2',operationId:'undo-1',revision:1});
 assert.equal(u.keptCount,1);assert.equal(u.pending.length,3);assert.equal(u.undoOperationId,null);
 assert.deepEqual(memorySnapshot(f.dir).records.map(x=>x.id),['note-0']);
});
test('lost responses can be retried once logically, while stale edits and changed operation IDs are rejected',t=>{
 const f=fixture(t),request={action:'remove',candidateId:'note-0',operationId:'remove-1',revision:0};
 f.store.mutate(request);const retried=f.store.mutate(request);assert.equal(retried.revision,1);
 assert.throws(()=>f.store.mutate({...request,candidateId:'note-1'}),/already used/);
 assert.throws(()=>f.store.mutate({...request,operationId:'different'}),/changed/);
 assert.equal(f.store.read().reviews.operations.length,1);
});
test('undo cannot overwrite a more recent swipe; removing a note does not blacklist similar future notes',t=>{
 const f=fixture(t);f.store.mutate({action:'remove',candidateId:'note-0',operationId:'remove-1',revision:0});
 f.store.mutate({action:'keep',candidateId:'note-1',operationId:'keep-1',revision:1});
 assert.throws(()=>f.store.mutate({action:'undo',targetOperationId:'remove-1',operationId:'undo-1',revision:2}),/no longer/);
 const state=JSON.parse(f.original);state.candidates.push({...state.candidates[0],id:'new-note',createdAt:3000});
 fs.writeFileSync(path.join(f.dir,'state.json'),JSON.stringify(state));
 assert.ok(memorySnapshot(f.dir).records.some(x=>x.id==='new-note'));
});
test('disabled delivery stays disabled after keeping; malformed review storage fails closed',t=>{
 const f=fixture(t);fs.writeFileSync(path.join(f.dir,'control.json'),JSON.stringify({useEnabled:false}));
 f.store.mutate({action:'keep',candidateId:'note-1',operationId:'keep-1',revision:0});
 assert.equal(memorySnapshot(f.dir).enabled,false);
 fs.writeFileSync(path.join(f.dir,'reviews.json'),'not json');
 assert.throws(()=>f.store.snapshot());assert.equal(memorySnapshot(f.dir).enabled,false);
});
test('an explicitly kept note is not displaced by a newer duplicate',t=>{
 const f=fixture(t),state=JSON.parse(f.original);
 state.candidates.push({...state.candidates[0],id:'new-copy',createdAt:9000});
 fs.writeFileSync(path.join(f.dir,'state.json'),JSON.stringify(state));
 f.store.mutate({action:'keep',candidateId:'note-0',operationId:'keep-old',revision:0});
 assert.deepEqual(memorySnapshot(f.dir).records.map(x=>x.id),['note-0']);
});
