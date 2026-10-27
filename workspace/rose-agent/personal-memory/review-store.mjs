import fs from 'node:fs';
import path from 'node:path';
import {activeMemories,eligibleMemory} from './context.mjs';
import {readReviewState} from './review-state.mjs';

const fail=(message,status=400)=>Object.assign(new Error(message),{status});
const identifier=value=>typeof value==='string'&&/^[a-zA-Z0-9-]{1,100}$/.test(value);
export class MemoryReviewStore {
 constructor(dir,now=Date.now){this.dir=dir;this.now=now;}
 read(){
  const state=JSON.parse(fs.readFileSync(path.join(this.dir,'state.json'),'utf8'));
  const control=JSON.parse(fs.readFileSync(path.join(this.dir,'control.json'),'utf8'));
  if(state.version!==1||!Array.isArray(state.candidates))throw fail('Memory storage is unavailable',503);
  return {candidates:state.candidates,control,reviews:readReviewState(this.dir)};
 }
 snapshot(data=this.read()){
  const {candidates,control,reviews}=data;
  const included=new Set(activeMemories(candidates,{...control,useEnabled:true,memoryReviewDecisions:reviews.decisions}).map(c=>c.id));
  const card=c=>({id:c.id,text:c.text,scope:c.scope,kept:included.has(c.id)});
  const pending=candidates.filter(c=>eligibleMemory(c)&&!Object.hasOwn(reviews.decisions,c.id)).sort((a,b)=>a.createdAt-b.createdAt).map(card);
  const kept=candidates.filter(c=>included.has(c.id)).sort((a,b)=>a.createdAt-b.createdAt).map(card);
  const last=reviews.operations.at(-1);
  return {revision:reviews.revision,pending,kept,keptCount:kept.length,undoOperationId:last?.kind==='review'?last.operationId:null};
 }
 save(reviews){
  const file=path.join(this.dir,'reviews.json'),tmp=file+'.'+process.pid+'.tmp';
  fs.writeFileSync(tmp,JSON.stringify(reviews,null,2)+'\n',{mode:0o600});fs.chmodSync(tmp,0o600);fs.renameSync(tmp,file);
 }
 mutate(input){
  if(!input||!identifier(input.operationId)||!['keep','remove','undo'].includes(input.action))throw fail('Invalid memory review');
  const data=this.read(),{reviews,candidates}=data;
  const old=reviews.operations.find(o=>o.operationId===input.operationId);
  if(old){
   if(old.action!==input.action||old.candidateId!==(input.candidateId||null)||old.targetOperationId!==(input.targetOperationId||null))throw fail('Operation ID already used',409);
   return {...this.snapshot(data),saved:true,operationId:old.operationId};
  }
  if(input.revision!==reviews.revision)throw fail('Memories changed. Refresh before swiping again.',409);
  let operation;
  if(input.action==='undo'){
   const target=reviews.operations.at(-1);
   if(!target||target.kind!=='review'||target.operationId!==input.targetOperationId)throw fail('That swipe can no longer be undone',409);
   if(target.previous===null)delete reviews.decisions[target.candidateId];else reviews.decisions[target.candidateId]=target.previous;
   operation={kind:'undo',action:'undo',operationId:input.operationId,candidateId:null,targetOperationId:target.operationId,at:this.now()};
  }else{
   const candidate=candidates.find(c=>c.id===input.candidateId);
   if(!candidate)throw fail('Memory not found',404);
   if(input.action==='keep'&&!eligibleMemory(candidate))throw fail('This note cannot be included in memory');
   const previous=reviews.decisions[candidate.id]||null;
   reviews.decisions[candidate.id]={choice:input.action,at:this.now(),operationId:input.operationId};
   operation={kind:'review',action:input.action,operationId:input.operationId,candidateId:candidate.id,targetOperationId:null,previous,at:this.now()};
  }
  reviews.revision++;reviews.operations.push(operation);this.save(reviews);
  return {...this.snapshot(data),saved:true,operationId:input.operationId};
 }
}
