package dev.pixel.builder;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * An in-memory builder for developing the screens before App Core's service exists. Each poll moves a job one stage;
 * a cancel is confirmed on the next poll; a project's third build is interrupted once, as after a builder restart. It
 * produces no APKs (download always fails) and has no share links. Nothing here touches the network, a server or
 * signing material.
 */
final class FakeBuilderApi implements BuilderApi {
 private final Map<String, Project> projects = new LinkedHashMap<>();
 private final Map<String, String[]> requests = new HashMap<>();
 private final Set<String> stopping = new HashSet<>(), interrupted = new HashSet<>();
 final List<InstallReceipt> receipts = new ArrayList<>();
 private int next = 1;
 /** The fake's clock: one tick per event, so start and delivery order is exact. */
 private long clock = 1;

 @Override public synchronized List<Project> projects() {
  List<Project> out = new ArrayList<>();
  for (String id : new ArrayList<>(projects.keySet())) out.add(project(id));
  return out;
 }

 @Override public synchronized Project project(String id) {
  Project p = projects.get(id);
  if (p == null || p.job == null || p.job.terminal()) return p;
  BuildJob.Stage s = stopping.remove(p.job.jobId) ? BuildJob.Stage.CANCELLED : BuildJob.Stage.values()[p.job.stage.ordinal() + 1];
  List<Version> versions = p.versions;
  BuildJob job;
  if (s == BuildJob.Stage.READY && p.versions.size() == 2 && interrupted.add(id)) {
   job = new BuildJob(p.job.jobId, BuildJob.Stage.FAILED, null, new BuildJob.Failure(BuildJob.Category.INTERNAL, "interrupted; outcome unknown", true), p.job.attempt, p.job.startedAt);
  } else {
   if (s == BuildJob.Stage.READY) {
    versions = new ArrayList<>(p.versions);
    long code = versions.size() + 1;
    versions.add(new Version(code, "1." + (code - 1), clock++, p.job.jobId, "fake-" + code, null, 0, null));
   }
   job = new BuildJob(p.job.jobId, s, null, null, p.job.attempt, p.job.startedAt);
  }
  Project q = new Project(p.projectId, p.name, p.packageName, p.signerSha256, versions, job);
  projects.put(id, q);
  return q;
 }

 /** The fake's stand-in for App Core's screening: a request naming "spyware" is refused, and one asking for Kotlin
  * gets a note. App Core's real screening is server-side; this only drives the screens. */
 static void screen(String text) throws OutOfScope {
  if (text.toLowerCase(java.util.Locale.ROOT).contains("spyware"))
   throw new OutOfScope("Rose won't build an app that secretly watches someone. Nothing was started.", "harm");
 }

 static List<String> notes(String text) {
  List<String> n = new ArrayList<>();
  if (text.toLowerCase(java.util.Locale.ROOT).contains("kotlin")) n.add("Generated apps are written in Java with Android's own features, so Kotlin isn't used.");
  return n;
 }

 @Override public synchronized Started create(String clientRequestId, String description) throws OutOfScope {
  String[] seen = requests.get(clientRequestId);
  if (seen != null) return new Started(projects.get(seen[0]), notes(description));
  screen(description);
  String id = String.valueOf(next++);
  String name = ShareMessage.clean(description, "New app");
  if (name.length() > 24) name = name.substring(0, 24).trim() + "…";
  // App Core's scheme: app.rose.gen.p<projectId>.
  Project p = new Project(id, name, "app.rose.gen.p" + id, "fake", new ArrayList<Version>(), new BuildJob("j" + next++, BuildJob.Stage.QUEUED, null, null, 1, clock++));
  projects.put(id, p);
  requests.put(clientRequestId, new String[]{id});
  return new Started(p, notes(description));
 }

 @Override public synchronized Started revise(String id, String clientRequestId, String request) throws OutOfScope {
  if (requests.containsKey(clientRequestId)) return new Started(projects.get(id), notes(request));
  screen(request);
  Project p = projects.get(id);
  Project q = new Project(p.projectId, p.name, p.packageName, p.signerSha256, p.versions, new BuildJob("j" + next++, BuildJob.Stage.QUEUED, null, null, 1, clock++));
  projects.put(id, q);
  requests.put(clientRequestId, new String[]{id});
  return new Started(q, notes(request));
 }

 @Override public synchronized CancelStatus cancel(String jobId) {
  for (Project p : projects.values()) {
   if (p.job == null || !p.job.jobId.equals(jobId)) continue;
   if (p.job.terminal()) return CancelStatus.NOT_RUNNING;
   stopping.add(jobId);
   return CancelStatus.ACCEPTED;
  }
  return CancelStatus.UNKNOWN_JOB;
 }

 @Override public synchronized int retry(String jobId) throws Refused {
  for (Map.Entry<String, Project> e : projects.entrySet()) {
   Project p = e.getValue();
   if (p.job == null || !p.job.jobId.equals(jobId)) continue;
   if (!BuildJob.offerRetry(p.job, p.newestVersionAt())) throw new Refused("not a retryable failed job, or a newer version exists");
   int attempt = p.job.attempt + 1;
   projects.put(e.getKey(), new Project(p.projectId, p.name, p.packageName, p.signerSha256, p.versions, new BuildJob(jobId, BuildJob.Stage.QUEUED, null, null, attempt, p.job.startedAt)));
   return attempt;
  }
  throw new Refused("unknown job");
 }

 @Override public void download(String apkSha256, long size, File dest) throws FileNotFoundException {
  throw new FileNotFoundException("the stand-in builder makes no APKs");
 }

 @Override public synchronized void reportInstall(InstallReceipt receipt) { receipts.add(receipt); }

 @Override public String shareUrl(String id, Long versionCode) { return null; }
}
