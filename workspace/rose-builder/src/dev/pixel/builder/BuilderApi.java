package dev.pixel.builder;

import java.io.File;
import java.util.List;

/**
 * Project 1: the phone's view of the Rose build service, mirroring App Core's builder API v1
 * (docs/APP-BUILDER.md §3). App Core owns orchestration, builds, signing, artifacts and the contract; this client only
 * asks and shows. The real transport (rose-auth-v1 signed requests under /builder/v1/, the app pairing as its own
 * device) is not in this module yet: the app runs against {@link FakeBuilderApi} only. Responses may gain fields
 * within v1 and a transport ignores unknown ones; requests carry exactly v1's fields.
 */
interface BuilderApi {
 /** §3.3 version: sourceRevision is the immutable id of its source; the APK is fetched by its SHA-256. */
 final class Version {
  final long versionCode, createdAt; final String versionName, jobId, sourceRevision, apkSha256, notes; final long apkSize;
  Version(long versionCode, String versionName, long createdAt, String jobId, String sourceRevision, String apkSha256, long apkSize, String notes) {
   this.versionCode = versionCode; this.versionName = versionName; this.createdAt = createdAt; this.jobId = jobId;
   this.sourceRevision = sourceRevision; this.apkSha256 = apkSha256; this.apkSize = apkSize; this.notes = notes;
  }
 }
 /** §3.3 project. job: its activeJob (queued or running) from the library, or else the last job this client followed
  * (GET jobs/<jobId>), so a finished build still shows its result. packageName and signerSha256 never change. */
 final class Project {
  final String projectId, name, packageName, signerSha256;
  final List<Version> versions; final BuildJob job;
  Project(String projectId, String name, String packageName, String signerSha256, List<Version> versions, BuildJob job) {
   this.projectId = projectId; this.name = name; this.packageName = packageName; this.signerSha256 = signerSha256;
   this.versions = versions; this.job = job;
  }
  Version latest() { return versions.isEmpty() ? null : versions.get(versions.size() - 1); }
  long newestVersionAt() { long t = 0; for (Version v : versions) t = Math.max(t, v.createdAt); return t; }
 }
 /** §3.1 cancel: ACCEPTED means stopping, not stopped. */
 enum CancelStatus { ACCEPTED, NOT_RUNNING, UNKNOWN_JOB }

 /** §3.5 install receipt: the server says "installed" only from this, never from publishing alone. versionCode is
  * the version the receipt is about; signerSha256 is what the phone has for the package afterwards (null if none). */
 final class InstallReceipt {
  final String projectId, packageName, signerSha256, detail; final long versionCode; final InstallPlan.Receipt status;
  InstallReceipt(String projectId, String packageName, long versionCode, String signerSha256, InstallPlan.Receipt status, String detail) {
   this.projectId = projectId; this.packageName = packageName; this.versionCode = versionCode; this.signerSha256 = signerSha256;
   this.status = status; this.detail = detail;
  }
 }
 /** The builder can't be reached: shown as UNREACHABLE, never as an empty library. */
 final class Unreachable extends Exception { Unreachable(String m) { super(m); } }
 /** The builder answered 400: the request no longer applies (for retry, the job isn't retryable or a newer version
  * exists). The client refreshes and shows the project as it now is. */
 final class Refused extends Exception { Refused(String m) { super(m); } }

 List<Project> projects() throws Unreachable;
 Project project(String projectId) throws Unreachable;
 /** clientRequestId makes it idempotent: a retry with the same id returns the same project and job. */
 Project create(String clientRequestId, String description) throws Unreachable;
 Project revise(String projectId, String clientRequestId, String request) throws Unreachable;
 CancelStatus cancel(String jobId) throws Unreachable;
 /** Fetches one version's APK by SHA-256 over the same signed link (§3.1 artifacts), stopping at its exact size
  * (apk.size, exact bytes): anything longer is refused. */
 void download(String apkSha256, long size, File dest) throws Exception;
 /** POST jobs/<jobId>/retry: a retryable failed job runs again under the same jobId as attempt + 1; anything else is
  * refused by the builder. Returns the job's new attempt. */
 int retry(String jobId) throws Unreachable, Refused;
 void reportInstall(InstallReceipt receipt) throws Unreachable;
 /** Public download link for sharing (Level 3, the Chariot owner's hosting; not part of builder API v1): one
  * immutable per version, or the latest when versionCode is null. Null until hosting exists. */
 String shareUrl(String projectId, Long versionCode) throws Unreachable;
}
