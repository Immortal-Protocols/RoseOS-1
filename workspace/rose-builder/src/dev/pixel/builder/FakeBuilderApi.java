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
   job = new BuildJob(p.job.jobId, BuildJob.Stage.FAILED, null, new BuildJob.Failure(BuildJob.Category.INTERNAL, "interrupted; outcome unknown", true), p.job.attempt);
  } else {
   if (s == BuildJob.Stage.READY) {
    versions = new ArrayList<>(p.versions);
    long code = versions.size() + 1;
    versions.add(new Version(code, "1." + (code - 1), p.job.jobId, "fake-" + code, null, 0, null));
   }
   job = new BuildJob(p.job.jobId, s, null, null, p.job.attempt);
  }
  Project q = new Project(p.projectId, p.name, p.packageName, p.signerSha256, versions, job);
  projects.put(id, q);
  return q;
 }

 @Override public synchronized Project create(String clientRequestId, String description) {
  String[] seen = requests.get(clientRequestId);
  if (seen != null) return projects.get(seen[0]);
  String id = String.valueOf(next++);
  String name = ShareMessage.clean(description, "New app");
  if (name.length() > 24) name = name.substring(0, 24).trim() + "…";
  // App Core's scheme: app.rose.gen.p<projectId>.
  Project p = new Project(id, name, "app.rose.gen.p" + id, "fake", new ArrayList<Version>(), new BuildJob("j" + next++, BuildJob.Stage.QUEUED, null, null));
  projects.put(id, p);
  requests.put(clientRequestId, new String[]{id});
  return p;
 }

 @Override public synchronized Project revise(String id, String clientRequestId, String request) {
  if (requests.containsKey(clientRequestId)) return projects.get(id);
  Project p = projects.get(id);
  Project q = new Project(p.projectId, p.name, p.packageName, p.signerSha256, p.versions, new BuildJob("j" + next++, BuildJob.Stage.QUEUED, null, null));
  projects.put(id, q);
  requests.put(clientRequestId, new String[]{id});
  return q;
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

 @Override public void download(String apkSha256, File dest) throws FileNotFoundException {
  throw new FileNotFoundException("the stand-in builder makes no APKs");
 }

 @Override public synchronized void reportInstall(InstallReceipt receipt) { receipts.add(receipt); }

 @Override public String shareUrl(String id, Long versionCode) { return null; }
}
