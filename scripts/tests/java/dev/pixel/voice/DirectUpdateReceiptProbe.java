package dev.pixel.voice;

import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Arrays;

/** Test-only: runs the app's own DirectUpdate receipt parser and eligibility check on a receipt file, so the
 * publisher's output is checked by the consumer it is written for (INFRA-APP-102). Arguments: receipt versionCode
 * codename abis(comma) sdk osBuild(- for unreadable) controller installed signerSha256. Prints one line:
 * ELIGIBLE versionCode sha256 size abis minSdk minOsBuild minController, INCOMPATIBLE reason or UNAVAILABLE reason. */
final class DirectUpdateReceiptProbe {
 public static void main(String[] a)throws Exception{
  String json=new String(Files.readAllBytes(Paths.get(a[0])),"UTF-8");
  DirectUpdate.Offer o;
  try{o=DirectUpdate.offer(MiniJson.parse(json),Long.parseLong(a[1]));}
  catch(IllegalArgumentException e){System.out.println("UNAVAILABLE "+e.getMessage());return;}
  DirectUpdate.Device d=new DirectUpdate.Device(a[2],Arrays.asList(a[3].split(",")),Integer.parseInt(a[4]),"-".equals(a[5])?null:a[5],
   Long.parseLong(a[6]),Long.parseLong(a[7]),a[8]);
  String why=DirectUpdate.incompatibility(o,d);
  if(why!=null){System.out.println("INCOMPATIBLE "+why);return;}
  StringBuilder abis=new StringBuilder();for(String x:o.abis)abis.append(abis.length()>0?",":"").append(x);
  System.out.println("ELIGIBLE "+o.versionCode+" "+o.sha256+" "+o.size+" "+abis+" "+o.minSdk+" "+o.minOsBuild+" "+o.minController);
 }
}
