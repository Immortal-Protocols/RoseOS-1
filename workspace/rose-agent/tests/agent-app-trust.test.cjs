const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{execFileSync}=require('node:child_process');
test('agent bridges keep exact OS package and single certificate trust boundaries',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-agent-trust-'));
 try{
  const java=path.resolve(__dirname,'../../tools/jdk/Contents/Home/bin');
  fs.writeFileSync(path.join(dir,'TrustTest.java'),`package dev.pixel.voice;
public class TrustTest {
 static void check(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  String pin=AgentAppTrust.CONTACTS_CERT;
  String gallery=AgentAppTrust.GALLERY_CERT;
  check(AgentAppTrust.allows("dev.pixel.notes.agent",true,null));
  check(AgentAppTrust.allows("com.android.contacts",false,new String[]{pin}));
  check(AgentAppTrust.allows("com.android.gallery3d",false,new String[]{gallery}));
  check(!AgentAppTrust.allows("com.android.contacts",false,new String[]{gallery}));
  check(!AgentAppTrust.allows("com.android.gallery3d",false,new String[]{pin}));
  check(!AgentAppTrust.allows("com.android.settings",false,new String[]{pin}));
  check(!AgentAppTrust.allows("evil.com.android.contacts",false,new String[]{pin}));
  check(!AgentAppTrust.allows("com.android.contacts",false,new String[]{"wrong"}));
  check(!AgentAppTrust.allows("com.android.contacts",false,new String[]{pin,"wrong"}));
  check(!AgentAppTrust.allows("com.android.contacts",false,null));
  check(!AgentAppTrust.allows("com.android.gallery3d",false,new String[]{}));
  check(AgentAppTrust.allows("dev.pixel.sky",false,new String[]{AgentAppTrust.SKY_CERT}));
  check(!AgentAppTrust.allows("dev.pixel.sky",false,new String[]{pin}));
  check(!AgentAppTrust.allows("dev.pixel.sky.other",false,new String[]{AgentAppTrust.SKY_CERT}));
  System.out.println("passed");
 }} `);
  execFileSync(path.join(java,'javac'),['-d',dir,path.resolve(__dirname,'../app/src/dev/pixel/voice/AgentAppTrust.java'),path.join(dir,'TrustTest.java')]);
  assert.equal(execFileSync(path.join(java,'java'),['-cp',dir,'dev.pixel.voice.TrustTest'],{encoding:'utf8'}).trim(),'passed');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
