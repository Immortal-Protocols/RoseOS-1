// build.sh overwrites some app-tree files with their sibling-module sources before packaging.
// Tests and reviewers read the app-tree copy, but the APK ships the source, so the two must
// stay byte-identical. The file list comes from build.sh's own cp lines.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const app=path.resolve(new URL('..',import.meta.url).pathname);
const root=path.resolve(app,'../..');
const stage=path.join(app,'app');
const vars={ROOT:root,APP:app,STAGE:stage};
const expand=s=>s.replace(/\$(ROOT|APP|STAGE)\b/g,(_,v)=>vars[v]);

// Each source → the app-tree file it replaces in the staged copy.
function stagedCopies(){
 const script=fs.readFileSync(path.join(app,'build.sh'),'utf8');
 const pairs=[];
 const add=(source,dest)=>{
  const target=dest.endsWith('/')?path.join(dest,path.basename(source)):dest;
  pairs.push({source,target});
 };
 for(const line of script.split('\n')){
  // for name in a b; do cp "$APP/dir/$name.js" "$STAGE/assets/"
  const loop=line.match(/^for name in ([\w\s-]+); do/);
  if(loop){
   const body=script.slice(script.indexOf(line)).split('\n')[1].match(/cp "([^"]+)" "([^"]+)"/);
   for(const name of loop[1].trim().split(/\s+/))add(expand(body[1].replace('$name',name)),expand(body[2]));
   continue;
  }
  const cp=line.match(/^cp ((?:"[^"]+"(?:\*\.\w+)?\s+)+)"(\$STAGE[^"]*)"$/);
  if(!cp)continue;
  const dest=expand(cp[2]);
  for(const [,quoted,ext] of cp[1].matchAll(/"([^"]+)"(?:\*(\.\w+))?/g)){
   const source=expand(quoted);
   if(ext)for(const f of fs.readdirSync(source).filter(f=>f.endsWith(ext)))add(path.join(source,f),dest);
   else add(source,dest);
  }
 }
 return pairs;
}

test('build.sh copy list is parsed',()=>{
 const pairs=stagedCopies();
 assert.ok(pairs.length>=10,`expected the staged copies from build.sh, found ${pairs.length}`);
 assert.ok(pairs.some(p=>p.target.endsWith('assets/voice-providers.js')),'voice-providers loop parsed');
 assert.ok(pairs.some(p=>p.target.endsWith('MediaControlRoutine.java')),'media-worker glob parsed');
});

test('every tracked app-tree copy matches the source build.sh ships',()=>{
 const drift=[];
 for(const {source,target} of stagedCopies()){
  // Private generated inputs (connection files) are outside Git and have no app-tree twin.
  if(!fs.existsSync(source)||!fs.existsSync(target))continue;
  if(!fs.readFileSync(source).equals(fs.readFileSync(target)))drift.push(`${path.relative(root,target)} differs from ${path.relative(root,source)}`);
 }
 assert.deepEqual(drift,[],'edit the source module (build.sh copies it over the app tree), then copy it here');
});
