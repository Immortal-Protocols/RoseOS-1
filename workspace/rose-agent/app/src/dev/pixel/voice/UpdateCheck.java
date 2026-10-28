package dev.pixel.voice;

import java.util.List;

/** Decides what "Update Rose Agent" may truthfully say from one authenticated GET /apps.
 * Pure logic (no Android types) so it is testable. It never installs anything: an available version
 * is only reported, and a version read failure or unreadable publication is never "no update".
 * Record fields follow delivery/publish.mjs: id (APK SHA-256), label, packageName, versionCode, sha256. */
final class UpdateCheck {
 enum State { NO_UPDATE, AVAILABLE, UNAVAILABLE, ERROR }
 /** One /apps entry as parsed from JSON; versionCode is kept raw so its type can be checked. */
 static final class Record {
  final String id,label,packageName,sha256;final Object versionCode;
  Record(String id,String label,String packageName,Object versionCode,String sha256){this.id=id;this.label=label;this.packageName=packageName;this.versionCode=versionCode;this.sha256=sha256;}
 }
 final State state;final long offered,installed;final String message;
 private UpdateCheck(State state,long offered,long installed,String message){this.state=state;this.offered=offered;this.installed=installed;this.message=message;}

 /** status: HTTP status, or 0 when the request failed before a response. apps: parsed entries, or
  * null when the body was not a readable {apps:[...]} object. installed: this app's versionCode, or
  * negative when it could not be read. voiceBusy: a voice session is active or connecting. */
 static UpdateCheck evaluate(int status,List<Record> apps,long installed,String self,boolean voiceBusy){
  if(status!=200)return error(installed,status==0?"Couldn’t reach the server. Try again.":"The server couldn’t check for updates ("+status+"). Try again.");
  if(apps==null)return error(installed,"Update information couldn’t be read. Try again.");
  if(installed<0)return error(installed,"Couldn’t read the installed version of Rose Agent.");
  Record mine=null;
  for(Record r:apps)if(self.equals(r.packageName)){mine=r;break;}
  if(mine==null)return new UpdateCheck(State.UNAVAILABLE,-1,installed,"No Rose Agent update has been published for this phone.");
  long offered=version(mine.versionCode);
  boolean hash=mine.id!=null&&mine.id.matches("[a-f0-9]{64}")&&mine.id.equals(mine.sha256);
  if(offered<=0||!hash)return new UpdateCheck(State.UNAVAILABLE,-1,installed,"The published Rose Agent update record is incomplete, so it can’t be offered.");
  if(offered<=installed)return new UpdateCheck(State.NO_UPDATE,offered,installed,"Rose Agent is up to date (version "+installed+").");
  return new UpdateCheck(State.AVAILABLE,offered,installed,"Version "+offered+" is available (installed "+installed+"). Installing from here isn’t enabled yet"+(voiceBusy?"; nothing will interrupt this call.":"."));
 }
 private static UpdateCheck error(long installed,String message){return new UpdateCheck(State.ERROR,-1,installed,message);}
 /** A positive whole versionCode; anything else (fraction, string, missing, overflow) is invalid. */
 static long version(Object v){
  if(v instanceof Integer||v instanceof Long)return ((Number)v).longValue();
  if(v instanceof Double){double d=(Double)v;return d>0&&d==Math.rint(d)&&d<9e15?(long)d:-1;}
  return -1;
 }
}
