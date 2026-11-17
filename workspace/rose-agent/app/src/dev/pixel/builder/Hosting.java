package dev.pixel.builder;

import java.util.Map;

/**
 * A client-server version's server set up in the project's Chariot agent container, as the builder reports it
 * (versions[].hosting, App Core): {state: "set_up", appUrl, setUpAt}. Present only on the version whose server is the
 * one currently set up there. It means that server passed its health check in the container before the version was
 * delivered; it does NOT say it is running now (the container idles and wakes on traffic), and the phone never says
 * more. Read strictly: an unknown state, a URL that isn't plain https, or a bad time is unreadable, never "set up". It
 * never fails the version or the library and never decides an install.
 */
final class Hosting {
 enum Status { ABSENT, SET_UP, UNREADABLE }

 final Status status;
 /** SET_UP only: the backend's public https address the app's client was built with. */
 final String appUrl;
 /** SET_UP only: when it was set up (ms). */
 final long setUpAt;

 private Hosting(Status status, String appUrl, long setUpAt) { this.status = status; this.appUrl = appUrl; this.setUpAt = setUpAt; }

 static Hosting absent() { return new Hosting(Status.ABSENT, null, 0); }
 static Hosting unreadable() { return new Hosting(Status.UNREADABLE, null, 0); }

 /** The served value (a parsed JSON value, or null when absent). Never throws. */
 static Hosting parse(Object served) {
  if (served == null) return absent();
  if (!(served instanceof Map)) return unreadable();
  @SuppressWarnings("unchecked") Map<String, Object> o = (Map<String, Object>) served;
  if (!"set_up".equals(o.get("state"))) return unreadable();
  String url = ShareState.link(o.get("appUrl"));
  Object at = o.get("setUpAt");
  if (url == null || !(at instanceof Long) || (Long) at <= 0) return unreadable();
  return new Hosting(Status.SET_UP, url, (Long) at);
 }

 boolean setUp() { return status == Status.SET_UP; }

 /** The version's hosting line, or null when absent. Never says the backend is running. */
 static String line(Hosting h) {
  switch (h.status) {
   case SET_UP: return "Backend set up · " + host(h.appUrl);
   case UNREADABLE: return "This version's backend setup couldn't be read.";
   default: return null;
  }
 }

 static String host(String url) {
  try { String h = new java.net.URI(url).getHost(); return h == null ? url : h; } catch (java.net.URISyntaxException e) { return url; }
 }
}
