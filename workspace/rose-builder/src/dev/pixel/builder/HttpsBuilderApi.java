package dev.pixel.builder;

import android.content.Context;
import dev.pixel.voice.RoseLink;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLSocketFactory;

/**
 * Project 1: builder API v1 over the paired link (docs/APP-BUILDER.md §3). Every request carries this app's
 * rose-auth-v1 signature over method, path, time and nonce, on TLS pinned to the backend's certificate, to its
 * Tailscale addresses in order (the last one that answered first). A refusal that isn't final is signed afresh once;
 * a final one asks the owner to pair again. Network: never call on the main thread.
 *
 * <p>The library carries each project's active job as {jobId, state}; the job's details (attempt, failure, start)
 * come from GET jobs/<jobId>. After a build ends, the client keeps following the last job it saw for that project, so
 * its result stays visible until the owner asks for something new.
 */
final class HttpsBuilderApi implements BuilderApi {
 static final int MAX_JSON = 1024 * 1024;

 final Pairing pairing;
 final RoseLink.Key key;
 final SSLSocketFactory tls;
 final List<String> hosts;
 /** projectId → the last job this client followed (create, revise, retry, or the library's active job). */
 final Map<String, String> followed = new HashMap<>();

 HttpsBuilderApi(Context c, Pairing pairing) throws java.security.GeneralSecurityException {
  this.pairing = pairing;
  RoseLink.Key k = RoseLink.existingKey();
  if (k == null || !k.deviceId().equals(pairing.deviceId)) throw new java.security.GeneralSecurityException("This phone's pairing key is missing; pair again.");
  this.key = k;
  this.tls = RoseLink.pinned(pairing.pin);
  this.hosts = new ArrayList<>(pairing.hosts);
 }

 /** A non-2xx answer, already classified. */
 static final class Answer extends Exception {
  final BuilderWire.Refusal refusal;
  Answer(BuilderWire.Refusal refusal) { super(refusal.kind.name()); this.refusal = refusal; }
 }

 // ---- calls -----------------------------------------------------------------------------------------------------

 @Override public synchronized List<Project> projects() throws Unreachable {
  List<Project> out = new ArrayList<>();
  for (BuilderWire.LibraryProject lp : library()) {
   if (lp.activeJobId != null) followed.put(lp.project.projectId, lp.activeJobId);
   out.add(lp.project);
  }
  return out;
 }

 @Override public synchronized Project project(String projectId) throws Unreachable {
  for (BuilderWire.LibraryProject lp : library()) {
   if (!lp.project.projectId.equals(projectId)) continue;
   String jobId = lp.activeJobId != null ? lp.activeJobId : followed.get(projectId);
   if (jobId == null) return lp.project;
   followed.put(projectId, jobId);
   BuildJob job;
   try { job = BuilderWire.job(get(BuilderWire.PREFIX + "/jobs/" + BuilderWire.segment(jobId))); }
   catch (Answer a) { followed.remove(projectId); return lp.project; }
   catch (BuilderWire.Malformed e) { throw malformed(); }
   Project p = lp.project;
   return new Project(p.projectId, p.name, p.packageName, p.signerSha256, p.versions, job);
  }
  return null;
 }

 @Override public synchronized Started create(String clientRequestId, String description) throws Unreachable, OutOfScope, Refused {
  BuilderWire.Accepted a = accepted(BuilderWire.PREFIX + "/projects", BuilderWire.createBody(clientRequestId, description), true);
  followed.put(a.projectId, a.jobId);
  return new Started(started(a.projectId), a.notes);
 }

 @Override public synchronized Started revise(String projectId, String clientRequestId, String request) throws Unreachable, OutOfScope, Refused {
  BuilderWire.Accepted a;
  try { a = accepted(BuilderWire.PREFIX + "/projects/" + BuilderWire.segment(projectId) + "/revisions", BuilderWire.reviseBody(clientRequestId, request), false); }
  catch (BuilderWire.Malformed e) { throw malformed(); }
  followed.put(projectId, a.jobId);
  return new Started(started(projectId), a.notes);
 }

 @Override public synchronized CancelStatus cancel(String jobId) throws Unreachable {
  try { return BuilderWire.cancel(post(BuilderWire.PREFIX + "/jobs/" + BuilderWire.segment(jobId) + "/cancel", BuilderWire.emptyBody())); }
  catch (Answer a) { throw unreachable(a); }
  catch (BuilderWire.Malformed e) { throw malformed(); }
 }

 @Override public synchronized int retry(String jobId) throws Unreachable, Refused {
  try { return BuilderWire.retryAttempt(post(BuilderWire.PREFIX + "/jobs/" + BuilderWire.segment(jobId) + "/retry", BuilderWire.emptyBody())); }
  catch (Answer a) {
   if (a.refusal.kind == BuilderWire.Kind.REFUSED) throw new Refused(a.refusal.message == null ? "refused" : a.refusal.message);
   throw unreachable(a);
  } catch (BuilderWire.Malformed e) { throw malformed(); }
 }

 @Override public synchronized void reportInstall(InstallReceipt receipt) throws Unreachable {
  try { post(BuilderWire.PREFIX + "/installs", BuilderWire.receiptBody(receipt)); }
  catch (Answer a) { throw unreachable(a); }
 }

 /** GET artifacts/<sha256> to dest, refusing anything longer than the recorded size; the caller verifies the rest. */
 @Override public void download(String apkSha256, long size, File dest) throws Exception {
  String path = BuilderWire.PREFIX + "/artifacts/" + BuilderWire.segment(apkSha256);
  for (int signed = 0; signed < 2; signed++) {
   for (String host : order()) {
    HttpsURLConnection h = null;
    try {
     h = connect(host, "GET", path);
     int code = h.getResponseCode();
     if (code / 100 != 2) {
      BuilderWire.Refusal r = BuilderWire.refusal(code, Pairing.read(h.getErrorStream(), MAX_JSON));
      if (r.kind == BuilderWire.Kind.RETRY_SIGNED && signed == 0) break;
      throw unreachable(new Answer(r));
     }
     long total = 0;
     try (InputStream in = h.getInputStream(); OutputStream out = new FileOutputStream(dest)) {
      byte[] buf = new byte[65536];
      for (int n; (n = in.read(buf)) > 0; ) {
       total += n;
       if (total > size) throw new IOException("the download is longer than the recorded app");
       out.write(buf, 0, n);
      }
     }
     answered(host);
     return;
    } catch (IOException e) {
     if (e.getMessage() != null && e.getMessage().startsWith("the download is longer")) throw e;
    } finally { if (h != null) h.disconnect(); }
   }
  }
  throw new Unreachable("unreachable");
 }

 /** Level 3 share links come from the Chariot owner's hosting, not builder API v1. */
 @Override public String shareUrl(String projectId, Long versionCode) { return null; }

 // ---- plumbing --------------------------------------------------------------------------------------------------

 private List<BuilderWire.LibraryProject> library() throws Unreachable {
  try { return BuilderWire.library(get(BuilderWire.PREFIX + "/projects")); }
  catch (Answer a) { throw unreachable(a); }
  catch (BuilderWire.Malformed e) { throw malformed(); }
 }

 private Project started(String projectId) throws Unreachable {
  Project p = project(projectId);
  if (p == null) throw new Unreachable("the new project isn't in the library yet");
  return p;
 }

 private BuilderWire.Accepted accepted(String path, String body, boolean create) throws Unreachable, OutOfScope, Refused {
  try { return BuilderWire.accepted(post(path, body), create); }
  catch (Answer a) {
   if (a.refusal.kind == BuilderWire.Kind.OUT_OF_SCOPE) throw new OutOfScope(a.refusal.message, a.refusal.category);
   if (a.refusal.kind == BuilderWire.Kind.REFUSED) throw new Refused(a.refusal.message == null ? "refused" : a.refusal.message);
   throw unreachable(a);
  } catch (BuilderWire.Malformed e) { throw malformed(); }
 }

 private String get(String path) throws Unreachable, Answer { return send("GET", path, null); }
 private String post(String path, String body) throws Unreachable, Answer { return send("POST", path, body); }

 /** One request: each host in order; a non-final signature refusal is signed afresh once. */
 private String send(String method, String path, String body) throws Unreachable, Answer {
  byte[] bytes = body == null ? null : body.getBytes(StandardCharsets.UTF_8);
  Answer last = null;
  for (int signed = 0; signed < 2; signed++) {
   boolean resign = false;
   for (String host : order()) {
    HttpsURLConnection h = null;
    try {
     h = connect(host, method, path);
     if (bytes != null) {
      h.setDoOutput(true);
      h.setRequestProperty("Content-Type", "application/json");
      h.setFixedLengthStreamingMode(bytes.length);
      try (OutputStream out = h.getOutputStream()) { out.write(bytes); }
     }
     int code = h.getResponseCode();
     String text = Pairing.read(code / 100 == 2 ? h.getInputStream() : h.getErrorStream(), MAX_JSON);
     answered(host);
     if (code / 100 == 2) return text;
     last = new Answer(BuilderWire.refusal(code, text));
     if (last.refusal.kind == BuilderWire.Kind.RETRY_SIGNED) { resign = true; break; }
     throw last;
    } catch (IOException e) {
     // Not reachable at this address; the next one may be.
    } finally { if (h != null) h.disconnect(); }
   }
   if (!resign) break;
  }
  if (last != null) throw last;
  throw new Unreachable("unreachable");
 }

 private HttpsURLConnection connect(String host, String method, String path) throws IOException {
  HttpsURLConnection h = Pairing.open(tls, host, pairing.port, path);
  h.setRequestMethod(method);
  try { for (Map.Entry<String, String> e : key.headers(method, path, pairing.pin).entrySet()) h.setRequestProperty(e.getKey(), e.getValue()); }
  catch (java.security.GeneralSecurityException e) { throw new IOException("signing failed"); }
  return h;
 }

 private synchronized List<String> order() { return new ArrayList<>(hosts); }
 private synchronized void answered(String host) { if (!hosts.get(0).equals(host)) { hosts.remove(host); hosts.add(0, host); } }

 private static Unreachable unreachable(Answer a) {
  if (a.refusal.kind == BuilderWire.Kind.PAIR_AGAIN) return new Unreachable("pair again", true);
  return new Unreachable(a.refusal.kind == BuilderWire.Kind.RETRY_SIGNED ? "clock" : "unavailable");
 }
 private static Unreachable malformed() { return new Unreachable("the builder's answer wasn't understood"); }
}
