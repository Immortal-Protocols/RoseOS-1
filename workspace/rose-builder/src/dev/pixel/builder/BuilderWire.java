package dev.pixel.builder;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Project 1: builder API v1 on the wire (docs/APP-BUILDER.md §3), as pure mapping between JSON and the client's
 * models, host-tested against App Core's shapes. Every response is an object with schemaVersion 1; fields the client
 * doesn't know are ignored, and a missing or mistyped field it needs is a Malformed answer, never a guess. Times
 * arrive as Unix epoch seconds with a fraction and are kept as milliseconds.
 */
final class BuilderWire {
 private BuilderWire() {}

 static final int SCHEMA = 1;
 static final String PREFIX = "/builder/v1";

 static final class Malformed extends Exception { Malformed(String m) { super(m); } }

 // ---- requests --------------------------------------------------------------------------------------------------

 static String createBody(String clientRequestId, String description) {
  Map<String, Object> m = base();
  m.put("clientRequestId", clientRequestId);
  m.put("description", text(description));
  return Json.write(m);
 }

 static String reviseBody(String clientRequestId, String request) {
  Map<String, Object> m = base();
  m.put("clientRequestId", clientRequestId);
  m.put("request", text(request));
  return Json.write(m);
 }

 static String emptyBody() { return Json.write(base()); }

 /** §3.5 limits: a receipt the builder would refuse is never sent (it would only fail). versionCode is 1…2^31−1. */
 static final int DETAIL_MAX = 500;
 static boolean sendable(BuilderApi.InstallReceipt r) { return r != null && r.versionCode >= 1 && r.versionCode <= Integer.MAX_VALUE; }

 /** At most n code points, never splitting a surrogate pair. */
 static String clip(String s, int n) {
  if (s == null) return "";
  return s.codePointCount(0, s.length()) <= n ? s : s.substring(0, s.offsetByCodePoints(0, n));
 }

 static String receiptBody(BuilderApi.InstallReceipt r) {
  Map<String, Object> m = base();
  m.put("projectId", r.projectId);
  m.put("packageName", r.packageName);
  m.put("versionCode", r.versionCode);
  m.put("signerSha256", r.signerSha256);
  m.put("status", r.status.wire());
  // Android's own message can be long; the builder takes at most 500 characters.
  m.put("detail", clip(text(r.detail == null ? "" : r.detail), DETAIL_MAX));
  return Json.write(m);
 }

 /** Path segments are the server's own ids; anything else is refused before a request is signed. */
 static String segment(String id) throws Malformed {
  if (id == null || !id.matches("[A-Za-z0-9._-]{1,128}")) throw new Malformed("bad id");
  return id;
 }

 private static Map<String, Object> base() { Map<String, Object> m = new LinkedHashMap<>(); m.put("schemaVersion", SCHEMA); return m; }

 /** The owner's words as valid Unicode: a lone surrogate (which the builder refuses) becomes U+FFFD. */
 static String text(String s) {
  StringBuilder b = new StringBuilder(s.length());
  for (int i = 0; i < s.length(); i++) {
   char c = s.charAt(i);
   if (Character.isHighSurrogate(c) && i + 1 < s.length() && Character.isLowSurrogate(s.charAt(i + 1))) { b.append(c).append(s.charAt(++i)); }
   else if (Character.isSurrogate(c)) b.append('�');
   else b.append(c);
  }
  return b.toString();
 }

 // ---- responses -------------------------------------------------------------------------------------------------

 /** A project as the library shows it, with its active job's id and state (details come from GET jobs/<id>). */
 static final class LibraryProject {
  final BuilderApi.Project project; final String activeJobId; final BuildJob.Stage activeState;
  LibraryProject(BuilderApi.Project project, String activeJobId, BuildJob.Stage activeState) {
   this.project = project; this.activeJobId = activeJobId; this.activeState = activeState;
  }
 }

 static List<LibraryProject> library(String body) throws Malformed {
  Map<String, Object> o = response(body);
  List<LibraryProject> out = new ArrayList<>();
  for (Object p : list(o, "projects")) out.add(project(asObject(p)));
  return out;
 }

 static LibraryProject project(Map<String, Object> p) throws Malformed {
  List<BuilderApi.Version> versions = new ArrayList<>();
  for (Object v : list(p, "versions")) versions.add(version(asObject(v)));
  versions.sort((a, b) -> Long.compare(a.versionCode, b.versionCode));
  String activeId = null; BuildJob.Stage activeState = null;
  Object a = p.get("activeJob");
  if (a != null) {
   Map<String, Object> aj = asObject(a);
   activeId = str(aj, "jobId");
   activeState = stage(str(aj, "state"));
  }
  BuildJob job = activeId == null ? null : new BuildJob(activeId, activeState, null, null);
  BuilderApi.Project project = new BuilderApi.Project(str(p, "projectId"), optStr(p, "name", "Untitled app"),
    str(p, "packageName"), optStr(p, "signerSha256", null), versions, job);
  return new LibraryProject(project, activeId, activeState);
 }

 static BuilderApi.Version version(Map<String, Object> v) throws Malformed {
  Map<String, Object> apk = asObject(v.get("apk"));
  long size = integer(apk, "size");
  String sha = str(apk, "sha256").toLowerCase(Locale.ROOT);
  if (!sha.matches("[0-9a-f]{64}") || size <= 0) throw new Malformed("bad apk");
  return new BuilderApi.Version(integer(v, "versionCode"), optStr(v, "versionName", String.valueOf(integer(v, "versionCode"))),
    millis(v, "createdAt"), optStr(v, "jobId", null), optStr(v, "sourceRevision", null), sha, size, optStr(v, "notes", null));
 }

 static BuildJob job(String body) throws Malformed {
  Map<String, Object> j = response(body);
  BuildJob.Failure failure = null;
  Object f = j.get("failure");
  if (f != null) {
   Map<String, Object> fo = asObject(f);
   failure = new BuildJob.Failure(category(str(fo, "category")), optStr(fo, "summary", null), bool(fo, "retryable"));
  }
  return new BuildJob(str(j, "jobId"), stage(str(j, "state")), optStr(j, "message", null), failure,
    (int) Math.max(1, Math.min(Integer.MAX_VALUE, integer(j, "attempt"))), millis(j, "startedAt"));
 }

 /** create: {projectId, jobId, notes}; revise: {jobId, notes}. */
 static final class Accepted {
  final String projectId, jobId; final List<String> notes;
  Accepted(String projectId, String jobId, List<String> notes) { this.projectId = projectId; this.jobId = jobId; this.notes = notes; }
 }

 static Accepted accepted(String body, boolean create) throws Malformed {
  Map<String, Object> o = response(body);
  List<String> notes = new ArrayList<>();
  Object n = o.get("notes");
  if (n != null) for (Object s : asList(n)) { if (!(s instanceof String)) throw new Malformed("bad notes"); notes.add((String) s); }
  return new Accepted(create ? str(o, "projectId") : null, str(o, "jobId"), notes);
 }

 static BuilderApi.CancelStatus cancel(String body) throws Malformed {
  switch (str(response(body), "status")) {
   case "accepted": return BuilderApi.CancelStatus.ACCEPTED;
   case "not_running": return BuilderApi.CancelStatus.NOT_RUNNING;
   case "unknown_job": return BuilderApi.CancelStatus.UNKNOWN_JOB;
   default: throw new Malformed("bad cancel status");
  }
 }

 static int retryAttempt(String body) throws Malformed { return (int) Math.max(1, Math.min(Integer.MAX_VALUE, integer(response(body), "attempt"))); }

 // ---- refusals --------------------------------------------------------------------------------------------------

 /** What a non-2xx answer means to the client. */
 enum Kind { OUT_OF_SCOPE, REFUSED, PAIR_AGAIN, RETRY_SIGNED, UNREACHABLE }

 static final class Refusal {
  final Kind kind; final String message, category;
  Refusal(Kind kind, String message, String category) { this.kind = kind; this.message = message; this.category = category; }
 }

 /**
  * 400 out_of_scope: the builder's own sentence (§3.6); any other 400, or a 404 for an id it doesn't know: refused
  * with its error. 401 final: the
  * server no longer knows this phone (pair again); 401 not final (stale time, replayed nonce): sign afresh and try
  * once more. Anything else, or an unreadable body, is the builder being unavailable.
  */
 static Refusal refusal(int status, String body) {
  Map<String, Object> o = null;
  try { Object v = Json.parse(body); if (v instanceof Map) o = asObject(v); } catch (Json.ParseError | Malformed e) { o = null; }
  String error = o == null || !(o.get("error") instanceof String) ? null : (String) o.get("error");
  if (status == 404) return new Refusal(Kind.REFUSED, error == null ? "not found" : error, null);
  if (status == 400) {
   if (o != null && "out_of_scope".equals(o.get("code")))
    return new Refusal(Kind.OUT_OF_SCOPE, error, o.get("category") instanceof String ? (String) o.get("category") : null);
   return new Refusal(Kind.REFUSED, error, null);
  }
  if (status == 401 || status == 403) {
   boolean fin = o != null && Boolean.TRUE.equals(o.get("final"));
   return new Refusal(fin ? Kind.PAIR_AGAIN : Kind.RETRY_SIGNED, error, null);
  }
  return new Refusal(Kind.UNREACHABLE, error, null);
 }

 // ---- field helpers ---------------------------------------------------------------------------------------------

 static Map<String, Object> response(String body) throws Malformed {
  Object v;
  try { v = Json.parse(body); } catch (Json.ParseError e) { throw new Malformed("not JSON"); }
  Map<String, Object> o = asObject(v);
  Object s = o.get("schemaVersion");
  if (!(s instanceof Long) || (Long) s != SCHEMA) throw new Malformed("unsupported schemaVersion");
  return o;
 }

 @SuppressWarnings("unchecked")
 static Map<String, Object> asObject(Object v) throws Malformed { if (!(v instanceof Map)) throw new Malformed("expected object"); return (Map<String, Object>) v; }
 @SuppressWarnings("unchecked")
 static List<Object> asList(Object v) throws Malformed { if (!(v instanceof List)) throw new Malformed("expected list"); return (List<Object>) v; }
 static List<Object> list(Map<String, Object> o, String k) throws Malformed { return asList(o.get(k)); }

 static String str(Map<String, Object> o, String k) throws Malformed {
  Object v = o.get(k);
  if (!(v instanceof String) || ((String) v).isEmpty()) throw new Malformed("missing " + k);
  return (String) v;
 }
 static String optStr(Map<String, Object> o, String k, String fallback) throws Malformed {
  Object v = o.get(k);
  if (v == null) return fallback;
  if (!(v instanceof String)) throw new Malformed("bad " + k);
  return ((String) v).isEmpty() ? fallback : (String) v;
 }
 static long integer(Map<String, Object> o, String k) throws Malformed {
  Object v = o.get(k);
  if (v instanceof Long) return (Long) v;
  throw new Malformed("missing " + k);
 }
 static boolean bool(Map<String, Object> o, String k) throws Malformed {
  Object v = o.get(k);
  if (v instanceof Boolean) return (Boolean) v;
  throw new Malformed("missing " + k);
 }
 /** Epoch seconds (fractional) as epoch milliseconds; absent is 0 (unknown). */
 static long millis(Map<String, Object> o, String k) throws Malformed {
  Object v = o.get(k);
  if (v == null) return 0;
  if (!(v instanceof Number)) throw new Malformed("bad " + k);
  double s = ((Number) v).doubleValue();
  if (s < 0 || s > 1e11) throw new Malformed("bad " + k);
  return Math.round(s * 1000);
 }

 static BuildJob.Stage stage(String s) throws Malformed {
  switch (s) {
   case "queued": return BuildJob.Stage.QUEUED;
   case "preparing": return BuildJob.Stage.PREPARING;
   case "building": return BuildJob.Stage.BUILDING;
   case "testing": return BuildJob.Stage.TESTING;
   case "signing": return BuildJob.Stage.SIGNING;
   case "ready": return BuildJob.Stage.READY;
   case "failed": return BuildJob.Stage.FAILED;
   case "cancelled": return BuildJob.Stage.CANCELLED;
   default: throw new Malformed("unknown state");
  }
 }

 /** An unknown category is shown as an internal error rather than refused: the job's state is still known. */
 static BuildJob.Category category(String s) {
  switch (s) {
   case "toolchain": return BuildJob.Category.TOOLCHAIN;
   case "dependency": return BuildJob.Category.DEPENDENCY;
   case "compile": return BuildJob.Category.COMPILE;
   case "test": return BuildJob.Category.TEST;
   case "signing": return BuildJob.Category.SIGNING;
   case "timeout": return BuildJob.Category.TIMEOUT;
   case "cancelled": return BuildJob.Category.CANCELLED;
   default: return BuildJob.Category.INTERNAL;
  }
 }
}
