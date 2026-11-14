package dev.pixel.builder;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Project 1: a version's advisory API level summary (docs/APP-BUILDER.md §3.3, versions[].apiReport), as the phone
 * reads and words it. It is detection in the built app, never device proof: no status is a pass, nothing here blocks
 * or changes a build or an install, and nothing limits what may be built. Pure, host-tested against App Core's shared
 * fixtures.
 *
 * <p>Reading follows §3.3's client rule: an unknown status or schemaVersion, a missing field its status requires, a
 * wrong type or negative count, or advisory/deviceProof other than true/false makes the summary unavailable ("couldn't
 * be read"), never clean, and never fails the rest of the library. No apiReport at all (an older server) is none.
 */
final class ApiReport {
 enum Status {
  COMPLETE, INCOMPLETE, UNAVAILABLE, NONE;
  String wire() { return name().toLowerCase(java.util.Locale.ROOT); }
 }

 final Status status;
 /** True when the served value broke §3.3's rule and was read as unavailable by this client. */
 final boolean unreadable;
 final String reason;
 /** complete and incomplete only; -1 otherwise. */
 final long minSdk, notGuardedHere, unverified, guarded, truncated, unlistedCount;
 /** A lower bound, never a ceiling: the highest level a kept unguarded or unverified finding names; null when none. */
 final Long highestLevel;

 private ApiReport(Status status, boolean unreadable, String reason, long minSdk, long notGuardedHere, long unverified,
   long guarded, Long highestLevel, long truncated, long unlistedCount) {
  this.status = status; this.unreadable = unreadable; this.reason = reason; this.minSdk = minSdk;
  this.notGuardedHere = notGuardedHere; this.unverified = unverified; this.guarded = guarded;
  this.highestLevel = highestLevel; this.truncated = truncated; this.unlistedCount = unlistedCount;
 }

 boolean hasCounts() { return status == Status.COMPLETE || status == Status.INCOMPLETE; }

 static ApiReport none() { return new ApiReport(Status.NONE, false, null, -1, -1, -1, -1, null, -1, -1); }
 static ApiReport unreadable() { return new ApiReport(Status.UNAVAILABLE, true, null, -1, -1, -1, -1, null, -1, -1); }

 /** Reads a served apiReport (a parsed JSON value, or null when absent). Never throws. */
 @SuppressWarnings("unchecked")
 static ApiReport parse(Object served) {
  if (served == null) return none();
  if (!(served instanceof Map)) return unreadable();
  Map<String, Object> o = (Map<String, Object>) served;
  if (!Long.valueOf(1).equals(o.get("schemaVersion")) || !Boolean.TRUE.equals(o.get("advisory"))
    || !Boolean.FALSE.equals(o.get("deviceProof")) || !(o.get("note") instanceof String)) return unreadable();
  Object s = o.get("status");
  Status status;
  if ("complete".equals(s)) status = Status.COMPLETE;
  else if ("incomplete".equals(s)) status = Status.INCOMPLETE;
  else if ("unavailable".equals(s)) status = Status.UNAVAILABLE;
  else if ("none".equals(s)) status = Status.NONE;
  else return unreadable();
  String reason = null;
  if (status == Status.INCOMPLETE || status == Status.UNAVAILABLE) {
   if (!(o.get("reason") instanceof String)) return unreadable();
   reason = ShareMessage.clean((String) o.get("reason"), null);
   if (reason != null) reason = BuilderWire.clip(reason, 300);
  }
  if (status == Status.NONE) return none();
  if (status == Status.UNAVAILABLE) return new ApiReport(Status.UNAVAILABLE, false, reason, -1, -1, -1, -1, null, -1, -1);
  if (!(o.get("counts") instanceof Map) || !o.containsKey("highestLevel")) return unreadable();
  Map<String, Object> c = (Map<String, Object>) o.get("counts");
  long minSdk = count(o.get("minSdk")), ng = count(c.get("notGuardedHere")), uv = count(c.get("unverified")),
    gd = count(c.get("guarded")), tr = count(o.get("truncated")), ul = count(o.get("unlistedCount"));
  Object h = o.get("highestLevel");
  Long highest = h == null ? null : (h instanceof Long && (Long) h >= 0 ? (Long) h : Long.valueOf(-1));
  if (minSdk < 0 || ng < 0 || uv < 0 || gd < 0 || tr < 0 || ul < 0 || (highest != null && highest < 0)) return unreadable();
  return new ApiReport(status, false, reason, minSdk, ng, uv, gd, highest, tr, ul);
 }

 /** A non-negative JSON integer, or -1 for anything else (missing, text, fractional, negative). */
 private static long count(Object v) { return v instanceof Long && (Long) v >= 0 ? (Long) v : -1; }

 static final String TITLE = "Advisory API check (detection in the built app, not proof it runs on any phone)";
 static final String NEVER_BLOCKS = "This check never blocks or changes a build or an install.";

 /** The lines the version shows, for a phone running deviceSdk. No line ever reads as a pass. */
 static List<String> lines(ApiReport r, int deviceSdk) {
  List<String> out = new ArrayList<>();
  out.add(TITLE);
  switch (r.status) {
   case NONE:
    out.add("No API level report was recorded for this version.");
    break;
   case UNAVAILABLE:
    out.add(r.unreadable ? "This version's API level report couldn't be read."
      : "The API level report isn't available" + (r.reason == null ? "." : ": " + r.reason + "."));
    break;
   default:
    out.add(plural(r.notGuardedHere, "use") + " of features newer than API " + r.minSdk + " with no version check in the same method, "
      + r.unverified + " it couldn't verify, and " + r.guarded + " with a version check.");
    if (r.highestLevel != null) {
     out.add("The newest flagged feature needs at least API " + r.highestLevel + "; this phone has API " + deviceSdk + ".");
     if (r.highestLevel > deviceSdk) out.add("Parts of the app that use it may fail on this phone.");
    }
    if (r.status == Status.INCOMPLETE)
     out.add("Not every part of the app was checked" + (r.reason == null ? "." : ": " + r.reason + "."));
    if (r.truncated > 0)
     out.add(plural(r.truncated, "more finding") + " weren't kept, so a newer feature may be needed than the one shown.");
    if (r.unlistedCount > 0)
     out.add(plural(r.unlistedCount, "use") + " aren't in the API database and were assumed available.");
  }
  out.add(NEVER_BLOCKS);
  return out;
 }

 private static String plural(long n, String noun) { return n + " " + noun + (n == 1 ? "" : "s"); }
}
