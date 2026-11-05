package dev.pixel.voice;

/** Which Home group an installed app belongs to, and which simplified glyph draws it (#403). Pure logic, no Android
 * types, so it is testable on the host.
 *
 * <p>Nathan asked that the system apps stop filling the app pages and sit in one "…" folder. The groups follow the
 * launcher's existing inventory split (MainActivity.attachLauncherPages) rather than a list of hidden apps:
 * <ul>
 * <li>SUITE: Rose's own suite (AppLabels.SUITE_PACKAGES, with the preferred camera), first on the app pages.</li>
 * <li>SYSTEM: an OS app (FLAG_SYSTEM or an updated system app) that is not one of ours, plus the known duplicates of a
 * suite app that AppLabels already names: the test Mail and Calendar builds, the alternate Weather, and every camera
 * app other than the one the suite uses, and retired apps (RETIRED, F02). These go in the folder, still launchable
 * from it.</li>
 * <li>MAIN: everything else (Play Store and other installs, and our other builds), on the app pages as before.</li>
 * </ul>
 * Regular installed Claude, ChatGPT and Amazon are always MAIN, even if a build ever preinstalled them. The grouping is
 * display only: it never grants, refuses or changes access to an app, and the owner's default phone, messages, camera
 * and browser apps still show on Home whichever group they are in. */
final class HomeAppGroups {
 private HomeAppGroups() {}

 static final int SUITE = 0, SYSTEM = 1, MAIN = 2;

 /** Apps that always stay on the app pages, outside the folder. */
 static final String[] KEEP_OUT = {"com.anthropic.claude", "com.openai.chatgpt", "com.amazon.mShop.android.shopping"};
 /** F02: retired standalone apps, by exact package. The obsolete Rose Models app (dev.pixel.models) is no longer
  * shipped (Release retires its distribution); a copy already installed is never removed by Rose, only moved off main
  * Home and the app pages into the System folder, where its owner can still open it or uninstall it (AppRemoval). */
 static final String[] RETIRED = {"dev.pixel.models"};
 /** Second copies of a suite app (AppLabels.label names each one as such). */
 static final String[] DUPLICATES = {"dev.pixel.mail.debug", "org.fossify.calendar.pixel.debug", "dev.pixel.weather.opus"};
 /** Camera apps; the one AppLabels.preferredCamera picks is the suite's Camera, the others are duplicates. */
 static final String[] CAMERAS = {"dev.pixel.aicamera", "net.sourceforge.opencamera", "app.grapheneos.camera"};

 /**
  * @param suite        AppLabels.suiteIndex(packageName, cameraPackage) >= 0
  * @param ours         MainActivity.ourBuild(packageName): our signing key or an established suite app
  * @param preinstalled MainActivity.preinstalled: FLAG_SYSTEM or FLAG_UPDATED_SYSTEM_APP
  */
 static int group(String packageName, boolean suite, boolean ours, boolean preinstalled, String cameraPackage) {
  if (has(KEEP_OUT, packageName)) return MAIN;
  if (has(RETIRED, packageName)) return SYSTEM;
  if (suite) return SUITE;
  if (has(DUPLICATES, packageName)) return SYSTEM;
  if (has(CAMERAS, packageName) && !packageName.equals(cameraPackage)) return SYSTEM;
  if (preinstalled && !ours) return SYSTEM;
  return MAIN;
 }

 /** GrapheneOS's own SMS app, by package, never by label: the pinned external/Messaging (GrapheneOS
  * platform_external_Messaging 041f08b) ships sysconfig whitelist_com.android.messaging.xml granting
  * com.android.messaging, the pinned framework's default_sms_application. */
 static final String OS_MESSAGING = "com.android.messaging";
 /** Rose's Messages (BlueBubbles), the messaging app Home keeps. */
 static final String MESSAGES = "com.bluebubbles.messaging";

 /** The package Home places for a default-role holder (F04): the OS messaging app's place goes to Rose's Messages, so
  * main Home never shows GrapheneOS Messaging; it stays installed and launchable from the System folder, and no
  * message, role or default is touched. Every other package is placed as it is. */
 static String homeRole(String packageName) {
  return OS_MESSAGING.equals(packageName) ? MESSAGES : packageName;
 }

 /** Rose's own apps that follow the default roles on Home (the reference's Mail, Calendar and Notes), where installed. */
 static final String[] HOME_SUITE = {"dev.pixel.mail", "org.fossify.calendar.pixel", "dev.pixel.notes.agent"};

 /** The folder's accessible name; its visible label is FOLDER_LABEL. */
 static String folderDescription(int count) {
  return FOLDER_LABEL + " folder, " + count + (count == 1 ? " app" : " apps");
 }
 static final String FOLDER_LABEL = "System";

 /** The simplified glyph (HomeGlyph key) for an app, or null to draw the app's own icon (its monochrome themed layer
  * where it has one). Suite apps by package (the "@camera" slot is the preferred camera), and each duplicate by the
  * suite app it repeats, so the folder reads the same way. */
 static String glyph(String packageName, String cameraPackage) {
  if (packageName == null) return null;
  if (!packageName.isEmpty() && packageName.equals(cameraPackage) || has(CAMERAS, packageName)) return "camera";
  for (int i = 0; i < GLYPH_PACKAGES.length; i++) if (GLYPH_PACKAGES[i].equals(packageName)) return GLYPH_KEYS[i];
  return null;
 }
 static final String[] GLYPH_PACKAGES = {"com.bluebubbles.messaging", "dev.pixel.mail", "dev.pixel.mail.debug",
   "org.fossify.calendar.pixel", "org.fossify.calendar.pixel.debug", "dev.pixel.clock", "dev.pixel.calculator.agent",
   "dev.pixel.notes.agent", "dev.pixel.reminders", "dev.pixel.news.feeder", "dev.pixel.books", "dev.pixel.podcasts",
   "dev.pixel.compass", "dev.pixel.sky", "dev.pixel.weather", "dev.pixel.weather.opus", "com.android.documentsui",
   "com.android.gallery3d", "com.android.dialer", "com.android.settings", "app.vanadium.browser", "com.android.contacts",
   "dev.pixel.memories"};
 static final String[] GLYPH_KEYS = {"messages", "mail", "mail",
   "calendar", "calendar", "clock", "calculator",
   "notes", "reminders", "news", "books", "podcasts",
   "compass", "sky", "weather", "weather", "files",
   "gallery", "phone", "settings", "browser", "contacts",
   "memories"};

 private static boolean has(String[] values, String value) {
  if (value == null) return false;
  for (String v : values) if (v.equals(value)) return true;
  return false;
 }
}
