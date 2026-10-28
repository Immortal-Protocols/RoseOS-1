package dev.pixel.voice;

import java.util.List;

/** Whether a HOME or app-icon return may keep the Home, pager and app catalog it already built
 * (OS-HOME-REUSE-001) instead of enumerating every launcher activity and recreating every page. The stamp
 * names what that build came from: installed-package changes (a generation the package receiver bumps),
 * the screen configuration (size, density, orientation, font scale, night mode, locales) and the owner's
 * default phone/messages/camera/browser apps. The suite camera choice follows installed packages, so the
 * generation covers it. Any difference, an open conversation or detail view, or a
 * changed Home setting means a full rebuild, exactly as before. Pure logic, no Android types. */
final class HomeReuse {
 private HomeReuse() {}

 static String stamp(int packageGeneration, String configuration, List<String> dockRoles) {
  return packageGeneration + "|" + configuration + "|" + String.join(",", dockRoles);
 }

 static boolean reuse(boolean homeEntry, boolean pagerShown, String builtStamp, String currentStamp,
                      boolean conversationOpen, boolean detailOpen, boolean homeSettingChanged) {
  return homeEntry && pagerShown && builtStamp != null && builtStamp.equals(currentStamp)
    && !conversationOpen && !detailOpen && !homeSettingChanged;
 }
}
