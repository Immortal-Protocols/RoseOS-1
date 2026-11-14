package dev.pixel.builder;

/**
 * Project 1, Level 3: the text the owner sends with Android's share sheet or SMS composer. The owner chooses the
 * recipient and presses send; nothing is sent automatically. The link is a public APK download only: it gives no
 * access to the creator's backend, account or data. Pure rules, host-tested.
 */
final class ShareMessage {
 private ShareMessage() {}

 /** latest: the link that always serves the newest version; otherwise this exact version's immutable link. */
 static String text(String appName, String versionName, String url, boolean latest) {
  String name = clean(appName, "An app");
  String version = clean(versionName, null);
  StringBuilder b = new StringBuilder();
  b.append(name);
  if (!latest && version != null) b.append(' ').append(version);
  b.append(" for Android: ").append(url).append('\n');
  b.append("Open the link on an Android phone and install it. Your phone may ask you to allow installs from your browser first.");
  return b.toString();
 }

 static String clean(String s, String fallback) {
  String c = s == null ? "" : s.replaceAll("\\p{Cntrl}", " ").trim();
  return c.isEmpty() ? fallback : c;
 }
}
