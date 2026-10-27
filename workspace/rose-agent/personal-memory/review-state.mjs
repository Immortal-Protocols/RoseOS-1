import fs from 'node:fs';
import path from 'node:path';

// Swipes are separate from extraction state: no note, source, or flag is rewritten.
export function readReviewState(dir) {
 try {
  const state=JSON.parse(fs.readFileSync(path.join(dir,'reviews.json'),'utf8'));
  if(state.version!==1||!Number.isInteger(state.revision)||!state.decisions||!Array.isArray(state.operations))throw Error('Invalid memory review state');
  return state;
 }catch(error){if(error.code==='ENOENT')return {version:1,revision:0,decisions:{},operations:[]};throw error;}
}
export function reviewControl(dir,control) {
 return {...control,memoryReviewDecisions:readReviewState(dir).decisions};
}
