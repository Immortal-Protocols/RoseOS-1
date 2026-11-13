package dev.pixel.builder;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * An in-memory builder for developing the screens before App Core's service exists. Each poll moves a job one stage;
 * it produces no APK, so its versions can't be downloaded or installed and it has no share links. Nothing here touches
 * the network, a server or signing material.
 */
final class FakeBuilderApi implements BuilderApi {
 private final Map<String, Project> projects = new LinkedHashMap<>();
 private final java.util.Set<String> stopping = new java.util.HashSet<>(), interrupted = new java.util.HashSet<>();
 final List<InstallReport> reports = new ArrayList<>();
 private int next = 1;

 @Override public synchronized List<Project> projects() {
  List<Project> out = new ArrayList<>();
  for (String id : new ArrayList<>(projects.keySet())) out.add(project(id));
  return out;
 }

 @Override public synchronized Project project(String id) {
  Project p = projects.get(id);
  if (p == null || p.job == null || p.job.terminal()) return p;
  // Like App Core: a cancel is confirmed by the job on a later poll, not by the request.
  BuildJob.Stage s = stopping.remove(p.job.jobId) ? BuildJob.Stage.CANCELLED : BuildJob.Stage.values()[p.job.stage.ordinal() + 1];
  List<Version> versions = p.versions;
  if (s == BuildJob.Stage.READY && p.versions.size() == 2 && interrupted.add(id)) {
   // A project's third build is interrupted once, as after a builder restart: an honest unknown outcome, never a
   // silent re-run. Asking again builds normally.
   BuildJob.Failure f = new BuildJob.Failure(BuildJob.Category.INTERNAL, "Interrupted, outcome unknown", true);
   Project q = new Project(p.projectId, p.name, p.description, p.packageName, p.signerSha256, p.versions, new BuildJob(p.job.jobId, BuildJob.Stage.FAILED, null, f, p.job.attempt));
   projects.put(id, q);
   return q;
  }
  if (s == BuildJob.Stage.READY) {
   versions = new ArrayList<>(p.versions);
   long code = versions.size() + 1;
   versions.add(new Version(code, "1." + (code - 1), "fake-" + code, null, null, 0));
  }
  Project q = new Project(p.projectId, p.name, p.description, p.packageName, p.signerSha256, versions, new BuildJob(p.job.jobId, s, null, null, p.job.attempt));
  projects.put(id, q);
  return q;
 }

 @Override public synchronized Project create(String description) {
  String id = "p" + next++;
  String name = ShareMessage.clean(description, "New app");
  if (name.length() > 24) name = name.substring(0, 24).trim() + "…";
  Project p = new Project(id, name, description, "dev.rose.generated." + id, "fake", new ArrayList<Version>(),
    new BuildJob("j" + next++, BuildJob.Stage.QUEUED, null, null));
  projects.put(id, p);
  return p;
 }

 @Override public synchronized Project revise(String id, String change) {
  Project p = projects.get(id);
  Project q = new Project(p.projectId, p.name, p.description, p.packageName, p.signerSha256, p.versions,
    new BuildJob("j" + next++, BuildJob.Stage.QUEUED, null, null));
  projects.put(id, q);
  return q;
 }

 @Override public synchronized void cancel(String id, String jobId) {
  Project p = projects.get(id);
  if (p == null || p.job == null || !p.job.jobId.equals(jobId) || p.job.terminal()) return;
  stopping.add(jobId);
 }

 @Override public synchronized void reportInstall(String id, InstallReport report) { reports.add(report); }

 @Override public String shareUrl(String id, Long versionCode) { return null; }
}
