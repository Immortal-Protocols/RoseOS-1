const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{execFileSync}=require('node:child_process');
test('native cache expires at five minutes, ignores positional noise, and bounds refresh retries',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-location-policy-'));
 try{
  const java=path.resolve(__dirname,'../../tools/jdk/Contents/Home/bin');
  fs.writeFileSync(path.join(dir,'PolicyTest.java'),`package dev.pixel.voice;
public class PolicyTest { public static void main(String[] args){
 if(LocationCachePolicy.stale(300000)||!LocationCachePolicy.stale(300001))throw new AssertionError("age");
 if(LocationCachePolicy.moved(550,50,50)||!LocationCachePolicy.moved(601,50,50))throw new AssertionError("accuracy");
 if(LocationCachePolicy.shouldRefresh(true,100,40000,1))throw new AssertionError("fresh");
 if(LocationCachePolicy.shouldRefresh(false,999999,20000,10000))throw new AssertionError("retry");
 if(!LocationCachePolicy.shouldRefresh(false,0,40000,10000))throw new AssertionError("missing");
 if(LocationCachePolicy.ageMs(9000000,10000000)!=0)throw new AssertionError("clock");
 System.out.println("passed"); }} `);
  execFileSync(path.join(java,'javac'),['-d',dir,path.resolve(__dirname,'../app/src/dev/pixel/voice/LocationCachePolicy.java'),path.join(dir,'PolicyTest.java')]);
  assert.equal(execFileSync(path.join(java,'java'),['-cp',dir,'dev.pixel.voice.PolicyTest'],{encoding:'utf8'}).trim(),'passed');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
