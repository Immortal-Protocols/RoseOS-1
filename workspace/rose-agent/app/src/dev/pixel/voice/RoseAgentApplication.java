package dev.pixel.voice;

import android.app.Application;

/** Rose Agent's process: starts tracking its own activities' displays before any activity exists (RoseActivities). */
public final class RoseAgentApplication extends Application {
 @Override public void onCreate() {
  super.onCreate();
  RoseActivities.install(this);
 }
}
