package dev.pixel.voice;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import org.json.JSONObject;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

/** Camera borrows physical input; the voice transport and mute intent remain intact. */
public final class CameraMicrophoneProvider extends ContentProvider {
 private static final Object LOCK=new Object();
 private static final Object TRANSITION=new Object();
 private static final ConcurrentHashMap<String,CompletableFuture<JSONObject>> replies=new ConcurrentHashMap<>();
 private static final long LEASE_MS=30000;
 private final Handler main=new Handler(Looper.getMainLooper());
 static boolean suspended(Context c){synchronized(LOCK){return prefs(c).getLong("until",0)>System.currentTimeMillis();}}
 private static SharedPreferences prefs(Context c){return c.getSharedPreferences("camera-microphone-lease",0);}
 @Override public boolean onCreate(){main.postDelayed(new Runnable(){public void run(){expire();main.postDelayed(this,5000);}},5000);return true;}
 private void requireCamera(){PackageManager pm=getContext().getPackageManager();int caller=Binder.getCallingUid();String[] names=pm.getPackagesForUid(caller);if(names==null||names.length!=1||!"dev.pixel.aicamera".equals(names[0])||pm.checkSignatures(Process.myUid(),caller)!=PackageManager.SIGNATURE_MATCH)throw new SecurityException("Only the signed Camera can borrow microphone input");}
 @Override public Bundle call(String method,String id,Bundle extras){
  requireCamera();if(id==null||!id.matches("[0-9a-f-]{36}"))throw new IllegalArgumentException("Lease ID required");
  long identity=Binder.clearCallingIdentity();synchronized(TRANSITION){try{
   boolean release;
   synchronized(LOCK){SharedPreferences p=prefs(getContext());String owner=p.getString("id","");boolean active=p.getLong("until",0)>System.currentTimeMillis();
    if("acquire".equals(method)){if(active&&!owner.equals(id))return result(false,"Microphone is already borrowed");p.edit().putString("id",id).putLong("until",System.currentTimeMillis()+LEASE_MS).commit();release=false;}
    else if("renew".equals(method)){if(!active||!owner.equals(id))return result(false,"Microphone lease expired");p.edit().putLong("until",System.currentTimeMillis()+LEASE_MS).commit();return result(true,"renewed");}
    else if("release".equals(method)){if(!owner.equals(id))return result(!active,"Lease does not match");p.edit().clear().commit();release=true;}
    else throw new IllegalArgumentException("Unknown microphone lease operation");
   }
   JSONObject reply=changeInput(release?false:true);
   if(!reply.optBoolean("ok")&&!release){synchronized(LOCK){if(id.equals(prefs(getContext()).getString("id","")))prefs(getContext()).edit().clear().commit();}requestChange(false,null);}
   Bundle out=new Bundle();out.putString("result",reply.toString());return out;
  }catch(Exception e){if("acquire".equals(method)){synchronized(LOCK){if(id.equals(prefs(getContext()).getString("id","")))prefs(getContext()).edit().clear().commit();}requestChange(false,null);}return result(false,"Voice microphone handoff was not confirmed");}finally{Binder.restoreCallingIdentity(identity);}}
 }
 private static Bundle result(boolean ok,String reason){Bundle b=new Bundle();try{b.putString("result",new JSONObject().put("ok",ok).put("reason",reason).toString());}catch(Exception ignored){}return b;}
 private static JSONObject changeInput(boolean suspend)throws Exception{
  VoiceService service=VoiceService.instance;
  if(service==null)return new JSONObject().put("ok",true).put("suspended",suspend).put("liveTracks",0).put("voiceActive",false);
  if(!service.loaded)throw new IllegalStateException("Voice is still starting");
  String token=UUID.randomUUID().toString();CompletableFuture<JSONObject> future=new CompletableFuture<>();replies.put(token,future);
  try{requestChange(suspend,token);JSONObject r=future.get(4,TimeUnit.SECONDS);if(r.optBoolean("suspended")!=suspend)throw new IllegalStateException("Handoff state does not match");if(suspend&&r.optInt("liveTracks",-1)!=0)throw new IllegalStateException("Voice still owns microphone");return r;}finally{replies.remove(token);}
 }
 private static void requestChange(boolean suspend,String token){VoiceService service=VoiceService.instance;if(service==null)return;service.runOnUiThread(()->{if(service.web!=null)service.web.evaluateJavascript("window.cameraMicrophoneHandoff&&window.cameraMicrophoneHandoff("+JSONObject.quote(token==null?"":token)+","+suspended(service)+")",null);});}
 static void complete(String token,String raw){CompletableFuture<JSONObject> future=replies.get(token);if(future==null||raw==null||raw.length()>2000)return;try{future.complete(new JSONObject(raw));}catch(Exception e){future.completeExceptionally(e);}}
 private void expire(){boolean expired=false;synchronized(LOCK){SharedPreferences p=prefs(getContext());if(p.contains("id")&&p.getLong("until",0)<=System.currentTimeMillis()){p.edit().clear().apply();expired=true;}}if(expired)requestChange(false,null);}
 @Override public Cursor query(Uri u,String[] p,String s,String[] a,String o){throw new UnsupportedOperationException();}
 @Override public String getType(Uri u){return null;}
 @Override public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
 @Override public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
 @Override public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
}
