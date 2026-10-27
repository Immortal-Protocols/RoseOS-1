package dev.pixel.voice;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.widget.*;
import java.io.*;
import java.net.URL;
import java.security.*;
import java.util.*;
import java.util.zip.*;
import javax.net.ssl.HttpsURLConnection;
import org.json.*;

/** Explicit migration export to the already paired Mac; never a model tool. */
public final class MigrationBackupActivity extends Activity {
 private TextView status;private Button start;private volatile boolean running;private boolean privateOnly;
 private static volatile String lastStatus="Not started";
 public static String status(){return lastStatus;}
 @Override public void onCreate(Bundle state){super.onCreate(state);privateOnly=getIntent().getBooleanExtra("privateOnly",false);
  LinearLayout page=new LinearLayout(this);page.setOrientation(LinearLayout.VERTICAL);page.setPadding(36,48,36,36);
  TextView title=new TextView(this);title.setText("Back up to paired Mac");title.setTextSize(24);page.addView(title);
  TextView scope=new TextView(this);scope.setText(privateOnly?"Exports Notes, Ideas, Daybook, Reminders and Books through temporary read-only providers restricted to our app signing identity. Copies stay on your paired Mac; no model receives them.":"Copies readable shared files and Rose Agent settings over the existing authenticated connection. It does not erase files, upload to a model, or back up other apps’ private data, eSIMs or account sessions. Temporary file access can be removed afterward.");page.addView(scope);
  status=new TextView(this);status.setText(lastStatus);page.addView(status);
  Button access=new Button(this);access.setText("Allow temporary file access");access.setOnClickListener(v->startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,Uri.parse("package:"+getPackageName()))));if(!privateOnly)page.addView(access);
  start=new Button(this);start.setText("Start backup");start.setOnClickListener(v->begin());page.addView(start);setContentView(page);
 }
 void update(String message){lastStatus=message;runOnUiThread(()->status.setText(message));}
 void begin(){
  if(running)return;if(!privateOnly&&!Environment.isExternalStorageManager()){update("Grant temporary file access first.");return;}
  VoiceService service=VoiceService.instance;if(service==null||service.ssl==null){update("Rose Agent connection is unavailable.");return;}
  running=true;start.setEnabled(false);new Thread(()->{
   HttpsURLConnection connection=null;
   try {
    update("Copying to the paired Mac…");
    connection=(HttpsURLConnection)new URL(service.endpoint+"/migration-backup/upload").openConnection();
    connection.setSSLSocketFactory(service.ssl);connection.setHostnameVerifier((h,s)->true);
    connection.setRequestProperty("Content-Type","application/zip");
    connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setChunkedStreamingMode(65536);connection.setConnectTimeout(10000);connection.setReadTimeout(120000);
    MessageDigest archiveHash=MessageDigest.getInstance("SHA-256");JSONArray entries=new JSONArray(),errors=new JSONArray();long[] totals={0,0};
    try(ZipOutputStream zip=new ZipOutputStream(new DigestOutputStream(connection.getOutputStream(),archiveHash))){
     zip.setLevel(1);
     if(!privateOnly){File shared=Environment.getExternalStorageDirectory();File[] children=shared.listFiles();
      if(children==null)throw new IOException("Shared storage is unreadable");
      Arrays.sort(children,Comparator.comparing(File::getName));
      for(File child:children)if(!child.getName().equals("Android"))copy(zip,child,"shared/"+child.getName(),entries,errors,totals);
     }else for(String pkg:new String[]{"dev.pixel.notes","dev.pixel.ideas","dev.pixel.daybook","dev.pixel.reminders","dev.pixel.books"})copyApp(zip,pkg,entries,totals);
     File own=new File(getApplicationInfo().dataDir);
     for(String name:new String[]{"shared_prefs","files","databases"}){File file=new File(own,name);if(file.exists())copy(zip,file,"pixel-voice-private/"+name,entries,errors,totals);}
     JSONArray apps=new JSONArray();for(android.content.pm.PackageInfo app:getPackageManager().getInstalledPackages(0))apps.put(new JSONObject().put("packageName",app.packageName).put("versionCode",app.getLongVersionCode()).put("versionName",String.valueOf(app.versionName)));
     JSONObject manifest=new JSONObject().put("device",android.os.Build.MODEL).put("build",android.os.Build.DISPLAY).put("createdAt",System.currentTimeMillis()).put("files",entries).put("errors",errors).put("apps",apps).put("fileBytes",totals[1]).put("exclusions",new JSONArray(Arrays.asList("Android shared subdirectory (OS access restriction)","Other apps private data","Accounts and Android Keystore","eSIM and Bluetooth pairing state")));
     zip.putNextEntry(new ZipEntry("manifest.json"));zip.write(manifest.toString(2).getBytes("UTF-8"));zip.closeEntry();
    }
    int code=connection.getResponseCode();if(code!=200)throw new IOException("Receiver returned HTTP "+code);
    JSONObject receipt=new JSONObject(VoiceService.read(connection.getInputStream()));String local=VoiceService.hex(archiveHash.digest());
    if(!local.equals(receipt.getString("sha256")))throw new IOException("Archive checksum mismatch");
    String result="Verified on Mac: "+totals[0]+" files, "+totals[1]+" bytes; "+errors.length()+" files skipped. Backup ID: "+receipt.getString("id");
    getSharedPreferences("migration-backup",0).edit().putString("receipt",receipt.toString()).putInt("errors",errors.length()).putLong("files",totals[0]).putLong("completedAt",System.currentTimeMillis()).apply();update(result);
   }catch(Exception e){update("Backup did not complete: "+e.getClass().getSimpleName()+": "+e.getMessage());}
   finally{if(connection!=null)connection.disconnect();running=false;runOnUiThread(()->start.setEnabled(true));}
  },"MigrationBackup").start();
 }
 void copyApp(ZipOutputStream zip,String pkg,JSONArray entries,long[] totals)throws Exception {
  String name="app-private/"+pkg+".zip";MessageDigest hash=MessageDigest.getInstance("SHA-256");long size=0;
  android.os.ParcelFileDescriptor descriptor=getContentResolver().openFileDescriptor(Uri.parse("content://"+pkg+".migrationbackup/snapshot"),"r");
  if(descriptor==null)throw new IOException("No export for "+pkg);
  try(InputStream in=new android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor)){
   zip.putNextEntry(new ZipEntry(name));byte[] buffer=new byte[65536];int n;while((n=in.read(buffer))!=-1){zip.write(buffer,0,n);hash.update(buffer,0,n);size+=n;}zip.closeEntry();
  }
  entries.put(new JSONObject().put("path",name).put("size",size).put("sha256",VoiceService.hex(hash.digest())).put("changedDuringCopy",false));totals[0]++;totals[1]+=size;
 }
 void copy(ZipOutputStream zip,File file,String name,JSONArray entries,JSONArray errors,long[] totals)throws Exception{
  if(java.nio.file.Files.isSymbolicLink(file.toPath())){errors.put(new JSONObject().put("path",name).put("reason","symbolic-link skipped"));return;}
  if(file.isDirectory()){
   File[] children=file.listFiles();if(children==null){errors.put(new JSONObject().put("path",name).put("reason","unreadable directory"));return;}
   Arrays.sort(children,Comparator.comparing(File::getName));for(File child:children)copy(zip,child,name+"/"+child.getName(),entries,errors,totals);return;
  }
  FileInputStream input;try{input=new FileInputStream(file);}catch(IOException e){errors.put(new JSONObject().put("path",name).put("reason","unreadable file"));return;}
  long length=file.length(),modified=file.lastModified(),size=0;MessageDigest hash=MessageDigest.getInstance("SHA-256");
  try(FileInputStream in=input){ZipEntry entry=new ZipEntry(name);entry.setTime(modified);zip.putNextEntry(entry);byte[] buffer=new byte[65536];int n;
   while((n=in.read(buffer))!=-1){zip.write(buffer,0,n);hash.update(buffer,0,n);size+=n;}zip.closeEntry();}
  boolean changed=length!=file.length()||modified!=file.lastModified()||length!=size;
  entries.put(new JSONObject().put("path",name).put("size",size).put("sha256",VoiceService.hex(hash.digest())).put("modifiedAt",modified).put("changedDuringCopy",changed));
  if(changed)errors.put(new JSONObject().put("path",name).put("reason","changed during copy"));totals[0]++;totals[1]+=size;
  if(totals[0]%50==0)update("Copying: "+totals[0]+" files, "+(totals[1]/1048576)+" MiB…");
 }
}
