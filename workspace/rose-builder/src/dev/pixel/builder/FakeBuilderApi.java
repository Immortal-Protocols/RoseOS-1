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
 private int next = 1;

 @Override public synchronized List<Project> projects() {
  List<Project> out = new ArrayList<>();
  for (String id : new ArrayList<>(projects.keySet())) out.add(project(id));
  return out;
 }

 @Override public synchronized Project project(String id) {
  Project p = projects.get(id);
  if (p == null || p.job == null || p.job.terminal()) return p;
  BuildJob.Stage s = BuildJob.Stage.values()[p.job.stage.ordinal() + 1];
  List<Version> versions = p.versions;
  if (s == BuildJob.Stage.READY) {
   versions = new ArrayList<>(p.versions);
   long code = versions.size() + 1;
   versions.add(new Version(code, "1." + (code - 1), "fake-" + code, null, null, 0));
  }
  Project q = new Project(p.projectId, p.name, p.description, p.packageName, p.signerSha256, versions, new BuildJob(p.job.jobId, s, null, null));
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
  projects.put(id, new Project(p.projectId, p.name, p.description, p.packageName, p.signerSha256, p.versions,
    new BuildJob(jobId, BuildJob.Stage.CANCELLED, null, null)));
 }

 @Override public String shareUrl(String id, Long versionCode) { return null; }
}
