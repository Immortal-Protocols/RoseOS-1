package dev.pixel.voice;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** "Update Rose Agent" directly from public pixel-ota GitHub releases (APP-GITHUB-002): no PixelVoice catalog,
 * pairing, configuration or credential is involved. Pure logic plus the PublicApkDownload.Http seam (no
 * Android types) so discovery, the receipt schema and eligibility are testable.
 *  - Discovery: anonymous GET of the exact release-list API route, at most MAX_PAGES pages of PER_PAGE, direct
 *    200 only. Only published (not draft) prereleases tagged rose-agent-N that carry uploaded
 *    dev.pixel.voice-N.apk and dev.pixel.voice-N.receipt.json are candidates; the newest N is considered.
 *    Errors, rate limits or a list that may continue beyond MAX_PAGES are never reported as "up to date".
 *  - Metadata: the client builds the receipt URL itself (never an API-supplied URL) and reads it through the
 *    same GitHub/CDN boundary as the APK, bounded to MAX_RECEIPT bytes.
 *  - Schema (proposed APP-GITHUB-002-IFACE-001, pending Infra's ACK): the publisher's app-publication receipt
 *    plus schemaVersion 1 and a required compatibility object. Anything missing, unknown or contradictory is
 *    unavailable, never eligible.
 *  - Eligibility: strictly newer integral version, signer equal to the installed signer, exact device codename,
 *    a supported ABI, minimum SDK, minimum Rose OS build and (when required) minimum controller version. */
final class DirectUpdate {
 static final String SELF="dev.pixel.voice",REPOSITORY="Immortal-Protocols/pixel-ota";
 static final String API="https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases?per_page=";
 static final int PER_PAGE=100,MAX_PAGES=3,MAX_PAGE_BYTES=2*1024*1024,MAX_RECEIPT=64*1024;
 static final long MAX_APK=150L*1024*1024;
 static final String UNIVERSAL="universal";
 static final Set<String> ABIS=new HashSet<>(Arrays.asList("arm64-v8a","armeabi-v7a","x86_64","x86"));
 private static final Pattern TAG=Pattern.compile("rose-agent-([1-9][0-9]{0,8})");
 private static final Pattern HEX64=Pattern.compile("[0-9a-f]{64}"),CODENAME=Pattern.compile("[a-z0-9_]{1,32}"),OS_BUILD=Pattern.compile("[0-9]{10}");
 private static final Set<String> RECEIPT_KEYS=new HashSet<>(Arrays.asList("receiptType","schemaVersion","packageName","versionCode","versionName","size","sha256",
  "signerSha256","candidate","minimumTestedOs","repository","tag","asset","apkUrl","prerelease","compatibility"));
 private static final Set<String> COMPATIBILITY_KEYS=new HashSet<>(Arrays.asList("deviceCodenames","abis","minSdk","minOsBuild","minControllerVersion"));

 static String apiUrl(int page){return API+PER_PAGE+"&page="+page;}
 static String tag(long n){return "rose-agent-"+n;}
 static String apkAsset(long n){return SELF+"-"+n+".apk";}
 static String receiptAsset(long n){return SELF+"-"+n+".receipt.json";}
 static String download(long n,String asset){return "https://github.com/"+REPOSITORY+"/releases/download/"+tag(n)+"/"+asset;}

 /** The phone, as observed locally. osBuild is the Rose build number (e.g. 2026102805), or null when it can't
  * be read; controller is the installed controller versionCode, or -1 when absent. */
 static final class Device {
  final String codename;final List<String> abis;final int sdk;final String osBuild;final long controller,installed;final String signerSha256;
  Device(String codename,List<String> abis,int sdk,String osBuild,long controller,long installed,String signerSha256){
   this.codename=codename;this.abis=abis;this.sdk=sdk;this.osBuild=osBuild;this.controller=controller;this.installed=installed;this.signerSha256=signerSha256;}
 }
 /** A validated update offer: everything the installer binds the download to. */
 static final class Offer {
  final long versionCode,size,minSdk,minController;final String versionName,sha256,signerSha256,apkUrl,minOsBuild;final List<String> codenames,abis;
  Offer(long versionCode,String versionName,long size,String sha256,String signerSha256,String apkUrl,List<String> codenames,List<String> abis,long minSdk,String minOsBuild,long minController){
   this.versionCode=versionCode;this.versionName=versionName;this.size=size;this.sha256=sha256;this.signerSha256=signerSha256;this.apkUrl=apkUrl;
   this.codenames=codenames;this.abis=abis;this.minSdk=minSdk;this.minOsBuild=minOsBuild;this.minController=minController;}
 }
 enum State { NO_UPDATE, AVAILABLE, UNAVAILABLE, ERROR }
 static final class Result {
  final State state;final String message;final Offer offer;
  Result(State state,String message,Offer offer){this.state=state;this.message=message;this.offer=offer;}
 }

 /** Newest candidate version on the list pages, and whether the list was read to its end. */
 static final class Discovery { final long newest;final boolean complete; Discovery(long newest,boolean complete){this.newest=newest;this.complete=complete;} }
 static Discovery discover(PublicApkDownload.Http http)throws IOException{
  long newest=-1;
  for(int page=1;page<=MAX_PAGES;page++){
   PublicApkDownload.Response r;
   try{r=http.get(apiUrl(page),Collections.singletonMap("Accept","application/vnd.github+json"));}
   catch(IOException e){throw new IOException("Couldn’t reach GitHub to check for updates ("+e.getClass().getSimpleName()+").");}
   if(r.status==403||r.status==429){close(r);throw new IOException("GitHub is limiting update checks right now. Try again later.");}
   if(r.status!=200){close(r);throw new IOException("GitHub couldn’t list releases ("+r.status+").");}
   Object list;
   try{list=MiniJson.parse(read(r.body,MAX_PAGE_BYTES));}catch(IllegalArgumentException e){throw new IOException("GitHub’s release list couldn’t be read.");}
   if(!(list instanceof List))throw new IOException("GitHub’s release list couldn’t be read.");
   List<?> releases=(List<?>)list;
   for(Object o:releases)newest=Math.max(newest,candidate(o));
   if(releases.size()<PER_PAGE)return new Discovery(newest,true);
  }
  return new Discovery(newest,false);
 }
 /** N for a published rose-agent-N prerelease with both canonical assets uploaded, else -1. */
 static long candidate(Object o){
  if(!(o instanceof Map))return -1;Map<?,?> r=(Map<?,?>)o;
  if(!Boolean.FALSE.equals(r.get("draft"))||!Boolean.TRUE.equals(r.get("prerelease"))||!(r.get("tag_name") instanceof String))return -1;
  Matcher m=TAG.matcher((String)r.get("tag_name"));if(!m.matches())return -1;
  long n=Long.parseLong(m.group(1));
  if(!(r.get("assets") instanceof List))return -1;
  boolean apk=false,receipt=false;
  for(Object a:(List<?>)r.get("assets")){
   if(!(a instanceof Map))continue;Map<?,?> asset=(Map<?,?>)a;
   if(!"uploaded".equals(asset.get("state")))continue;
   if(apkAsset(n).equals(asset.get("name")))apk=true;
   if(receiptAsset(n).equals(asset.get("name")))receipt=true;
  }
  return apk&&receipt?n:-1;
 }

 /** The receipt for release n as a validated Offer; IllegalArgumentException names why it is unusable. */
 static Offer offer(Object json,long n){
  if(!(json instanceof Map))throw new IllegalArgumentException("The release’s update information couldn’t be read.");
  Map<?,?> r=(Map<?,?>)json;
  if(!"app-publication".equals(r.get("receiptType"))||!Long.valueOf(1).equals(r.get("schemaVersion")))
   throw new IllegalArgumentException("This release doesn’t carry update information this version of Rose Agent understands.");
  for(Object k:r.keySet())if(!RECEIPT_KEYS.contains(k))throw new IllegalArgumentException("This release’s update information has fields this version doesn’t understand.");
  if(!SELF.equals(r.get("packageName"))||!Long.valueOf(n).equals(r.get("versionCode")))throw new IllegalArgumentException("The release’s update information names a different app or version.");
  if(!REPOSITORY.equals(r.get("repository"))||!tag(n).equals(r.get("tag"))||!apkAsset(n).equals(r.get("asset"))||!download(n,apkAsset(n)).equals(r.get("apkUrl"))||!Boolean.TRUE.equals(r.get("prerelease")))
   throw new IllegalArgumentException("The release’s update information doesn’t match where it was published.");
  Object name=r.get("versionName");if(!(name instanceof String)||((String)name).isEmpty()||((String)name).length()>64)throw new IllegalArgumentException("The release has no version name.");
  long size=whole(r.get("size"));if(size<=0||size>MAX_APK)throw new IllegalArgumentException("The release’s size is missing or too large.");
  String sha=hex(r.get("sha256")),signer=hex(r.get("signerSha256"));
  if(sha==null||signer==null)throw new IllegalArgumentException("The release’s integrity details are missing.");
  if(!(r.get("compatibility") instanceof Map))throw new IllegalArgumentException("This release doesn’t say which phones it supports.");
  Map<?,?> c=(Map<?,?>)r.get("compatibility");
  for(Object k:c.keySet())if(!COMPATIBILITY_KEYS.contains(k))throw new IllegalArgumentException("This release’s compatibility has requirements this version doesn’t understand.");
  if(!c.keySet().containsAll(COMPATIBILITY_KEYS))throw new IllegalArgumentException("This release doesn’t fully say which phones it supports.");
  List<String> codenames=strings(c.get("deviceCodenames"),CODENAME);if(codenames==null)throw new IllegalArgumentException("This release doesn’t list its supported phones.");
  List<String> abis=strings(c.get("abis"),null);
  if(abis==null||!(abis.equals(Collections.singletonList(UNIVERSAL))||ABIS.containsAll(abis)))throw new IllegalArgumentException("This release doesn’t list its supported processor types.");
  long minSdk=whole(c.get("minSdk"));if(minSdk<1||minSdk>1000)throw new IllegalArgumentException("This release doesn’t give its minimum Android version.");
  Object os=c.get("minOsBuild");if(!(os instanceof String)||!OS_BUILD.matcher((String)os).matches())throw new IllegalArgumentException("This release doesn’t give its minimum Rose OS build.");
  Object ctl=c.get("minControllerVersion");long minController=ctl==MiniJson.NULL?0:whole(ctl);
  if(ctl!=MiniJson.NULL&&minController<1)throw new IllegalArgumentException("This release’s controller requirement is invalid.");
  return new Offer(n,(String)name,size,sha,signer,download(n,apkAsset(n)),codenames,abis,minSdk,(String)os,minController);
 }
 /** null when this phone may install the offer, otherwise the truthful reason it may not. */
 static String incompatibility(Offer o,Device d){
  if(d.codename==null||!o.codenames.contains(d.codename))return "This update isn’t for this phone model.";
  if(!o.abis.contains(UNIVERSAL)&&(d.abis==null||Collections.disjoint(o.abis,d.abis)))return "This update doesn’t support this phone’s processor.";
  if(d.sdk<o.minSdk)return "This update needs a newer Android version.";
  if(d.osBuild==null||!OS_BUILD.matcher(d.osBuild).matches())return "This phone’s Rose OS build couldn’t be read, so the update can’t be checked.";
  if(d.osBuild.compareTo(o.minOsBuild)<0)return "This update needs Rose OS "+o.minOsBuild+" or newer.";
  if(o.minController>0&&d.controller<o.minController)return "This update needs a newer background controller.";
  if(d.signerSha256==null||!d.signerSha256.equals(o.signerSha256))return "This update isn’t signed like the installed Rose Agent.";
  return null;
 }

 /** The whole check: discovery, the newest release's receipt and this phone's eligibility. */
 static Result check(PublicApkDownload.Http http,Device d){
  if(d.installed<0)return new Result(State.ERROR,"Couldn’t read the installed version of Rose Agent.",null);
  Discovery found;
  try{found=discover(http);}catch(IOException e){return new Result(State.ERROR,e.getMessage(),null);}
  if(found.newest<0)return found.complete?new Result(State.UNAVAILABLE,"No Rose Agent update has been published on GitHub.",null)
   :new Result(State.ERROR,"GitHub’s release list was too long to check completely.",null);
  if(found.newest<=d.installed)return found.complete?new Result(State.NO_UPDATE,"Rose Agent is up to date (version "+d.installed+").",null)
   :new Result(State.ERROR,"GitHub’s release list was too long to check completely.",null);
  Offer o;
  try{
   ByteArrayOutputStream body=new ByteArrayOutputStream();
   PublicApkDownload.fetchMetadata(http,download(found.newest,receiptAsset(found.newest)),body,MAX_RECEIPT);
   o=offer(MiniJson.parse(new String(body.toByteArray(),"UTF-8")),found.newest);
  }catch(IOException e){return new Result(State.ERROR,e.getMessage(),null);}
  catch(IllegalArgumentException e){return new Result(State.UNAVAILABLE,e.getMessage()==null||e.getMessage().length()<12?"The release’s update information couldn’t be read.":e.getMessage(),null);}
  String why=incompatibility(o,d);
  if(why!=null)return new Result(State.UNAVAILABLE,"Version "+o.versionCode+" was published, but it can’t be installed: "+why,null);
  return new Result(State.AVAILABLE,"Version "+o.versionCode+" is available (installed "+d.installed+").",o);
 }

 static long whole(Object v){return v instanceof Long?(Long)v:-1;}
 private static String hex(Object v){return v instanceof String&&HEX64.matcher((String)v).matches()?(String)v:null;}
 private static List<String> strings(Object v,Pattern each){
  if(!(v instanceof List)||((List<?>)v).isEmpty()||((List<?>)v).size()>64)return null;
  java.util.ArrayList<String> out=new java.util.ArrayList<>();
  for(Object x:(List<?>)v){if(!(x instanceof String)||(each!=null&&!each.matcher((String)x).matches())||out.contains(x))return null;out.add((String)x);}
  return out;
 }
 private static String read(InputStream in,int max)throws IOException{
  if(in==null)throw new IOException("GitHub returned no release list.");
  try(InputStream body=in){ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[16384];int n;
   while((n=body.read(b))!=-1){if(out.size()+n>max)throw new IOException("GitHub’s release list is too large.");out.write(b,0,n);}
   return new String(out.toByteArray(),"UTF-8");}
  catch(IOException e){throw new IOException(e.getMessage()!=null&&e.getMessage().startsWith("GitHub")?e.getMessage():"GitHub’s release list couldn’t be read.");}
 }
 private static void close(PublicApkDownload.Response r){try{if(r.body!=null)r.body.close();}catch(IOException ignored){}}
}
