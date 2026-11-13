package dev.pixel.builder;

import java.util.List;

/**
 * Project 1: the phone's view of the Rose build service. App Core owns orchestration, builds, signing and artifacts;
 * this client only asks and shows. The shape follows Home's contract proposal to App Core (Nov 13) and is provisional
 * until App Core publishes the real contract: until then the app runs against {@link FakeBuilderApi} only, and no
 * network transport exists in this module.
 */
interface BuilderApi {
 final class Version {
  final long versionCode; final String versionName, apkUrl, apkSha256; final long apkSize;
  Version(long versionCode, String versionName, String apkUrl, String apkSha256, long apkSize) {
   this.versionCode = versionCode; this.versionName = versionName; this.apkUrl = apkUrl; this.apkSha256 = apkSha256; this.apkSize = apkSize;
  }
 }
 final class Project {
  final String projectId, name, description, packageName, signerSha256;
  final List<Version> versions; final BuildJob job;
  Project(String projectId, String name, String description, String packageName, String signerSha256, List<Version> versions, BuildJob job) {
   this.projectId = projectId; this.name = name; this.description = description; this.packageName = packageName;
   this.signerSha256 = signerSha256; this.versions = versions; this.job = job;
  }
  Version latest() { return versions.isEmpty() ? null : versions.get(versions.size() - 1); }
 }
 /** Thrown when the builder can't be reached: shown as UNREACHABLE, never as an empty library. */
 final class Unreachable extends Exception { Unreachable(String m) { super(m); } }

 List<Project> projects() throws Unreachable;
 Project project(String projectId) throws Unreachable;
 /** Starts a new app from a description; returns its project with the first job. */
 Project create(String description) throws Unreachable;
 /** Requests a change to an existing app; a new version keeps the project's package and signer. */
 Project revise(String projectId, String change) throws Unreachable;
 void cancel(String projectId, String jobId) throws Unreachable;
 /** Public download links for sharing: one immutable per version, one that always serves the newest. Null until
  * hosting exists (Level 3 depends on the Chariot owner). */
 String shareUrl(String projectId, Long versionCode) throws Unreachable;
}
