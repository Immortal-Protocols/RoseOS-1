package dev.pixel.builder;

import java.net.URI;
import java.util.Map;

/**
 * Project 1, Level 3: a version's share state as the builder records it (proposed versions[].share, schemaVersion 1;
 * hosting is the Chariot engineering worker's, the record is App Core's). The phone shares only a link the builder
 * recorded for exactly that version and never invents or derives one, and it never contacts the hosting itself.
 *
 * <p>Read strictly: anything other than schemaVersion 1 with a known status, published without both links, a link that
 * isn't a plain absolute https URL (no credentials, fragment, spaces or non-ASCII; at most 2048 characters), or
 * unavailable without a reason, is "couldn't be read" and never a link. A missing field (an older server, or no
 * hosting yet) is unpublished. A bad share state never fails the version or the library. Pure, host-tested.
 */
final class ShareState {
 enum Status { PUBLISHED, UNPUBLISHED, UNAVAILABLE }

 final Status status;
 final boolean unreadable;
 final String versionUrl, latestUrl, reason;

 private ShareState(Status status, boolean unreadable, String versionUrl, String latestUrl, String reason) {
  this.status = status; this.unreadable = unreadable; this.versionUrl = versionUrl; this.latestUrl = latestUrl; this.reason = reason;
 }

 boolean published() { return status == Status.PUBLISHED; }

 static ShareState unpublished() { return new ShareState(Status.UNPUBLISHED, false, null, null, null); }
 static ShareState unreadable() { return new ShareState(Status.UNAVAILABLE, true, null, null, null); }

 @SuppressWarnings("unchecked")
 static ShareState parse(Object served) {
  if (served == null) return unpublished();
  if (!(served instanceof Map)) return unreadable();
  Map<String, Object> o = (Map<String, Object>) served;
  if (!Long.valueOf(1).equals(o.get("schemaVersion"))) return unreadable();
  Object s = o.get("status");
  if ("unpublished".equals(s)) return unpublished();
  if ("unavailable".equals(s)) {
   if (!(o.get("reason") instanceof String)) return unreadable();
   String r = ShareMessage.clean((String) o.get("reason"), null);
   return new ShareState(Status.UNAVAILABLE, false, null, null, r == null ? null : BuilderWire.clip(r, 300));
  }
  if (!"published".equals(s)) return unreadable();
  String v = link(o.get("versionUrl")), l = link(o.get("latestUrl"));
  if (v == null || l == null) return unreadable();
  return new ShareState(Status.PUBLISHED, false, v, l, null);
 }

 /** A plain absolute https URL the builder recorded, or null. */
 static String link(Object v) {
  if (!(v instanceof String)) return null;
  String s = (String) v;
  if (s.isEmpty() || s.length() > 2048 || !s.startsWith("https://")) return null;
  for (int i = 0; i < s.length(); i++) { char c = s.charAt(i); if (c <= 0x20 || c >= 0x7f) return null; }
  try {
   URI u = new URI(s);
   if (!"https".equals(u.getScheme()) || u.getHost() == null || u.getHost().isEmpty() || u.getRawUserInfo() != null || u.getRawFragment() != null) return null;
  } catch (java.net.URISyntaxException e) { return null; }
  return s;
 }

 /** The version's share status line, or null when published (the share buttons say it then). */
 static String line(ShareState s) {
  switch (s.status) {
   case PUBLISHED: return null;
   case UNPUBLISHED: return "Not shared yet: there's no public download link for this version.";
   default: return s.unreadable ? "This version's share link couldn't be read, so it can't be shared."
     : "Sharing isn't available right now" + (s.reason == null ? "." : ": " + s.reason + ".");
  }
 }
}
