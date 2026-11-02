package dev.pixel.voice;

/** Explicit "Update Rose Agent" installation, decided step by step from the existing delivery contract
 * (APP-UPDATER-002-IFACE-001: GET /apps, /apps/{id}/manifest, /apk, optional /idsig over the pinned
 * connection; manifest id = sha256 of the signed APK, packageName, positive whole versionCode, positive
 * size). Pure logic (no Android types) so it is testable. Nothing here grants a permission, changes a
 * signing identity or treats a download, a committed session or a request as an installed update. */
final class SelfUpdate {
 // PackageInstaller.STATUS_* values (stable public Android API).
 static final int PENDING_USER_ACTION=-1,SUCCESS=0,FAILURE=1,BLOCKED=2,ABORTED=3,INVALID=4,CONFLICT=5,STORAGE=6,INCOMPATIBLE=7;

 enum Admission { START, NOT_NEWER, VOICE_BUSY, IN_PROGRESS, NO_PERMISSION }
 /** The owner tapped Install for the offered version. */
 static Admission admit(long offered,long installed,boolean voiceBusy,boolean inProgress,boolean canRequestInstalls){
  if(offered<=0||installed<0||offered<=installed)return Admission.NOT_NEWER;
  if(voiceBusy)return Admission.VOICE_BUSY;
  if(inProgress)return Admission.IN_PROGRESS;
  if(!canRequestInstalls)return Admission.NO_PERMISSION;
  return Admission.START;
 }
 static String admissionText(Admission a){
  switch(a){
   case NOT_NEWER:return "This version isn’t newer than the installed Rose Agent, so it won’t be installed.";
   case VOICE_BUSY:return "A call is active. Install after it ends; nothing will interrupt it.";
   case IN_PROGRESS:return "An installation is already in progress.";
   case NO_PERMISSION:return "Rose Agent isn’t allowed to install apps on this phone, so it can’t install the update. That setting is in Android’s app settings; Rose Agent won’t change it.";
   default:return "Downloading the update…";
  }
 }

 /** The record selected from /apps, its manifest, the downloaded bytes and the archive Android parsed
  * from them must all name the same newer Rose Agent, signed like the installed one. Returns null when
  * they do, otherwise the reason nothing will be installed. */
 static String verify(String selectedId,long selectedVersion,String self,long installed,
                      String manifestId,String manifestSha,String manifestPackage,Object manifestVersion,Object manifestSize,
                      String downloadedSha,long downloadedSize,
                      String archivePackage,long archiveVersion,boolean signedLikeInstalled){
  if(selectedId==null||!selectedId.matches("[a-f0-9]{64}"))return "The update record is invalid.";
  if(!selectedId.equals(manifestId)||!selectedId.equals(manifestSha))return "The update’s details don’t match the version that was offered.";
  if(!self.equals(manifestPackage))return "The update is for a different app.";
  if(UpdateCheck.version(manifestVersion)!=selectedVersion)return "The update’s version changed since it was offered.";
  long size=UpdateCheck.version(manifestSize);if(size<=0)return "The update’s size is missing.";
  if(!selectedId.equals(downloadedSha)||downloadedSize!=size)return "The download didn’t match the published update.";
  // #407: these bytes are the published ones (checked above), so say exactly what Android read from them: an archive it
  // cannot parse, another package and another versionCode are different causes and are never one message.
  if(archivePackage==null)return "Android couldn’t read the downloaded app, so version "+selectedVersion+" wasn’t checked or installed.";
  if(!self.equals(archivePackage))return "The downloaded app is "+archivePackage+", not Rose Agent.";
  if(archiveVersion!=selectedVersion)return "The downloaded app is version "+archiveVersion+", not the offered version "+selectedVersion+".";
  if(!signedLikeInstalled)return "The downloaded app isn’t signed like the installed Rose Agent.";
  if(selectedVersion<=installed)return "Rose Agent is already at this version or newer.";
  return null;
 }

 /** Rechecked immediately before the session is committed: the install replaces this app and would end a call. */
 static String beforeCommit(long target,long installedNow,boolean voiceBusyNow){
  if(installedNow<0||target<=installedNow)return "Rose Agent is already at this version or newer; nothing was installed.";
  if(voiceBusyNow)return "A call started during the download, so the update wasn’t installed. Install again after the call.";
  return null;
 }

 enum Outcome { WAITING_FOR_USER, INSTALLED, CANCELLED, FAILED }
 static Outcome outcome(int status){
  if(status==PENDING_USER_ACTION)return Outcome.WAITING_FOR_USER;
  if(status==SUCCESS)return Outcome.INSTALLED;
  if(status==ABORTED)return Outcome.CANCELLED;
  return Outcome.FAILED;
 }

 /** What the update dialog may say about the last recorded attempt for `target`. Success is believed only
  * from the installed version itself, so a reported or stale success never stands in for this version. */
 static String attemptText(long target,String recorded,long installed){
  if(target<=0||recorded==null||recorded.isEmpty())return "";
  if(installed>=target)return "Rose Agent "+installed+" is installed.";
  switch(recorded){
   case "downloading":return "Downloading version "+target+"…";
   case "committed":return "Waiting for Android to install version "+target+"…";
   case "WAITING_FOR_USER":return "Confirm the installation of version "+target+" in Android.";
   case "CANCELLED":return "Installation of version "+target+" was cancelled.";
   case "INSTALLED":return "Android reported version "+target+" installed, but it isn’t the installed version yet.";
   default:return "Version "+target+" wasn’t installed: "+recorded;
  }
 }
}
