package dev.pixel.voice;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.Map;
import java.util.regex.Pattern;

/** Public APK bytes for an explicit self-update (APP-GITHUB-001-IFACE-001). The authenticated PixelVoice
 * manifest names the asset (apkUrl); the bytes come from GitHub Releases over a separate anonymous
 * transport. The boundary is compiled in and never widened at runtime:
 *  - initial: https://github.com/Immortal-Protocols/pixel-ota/releases/download/{versioned-tag}/{asset}.apk
 *    (port absent or 443; no userinfo, query, fragment, latest alias, encoding, dot segments or backslashes);
 *  - then a direct 200, or exactly one 302 to
 *    https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/{asset-id}
 *    followed by a 200. Any other status, host, repository, scheme or a second redirect fails closed.
 * The CDN's redirect query is opaque and transient: it is used for that one request and never appears in
 * an error, a log or stored state. Requests carry no headers at all (no credentials, cookies or referer).
 * The SHA-256 and exact size bind the bytes to the selected record. Pure logic plus an Http seam (no
 * Android types) so the policy and the download loop are testable. */
final class PublicApkDownload {
 static final String INITIAL_HOST="github.com",CDN_HOST="release-assets.githubusercontent.com";
 static final String RELEASES="/Immortal-Protocols/pixel-ota/releases/download/",CDN_PATH="/github-production-release-asset/1430356511/";
 private static final Pattern TAG=Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,99}"),ASSET=Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,150}\\.apk"),ASSET_ID=Pattern.compile("[0-9]{1,20}");

 /** One anonymous GET without following redirects; status, Location (for a redirect) and the body. */
 interface Http { Response get(String url,Map<String,String> headers)throws IOException; }
 static final class Response {
  final int status;final String location;final InputStream body;
  Response(int status,String location,InputStream body){this.status=status;this.location=location;this.body=body;}
 }

 /** null when url is an allowed initial release-asset URL, otherwise why not. */
 static String initial(String url){
  URI u=parse(url);if(u==null)return "The update’s download address is invalid.";
  if(!"https".equals(u.getScheme())||!INITIAL_HOST.equals(u.getRawAuthority()==null?null:host(u)))return "The update isn’t published on the expected GitHub repository.";
  if(u.getRawUserInfo()!=null||u.getRawQuery()!=null||u.getRawFragment()!=null||!port(u))return "The update’s download address isn’t a plain release-asset address.";
  String path=u.getRawPath();
  if(path==null||!path.startsWith(RELEASES))return "The update isn’t published on the expected GitHub repository.";
  String[] parts=path.substring(RELEASES.length()).split("/",-1);
  if(parts.length!=2||!TAG.matcher(parts[0]).matches()||!ASSET.matcher(parts[1]).matches())return "The update’s release asset address isn’t supported.";
  if("latest".equalsIgnoreCase(parts[0])||!parts[0].matches(".*[0-9].*")||parts[0].matches("[.]+")||parts[1].startsWith("."))return "The update must name a versioned release, not a moving one.";
  return null;
 }
 /** null when location is the one allowed CDN redirect target, otherwise why not. Its query is not inspected or kept. */
 static String redirect(String location){
  URI u=parse(location);if(u==null)return "The download was redirected to an invalid address.";
  if(!"https".equals(u.getScheme())||!CDN_HOST.equals(u.getRawAuthority()==null?null:host(u))||u.getRawUserInfo()!=null||u.getRawFragment()!=null||!port(u))return "The download was redirected somewhere unexpected.";
  String path=u.getRawPath();
  if(path==null||!path.startsWith(CDN_PATH)||!ASSET_ID.matcher(path.substring(CDN_PATH.length())).matches())return "The download was redirected somewhere unexpected.";
  return null;
 }
 private static URI parse(String s){
  if(s==null||s.isEmpty()||s.length()>4096||s.indexOf('\\')>=0||s.chars().anyMatch(c->c<0x21||c>0x7e))return null;
  try{URI u=new URI(s);return u.isAbsolute()&&!u.isOpaque()?u:null;}catch(Exception e){return null;}
 }
 private static String host(URI u){String h=u.getHost();return h==null?null:h;}
 private static boolean port(URI u){
  String a=u.getRawAuthority();if(a==null)return false;
  int colon=a.lastIndexOf(':');if(colon<0)return u.getPort()==-1;
  return a.substring(colon+1).equals("443")&&u.getPort()==443;
 }

 /** Downloads url into out under the boundary above; returns the byte count once the SHA-256 equals
  * expectedSha and the size equals expectedSize. Every failure message is free of URLs and queries. */
 static long fetch(Http http,String url,OutputStream out,String expectedSha,long expectedSize,long maximum)throws IOException{
  String bad=initial(url);if(bad!=null)throw new IOException(bad);
  if(expectedSize<=0||expectedSize>maximum)throw new IOException("The update’s size is missing or too large.");
  Map<String,String> none=Collections.emptyMap();
  Response r=get(http,url,none);
  if(r.status==302){
   close(r);String next=redirect(r.location);if(next!=null)throw new IOException(next);
   r=get(http,r.location,none);
   if(r.status>=300&&r.status<400){close(r);throw new IOException("The download was redirected more than once.");}
  }else if(r.status>=300&&r.status<400){close(r);throw new IOException("The download was redirected in an unsupported way ("+r.status+").");}
  if(r.status!=200){close(r);throw new IOException("Download failed ("+r.status+").");}
  MessageDigest digest;try{digest=MessageDigest.getInstance("SHA-256");}catch(Exception e){throw new IOException("SHA-256 unavailable");}
  long size=0;
  try(InputStream in=r.body){
   if(in==null)throw new IOException("The download was empty.");
   byte[] b=new byte[65536];int n;
   while((n=read(in,b))!=-1){size+=n;if(size>expectedSize)throw new IOException("The download is larger than the published update.");digest.update(b,0,n);out.write(b,0,n);}
  }
  if(size!=expectedSize)throw new IOException("The download didn’t match the published update’s size.");
  if(!hex(digest.digest()).equals(expectedSha))throw new IOException("The download didn’t match the published update.");
  return size;
 }
 /** Transport errors are reported by kind only: their messages can name the (query-bearing) address. */
 private static Response get(Http http,String url,Map<String,String> headers)throws IOException{
  try{return http.get(url,headers);}catch(IOException e){throw new IOException("Couldn’t reach the update download ("+e.getClass().getSimpleName()+").");}
 }
 private static int read(InputStream in,byte[] b)throws IOException{
  try{return in.read(b);}catch(IOException e){throw new IOException("The update download was interrupted ("+e.getClass().getSimpleName()+").");}
 }
 private static void close(Response r){try{if(r.body!=null)r.body.close();}catch(IOException ignored){}}
 static String hex(byte[] a){StringBuilder s=new StringBuilder();for(byte b:a)s.append(String.format("%02x",b));return s.toString();}
}
