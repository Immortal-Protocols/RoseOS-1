package dev.pixel.voice;

import android.app.Activity;
import android.app.Application;
import android.os.Bundle;
import android.view.Display;
import java.util.Collections;
import java.util.Set;
import java.util.WeakHashMap;

/**
 * Rose Agent's live activities, tracked from process start (RoseAgentApplication), so a phone command can ask where
 * they are now. ClipboardService silently drops a default-device clipboard write from an app whose activities run on a
 * virtual device's display (F07: ClipboardWrite), and the SDK exposes no public task-display query: this is how Rose
 * knows her own. Main thread only.
 */
final class RoseActivities {
 private RoseActivities() {}

 private static final Set<Activity> live = Collections.newSetFromMap(new WeakHashMap<>());
 private static boolean installed;

 static void install(Application application) {
  if (installed) return;
  installed = true;
  application.registerActivityLifecycleCallbacks(new Application.ActivityLifecycleCallbacks() {
   @Override public void onActivityCreated(Activity a, Bundle saved) { live.add(a); }
   @Override public void onActivityDestroyed(Activity a) { live.remove(a); }
   @Override public void onActivityStarted(Activity a) {}
   @Override public void onActivityResumed(Activity a) {}
   @Override public void onActivityPaused(Activity a) {}
   @Override public void onActivityStopped(Activity a) {}
   @Override public void onActivitySaveInstanceState(Activity a, Bundle out) {}
  });
 }

 /** Whether every live Rose Agent activity is on the default display now; false when that is not known. */
 static boolean allOnDefaultDisplay() {
  if (!installed) return false;
  for (Activity a : live) {
   if (a.isDestroyed()) continue;
   Display d;
   try { d = android.os.Build.VERSION.SDK_INT >= 30 ? a.getDisplay() : a.getWindowManager().getDefaultDisplay(); }
   catch (RuntimeException e) { return false; }
   if (d == null || d.getDisplayId() != Display.DEFAULT_DISPLAY) return false;
  }
  return true;
 }
}
