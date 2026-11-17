package dev.pixel.builder;

import java.io.File;
import java.util.List;

/**
 * Project 1: the phone's view of the Rose build service, mirroring App Core's builder API v1
 * (docs/APP-BUILDER.md §3). App Core owns orchestration, builds, signing, artifacts and the contract; this client only
 * asks and shows. {@link HttpsBuilderApi} is the real transport (rose-auth-v1 signed requests under /builder/v1/ over
 * pinned TLS, this app paired as its own device); {@link FakeBuilderApi} is the labelled demo. Responses may gain fields
 * within v1 and a transport ignores unknown ones; requests carry exactly v1's fields.
 */
interface BuilderApi {
 /** §3.3 version: sourceRevision is the immutable id of its source; the APK is fetched by its SHA-256. */
 final class Version {
  final long versionCode, createdAt; final String versionName, jobId, sourceRevision, apkSha256, notes; final long apkSize;
  /** §3.3 advisory API level summary: never null (absent reads as none), never a pass, never a gate. */
  final ApiReport apiReport;
  /** Level 3: the public links the builder recorded for this version; never null (absent reads as unpublished). */
  final ShareState share;
  /** Slice 9: a client-server version's server summary; never null (absent for a plain version), never a gate. */
  final ServerSummary server;
  /** This version's server set up in the project's agent container (versions[].hosting); never null (absent unless
   *  this is the version whose server is set up there), never a gate. */
  final Hosting hosting;
  Version(long versionCode, String versionName, long createdAt, String jobId, String sourceRevision, String apkSha256, long apkSize, String notes) {
   this(versionCode, versionName, createdAt, jobId, sourceRevision, apkSha256, apkSize, notes, ApiReport.none(), ShareState.unpublished());
  }
  Version(long versionCode, String versionName, long createdAt, String jobId, String sourceRevision, String apkSha256, long apkSize, String notes,
    ApiReport apiReport, ShareState share) {
   this(versionCode, versionName, createdAt, jobId, sourceRevision, apkSha256, apkSize, notes, apiReport, share, ServerSummary.absent());
  }
  Version(long versionCode, String versionName, long createdAt, String jobId, String sourceRevision, String apkSha256, long apkSize, String notes,
    ApiReport apiReport, ShareState share, ServerSummary server) {
   this(versionCode, versionName, createdAt, jobId, sourceRevision, apkSha256, apkSize, notes, apiReport, share, server, Hosting.absent());
  }
  Version(long versionCode, String versionName, long createdAt, String jobId, String sourceRevision, String apkSha256, long apkSize, String notes,
    ApiReport apiReport, ShareState share, ServerSummary server, Hosting hosting) {
   this.hosting = hosting == null ? Hosting.absent() : hosting;
   this.versionCode = versionCode; this.versionName = versionName; this.createdAt = createdAt; this.jobId = jobId;
   this.sourceRevision = sourceRevision; this.apkSha256 = apkSha256; this.apkSize = apkSize; this.notes = notes;
   this.apiReport = apiReport == null ? ApiReport.none() : apiReport;
   this.share = share == null ? ShareState.unpublished() : share;
   this.server = server == null ? ServerSummary.absent() : server;
  }
 }
 /** §3.3 project, re-read from the library on every refresh (signerSha256 is null until the first signed build and
  * fixed after, so a null from an early read is never kept). job: its activeJob (queued or running) from the library, or else the last job this client followed
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
 final class Unreachable extends Exception {
  /** The builder no longer knows this phone (a final 401): only pairing again helps. */
  final boolean pairAgain;
  Unreachable(String m) { this(m, false); }
  Unreachable(String m, boolean pairAgain) { super(m); this.pairAgain = pairAgain; }
 }
 /** The builder answered 400: the request no longer applies (for retry, the job isn't retryable or a newer version
  * exists). The client refreshes and shows the project as it now is. */
 final class Refused extends Exception { Refused(String m) { super(m); } }

 List<Project> projects() throws Unreachable;
 Project project(String projectId) throws Unreachable;
 /** An accepted create or revise: the project with its new job, and the builder's notes (§3.6, often empty), plain
  * sentences explaining what happens instead when the request named a power, library or server generated apps never
  * get. */
 final class Started {
  final Project project; final List<String> notes;
  Started(Project project, List<String> notes) { this.project = project; this.notes = notes; }
 }
 /** A 400 carrying a legacy refusal code (out_of_scope or review_required). The builder imposes no rules on what
  * people ask for and no longer sends either code (#782); the client still tolerates one, showing the builder's own
  * sentence as plain text, never treating it as success or retrying it. */
 final class OutOfScope extends Exception {
  final String category;
  OutOfScope(String message, String category) { super(message); this.category = category; }
 }

 /** clientRequestId makes it idempotent: a retry with the same id returns the same project and job. */
 Started create(String clientRequestId, String description) throws Unreachable, OutOfScope, Refused;
 Started revise(String projectId, String clientRequestId, String request) throws Unreachable, OutOfScope, Refused;
 CancelStatus cancel(String jobId) throws Unreachable;
 /** Fetches one version's APK by SHA-256 over the same signed link (§3.1 artifacts), stopping at its exact size
  * (apk.size, exact bytes): anything longer is refused. */
 void download(String apkSha256, long size, File dest) throws Exception;
 /** POST jobs/<jobId>/retry: a retryable failed job runs again under the same jobId as attempt + 1; anything else is
  * refused by the builder. Returns the job's new attempt. */
 int retry(String jobId) throws Unreachable, Refused;
 void reportInstall(InstallReceipt receipt) throws Unreachable;

 /** The creator's one-time invite to their own client-server app: a credential, held only in memory, handed straight
  * to the installed app and never shown, stored or logged. toString never includes the token. */
 final class Invite {
  final String projectId, account, token; final long versionCode, expiresInS;
  Invite(String projectId, long versionCode, String account, String token, long expiresInS) {
   this.projectId = projectId; this.versionCode = versionCode; this.account = account; this.token = token; this.expiresInS = expiresInS;
  }
  @Override public String toString() { return "Invite(" + account + ", <redacted>)"; }
 }
 /** The builder didn't issue an invite: its reason word (invalid_account, not_hosted, open_admission, already_enrolled,
  * server_unavailable, not_found, or another it may add) and whether asking again may help. */
 final class InviteRefused extends Exception {
  final String word; final boolean retryable;
  InviteRefused(String word, boolean retryable) { super(word); this.word = word; this.retryable = retryable; }
 }
 /** POST projects/<projectId>/invite {account}: a fresh single-use invite from the project's set-up backend. */
 Invite invite(String projectId, String account) throws Unreachable, InviteRefused;
}
