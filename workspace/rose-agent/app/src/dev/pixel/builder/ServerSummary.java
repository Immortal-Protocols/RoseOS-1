package dev.pixel.builder;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Project 1, slice 9: a client-server version's server summary (docs/APP-BUILDER.md §3.3, versions[].server), as the
 * phone reads and words it: which contract api the version's server speaks, and the oldest app version whose client
 * that server still serves. Pure, host-tested against App Core's shared fixture (server-summary-v1.json).
 *
 * <p>It describes compatibility between a version's client and its server, nothing more. It never says the server
 * runs or can be reached (no server is hosted yet, §7.7), never blocks or changes an install (InstallPlan decides that
 * alone), never limits what may be built, and says nothing about versions newer than the one it belongs to.
 *
 * <p>Reading follows §3.3's client rule: an unknown schemaVersion or status, a field missing or extra for its status,
 * an api or minClientVersionCode that is not a JSON integer from 1 to 2^31-1, a minClientVersionCode above the
 * version's own versionCode, or a value that is not an object reads as unavailable. Only the summary is refused, never
 * the version or the library. No server field (a plain version, or an older builder) is absent and shows nothing.
 */
final class ServerSummary {
 enum Status { ABSENT, OK, UNAVAILABLE }

 static final long MAX = 2147483647L;

 final Status status;
 /** True when the served value broke §3.3's rule and was read as unavailable by this client. */
 final boolean unreadable;
 /** OK only; -1 otherwise. */
 final long api, minClientVersionCode;

 private ServerSummary(Status status, boolean unreadable, long api, long minClientVersionCode) {
  this.status = status; this.unreadable = unreadable; this.api = api; this.minClientVersionCode = minClientVersionCode;
 }

 static ServerSummary absent() { return new ServerSummary(Status.ABSENT, false, -1, -1); }
 static ServerSummary unreadable() { return new ServerSummary(Status.UNAVAILABLE, true, -1, -1); }

 private static final Set<String> UNAVAILABLE_KEYS = keys("schemaVersion", "status");
 private static final Set<String> OK_KEYS = keys("schemaVersion", "status", "api", "minClientVersionCode");

 /** Reads a served server summary (a parsed JSON value, or null when absent) for the version versionCode. Never throws. */
 @SuppressWarnings("unchecked")
 static ServerSummary parse(Object served, long versionCode) {
  if (served == null) return absent();
  if (!(served instanceof Map)) return unreadable();
  Map<String, Object> o = (Map<String, Object>) served;
  if (!Long.valueOf(1).equals(o.get("schemaVersion"))) return unreadable();
  Object s = o.get("status");
  if ("unavailable".equals(s))
   return o.keySet().equals(UNAVAILABLE_KEYS) ? new ServerSummary(Status.UNAVAILABLE, false, -1, -1) : unreadable();
  if (!"ok".equals(s) || !o.keySet().equals(OK_KEYS)) return unreadable();
  long api = whole(o.get("api")), first = whole(o.get("minClientVersionCode"));
  if (api < 1 || first < 1 || first > versionCode) return unreadable();
  return new ServerSummary(Status.OK, false, api, first);
 }

 /** A JSON integer from 1 to 2^31-1, or -1 for anything else (missing, text, boolean, fractional, out of range). */
 private static long whole(Object v) { return v instanceof Long && (Long) v >= 1 && (Long) v <= MAX ? (Long) v : -1; }

 /**
  * What the phone shows for the version shownVersionCode. names gives a version's name by versionCode (the
  * project's versions), falling back to the number. installedVersionCode is the version of this app on the phone, or
  * null when it isn't installed. Every line is information only.
  */
 static List<String> lines(ServerSummary s, long shownVersionCode, Map<Long, String> names, Long installedVersionCode) {
  List<String> out = new ArrayList<>();
  switch (s.status) {
   case ABSENT:
    break;
   case UNAVAILABLE:
    out.add("This version's server summary isn't available, so it doesn't say which app versions its server serves.");
    break;
   default:
    out.add("This version's server uses contract API " + s.api + ".");
    // A project's first client-server version is its own minimum: the versions before it never had a server.
    if (s.minClientVersionCode < shownVersionCode) {
     String first = name(names, s.minClientVersionCode);
     out.add("App versions before " + first + " would need to update to use this version's server.");
     if (installedVersionCode != null && installedVersionCode < s.minClientVersionCode)
      out.add("The copy on this phone is older than " + first + ", so it would need an update to use this version's server.");
    }
  }
  return out;
 }

 private static String name(Map<Long, String> names, long versionCode) {
  String n = names == null ? null : names.get(versionCode);
  return n == null ? "version " + versionCode : "version " + n;
 }

 private static Set<String> keys(String... names) {
  Set<String> out = new HashSet<>();
  for (String n : names) out.add(n);
  return out;
 }
}
