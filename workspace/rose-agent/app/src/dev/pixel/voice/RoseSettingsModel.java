package dev.pixel.voice;

/**
 * Rose Agent's settings, as the owner asked (Pair with Mac and About only). This is the text the
 * screen shows; SettingsDialog draws it. Pure rules with no Android types, tested on the host.
 *
 * The Pair with Mac row opens the established QR pairing flow (LocalRosePairActivity), which already
 * offers scanning a new code to pair again or change Macs, Unpair, and Cancel. Its state line comes
 * from LocalRoseConnection: the paired Mac's name and the connection state, never an address, code or key.
 */
final class RoseSettingsModel {
 private RoseSettingsModel() {}

 static final String TITLE = "Rose Agent settings", PAIR = "Pair with Mac", ABOUT = "About", APP = "Rose Agent";
 static final int MAX_NAME = 40;

 /**
  * The Pair with Mac state line.
  * @param server  the paired Mac's name, or null when this phone is not paired
  * @param damaged this phone was paired but its pairing record can't be read (LocalRoseConnection.pairedFlag)
  * @param state   LocalRoseConnection.status "state" for a paired phone: connected, connecting, offline or unpaired
  * @param reason  its "reason": clock (the Mac refused this phone's time) or revoked, else empty
  */
 static String pairing(String server, boolean damaged, String state, String reason) {
  if (server == null) return damaged ? "This phone's pairing was lost. Pair again." : "Not paired. Scan the code your Mac shows.";
  String name = name(server);
  switch (state == null ? "" : state) {
   case "connected": return "Paired with " + name + " · Connected";
   case "unpaired": return "Paired with " + name + " · The Mac no longer accepts this phone. Pair again.";
   case "offline": return "Paired with " + name + " · " + ("clock".equals(reason)
     ? "Turn on automatic date and time" : "Offline");
   default: return "Paired with " + name + " · Connecting…";
  }
 }

 /** The Mac's name as shown: control characters removed, trimmed, at most MAX_NAME characters. */
 static String name(String server) {
  String clean = server == null ? "" : server.replaceAll("\\p{Cntrl}", "").trim();
  if (clean.isEmpty()) return "your Mac";
  return clean.length() > MAX_NAME ? clean.substring(0, MAX_NAME - 1) + "…" : clean;
 }

 /** The About line: the installed version name and build number. */
 static String version(String versionName, long versionCode) {
  String name = versionName == null || versionName.trim().isEmpty() ? "unknown" : versionName.trim();
  return "Version " + name + " (build " + versionCode + ")";
 }
}
