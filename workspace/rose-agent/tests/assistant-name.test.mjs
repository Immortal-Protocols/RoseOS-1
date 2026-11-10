import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const app=fileURLToPath(new URL('../app/',import.meta.url));
const src=n=>fs.readFileSync(path.join(app,'src/dev/pixel/voice',n+'.java'),'utf8');
const classes=fs.mkdtempSync(path.join(os.tmpdir(),'assistant-name-'));
test.after(()=>fs.rmSync(classes,{recursive:true,force:true}));

test('name validation and agent-facing copy use the saved identity',()=>{
 execFileSync('javac',['-d',classes,path.join(app,'src/dev/pixel/voice/AssistantName.java'),path.join(app,'tests/AssistantNameTest.java')]);
 assert.match(execFileSync('java',['-cp',classes,'dev.pixel.voice.AssistantNameTest'],{encoding:'utf8'}),/checks passed/);
});
test('native event consumer handles hello and live updates without historical or retired-link rollback',()=>{
 const link=src('LocalRoseLink'),identity=src('LocalAssistantIdentity');
 assert.match(link,/final String identityScope = LocalAssistantIdentity.scope\(context\);/);
 assert.match(link,/current != LocalRoseLink.this \|\| !running/);
 assert.match(link,/LocalAssistantIdentity.accept\(context, identityScope, m\);/);
 assert.match(identity,/event.optBoolean\("replay"\)/);
 assert.match(identity,/scope.equals\(scope\(context\)\)/);
 assert.match(identity,/"hello".equals[\s\S]*state.opt\("assistant_name"\)/);
 assert.match(identity,/"assistant_name".equals[\s\S]*event.opt\("name"\)/);
 assert.match(identity,/if \(name == null\) return/);
 assert.match(identity,/putString\("scope", scope\).putString\("name", name\).commit\(\)/);
 assert.match(identity,/scope.isEmpty\(\) \|\| !scope.equals\(prefs.getString\("scope", ""\)\)/);
});
test('conversation identity refreshes from persisted name, leaves package branding and color code alone',()=>{
 const activity=src('RoseConversationActivity');
 assert.match(activity,/String agentName=LocalAssistantIdentity.name\(this\);/);
 assert.match(activity,/agentTitle.setText\(agentName\)/);
 assert.match(activity,/title.setSingleLine\(true\);title.setEllipsize\(android.text.TextUtils.TruncateAt.END\)/);
 assert.match(activity,/hp.rightMargin=dp\(16\+48\+48\)/);
 assert.match(activity,/Open "\+agentName\+"’s background work/);
 assert.match(activity,/AssistantName.inText\(cap,agentName\)/);
 assert.match(activity,/AssistantName.inText\(RosePresenceModel.describe\(visual\),agentName\)/);
 assert.match(activity,/m.getMenu\(\).add\("Update Rose Agent"\)/);
});

test('native identity cache survives recreation and rejects replay, malformed events and another pairing',()=>{
 // Minimal platform adapters exercise the real cache/event consumer on a JVM. No device or network.
 const fixture=path.join(classes,'fixtures');fs.mkdirSync(fixture,{recursive:true});
 const files={
  'Context.java':`package android.content; public abstract class Context { public abstract SharedPreferences getSharedPreferences(String name,int mode); }`,
  'SharedPreferences.java':`package android.content; public interface SharedPreferences { String getString(String key,String fallback); Editor edit(); interface Editor { Editor putString(String key,String value); boolean commit(); } }`,
  'JSONObject.java':`package org.json; public class JSONObject { private final java.util.Map<String,Object> data=new java.util.HashMap<>(); public JSONObject put(String k,Object v){data.put(k,v);return this;} public Object opt(String k){return data.get(k);} public boolean optBoolean(String k){return Boolean.TRUE.equals(opt(k));} public String optString(String k){Object v=opt(k);return v instanceof String?(String)v:"";} public JSONObject optJSONObject(String k){Object v=opt(k);return v instanceof JSONObject?(JSONObject)v:null;} }`,
  'LocalRoseLink.java':`package dev.pixel.voice; class LocalRoseLink { static boolean enabled=true; static boolean enabled(android.content.Context c){return enabled;} }`,
  'LocalRoseConnection.java':`package dev.pixel.voice; class LocalRoseConnection { static class Paired { String pin,deviceId; Paired(String p,String d){pin=p;deviceId=d;} } static Paired current=new Paired("server-a","phone-a"); static Paired paired(android.content.Context c){return current;} }`,
  'IdentityCacheTest.java':`package dev.pixel.voice;
import android.content.*; import org.json.JSONObject; import java.util.*;
public class IdentityCacheTest {
 static int checks; static void eq(Object want,Object actual){checks++;if(!Objects.equals(want,actual))throw new AssertionError(want+" != "+actual);}
 static class Prefs implements SharedPreferences { Map<String,String> values=new HashMap<>(); public String getString(String k,String d){return values.getOrDefault(k,d);} public Editor edit(){return new Editor(){Map<String,String> writes=new HashMap<>();public Editor putString(String k,String v){writes.put(k,v);return this;}public boolean commit(){values.putAll(writes);return true;}};} }
 static class C extends Context { final Map<String,Prefs> disk; C(Map<String,Prefs> d){disk=d;}public SharedPreferences getSharedPreferences(String n,int m){return disk.computeIfAbsent(n,k->new Prefs());} }
 static JSONObject event(String name){return new JSONObject().put("kind","assistant_name").put("name",name);}
 public static void main(String[] args){
  Map<String,Prefs> disk=new HashMap<>(); C c=new C(disk);String scope=LocalAssistantIdentity.scope(c);
  eq("Rose",LocalAssistantIdentity.name(c));
  LocalAssistantIdentity.accept(c,scope,new JSONObject().put("kind","hello").put("state",new JSONObject().put("assistant_name","Nova")));
  eq("Nova",LocalAssistantIdentity.name(c));eq("Nova",LocalAssistantIdentity.name(new C(disk)));
  LocalAssistantIdentity.accept(c,scope,event("Iris").put("replay",true));eq("Nova",LocalAssistantIdentity.name(c));
  LocalAssistantIdentity.accept(c,scope,event("<bad>"));eq("Nova",LocalAssistantIdentity.name(c));
  LocalAssistantIdentity.accept(c,scope,new JSONObject().put("kind","hello").put("state",new JSONObject()));eq("Nova",LocalAssistantIdentity.name(c));
  LocalAssistantIdentity.accept(c,scope,new JSONObject().put("kind","assistant_name").put("name",123));eq("Nova",LocalAssistantIdentity.name(c));
  LocalAssistantIdentity.accept(c,scope,event("Iris"));eq("Iris",LocalAssistantIdentity.name(new C(disk)));
  LocalRoseConnection.current=new LocalRoseConnection.Paired("server-b","phone-b");eq("Rose",LocalAssistantIdentity.name(c));
  LocalAssistantIdentity.accept(c,scope,event("Old"));eq("Rose",LocalAssistantIdentity.name(c));
  String next=LocalAssistantIdentity.scope(c);LocalAssistantIdentity.accept(c,next,event("Luna"));eq("Luna",LocalAssistantIdentity.name(c));
  LocalRoseLink.enabled=false;eq("Rose",LocalAssistantIdentity.name(c));LocalAssistantIdentity.accept(c,next,event("Old"));
  LocalRoseLink.enabled=true;eq("Luna",LocalAssistantIdentity.name(c));
  LocalRoseConnection.current=null;eq("Rose",LocalAssistantIdentity.name(c));
  System.out.println("IdentityCacheTest: "+checks+" checks passed");
 }
}`
 };
 for(const [name,content] of Object.entries(files))fs.writeFileSync(path.join(fixture,name),content);
 execFileSync('javac',['-d',classes,...Object.keys(files).map(n=>path.join(fixture,n)),path.join(app,'src/dev/pixel/voice/AssistantName.java'),path.join(app,'src/dev/pixel/voice/LocalAssistantIdentity.java')]);
 assert.match(execFileSync('java',['-cp',classes,'dev.pixel.voice.IdentityCacheTest'],{encoding:'utf8'}),/14 checks passed/);
});
