package dev.pixel.voice;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Arrays;
import java.util.Map;

/** Test-only (APP-IDSIG-1030-M005): runs the app's own DirectUpdate.check() and, when it offers an update, its APK and
 * v4 signature fetchers, against one rose-agent-N prerelease made of the publisher's generated files in a directory.
 * GitHub is a local stand-in: the release list names exactly the files present (all "uploaded"), and every canonical
 * download URL answers 200 with that file's bytes. Arguments: assetsDir versionCode codename abis(comma) sdk
 * osBuild(- for unreadable) controller installed signerSha256. Prints one line: the result state and message, then
 * FETCHED apkSize idsigSize when both downloads verified against the receipt, or FETCH-FAILED reason. */
final class DirectUpdateCheckProbe {
 public static void main(String[] a)throws Exception{
  Path dir=Paths.get(a[0]);long n=Long.parseLong(a[1]);
  StringBuilder assets=new StringBuilder();
  for(Path p:Files.list(dir).sorted().toArray(Path[]::new)){
   if(assets.length()>0)assets.append(',');
   assets.append("{\"name\":\"").append(p.getFileName()).append("\",\"state\":\"uploaded\"}");
  }
  byte[] list=("[{\"tag_name\":\"rose-agent-"+n+"\",\"draft\":false,\"prerelease\":true,\"assets\":["+assets+"]}]").getBytes("UTF-8");
  PublicApkDownload.Http http=(url,headers)->{
   if(url.equals(DirectUpdate.apiUrl(1)))return new PublicApkDownload.Response(200,null,new ByteArrayInputStream(list));
   String prefix=DirectUpdate.download(n,"");
   if(url.startsWith(prefix)){
    Path f=dir.resolve(url.substring(prefix.length()));
    if(Files.isRegularFile(f))return new PublicApkDownload.Response(200,null,new ByteArrayInputStream(Files.readAllBytes(f)));
   }
   return new PublicApkDownload.Response(404,null,null);
  };
  DirectUpdate.Device d=new DirectUpdate.Device(a[2],Arrays.asList(a[3].split(",")),Integer.parseInt(a[4]),"-".equals(a[5])?null:a[5],
   Long.parseLong(a[6]),Long.parseLong(a[7]),a[8]);
  DirectUpdate.Result r=DirectUpdate.check(http,d);
  StringBuilder out=new StringBuilder(r.state+" "+r.message);
  if(r.offer!=null&&r.state==DirectUpdate.State.AVAILABLE){
   try{
    long apk=PublicApkDownload.fetch(http,r.offer.apkUrl,new ByteArrayOutputStream(),r.offer.sha256,r.offer.size,DirectUpdate.MAX_APK);
    long idsig=PublicApkDownload.fetchSignature(http,r.offer.idsigUrl,new ByteArrayOutputStream(),r.offer.idsigSha256,r.offer.idsigSize,DirectUpdate.MAX_IDSIG);
    out.append(" | FETCHED ").append(apk).append(' ').append(idsig);
   }catch(IOException e){out.append(" | FETCH-FAILED ").append(e.getMessage());}
  }
  System.out.println(out);
 }
}
