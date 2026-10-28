package dev.pixel.voice;
import android.app.*;import android.content.*;import android.content.pm.PackageInstaller;import android.net.Uri;import android.provider.Settings;import java.io.*;import java.net.*;import java.security.*;import javax.net.ssl.*;import org.json.JSONObject;
public class Delivery {
 public static void install(VoiceService a,String id,String label,String endpoint,SSLSocketFactory ssl){
 if(!id.matches("[a-f0-9]{64}"))throw new IllegalArgumentException("Invalid app identifier");
 if(!a.getPackageManager().canRequestPackageInstalls()){
  a.pendingInstallId=id;a.pendingInstallLabel=label;
  a.runOnUiThread(()->{a.deliveryStatus("Allow Rose Agent to install apps, then return to continue.");a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+a.getPackageName())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));});return;
 }
 int sessionId=-1;File file=new File(a.getCacheDir(),"delivery.apk"),signature=new File(a.getCacheDir(),"delivery.apk.idsig");
 try{
  a.deliveryStatus("Downloading "+label+"…");
  JSONObject metadata=manifest(endpoint+"/apps/"+id+"/manifest",ssl);
  // Rose Agent itself is installed only through the explicit Update Rose Agent flow (voice idle, identity checked).
  if(a.getPackageName().equals(metadata.optString("packageName")))throw new IOException("Use Update Rose Agent to update Rose Agent");
  long size=download(endpoint+"/apps/"+id+"/apk",ssl,file,id,150L*1024*1024);
  JSONObject v4=metadata.optJSONObject("v4Signature");long signatureSize=0;
  if(v4!=null){String hash=v4.getString("sha256");if(!hash.matches("[a-f0-9]{64}"))throw new IOException("Invalid signature metadata");signatureSize=download(endpoint+"/apps/"+id+"/idsig",ssl,signature,hash,1024*1024);if(signatureSize!=v4.getLong("size"))throw new IOException("Signature size mismatch");}
  PackageInstaller pi=a.getPackageManager().getPackageInstaller();PackageInstaller.SessionParams params=new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);params.setSize(size+signatureSize);params.setAppLabel(label);if(android.os.Build.VERSION.SDK_INT>=31)params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);sessionId=pi.createSession(params);
  try(PackageInstaller.Session session=pi.openSession(sessionId)){stage(session,file,"base.apk");if(v4!=null)stage(session,signature,"base.apk.idsig");}
  Intent callback=new Intent(a,InstallReceiver.class);PendingIntent result=PendingIntent.getBroadcast(a,sessionId,callback,PendingIntent.FLAG_UPDATE_CURRENT|(android.os.Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0));
  a.deliveryStatus("Download verified. Waiting for Android installer…");try(PackageInstaller.Session session=pi.openSession(sessionId)){session.commit(result.getIntentSender());}sessionId=-1;
 }catch(Exception e){a.deliveryStatus("Install failed: "+e.getMessage());if(sessionId>=0)a.getPackageManager().getPackageInstaller().abandonSession(sessionId);}finally{file.delete();signature.delete();}
 }
 /** Explicit self-update (SelfUpdate): the offered record, its manifest, the downloaded bytes and the archive
  * Android parses from them must name the same newer Rose Agent signed like this one; voice must still be idle
  * at commit. Android's own installer confirmation and signature enforcement are unchanged. */
 static void installSelf(VoiceService a,String id,long version,String endpoint,SSLSocketFactory ssl){
  int sessionId=-1;File file=new File(a.getCacheDir(),"self-update.apk"),signature=new File(a.getCacheDir(),"self-update.apk.idsig");
  String self=a.getPackageName();
  try{
   JSONObject m=manifest(endpoint+"/apps/"+id+"/manifest",ssl);
   // Bytes come only from the manifest's public release asset (APP-GITHUB-001), never the pinned /apk route or any fallback.
   String apkUrl=m.optString("apkUrl","");if(apkUrl.isEmpty())throw new IOException("No public download is published for this update, so it can’t be installed here.");
   long size;try(OutputStream out=new FileOutputStream(file)){size=PublicApkDownload.fetch(new PublicHttp(),apkUrl,out,id,UpdateCheck.version(m.opt("size")),150L*1024*1024);} // throws unless SHA-256 == id and the size matches
   android.content.pm.PackageManager pm=a.getPackageManager();
   android.content.pm.PackageInfo archive=pm.getPackageArchiveInfo(file.getPath(),android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES);
   android.content.pm.PackageInfo installed=pm.getPackageInfo(self,android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES);
   String reason=SelfUpdate.verify(id,version,self,installed.getLongVersionCode(),m.optString("id",null),m.optString("sha256",null),m.optString("packageName",null),m.opt("versionCode"),m.opt("size"),
    id,size,archive==null?null:archive.packageName,archive==null?-1:archive.getLongVersionCode(),sameSigners(archive,installed));
   if(reason!=null)throw new IOException(reason);
   JSONObject v4=m.optJSONObject("v4Signature");long signatureSize=0;
   if(v4!=null){String hash=v4.getString("sha256");if(!hash.matches("[a-f0-9]{64}"))throw new IOException("Invalid signature metadata");signatureSize=download(endpoint+"/apps/"+id+"/idsig",ssl,signature,hash,1024*1024);if(signatureSize!=v4.getLong("size"))throw new IOException("Signature size mismatch");}
   PackageInstaller pi=pm.getPackageInstaller();PackageInstaller.SessionParams params=new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);params.setSize(size+signatureSize);params.setAppPackageName(self);if(android.os.Build.VERSION.SDK_INT>=31)params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);sessionId=pi.createSession(params);
   try(PackageInstaller.Session session=pi.openSession(sessionId)){stage(session,file,"base.apk");if(v4!=null)stage(session,signature,"base.apk.idsig");}
   String late=SelfUpdate.beforeCommit(version,a.selfVersion(),a.voiceBusy());
   if(late!=null)throw new IOException(late);
   Intent callback=new Intent(a,InstallReceiver.class).putExtra(InstallReceiver.SELF_TARGET,version);PendingIntent result=PendingIntent.getBroadcast(a,sessionId,callback,PendingIntent.FLAG_UPDATE_CURRENT|(android.os.Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0));
   a.recordSelfUpdate(version,"committed");try(PackageInstaller.Session session=pi.openSession(sessionId)){session.commit(result.getIntentSender());}sessionId=-1;
  }catch(Exception e){a.recordSelfUpdate(version,String.valueOf(e.getMessage()));if(sessionId>=0)a.getPackageManager().getPackageInstaller().abandonSession(sessionId);}finally{file.delete();signature.delete();}
 }
 /** Anonymous public HTTPS for release assets (PublicApkDownload): system CA trust and hostname verification,
  * no client key (unlike VoiceService.ssl and DeviceIdentity.systemTrustedSsl), no redirects followed, no
  * cache, and no cookies. Nothing global is changed; the PixelVoice pin and identity are not involved. */
 static final class PublicHttp implements PublicApkDownload.Http {
  private final SSLSocketFactory tls;
  PublicHttp()throws Exception{
   if(java.net.CookieHandler.getDefault()!=null)throw new IOException("Public download refused: a shared cookie store is installed.");
   SSLContext context=SSLContext.getInstance("TLS");context.init(null,null,null);tls=context.getSocketFactory();
  }
  @Override public PublicApkDownload.Response get(String url,java.util.Map<String,String> headers)throws IOException{
   final HttpsURLConnection c=(HttpsURLConnection)new URL(url).openConnection();
   c.setSSLSocketFactory(tls);c.setHostnameVerifier(HttpsURLConnection.getDefaultHostnameVerifier());
   c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setConnectTimeout(10000);c.setReadTimeout(60000);
   for(java.util.Map.Entry<String,String> h:headers.entrySet())c.setRequestProperty(h.getKey(),h.getValue());
   int status=c.getResponseCode();
   if(status!=200){String location=c.getHeaderField("Location");c.disconnect();return new PublicApkDownload.Response(status,location,null);}
   return new PublicApkDownload.Response(status,null,new FilterInputStream(c.getInputStream()){@Override public void close()throws IOException{try{super.close();}finally{c.disconnect();}}});
  }
 }
 /** Same current signing certificates as the installed app (no rotation is introduced by this flow). */
 static boolean sameSigners(android.content.pm.PackageInfo archive,android.content.pm.PackageInfo installed){
  if(archive==null||archive.signingInfo==null||installed.signingInfo==null)return false;
  java.util.Set<String> a=new java.util.HashSet<>(),b=new java.util.HashSet<>();
  for(android.content.pm.Signature s:archive.signingInfo.getApkContentsSigners())a.add(s.toCharsString());
  for(android.content.pm.Signature s:installed.signingInfo.getApkContentsSigners())b.add(s.toCharsString());
  return !a.isEmpty()&&a.equals(b);
 }
 private static HttpsURLConnection connection(String url,SSLSocketFactory ssl)throws Exception{HttpsURLConnection c=(HttpsURLConnection)new URL(url).openConnection();c.setSSLSocketFactory(ssl);c.setHostnameVerifier((h,s)->true);c.setConnectTimeout(10000);c.setReadTimeout(60000);return c;}
 private static JSONObject manifest(String url,SSLSocketFactory ssl)throws Exception{
  HttpsURLConnection c=connection(url,ssl);
  try{if(c.getResponseCode()!=200)throw new IOException("App manifest unavailable ("+c.getResponseCode()+")");try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[4096];int n;while((n=in.read(b))!=-1){if(out.size()+n>65536)throw new IOException("App manifest too large");out.write(b,0,n);}return new JSONObject(out.toString("UTF-8"));}}finally{c.disconnect();}
 }
 private static long download(String url,SSLSocketFactory ssl,File file,String expected,long maximum)throws Exception{
  HttpsURLConnection c=connection(url,ssl);MessageDigest digest=MessageDigest.getInstance("SHA-256");long size=0;
  try{if(c.getResponseCode()!=200)throw new IOException("Download failed ("+c.getResponseCode()+")");try(InputStream in=c.getInputStream();OutputStream out=new FileOutputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){size+=n;if(size>maximum)throw new IOException("Download is too large");digest.update(b,0,n);out.write(b,0,n);}}}finally{c.disconnect();}
  if(!expected.equals(VoiceService.hex(digest.digest())))throw new IOException("Download verification failed");return size;
 }
 private static void stage(PackageInstaller.Session session,File file,String name)throws Exception{
  try(InputStream in=new FileInputStream(file);OutputStream out=session.openWrite(name,0,file.length())){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)out.write(b,0,n);session.fsync(out);}
 }
}
